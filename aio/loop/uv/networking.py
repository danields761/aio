import dataclasses
import functools
import operator
import selectors
from typing import Callable

from aio.interfaces import IOEventCallback, IOSelectorRegistry
from aio.loop.pure.networking import SelectorNetworking
from aio.loop.uv.uvcffi import error_for_code, ffi, invoke_uv_fn, lib
from aio.types import Logger

assert selectors.EVENT_READ == lib.UV_READABLE
assert selectors.EVENT_WRITE == lib.UV_WRITABLE


@dataclasses.dataclass
class _PollData:
    fd: int
    callbacks: dict[IOEventCallback, int]
    exception_hook: Callable[[BaseException], None]
    uv_poll_ptr: object
    # Keep self C ptr to avoid GC
    self_ptr: object


@ffi.callback("uv_poll_cb")
def _poll_callback(uv_poll_ptr: object, status: int, events: int) -> None:
    data = ffi.from_handle(uv_poll_ptr.data)
    assert isinstance(data, _PollData)

    exc = None
    if status < 0:
        exc = error_for_code(status)

    for cb, need_events in data.callbacks.items():
        if need_events & events == 0:
            continue

        try:
            cb(data.fd, events, exc)
        except BaseException as exc:
            data.exception_hook(exc)


class UVSelector(IOSelectorRegistry):
    def __init__(self, c_loop: object, exception_hook: Callable[[BaseException], None]) -> None:
        self._c_loop = c_loop
        self._exception_hook = exception_hook

        self._polls: dict[int, _PollData] = {}

    def select(self, time_: float | None) -> list[tuple[IOEventCallback, int, int]]:
        raise NotImplementedError("Does not make sense for UV selector")

    def add_watch(self, fd: int, events: int, cb: IOEventCallback) -> None:
        try:
            _, data = self._polls[fd]
        except KeyError:
            self._polls[fd] = self._create_poll(fd, events, cb)
        else:
            self._update_poll(data, events, cb)

    def stop_watch(self, fd: int, events: int | None, cb: IOEventCallback | None) -> None:
        if (events is None) != (cb is None):
            raise ValueError("Ether both `events` and `cb` should be given, or both none")

        try:
            data = self._polls[fd]
        except KeyError:
            return

        if events is not None and cb is not None:
            try:
                cb_events = data.callbacks[cb]
            except KeyError:
                pass
            else:
                new_events = cb_events & (~events)
                if new_events != 0:
                    data.callbacks[cb] = new_events
                else:
                    del data.callbacks[cb]

        if data.callbacks:
            self._update_poll_handle(data)
            return

        invoke_uv_fn(lib.uv_poll_stop, data.uv_poll_ptr)
        del self._polls[fd]

    def wakeup_thread_safe(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        for fd in self._polls:
            self.stop_watch(fd, None, None)

    def _create_poll(self, fd: int, events: int, cb: IOEventCallback) -> _PollData:
        data = _PollData(
            fd=fd,
            callbacks={cb: events},
            exception_hook=self._exception_hook,
            uv_poll_ptr=None,
            self_ptr=None,
        )

        uv_poll_ptr = ffi.new("uv_poll_t*")
        uv_poll_ptr.data = data_ptr = ffi.new_handle(data)

        data.uv_poll_ptr = uv_poll_ptr
        data.self_c_ptr = data_ptr

        invoke_uv_fn(lib.uv_poll_init, self._c_loop, uv_poll_ptr, fd)
        self._update_poll_handle(data)

        return data

    def _update_poll(self, data: _PollData, events: int, cb: IOEventCallback) -> None:
        try:
            data.callbacks[cb] |= events
        except KeyError:
            data.callbacks[cb] = events

        self._update_poll_handle(data)

    def _update_poll_handle(self, data: _PollData) -> None:
        new_poll_events = functools.reduce(operator.or_, data.callbacks.values())
        if new_poll_events & lib.UV_READABLE:
            new_poll_events |= lib.UV_DISCONNECT
        invoke_uv_fn(lib.uv_poll_start, data.uv_poll_ptr, new_poll_events, _poll_callback)


class UVNetworking(SelectorNetworking):
    def __init__(
        self,
        selector: UVSelector,
        *,
        logger: Logger | None = None,
    ) -> None:
        super().__init__(selector, logger=logger)
