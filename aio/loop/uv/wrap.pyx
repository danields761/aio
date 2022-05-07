# distutils: libraries = uv
# cython: language_level = 3
from typing import NoReturn

from aio.loop.uv.libuv cimport

(
    uv_async_t,
    uv_loop_close,
    uv_loop_init,
    uv_loop_t,
    uv_now,
    uv_run,
    uv_run_mode,
    uv_stop,
    uv_update_time,
)
from libc.stdint cimport

uint64_t
from libc.stdlib cimport

free, malloc

from aio.interfaces import LoopStopped


cdef class AsyncHandle:
    cdef UVLoop loop
    cdef uv_async_t* handle
    
    def __cinit__(self, UVLoop loop) -> None:
        self._loop = loop


cdef class UVLoop:
    cdef uv_loop_t* _loop
    
    def __cinit__(self):
        self._loop = <uv_loop_t*> malloc(sizeof(uv_loop_t*))
        uv_loop_init(self._loop)
        if not self._loop:
            raise MemoryError
    
    def __dealloc__(self):
        if not self._loop:
            return

        uv_loop_close(self._loop)
        free(self._loop)
    
    def call_soon(self, target, args) -> int:
        pass

    def call_later(self, timeout, target, args) -> int:
        pass

    cdef uint64_t _now(self):
        uv_update_time(self._loop)
        return uv_now(self._loop)
    
    def now(self) -> int:
        return self._now()
    
    def run(self) -> NoReturn:
        res = uv_run(self._loop, uv_run_mode.UV_RUN_DEFAULT)
        if res != 0:
            raise Exception("Failed to run loop")
        raise LoopStopped
    
    def stop(self) -> None:
        uv_stop(self._loop)
