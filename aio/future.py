from __future__ import annotations

import contextvars
import inspect
import warnings
from enum import Enum, IntEnum
from typing import (
    Any,
    Coroutine,
    Generator,
    Generic,
    Literal,
    Mapping,
    Protocol,
    TypeVar,
)

from aio.exceptions import Cancelled, FutureFinishedError, FutureNotReady
from aio.interfaces import EventLoop
from aio.loop._priv import _get_running_loop

T = TypeVar("T")


class _Sentry(Enum):
    not_set = "not-set"


class FutureResultCallback(Protocol[T]):
    def __call__(self, fut: Future[T], /) -> None:
        raise NotImplementedError


class Promise(Protocol[T]):
    def set_result(self, val: T) -> None:
        raise NotImplementedError

    def set_exception(self, exc: BaseException) -> None:
        raise NotImplementedError

    def cancel(self, msg: str | None = None) -> None:
        raise NotImplementedError

    @property
    def future(self) -> Future[T]:
        raise NotImplementedError


class _FuturePromise(Promise[T]):
    def __init__(self, future: Future[T]) -> None:
        self._fut = future

    def set_result(self, val: T) -> None:
        self._fut._set_result(val=val)

    def set_exception(self, exc: BaseException) -> None:
        if isinstance(exc, Cancelled):
            raise TypeError(
                f"Use cancellation API instead of passing exception "
                f"`{Cancelled.__module__}.{Cancelled.__qualname__}` manually"
            )
        self._fut._set_result(exc=exc)

    def cancel(self, msg: str | None = None) -> None:
        self._fut._cancel(msg)

    @property
    def future(self) -> Future[T]:
        return self._fut


class Future(Generic[T]):
    class State(IntEnum):
        created = 0
        scheduled = 1
        running = 2
        finishing = 3
        finished = 4

    def __init__(self, loop: EventLoop, label: str | None = None, **context: Any) -> None:
        self._value: T | Literal[_Sentry.not_set] = _Sentry.not_set
        self._exc: BaseException | None = None
        self._label = label
        self._context: Mapping[str, Any] = {
            "future": self,
            "future_label": label,
            **context,
        }

        self._result_callbacks: set[FutureResultCallback[T]] = set()
        self._state = Future.State.running

        self._loop = loop

    @property
    def state(self) -> Future.State:
        return self._state

    @property
    def subscribers_count(self) -> int:
        return len(self._result_callbacks)

    def result(self) -> T:
        if not self.is_finished:
            raise FutureNotReady
        if self._exc is not None:
            raise self._exc
        assert self._value is not _Sentry.not_set
        return self._value

    def exception(self) -> BaseException | None:
        if not self.is_finished:
            raise FutureNotReady
        return self._exc

    def add_callback(self, cb: FutureResultCallback[T]) -> None:
        if cb in self._result_callbacks:
            return

        if self.is_finished:
            self._schedule_callback(cb)
            return

        self._result_callbacks.add(cb)

    def remove_callback(self, cb: FutureResultCallback[T]) -> None:
        try:
            self._result_callbacks.remove(cb)
        except ValueError:
            pass

    @property
    def is_finished(self) -> bool:
        finished = self._state == Future.State.finished
        return finished

    @property
    def is_cancelled(self) -> bool:
        return self.is_finished and isinstance(self._exc, Cancelled)

    def _call_callbacks(self) -> None:
        if not self.is_finished:
            raise RuntimeError("Future must finish before calling callbacks")

        for cb in self._result_callbacks:
            self._schedule_callback(cb)

    def _schedule_callback(self, cb: FutureResultCallback[T]) -> None:
        assert self.is_finished

        self._loop.call_soon(cb, self, context=self._context)

    def _set_result(
        self,
        val: T | Literal[_Sentry.not_set] = _Sentry.not_set,
        exc: BaseException | None = None,
    ) -> None:
        if val is not _Sentry.not_set and exc is not None:
            raise ValueError(
                "Both result value and exception given, but they are mutually exclusive"
            )

        if self.is_finished:
            raise FutureFinishedError

        self._state = Future.State.finished
        self._value = val
        self._exc = exc

        self._call_callbacks()

    def _cancel(self, msg: str | None = None) -> None:
        self._set_result(exc=Cancelled(msg))

    def __await__(self) -> Generator[Future[Any], None, T]:
        if not self.is_finished:
            yield self

        if not self.is_finished:
            raise FutureNotReady("The future object resumed before result has been set")

        if self._exc is not None:
            raise self._exc

        if self._value is _Sentry.not_set:
            raise RuntimeError("Both result value and exception being set")
        return self._value

    def __repr__(self) -> str:
        label = self._label if self._label else ""
        return f"<Future label={label} state={self._state.name} at {hex(id(self))}>"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self is other

    def __del__(self) -> None:
        if not self.is_finished:
            warnings.warn(
                (
                    f"Feature `{self}` is about to be destroyed, "
                    f"but not finished, that normally should never occur "
                    f"(feature context `{self._context}`)"
                ),
                stacklevel=2,
            )


_current_task: contextvars.ContextVar[Task[Any]] = contextvars.ContextVar("current-task")


class Task(Future[T]):
    def __init__(
        self, coroutine: Coroutine[Future[Any], None, T], loop: EventLoop, label: str | None = None
    ) -> None:
        if not inspect.iscoroutine(coroutine):
            raise TypeError(f"Coroutine object is expected, not `{coroutine}`")

        super().__init__(loop, label, task=self, future=None)

        self._coroutine = coroutine
        self._waiting_on: Future[Any] | None = None
        self._pending_cancellation = False
        self._state = Future.State.created
        self._started_promise: Promise[None] = _create_promise(
            "task-started-future", _loop=loop, served_task=self
        )

    def _cancel(self, msg: str | None = None) -> None:
        if self.is_finished:
            raise FutureFinishedError

        self._state = Future.State.finishing

        if self._waiting_on:
            # Recursively cancel all inner tasks
            self._waiting_on._cancel(msg)
        else:
            # TODO Check coroutine finished or not started
            super()._cancel(msg)

    def _schedule_execution(self, _: Future[Any] | None = None) -> None:
        self._loop.call_soon(self._execute_coroutine_step)
        if self._state == Future.State.created:
            self._state = Future.State.scheduled

    def _execute_coroutine_step(self) -> None:
        cv_context = contextvars.copy_context()
        try:
            try:
                future = cv_context.run(self._send_to_coroutine_within_new_context)
            except StopIteration as exc:
                val: T = exc.value
                self._set_result(val=val)
                return
            except BaseException as exc:
                self._set_result(exc=exc)
                return
        finally:
            if self._waiting_on:
                self._waiting_on.remove_callback(self._schedule_execution)

        if self._state == Future.State.scheduled:
            self._state = Future.State.running
            self._started_promise.set_result(None)

        if future is self:
            raise RuntimeError(
                "Task awaiting on itself, this will cause "
                "infinity awaiting that's why is forbidden"
            )

        if future._loop is not self._loop:
            raise RuntimeError(
                f"During processing task `{self!r}` another "
                f"feature has been `{future!r}` received, which "
                "does not belong to the same loop"
            )

        future.add_callback(self._schedule_execution)
        self._waiting_on = future

    def _set_result(
        self, val: T | Literal[_Sentry.not_set] = _Sentry.not_set, exc: BaseException | None = None
    ) -> None:
        super()._set_result(val, exc)
        if not self._started_promise.future.is_finished:
            self._started_promise.set_result(None)

    def _send_to_coroutine_within_new_context(self) -> Future[Any]:
        reset_token = _current_task.set(self)
        try:
            maybe_feature = self._coroutine.send(None)
            if not isinstance(maybe_feature, Future):
                raise RuntimeError("All `aio` coroutines must yield and `Feature` instance")
            return maybe_feature
        finally:
            _current_task.reset(reset_token)

    def __repr__(self) -> str:
        return (
            "<Task "
            f"label={self._label} "
            f"state={self._state.name} "
            f"for {self._coroutine!r}"
            ">"
        )


def _create_promise(
    label: str | None = None, *, _loop: EventLoop | None = None, **context: Any
) -> Promise[T]:
    if _loop is None:
        _loop = _get_running_loop()
    future: Future[T] = Future(_loop, label=label, **context)
    return _FuturePromise(future)


def _create_task(
    coro: Coroutine[Future[Any], None, T],
    label: str | None = None,
    *,
    _loop: EventLoop | None = None,
) -> Task[T]:
    if _loop is None:
        _loop = _get_running_loop()
    task = Task(coro, _loop, label=label)
    task._schedule_execution()
    return task
