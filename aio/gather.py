from contextlib import asynccontextmanager
from typing import Any, AsyncIterable, AsyncIterator

from aio.interfaces import Future
from aio.queue import Queue


@asynccontextmanager
async def iter_done_futures(
    *futures: Future[Any],
) -> AsyncIterator[AsyncIterable[Future[Any]]]:
    queue: Queue[Any] = Queue(max_capacity=len(futures))
    undone_count = len(futures)

    def on_future_done(fut_: Future[Any]) -> None:
        nonlocal undone_count

        queue.put_no_wait(fut_)
        undone_count -= 1
        if undone_count <= 0:
            queue.close()

    for fut in futures:
        fut.add_callback(on_future_done)

    try:
        yield queue
    finally:
        queue.close()
        for fut in futures:
            fut.remove_callback(on_future_done)
