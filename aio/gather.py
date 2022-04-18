from contextlib import asynccontextmanager
from typing import Any, AsyncIterable, AsyncIterator

from aio import channel
from aio.interfaces import Future


@asynccontextmanager
async def iter_done_futures(
    *futures: Future[Any],
) -> AsyncIterator[AsyncIterable[Future[Any]]]:
    undone_count = len(futures)

    def on_future_done(fut_: Future[Any]) -> None:
        nonlocal undone_count

        left.put_no_wait(fut_)
        undone_count -= 1
        if undone_count <= 0:
            left.close()

    for fut in futures:
        fut.add_callback(on_future_done)

    try:
        with channel.create(max_capacity=len(futures)) as (left, right):
            yield right
    finally:
        for fut in futures:
            fut.remove_callback(on_future_done)
