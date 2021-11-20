import threading
from concurrent.futures import Executor as _Executor
from concurrent.futures import Future as _Future
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, TypeVar, ParamSpec

from aio.exceptions import Cancelled, FutureFinishedError
from aio.funcs import get_loop
from aio.future import Promise, _create_promise
from aio.interfaces import EventSelector, Executor

T = TypeVar('T')
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
    loop = await get_loop()

    waiter: Promise[None] = _create_promise('thread-waiter')

    def thread_fn() -> None:
        try:
            fn(*args, **kwargs)
        except Exception as exc:
            loop.call_soon_thread_safe(_ignore_feature_finished_error, waiter.set_exception, exc)
        except BaseException as exc:
            new_exc = RuntimeError('Callable raises BaseException subclass')
            new_exc.__cause__ = exc
            loop.call_soon_thread_safe(
                _ignore_feature_finished_error, waiter.set_exception, new_exc
            )
        else:
            loop.call_soon_thread_safe(_ignore_feature_finished_error, waiter.set_result, None)

    thread = threading.Thread(target=thread_fn, name=name)
    thread.start()
    await waiter.future
    return


class ConcurrentExecutor(Executor):
    def __init__(self, selector: EventSelector, executor: _Executor) -> None:
        self._selector = selector
        self._executor = executor

    async def execute_sync_callable(
        self, fn: Callable[CPS, T], /, *args: CPS.args, **kwargs: CPS.kwargs
    ) -> T:
        loop = await get_loop()
        cfuture = self._executor.submit(fn, args)
        waiter: Promise[T] = _create_promise(label='executor-waiter')

        def on_result_from_executor(_: _Future[T]) -> None:
            assert cfuture.done()

            if cfuture.cancelled():
                loop.call_soon_thread_safe(_ignore_feature_finished_error, waiter.cancel)
            elif exc := cfuture.exception():
                if not isinstance(exc, Exception):
                    new_exc = RuntimeError('Callable raises `BaseException` subclass')
                    new_exc.__cause__ = exc
                    exc = new_exc

                loop.call_soon_thread_safe(
                    _ignore_feature_finished_error, waiter.set_exception, exc
                )
            else:
                loop.call_soon_thread_safe(
                    _ignore_feature_finished_error,
                    waiter.set_result,
                    cfuture.result,
                )

        cfuture.add_done_callback(on_result_from_executor)

        try:
            return await waiter.future
        except Cancelled:
            self._executor.submit(cfuture.cancel)
            raise


async def _close_std_executor(executor: _Executor) -> None:
    await _stupid_execute_on_thread(
        executor.shutdown,
        'executor-shutdowner',
        wait=True,
        cancel_futures=True,
    )


@asynccontextmanager
async def concurrent_executor_factory(
    selector: EventSelector, override_executor: _Executor | None = None
) -> AsyncIterator[Executor]:
    std_executor = override_executor or ThreadPoolExecutor(thread_name_prefix='loop-executor')
    executor = ConcurrentExecutor(selector, std_executor)
    try:
        yield executor
    finally:
        if not override_executor:
            await _close_std_executor(std_executor)
