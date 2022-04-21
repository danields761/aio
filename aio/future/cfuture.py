from typing import Generic, TypeVar

from aio.exceptions import Cancelled
from aio.future._cfuture import Future
from aio.future.utils import coerce_cancel_arg
from aio.interfaces import EventLoop
from aio.interfaces import Future as ABCFuture
from aio.interfaces import Promise as ABCPromise

ABCFuture.register(Future)


T = TypeVar("T")


class FuturePromise(ABCPromise, Generic[T]):
    __slots__ = ("fut",)

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
        return self._fut


def create_promise(loop: EventLoop, label: str | None) -> ABCPromise[T]:
    fut = Future(loop, label or "unnamed")
    return FuturePromise(fut)


def cancel_future(future: ABCFuture[object], msg: str | Cancelled | None = None) -> None:
    if not isinstance(future, Future):  # and not isinstance(future, Task):
        raise TypeError("Could only cancel C futures")

    future._set_exception(coerce_cancel_arg(msg))
