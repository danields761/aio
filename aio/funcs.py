from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, AsyncIterator, TypeVar

from aio.exceptions import Cancelled
from aio.future import Future, Promise, Task, _create_promise, _current_task
from aio.loop import EventLoop, _running_loop

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
T_contra = TypeVar("T_contra", contravariant=True)


async def get_loop() -> EventLoop:
    """Get event loop on which calling coroutine is running."""
    try:
        return _running_loop.get()
    except LookupError:
        raise RuntimeError(
            "Current loop isn't accessible from current coroutine, "
            "probably it being run on non-`aio` event loop instance"
        )


async def get_current_task() -> Task[Any]:
    try:
        return _current_task.get()
    except LookupError:
        raise RuntimeError(
            "Current task isn't accessible from current coroutine, "
            "probably it being run on non-`aio` event loop instance"
        )


def shield(future: Future[T]) -> Future[T]:
    shield_promise: Promise[T] = _create_promise()

    def future_done_cb(_: Future[T]) -> None:
        assert future.is_finished

        exc = future.exception()
        if exc:
            shield_promise.set_exception(exc)
        else:
            shield_promise.set_result(future.result())

    future.add_callback(future_done_cb)

    return shield_promise.future


async def sleep(sec: float) -> None:
    loop = await get_loop()

    sleep_promise: Promise[None] = _create_promise()
    handle = loop.call_later(sec, sleep_promise.set_result, None)

    try:
        await sleep_promise.future
    except Cancelled:
        handle.cancel()
        raise


@asynccontextmanager
async def guard_async_gen(
    async_gen: AsyncGenerator[T_co, T_contra]
) -> AsyncIterator[AsyncGenerator[T_co, T_contra]]:
    try:
        yield async_gen
    finally:
        await async_gen.aclose()
