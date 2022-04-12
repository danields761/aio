from __future__ import annotations

import abc
import contextlib
import contextvars
import inspect
import sys
import traceback
import warnings
from dataclasses import dataclass
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
    AsyncIterator,
    AsyncContextManager,
)

from aio.exceptions import (
    Cancelled,
    FutureFinishedError,
    FutureNotReady,
    CancelledByChild,
    CancelledByParent,
)
from aio.interfaces import EventLoop, Handle
from aio.loop._priv import _get_running_loop
from aio.utils import is_coro_running

T = TypeVar("T")


class _Sentry(Enum):
    not_set = "not-set"


class FutureResultCallback(Protocol[T]):
    def __call__(self, fut: Future[T], /) -> None:
        raise NotImplementedError


class Promise(abc.ABC, Generic[T]):
    @abc.abstractmethod
    def set_result(self, val: T) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def set_exception(self, exc: BaseException, /) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def cancel(self, msg: Cancelled | str | None = None, /) -> None:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def future(self) -> Future[T]:
        raise NotImplementedError


class Future(abc.ABC, Generic[T]):
    class State(IntEnum):
        created = 0
        scheduled = 1
        running = 2
        finishing = 3
        finished = 4

    @property
    @abc.abstractmethod
    def state(self) -> Future.State:
        raise NotImplementedError

    @abc.abstractmethod
    def result(self) -> T:
        raise NotImplementedError

    @abc.abstractmethod
    def exception(self) -> BaseException | None:
        raise NotImplementedError

    @abc.abstractmethod
    def add_callback(self, cb: FutureResultCallback[T]) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def remove_callback(self, cb: FutureResultCallback[T]) -> None:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def is_finished(self) -> bool:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def is_cancelled(self) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    def __await__(self) -> Generator[Future[Any], None, T]:
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

    def cancel(self, msg: Cancelled | str | None = None, /) -> None:
        match msg:
            case str() | None:
                self._fut._cancel(msg=msg)
            case Cancelled():
                self._fut._cancel(exc=msg)
            case _:
                assert False

    @property
    def future(self) -> Future[T]:
        return self._fut


@dataclass(frozen=True)
class _PendingState(Generic[T]):
    result_callbacks: dict[int, FutureResultCallback[T]]


@dataclass
class _SuccessState(Generic[T]):
    result: T
    scheduled_cbs: dict[int, Handle]


@dataclass
class _FailedState:
    exc: BaseException
    exc_retrieved: bool
    scheduled_cbs: dict[int, Handle]


class _SimpleFuture(Future[T], Generic[T]):
    def __init__(self, loop: EventLoop, label: str | None = None, **context: Any) -> None:
        self._state: _PendingState[T] | _SuccessState[T] | _FailedState = _PendingState(
            result_callbacks={}
        )

        self._label = label
        self._context: Mapping[str, Any] = {
            "future": self,
            "future_label": label,
            **context,
        }

        self._loop = loop

    @property
    def state(self) -> Future.State:
        match self._state:
            case _PendingState():
                return Future.State.running
            case _SuccessState() | _FailedState():
                return Future.State.finished
            case _:
                assert False

    def result(self) -> T:
        match self._state:
            case _PendingState():
                raise FutureNotReady
            case _SuccessState(result=result):
                return result
            case _FailedState(exc=exc):
                raise exc
            case _:
                assert False

    def exception(self) -> BaseException | None:
        match self._state:
            case _PendingState():
                raise FutureNotReady
            case _SuccessState():
                return None
            case _FailedState() as state:
                state.exc_retrieved = True
                return state.exc
            case _:
                assert False

    def add_callback(self, cb: FutureResultCallback[T]) -> None:
        match self._state:
            case _PendingState(result_callbacks=cbs):
                cbs[id(cb)] = cb
            case _SuccessState() | _FailedState():
                raise FutureFinishedError("Could not schedule callback for already finished future")
            case _:
                assert False

    def remove_callback(self, cb: FutureResultCallback[T]) -> None:
        match self._state:
            case _PendingState(result_callbacks=cbs):
                try:
                    del cbs[id(cb)]
                except KeyError:
                    pass
            case _SuccessState(scheduled_cbs=cbs) | _FailedState(scheduled_cbs=cbs):
                try:
                    handle = cbs.pop(id(cb))
                except KeyError:
                    pass
                else:
                    handle.cancel()
            case _:
                assert False

    @property
    def is_finished(self) -> bool:
        return isinstance(self._state, _SuccessState | _FailedState)

    @property
    def is_cancelled(self) -> bool:
        return isinstance(self._state, _FailedState) and isinstance(self._state.exc, Cancelled)

    def _schedule_callbacks(self) -> dict[int, Handle]:
        if not isinstance(self._state, _PendingState):
            raise RuntimeError("Future must finish before calling callbacks")

        return {
            cb_id: self._loop.call_soon(cb, self, context=self._context)
            for cb_id, cb in self._state.result_callbacks.items()
        }

    def _set_result(
        self,
        val: T | Literal[_Sentry.not_set] = _Sentry.not_set,
        exc: BaseException | None = None,
    ) -> None:
        if val is not _Sentry.not_set and exc is not None:
            raise ValueError(
                "Both result value and exception given, but they are mutually exclusive"
            )

        scheduled_cbs = self._schedule_callbacks()
        if val is not _Sentry.not_set:
            self._state = _SuccessState(result=val, scheduled_cbs=scheduled_cbs)
        elif exc is not None:
            self._state = _FailedState(exc=exc, scheduled_cbs=scheduled_cbs, exc_retrieved=False)
        else:
            assert False

    def _cancel(self, msg: str | None = None, exc: Cancelled | None = None) -> None:
        if msg and exc:
            raise ValueError("Either `msg` or `exc` arg should be given")

        self._set_result(exc=exc or Cancelled(msg))

    def __await__(self) -> Generator[Future[Any], None, T]:
        if isinstance(self._state, _PendingState):
            yield self

        if isinstance(self._state, _PendingState):
            raise RuntimeError("Future being resumed after first yield, but still not finished!")

        return self.result()

    def __repr__(self) -> str:
        label = self._label if self._label else ""
        return f"<Future label={label} state={self.state.name} at {hex(id(self))}>"

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
        if isinstance(self._state, _FailedState) and not self._state.exc_retrieved:
            exc_tb = ''.join(traceback.format_exception(self._state.exc))

            warnings.warn(
                (
                    f"Feature `{self}` is about to be destroyed, but her exception was never "
                    f"retrieved. Please, `await` this feature, or call `result` or "
                    f"`exception` methods to prevent exception being ignored silently. "
                    f"Ignored exception:\n{exc_tb}"
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
