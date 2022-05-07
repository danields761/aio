from __future__ import annotations

import dataclasses
from typing import Callable

from aio.interfaces import Handle
from aio.loop.uv.utils import sec_to_msec
from aio.loop.uv.uvcffi import ffi, invoke_uv_fn, lib


@dataclasses.dataclass(kw_only=True)
class UVHandle(Handle):
    exception_hook: Callable[[BaseException], None]
    _uv_handle_ptr: object = None
    _uv_data_ptr: object = None


class UVSoonHandle(UVHandle):
    def schedule(self, c_loop: object) -> None:
        if self._uv_data_ptr or self._uv_handle_ptr:
            raise RuntimeError("Already scheduled")

        uv_async = ffi.new("uv_async_t*")
        uv_async.data = data_ptr = ffi.new_handle(self)

        self._uv_handle_ptr = uv_async
        self._uv_data_ptr = data_ptr

        invoke_uv_fn(lib.uv_async_init, c_loop, uv_async, _async_callback)
        invoke_uv_fn(lib.uv_async_send, uv_async)

    def cancel(self) -> None:
        super().cancel()
        self.executed = True


class UVTimerHandle(UVHandle):
    when: float

    def schedule(self, c_loop: object) -> None:
        if self._uv_data_ptr or self._uv_handle_ptr:
            raise RuntimeError("Already scheduled")

        uv_timer_ptr = ffi.new("uv_timer_t*")
        uv_timer_ptr.data = self_ptr = ffi.new_handle(self)

        self._uv_handle_ptr = uv_timer_ptr
        self._uv_data_ptr = self_ptr

        invoke_uv_fn(lib.uv_timer_init, c_loop, uv_timer_ptr)
        invoke_uv_fn(
            lib.uv_timer_start,
            uv_timer_ptr,
            _timer_callback,
            sec_to_msec(self.when),
            10000,
        )

    def cancel(self) -> None:
        invoke_uv_fn(lib.uv_timer_stop, self._uv_handle_ptr)
        handle_ptr = ffi.cast("uv_handle_t*", self._uv_handle_ptr)
        if not lib.uv_is_closing(handle_ptr):
            lib.uv_close(handle_ptr, _noop_close)

        super().cancel()
        self.executed = True


def _invoke_handle_callback_from_uv_handler(uv_handle_ptr: object) -> None:
    assert hasattr(uv_handle_ptr, "data")
    handle = ffi.from_handle(uv_handle_ptr.data)
    assert isinstance(handle, UVHandle)

    if handle.cancelled:
        return

    try:
        handle.callback(*handle.args)
    except BaseException as exc:
        handle.exception_hook(exc)
    finally:
        handle.executed = True


@ffi.callback("uv_close_cb")
def _noop_close(handle: object) -> None:
    pass


@ffi.callback("uv_async_cb")
def _async_callback(uv_async_ptr: object) -> None:
    try:
        _invoke_handle_callback_from_uv_handler(uv_async_ptr)
    finally:
        lib.uv_close(ffi.cast("uv_handle_t*", uv_async_ptr), _noop_close)


@ffi.callback("uv_timer_cb")
def _timer_callback(uv_timer_ptr: object) -> None:
    try:
        _invoke_handle_callback_from_uv_handler(uv_timer_ptr)
    finally:
        lib.uv_timer_stop(uv_timer_ptr)
        lib.uv_close(ffi.cast("uv_handle_t*", uv_timer_ptr), _noop_close)
