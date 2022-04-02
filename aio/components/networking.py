from __future__ import annotations

import functools
import selectors
import socket
from contextlib import closing, contextmanager
from typing import TYPE_CHECKING, Any, ContextManager, Iterator, cast

import structlog
from collections import defaultdict

from aio.exceptions import SocketConfigurationError
from aio.interfaces import (
    EventCallback,
    EventSelector,
    Networking,
)

if TYPE_CHECKING:
    from aio.future import Promise
    from aio.types import Logger, STDSelector

_log = structlog.get_logger(__name__)

_SelectorKeyData = set[tuple[int, EventCallback]]


class _SelectorWakeupper:
    def __init__(
        self,
        selector: STDSelector[_SelectorKeyData],
        *,
        logger: Logger | None = None,
    ) -> None:
        self._selector = selector
        self._receiver, self._sender = socket.socketpair()
        self._logger = (logger or _log).bind(component="selector-wakeupper")

        self._init_self_pipe()

    def _init_self_pipe(self) -> None:
        self._receiver.setblocking(False)

        self._selector.register(
            self._receiver,
            selectors.EVENT_READ,
            {
                (
                    selectors.EVENT_READ,
                    cast(EventCallback, self._on_receiver_input),
                )
            },
        )

    def wakeup(self) -> None:
        try:
            self._logger.debug("Wake-up request arrived, writing one byte to self-pipe")
            self._sender.send(b"1")
        except OSError as exc:
            self._logger.warning(
                "Writing to self-pipe failed",
                exc_info=exc,
                sender_socket_fileno=self._sender.fileno(),
            )

    def _on_receiver_input(self, _: int, __: int) -> None:
        self._logger.debug("Self-pipe received READ event")
        try:
            self._receiver.recv(1024)
        except BlockingIOError:
            pass

    def close(self) -> None:
        self._selector.unregister(self._receiver)
        for sock in (self._sender, self._receiver):
            try:
                sock.close()
            except OSError as exc:
                self._logger.warning(
                    "Exception occurred while closing self-pipe",
                    exc_info=exc,
                    sock_fileno=sock.fileno(),
                )


class SelectorsEventsSelector(EventSelector):
    def __init__(
        self,
        *,
        selector: STDSelector[_SelectorKeyData] | None = None,
        logger: Logger | None = None,
    ) -> None:
        """

        :param selector: selector to use
            This class owns selector object and takes care of finalization of it,
            thus the argument intended to be used in tests.
        """
        self._selector: STDSelector[_SelectorKeyData] = selector or cast(
            STDSelector[_SelectorKeyData], selectors.DefaultSelector()
        )
        self._logger = (logger or _log).bind(component="selectors-event-selector")

        self._wakeupper = _SelectorWakeupper(self._selector, logger=logger)
        self._is_finalized = False

    def select(self, timeout: float | None) -> list[tuple[EventCallback, int, int]]:
        self._check_not_finalized()

        ready = self._selector.select(timeout)
        return [
            (callback, key.fd, events)
            for key, events in ready
            for need_events, callback in key.data
            if events & need_events == need_events
        ]

    def add_watch(self, fd: int, events: int, cb: EventCallback) -> None:
        self._check_not_finalized()

        if fd not in self._selector.get_map():
            self._selector.register(fd, events, {(events, cb)})
        else:
            key = self._selector.get_key(fd)
            self._selector.modify(
                fd,
                functools.reduce(lambda l, r: l | r, (e for e, _ in key.data)),
                key.data | {(events, cb)},
            )

    def stop_watch(self, fd: int, events: int | None, cb: EventCallback | None) -> None:
        self._check_not_finalized()

        if (events is None) != (cb is None):
            raise ValueError("`events` and `cb` args must be both either defined or not")
        if events is None and cb is None:
            self._selector.unregister(fd)
            return

        assert events is not None and cb is not None

        key = self._selector.get_key(fd)
        exists_need_events: int | None = None
        for reg_events, reg_cb in key.data:
            if reg_cb != cb:
                continue
            exists_need_events = reg_events

        if exists_need_events:
            key.data.remove((exists_need_events, cb))
            new_need_events = exists_need_events & (~events)
            if new_need_events != 0:
                key.data.add((new_need_events, cb))

        if len(key.data) == 0:
            self._selector.unregister(fd)

    def wakeup_thread_safe(self) -> None:
        self._check_not_finalized()

        self._wakeupper.wakeup()

    def close(self) -> None:
        self._is_finalized = True
        self._wakeupper.close()
        self._selector.close()

    def _check_not_finalized(self) -> None:
        if self._is_finalized:
            raise RuntimeError("Attempt to use selector which has been already finalized")


class SelectorNetworking(Networking):
    def __init__(
        self,
        selector: EventSelector,
        *,
        logger: Logger | None = None,
    ) -> None:
        self._selector = selector
        self._logger = (logger or _log).bind(component="networking")

        self._managed_sockets_refs: defaultdict[int, int] = defaultdict(lambda: 0)
        self._waiters: set[Promise[Any]] = set()

    async def wait_sock_ready_to_read(self, sock: socket.socket) -> None:
        await self._wait_sock_events(sock, selectors.EVENT_READ, "read")

    async def wait_sock_ready_to_write(self, sock: socket.socket) -> None:
        await self._wait_sock_events(sock, selectors.EVENT_WRITE, "write")

    async def _wait_sock_events(self, sock: socket.socket, events: int, event_name: str) -> None:
        from aio.future import _create_promise

        waiter: Promise[Any] = _create_promise(f"socket-{event_name}-waiter", sock=sock)

        def done_cb(_: int, __: int) -> None:
            if not waiter.future.is_finished:
                waiter.set_result(None)

        self._selector.add_watch(sock.fileno(), events, done_cb)

        self._waiters.add(waiter)
        try:
            await waiter.future
        finally:
            self._waiters.remove(waiter)
            self._selector.stop_watch(sock.fileno(), events, done_cb)

    async def sock_connect(self, sock: socket.socket, addr: str) -> None:
        with self._manage_sock(sock):
            while True:
                try:
                    sock.connect(addr)
                    return
                except BlockingIOError:
                    pass

                await self._wait_sock_events(sock, selectors.EVENT_WRITE, "connect")

    async def sock_accept(self, sock: socket.socket) -> tuple[socket.socket, Any]:
        with self._manage_sock(sock):
            while True:
                try:
                    conn, addr = sock.accept()
                except BlockingIOError:
                    pass
                else:
                    conn.setblocking(False)
                    self._managed_sockets_refs[conn.fileno()] += 1
                    return conn, addr

                await self._wait_sock_events(sock, selectors.EVENT_READ, "accept")

    async def sock_read(self, sock: socket.socket, amount: int) -> bytes:
        with self._manage_sock(sock):
            while True:
                try:
                    return sock.recv(amount)
                except BlockingIOError:
                    pass

                await self.wait_sock_ready_to_read(sock)

    async def sock_write(self, sock: socket.socket, data: bytes) -> None:
        with self._manage_sock(sock):
            sent = 0
            while sent != len(data):
                try:
                    sent += sock.send(data[sent:])
                except BlockingIOError:
                    pass

                await self.wait_sock_ready_to_write(sock)

    def close(self) -> None:
        for waiter in self._waiters:
            waiter.cancel()

        for sock_fileno, refs in self._managed_sockets_refs.items():
            if refs > 0 and sock_fileno:
                self._selector.stop_watch(sock_fileno, None, None)

    @contextmanager
    def _manage_sock(self, sock: socket.socket) -> Iterator[None]:
        self._check_socket(sock)
        sock_fileno = sock.fileno()
        self._managed_sockets_refs[sock_fileno] += 1
        try:
            yield
        finally:
            self._managed_sockets_refs[sock_fileno] -= 1
            if self._managed_sockets_refs[sock_fileno] <= 0:
                del self._managed_sockets_refs[sock_fileno]

    @staticmethod
    def _check_socket(sock: socket.socket) -> None:
        if sock.fileno() is None or sock.fileno() <= 0:
            raise SocketConfigurationError("Socket has invalid file-id")
        if sock.getblocking():
            raise SocketConfigurationError("Socket is blocking")


def create_selectors_event_selector(
    logger: Logger | None = None,
) -> ContextManager[EventSelector]:
    return closing(SelectorsEventsSelector(logger=logger))


def create_selector_networking(
    selector: EventSelector, logger: Logger | None = None
) -> ContextManager[SelectorNetworking]:
    return closing(SelectorNetworking(selector, logger=logger))
