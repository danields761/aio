from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Optional, TypeVar

from aio.exceptions import Cancelled, MultiError
from aio.funcs import shield
from aio.future import Task, _create_task
from aio.gather import iter_done_futures

if TYPE_CHECKING:
    from aio.types import Coroutine

T = TypeVar('T', covariant=True)


class TaskGroup:
    def __init__(self):
        self._tasks: list[Task[T]] = []
        self._is_finalized = False

    def spawn(self, coroutine: Coroutine[T]) -> Task[T]:
        self._check_not_finalized()
        task = _create_task(coroutine)
        self._tasks.append(task)
        return task

    async def wait_started(self, coroutine: Coroutine[T]) -> Task[T]:
        self._check_not_finalized()
        task = self.spawn(coroutine)
        await shield(task._started_future)
        return task

    def cancel(self, msg: Optional[str] = None) -> None:
        for task in self._tasks:
            task._cancel(msg)

    async def _join(self) -> None:
        self._is_finalized = True
        tasks = tuple(self._tasks)

        try:
            async with iter_done_futures(*self._tasks) as iterator:
                async for _ in iterator:
                    pass
        except Cancelled:
            raise RuntimeError(
                f'Task group `{self}` being cancelled while '
                f'joining on child tasks, that should never happened!'
            )

        assert all(
            task.is_finished() for task in tasks
        ), 'All task must be finished here'
        task_exceptions = [task.exception for task in tasks if task.exception]

        if task_exceptions:
            raise MultiError('Child task errors', *task_exceptions)

    def _check_not_finalized(self) -> None:
        if self._is_finalized:
            raise RuntimeError(
                'Spawning tasks inside task group after finalization is forbidden'
            )


@asynccontextmanager
async def task_group() -> AsyncIterator[TaskGroup]:
    body_exc: Optional[Exception] = None
    tg = TaskGroup()
    try:
        yield tg
    except Exception as exc:
        body_exc = exc

    if body_exc:
        tg.cancel(
            'Cancelling task group due to exception raised inside task group body'
        )

    join_task = _create_task(tg._join())
    try:
        # Here we will loop until `join_task` will be done ignoring all cancellation
        # requests, because we have to guarantee that after exiting from task group
        # scope all inner task will be finalized and in finished state
        while True:
            try:
                await shield(join_task)
                break
            except Cancelled:
                if join_task.is_cancelled():
                    raise
                else:
                    # TODO does it OK to cancel tasks which is already cancelling?
                    tg.cancel()

        assert join_task.is_finished()
    except (MultiError, Cancelled) as exc:
        if body_exc:
            raise MultiError('Body exception aborts children task', body_exc, exc)
        else:
            raise

    if body_exc:
        raise body_exc
