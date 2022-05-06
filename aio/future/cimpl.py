from typing import Coroutine, Generic, TypeVar, cast

from aio.exceptions import Cancelled
from aio.future._cimpl import Future, Task
from aio.future.utils import coerce_cancel_arg
from aio.interfaces import EventLoop
from aio.interfaces import Future as ABCFuture
from aio.interfaces import Promise as ABCPromise
from aio.interfaces import Task as ABCTask

ABCFuture.register(Future)
ABCTask.register(Task)


T = TypeVar("T")


class FuturePromise(ABCPromise[T], Generic[T]):
    __slots__ = ("_fut",)

    def __init__(self, fut: Future) -> None:
        self._fut = fut

    def set_result(self, val: T, /) -> None:
        self._fut._set_result(val)

    def set_exception(self, exc: BaseException, /) -> None:
        if isinstance(exc, Cancelled):
            exc_type = type(exc)
            raise TypeError(
                f"Use cancellation API instead of passing exception "
                f"`{exc_type.__module__}.{exc_type.__qualname__}` manually"
            )

        self._fut._set_exception(exc)

    def cancel(self, msg: Cancelled | str | None = None, /) -> None:
        self._fut._set_exception(coerce_cancel_arg(msg))

    @property
    def future(self) -> ABCFuture[T]:
        return cast(ABCFuture[T], self._fut)


def create_promise(loop: EventLoop, label: str | None) -> ABCPromise[T]:
    fut = Future(loop, label or "unnamed")
    return FuturePromise(fut)


def create_task(
    coroutine: Coroutine[ABCFuture[object], None, T], loop: EventLoop, label: str | None = None
) -> ABCTask[T]:
    task = Task(coroutine, loop, label or "unnamed")
    task._schedule()
    assert isinstance(task, ABCTask)
    return cast(ABCTask[T], task)


def cancel_future(future: ABCFuture[object], msg: str | Cancelled | None = None) -> None:
    match future:
        case Task():  # type: ignore[misc]
            future._cancel(coerce_cancel_arg(msg))
        case Future():  # type: ignore[misc]
            future._set_exception(coerce_cancel_arg(msg))
        case _:
            raise TypeError("Could only cancel C futures")
