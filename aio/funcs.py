from contextlib import asynccontextmanager
from typing import AsyncGenerator, AsyncIterable, TypeVar

from aio.exceptions import Cancelled
from aio.future import _create_promise
from aio.loop import _get_loop_inner

T = TypeVar('T')
T_co = TypeVar('T_co', covariant=True)
T_contra = TypeVar('T_contra', contravariant=True)


async def sleep(sec: float) -> None:
    loop = _get_loop_inner()

    sleep_promise = _create_promise()
    handle = loop.call_later(sec, sleep_promise.set_result, None)

    try:
        await sleep_promise.future
    except Cancelled:
        handle.cancel()
        raise


@asynccontextmanager
async def guard_async_gen(
    async_gen: AsyncGenerator[T_co, T_contra]
) -> AsyncIterable[T_co]:
    try:
        yield async_gen
    finally:
        await async_gen.aclose()
