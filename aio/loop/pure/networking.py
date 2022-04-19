from __future__ import annotations

import functools
import selectors
import socket
from collections import defaultdict
from contextlib import closing, contextmanager
from typing import Any, ContextManager, Iterator, Literal

from aio.exceptions import SocketConfigurationError
from aio.interfaces import (
    IOEventCallback,
    IOSelector,
    IOSelectorRegistry,
    Networking,
    Promise,
    SocketAddress,
    SocketLike,
)
from aio.types import Logger, STDSelector
from aio.utils import get_logger

_SelectorKeyData = set[tuple[int, IOEventCallback]]


class _SelectorWakeupper:
    def __init__(
        self,
        selector: STDSelector[_SelectorKeyData],
        *,
        logger: Logger | None = None,
    ) -> None:
        self._selector = selector
        self._receiver, self._sender = socket.socketpair()
        self._logger = (logger or get_logger()).bind(component="selector-wakeupper")

        self._init_self_pipe()

    def _init_self_pipe(self) -> None:
        self._receiver.setblocking(False)

        self._selector.register(
            self._receiver,
            selectors.EVENT_READ,
            {(selectors.EVENT_READ, self._on_receiver_input)},
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


class SelectorsEventsSelector(IOSelectorRegistry, IOSelector):
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
        self._selector: STDSelector[_SelectorKeyData] = selector or selectors.DefaultSelector()
        self._logger = (logger or get_logger()).bind(component="selectors-event-selector")

        self._wakeupper = _SelectorWakeupper(self._selector, logger=logger)
        self._is_finalized = False

    def select(
        self, timeout: float | None
    ) -> list[tuple[IOEventCallback, int, int, OSError | None]]:
        self._check_not_finalized()

        try:
            ready = self._selector.select(timeout)
        except OSError as exc:
            self._logger.warning("Exception while poll")
            return [
                (callback, key.fd, 0, exc)
                for key in self._selector.get_map().values()
                for _, callback in key.data
            ]

        return [
            (callback, key.fd, events, None)
            for key, events in ready
            for need_events, callback in key.data
            if events & need_events != 0
        ]

    def add_watch(self, fd: int, events: int, cb: IOEventCallback) -> None:
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

    def stop_watch(self, fd: int, events: int | None, cb: IOEventCallback | None) -> None:
        self._check_not_finalized()

        if (events is None) != (cb is None):
            raise ValueError("`events` and `cb` args must be both either defined or not")
        if events is None and cb is None:
            try:
                self._selector.unregister(fd)
            except KeyError:
                pass
            return

        assert events is not None and cb is not None

        try:
            key = self._selector.get_key(fd)
        except KeyError:
            return
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
            try:
                self._selector.unregister(fd)
            except KeyError:
                pass

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
        selector: IOSelectorRegistry,
        *,
        logger: Logger | None = None,
    ) -> None:
        self._selector = selector
        self._logger = (logger or get_logger()).bind(component="networking")

        self._managed_sockets_refs: defaultdict[int, int] = defaultdict(lambda: 0)
        self._waiters: set[Promise[Any]] = set()

    async def wait_sock_event(
        self, sock: SocketLike, *events: Literal["read", "write"], label: str | None = None
    ) -> None:
        with self._manage_sock(sock) as fd:
            int_events = 0
            if "read" in events:
                int_events |= selectors.EVENT_READ
            if "write" in events:
                int_events |= selectors.EVENT_WRITE

            if not label:
                label = "-".join(events)

            return await self._wait_sock_events(fd, int_events, label)

    async def _wait_sock_events(self, sock: int, events: int, event_name: str) -> None:
        from aio.future import _create_promise

        waiter: Promise[Any] = _create_promise(f"socket-{event_name}-waiter", sock=sock)

        def done_cb(_: int, __: int, exc: BaseException | None) -> None:
            if waiter.future.is_finished:
                return

            if not exc:
                waiter.set_result(None)
            else:
                waiter.set_exception(exc)

        self._selector.add_watch(sock, events, done_cb)

        self._waiters.add(waiter)
        try:
            await waiter.future
        finally:
            self._waiters.remove(waiter)
            self._selector.stop_watch(sock, events, done_cb)

    async def sock_connect(self, sock: socket.socket, addr: SocketAddress) -> None:
        with self._manage_sock(sock) as fd:
            while True:
                try:
                    sock.connect(addr)
                    return
                except BlockingIOError:
                    pass

                await self._wait_sock_events(
                    fd, selectors.EVENT_WRITE | selectors.EVENT_READ, "connect"
                )

    async def sock_accept(self, sock: socket.socket) -> tuple[socket.socket, SocketAddress]:
        with self._manage_sock(sock) as fd:
            while True:
                try:
                    conn, addr = sock.accept()
                except BlockingIOError:
                    pass
                else:
                    conn.setblocking(False)
                    self._managed_sockets_refs[conn.fileno()] += 1
                    return conn, addr

                await self._wait_sock_events(fd, selectors.EVENT_READ, "accept")

    async def sock_read(self, sock: SocketLike, amount: int) -> bytes:
        with self._manage_sock(sock) as fd:
            while True:
                try:
                    return sock.recv(amount)
                except BlockingIOError:
                    pass

                await self._wait_sock_events(fd, selectors.EVENT_READ, "read")

    async def sock_write(self, sock: SocketLike, data: bytes) -> int:
        with self._manage_sock(sock) as fd:
            while True:
                try:
                    return sock.send(data)
                except BlockingIOError:
                    pass

                await self._wait_sock_events(fd, selectors.EVENT_WRITE, "write")

    async def sock_write_all(self, sock: SocketLike, data: bytes) -> None:
        with self._manage_sock(sock) as fd:
            sent = 0
            while sent != len(data):
                try:
                    sent_l = sock.send(data[sent:])
                    assert sent_l > 0
                    sent += sent_l

                    if sent == len(data):
                        return
                except BlockingIOError:
                    pass

                await self._wait_sock_events(fd, selectors.EVENT_WRITE, "write-all")

    def close(self) -> None:
        for waiter in self._waiters:
            waiter.cancel()

        for sock_fileno, refs in self._managed_sockets_refs.items():
            if refs > 0 and sock_fileno:
                self._selector.stop_watch(sock_fileno, None, None)

    @contextmanager
    def _manage_sock(self, sock: SocketLike) -> Iterator[int]:
        sock_fileno = self._check_socket(sock)
        self._managed_sockets_refs[sock_fileno] += 1
        try:
            yield sock_fileno
        finally:
            self._managed_sockets_refs[sock_fileno] -= 1
            if self._managed_sockets_refs[sock_fileno] <= 0:
                del self._managed_sockets_refs[sock_fileno]

    @staticmethod
    def _check_socket(sock: SocketLike) -> int:
        match sock:
            case int():
                if sock <= 0:
                    raise SocketConfigurationError("Socket has invalid file-id")
                return sock
            case socket.socket():
                if sock.fileno() is None or sock.fileno() <= 0:
                    raise SocketConfigurationError("Socket has invalid file-id")
                if sock.getblocking():
                    raise SocketConfigurationError("Socket is blocking")
                return sock.fileno()
            case _:
                if sock.fileno() is None or sock.fileno() <= 0:
                    raise SocketConfigurationError("Socket has invalid file-id")
                return sock.fileno()


def create_selectors_event_selector(
    logger: Logger | None = None,
) -> ContextManager[SelectorsEventsSelector]:
    return closing(SelectorsEventsSelector(logger=logger))


def create_selector_networking(
    selector: IOSelectorRegistry, logger: Logger | None = None
) -> ContextManager[SelectorNetworking]:
    return closing(SelectorNetworking(selector, logger=logger))
