import selectors
import socket
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Iterator, Optional, cast

from structlog import get_logger
from structlog.stdlib import BoundLogger

from aio.exceptions import SocketMustBeNonBlockingError
from aio.interfaces import Clock, EventCallback, EventSelector, Networking
from aio.log import log

if TYPE_CHECKING:
    from aio.future import Promise


class MonotonicClock(Clock):
    def __init__(self):
        self._resolution = time.get_clock_info('monotonic').resolution

    def now(self) -> float:
        return time.monotonic()

    def resolution(self) -> float:
        return self._resolution


class DummySelector(EventSelector):
    MAX_WAIT_TIME = 2.0

    def select(self, timeout: float) -> None:
        if __debug__:
            log(f'dummy selector will sleep for {timeout} sec')

        if timeout is None:
            timeout = self.MAX_WAIT_TIME
        elif timeout < 0:
            timeout = 0
        time.sleep(timeout)

    def add_watch(self, fd: int, event_mask: int, cb: EventCallback) -> None:
        raise NotImplementedError('dummy cant do this :(')

    def stop_watch(self, fd: int, events: Optional[int], cb: Optional[EventCallback]):
        raise NotImplementedError('dummy cant do this :(')

    def wakeup_thread_safe(self) -> None:
        raise NotImplementedError('dummy cant do this :(')

    def finalize(self) -> None:
        pass


class SelectorsSelectorImpl(EventSelector):
    def __init__(
        self,
        callback_invoker: Callable[[EventCallback, int, int], None],
        selector: Optional[selectors.BaseSelector] = None,
    ):
        self._selector = selector or selectors.DefaultSelector()
        self._is_finalized = False
        self._callback_invoker = callback_invoker

    def select(self, timeout: Optional[float]) -> None:
        self._check_not_finalized()
        ready = self._selector.select(timeout)
        for key, events in ready:
            for need_events, callbacks in key.data.items():
                if events & need_events == 0:
                    continue
                for callback in callbacks:
                    assert callable(callback)
                    self._callback_invoker(
                        cast(EventCallback, callback), key.fd, events
                    )

    def add_watch(self, fd: int, events: int, cb: EventCallback) -> None:
        self._check_not_finalized()
        try:
            self._selector.register(fd, events, {events: [cb]})
        except KeyError:
            key = self._selector.get_key(fd)
            self._selector.modify(
                fd,
                key.events | events,
                key.data | {events: key.data.get(events, []) + [cb]},
            )

    def stop_watch(
        self, fd: int, events: Optional[int], cb: Optional[EventCallback]
    ) -> None:
        self._check_not_finalized()

        if (events is not None) != (cb is not None):
            raise ValueError('`events` and `cb` args must be both defined or not')
        if events is None and cb is None:
            self._selector.unregister(fd)
            return

        key = self._selector.get_key(fd)
        data_dict = dict(key.data)
        try:
            data_dict.get(events, []).remove(cb)
        except ValueError:
            pass

        if len(data_dict) == 0 or all(len(cbs) == 0 for cbs in data_dict.values()):
            self._selector.unregister(fd)

    def wakeup_thread_safe(self) -> None:
        raise NotImplementedError

    def finalize(self) -> None:
        self._is_finalized = True
        self._selector.close()

    def _check_not_finalized(self) -> None:
        if self._is_finalized:
            raise RuntimeError(
                'Attempt to use selector which has been already finalized'
            )


class SelectorNetworking(Networking):
    def __init__(
        self,
        *,
        selector: Optional[EventSelector] = None,
        logger: Optional[BoundLogger] = None,
    ):
        if not logger:
            logger = get_logger(__name__)
        self._logger = logger
        self._selector = selector
        self._managed_sockets: set[socket.socket] = set()
        self._waiters: set[Promise[Any]] = set()

    async def wait_sock_ready_to_read(self, sock: socket.socket) -> None:
        await self._wait_sock_events(sock, selectors.EVENT_READ, 'read')

    async def wait_sock_ready_to_write(self, sock: socket.socket) -> None:
        await self._wait_sock_events(sock, selectors.EVENT_WRITE, 'write')

    async def _wait_sock_events(
        self, sock: socket.socket, events: int, event_name: str
    ) -> None:
        from aio.future import _create_promise

        waiter = _create_promise(f'socket-{event_name}-waiter', sock=sock)

        def done_cb(_: int, __: int) -> None:
            waiter.set_result(None)

        self._selector.add_watch(sock.fileno(), events, done_cb)

        try:
            await waiter.future
        finally:
            self._selector.stop_watch(sock.fileno(), events, done_cb)

    async def sock_connect(self, sock: socket.socket, addr: str) -> None:
        with self._manage_sock(sock):
            while True:
                try:
                    sock.connect(addr)
                    return
                except BlockingIOError:
                    pass

                await self._wait_sock_events(sock, selectors.EVENT_WRITE, 'connect')

    async def sock_accept(self, sock: socket.socket) -> tuple[socket.socket, Any]:
        with self._manage_sock(sock):
            while True:
                try:
                    conn, addr = sock.accept()
                    conn.setblocking(False)
                    return conn, addr
                except BlockingIOError:
                    pass

                await self._wait_sock_events(sock, selectors.EVENT_READ, 'accept')

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

    def finalize(self) -> None:
        for waiter in self._waiters:
            waiter.cancel()

        for sock in self._managed_sockets:
            self._selector.stop_watch(sock.fileno(), None, None)

    @contextmanager
    def _manage_sock(self, sock: socket.socket) -> Iterator[None]:
        self._check_non_blocking(sock)
        self._managed_sockets.add(sock)
        try:
            yield
        finally:
            self._managed_sockets.remove(sock)

    @staticmethod
    def _check_non_blocking(sock: socket.socket) -> None:
        if sock.getblocking():
            raise SocketMustBeNonBlockingError(sock)
