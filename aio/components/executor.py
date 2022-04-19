import threading
from concurrent.futures import Executor as _Executor
from concurrent.futures import Future as _Future
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, ParamSpec, TypeVar

from aio.exceptions import Cancelled, FutureFinishedError
from aio.future import create_promise
from aio.interfaces import Executor, IOSelector, Promise
from aio.loop import get_running

T = TypeVar("T")
CPS = ParamSpec("CPS")


def _ignore_feature_finished_error(
    fn: Callable[CPS, None], *args: CPS.args, **kwargs: CPS.kwargs
) -> None:
    try:
        fn(*args, **kwargs)
    except FutureFinishedError:
        pass


async def _stupid_execute_on_thread(
    fn: Callable[CPS, Any], name: str, /, *args: CPS.args, **kwargs: CPS.kwargs
) -> None:
    loop = await get_running()

    def thread_fn() -> None:
        try:
            fn(*args, **kwargs)
        except BaseException as exc:
            loop.call_soon_thread_safe(_ignore_feature_finished_error, waiter.set_exception, exc)
        else:
            loop.call_soon_thread_safe(_ignore_feature_finished_error, waiter.set_result, None)

    thread = threading.Thread(target=thread_fn, name=name)
    thread.start()

    async with create_promise("thread-waiter") as waiter:
        await waiter.future
    return


class ConcurrentExecutor(Executor):
    def __init__(self, executor: _Executor) -> None:
        self._executor = executor

    async def execute_sync_callable(
        self, fn: Callable[CPS, T], /, *args: CPS.args, **kwargs: CPS.kwargs
    ) -> T:
        loop = await get_running()
        cfuture = self._executor.submit(fn, args)

        def on_result_from_executor(_: _Future[T]) -> None:
            assert cfuture.done()

            if cfuture.cancelled():
                loop.call_soon_thread_safe(_ignore_feature_finished_error, waiter.cancel)
            elif exc := cfuture.exception():
                loop.call_soon_thread_safe(
                    _ignore_feature_finished_error, waiter.set_exception, exc
                )
            else:
                loop.call_soon_thread_safe(
                    _ignore_feature_finished_error,
                    waiter.set_result,
                    cfuture.result,
                )

        async with create_promise(label="executor-waiter") as waiter:
            cfuture.add_done_callback(on_result_from_executor)
            try:
                return await waiter.future
            except Cancelled:
                self._executor.submit(cfuture.cancel)
                raise


async def _close_std_executor(executor: _Executor) -> None:
    await _stupid_execute_on_thread(
        executor.shutdown,
        "executor-shutdowner",
        wait=True,
        cancel_futures=True,
    )


@asynccontextmanager
async def concurrent_executor_factory(
    override_executor: _Executor | None = None,
) -> AsyncIterator[Executor]:
    std_executor = override_executor or ThreadPoolExecutor(thread_name_prefix="loop-executor")
    executor = ConcurrentExecutor(std_executor)
    try:
        yield executor
    finally:
        if not override_executor:
            await _close_std_executor(std_executor)
