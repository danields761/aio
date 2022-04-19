from __future__ import annotations

import contextlib
import sys
from typing import Any, AsyncContextManager, AsyncIterator, Coroutine, TypeVar

from aio.exceptions import Cancelled, CancelledByChild, CancelledByParent
from aio.future._priv import current_task_cv
from aio.future.pure import _create_promise, _create_task
from aio.interfaces import Future, Promise, Task

T = TypeVar("T")


@contextlib.asynccontextmanager
async def create_promise(label: str | None = None) -> AsyncIterator[Promise[T]]:
    promise = _create_promise(label)
    try:
        yield promise
    finally:
        if not promise.future.is_finished:
            promise.cancel()


def create_task(
    coro: Coroutine[Future[Any], None, T], label: str | None = None
) -> AsyncContextManager[Task[T]]:
    task = _create_task(coro, label)
    return _guard_task(task)


@contextlib.asynccontextmanager
async def _guard_task(task: Task[T]) -> AsyncIterator[Task[T]]:
    from aio.funcs import shield

    current_task = current_task_cv.get()

    def on_task_done(fut: Future[T]) -> None:
        if not fut.exception():
            return

        assert isinstance(current_task, Task)  # mypy
        current_task.cancel(exc=CancelledByChild("Child task finished with an exception"))

    task.add_callback(on_task_done)
    try:
        yield task
    except (Exception, Cancelled):
        task.remove_callback(on_task_done)
        if not task.is_finished:
            new_exc = CancelledByParent("Parent task being aborted with an exception")
            task.cancel(exc=new_exc)
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
