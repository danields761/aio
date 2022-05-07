from __future__ import annotations

import contextlib
import errno
import warnings
from typing import Any, AsyncContextManager, AsyncIterator, Iterator

from aio import Executor, Networking
from aio.interfaces import LoopPolicy, LoopRunner
from aio.loop._priv import get_running_loop
from aio.loop.pure.networking import SelectorNetworking
from aio.loop.uv.loop import UVEventLoop, UVLoopRunner
from aio.loop.uv.networking import UVSelector
from aio.loop.uv.uvcffi import collect_loop_handles, ffi, invoke_uv_fn, lib


class UVPolicy(LoopPolicy[UVEventLoop]):
    @contextlib.contextmanager
    def create_loop(self, **kwargs: Any) -> Iterator[UVEventLoop]:
        c_loop = ffi.new("uv_loop_t*")
        invoke_uv_fn(lib.uv_loop_init, c_loop)
        try:
            yield UVEventLoop(c_loop)
        finally:
            try:
                invoke_uv_fn(lib.uv_loop_close, c_loop)
            except OSError as exc:
                if exc.errno != errno.EBUSY:
                    raise

                active_handlers = collect_loop_handles(c_loop)
                warnings.warn(
                    "Trying to close event loop while there "
                    f"is still active handlers: {active_handlers}"
                )
                raise

    def create_loop_runner(self, loop: UVEventLoop) -> LoopRunner[UVEventLoop]:
        return UVLoopRunner(loop)

    @contextlib.asynccontextmanager
    async def create_networking(self) -> AsyncIterator[Networking]:
        loop = get_running_loop()
        assert isinstance(loop, UVEventLoop)

        with contextlib.closing(UVSelector(loop.c_loop, loop.report_exception)) as selector:
            yield SelectorNetworking(selector)

    def create_executor(self) -> AsyncContextManager[Executor]:
        raise NotImplementedError
