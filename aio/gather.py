from contextlib import asynccontextmanager
from typing import Any, AsyncIterable, AsyncIterator

from aio.future import Future
from aio.queue import Queue


@asynccontextmanager
async def iter_done_futures(
    *futures: Future[Any],
) -> AsyncIterator[AsyncIterable[Future[Any]]]:
    queue = Queue(max_capacity=len(futures))

    def on_future_done(fut_: Future[Any]) -> None:
        queue.put_no_wait(fut_)
        if all(fut.is_finished() for fut in futures):
            queue.close()

    for fut in futures:
        fut.add_callback(on_future_done)

    try:
        yield queue
    finally:
        for fut in futures:
            fut.remove_callback(on_future_done)
