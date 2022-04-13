from contextlib import asynccontextmanager
from typing import AsyncGenerator, AsyncIterator, TypeVar

from aio.exceptions import Cancelled, FutureFinishedError
from aio.future import create_promise
from aio.interfaces import Future
from aio.loop import get_running as get_running_loop

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
T_contra = TypeVar("T_contra", contravariant=True)


async def shield(future: Future[T]) -> T:
    if future.is_finished:
        return await future

    def future_done_cb(_: Future[T]) -> None:
        assert future.is_finished

        exc = future.exception()
        match exc:
            case Cancelled():
                shield_promise.cancel(exc)
            case BaseException():
                shield_promise.set_exception(exc)
            case None:
                shield_promise.set_result(future.result())
            case _:
                assert False

    async with create_promise("shield-promise") as shield_promise:
        future.add_callback(future_done_cb)
        try:
            return await shield_promise.future
        finally:
            future.remove_callback(future_done_cb)


async def sleep(sec: float) -> None:
    def on_timeout() -> None:
        try:
            sleep_promise.set_result(None)
        except FutureFinishedError:
            pass

    loop = await get_running_loop()
    async with create_promise("sleep-promise") as sleep_promise:
        if sec == 0:
            handle = loop.call_soon(on_timeout)
        else:
            handle = loop.call_later(sec, on_timeout)

        try:
            await sleep_promise.future
        finally:
            handle.cancel()


@asynccontextmanager
async def guard_async_gen(
    async_gen: AsyncGenerator[T_co, T_contra]
) -> AsyncIterator[AsyncGenerator[T_co, T_contra]]:
    try:
        yield async_gen
    finally:
        await async_gen.aclose()
