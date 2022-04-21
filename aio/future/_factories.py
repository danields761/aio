from __future__ import annotations

import contextlib
import sys
from typing import Any, AsyncContextManager, AsyncIterator, Coroutine, TypeVar

from aio.exceptions import Cancelled, CancelledByChild, CancelledByParent
from aio.future import cfuture, pure
from aio.future._priv import current_task_cv
from aio.interfaces import Future, Promise, Task
from aio.loop._priv import get_running_loop

T = TypeVar("T")

create_promise_from_loop = pure.create_promise
create_task_from_loop = pure.create_task


def cancel_future(future: Future[object], msg: str | Cancelled | None = None) -> None:
    if isinstance(future, cfuture.Future):
        return cfuture.cancel_future(future, msg)
    elif isinstance(future, pure.Future):
        return pure.cancel_future(future, msg)

    type_ = type(future)
    raise TypeError(
        f"Unexpected future implementation of {type_.__module__}.{type.__qualname__}: {future!r}"
    )


@contextlib.asynccontextmanager
async def create_promise(label: str | None = None) -> AsyncIterator[Promise[T]]:
    promise = create_promise_from_loop(get_running_loop(), label)
    try:
        yield promise
    finally:
        if not promise.future.is_finished:
            promise.cancel()


def create_task(
    coro: Coroutine[Future[Any], None, T], label: str | None = None
) -> AsyncContextManager[Task[T]]:
    task = create_task_from_loop(coro, get_running_loop(), label)
    return _guard_task(task)


@contextlib.asynccontextmanager
async def _guard_task(task: Task[T]) -> AsyncIterator[Task[T]]:
    from aio.funcs import shield

    current_task = current_task_cv.get()

    def on_task_done(fut: Future[T]) -> None:
        if not fut.exception():
            return

        assert isinstance(current_task, Task)  # mypy
        cancel_future(current_task, CancelledByChild("Child task finished with an exception"))

    task.add_callback(on_task_done)
    try:
        yield task
    except (Exception, Cancelled):
        task.remove_callback(on_task_done)
        if not task.is_finished:
            new_exc = CancelledByParent("Parent task being aborted with an exception")
            cancel_future(task, new_exc)
        raise
    finally:
        task.remove_callback(on_task_done)
        _, parent_exc, _ = sys.exc_info()
        try:
            await shield(task)
        except CancelledByParent:
            pass
        except (Exception, Cancelled) as child_exc:
            if isinstance(parent_exc, CancelledByChild):
                raise child_exc
