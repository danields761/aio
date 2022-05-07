from __future__ import annotations

import sys
import traceback
from typing import Any, Callable, Mapping, NoReturn, ParamSpec

from aio import EventLoop, Handle
from aio.interfaces import Clock, LoopRunner, LoopStopped
from aio.loop._priv import running_loop
from aio.loop.uv.clock import UVClock
from aio.loop.uv.handles import UVSoonHandle, UVTimerHandle
from aio.loop.uv.signal import reinstall_std_signal_handlers
from aio.loop.uv.uvcffi import invoke_uv_fn, lib

CPS = ParamSpec("CPS")


class UVEventLoop(EventLoop):
    def __init__(self, loop: object) -> None:
        self._loop = loop
        self._clock = UVClock(loop)
        self._ptr_store: set[object] = set()

    def report_exception(self, exc: BaseException) -> None:
        print("EXCEPTION", file=sys.stderr)
        traceback.print_exception(exc)

    @property
    def c_loop(self) -> object:
        return self._loop

    def get_prt_store(self) -> set[object]:
        return self._ptr_store

    def call_soon(
        self,
        target: Callable[CPS, None],
        *args: CPS.args,
        context: Mapping[str, Any] | None = None,
    ) -> Handle:
        handle = UVSoonHandle(
            when=None,
            callback=target,
            args=args,
            context=context or {},
            exception_hook=self.report_exception,
        )
        handle.schedule(self.c_loop)
        return handle

    def call_later(
        self,
        timeout: float,
        target: Callable[CPS, None],
        *args: CPS.args,
        context: Mapping[str, Any] | None = None,
    ) -> Handle:
        handle = UVTimerHandle(
            when=timeout,
            callback=target,
            args=args,
            context=context or {},
            exception_hook=self.report_exception,
        )
        handle.schedule(self.c_loop)
        return handle

    @property
    def clock(self) -> Clock:
        return self._clock


class UVLoopRunner(LoopRunner[UVEventLoop]):
    def __init__(self, loop: UVEventLoop) -> None:
        self._loop = loop
        self._c_loop = loop.c_loop

    def get_loop(self) -> UVEventLoop:
        return self._loop

    def run_loop(self) -> NoReturn:
        token = running_loop.set(self._loop)
        try:
            res = 1
            while res > 0:
                with reinstall_std_signal_handlers(self._loop):
                    invoke_uv_fn(lib.uv_run, self._c_loop, lib.UV_RUN_DEFAULT)

                res = invoke_uv_fn(lib.uv_run, self._c_loop, lib.UV_RUN_DEFAULT)
        finally:
            running_loop.reset(token)
        raise LoopStopped

    def stop_loop(self) -> None:
        lib.uv_stop(self._c_loop)
