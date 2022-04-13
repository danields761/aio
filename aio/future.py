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
        finished = 4

    @property
    @abc.abstractmethod
    def state(self) -> Future.State:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def loop(self) -> EventLoop:
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
    def __init__(self, future: _PureFuture[T]) -> None:
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


class _PureFuture(Future[T], Generic[T]):
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

    @property
    def loop(self) -> EventLoop:
        return self._loop

    def result(self) -> T:
        match self._state:
            case _PendingState():
                raise FutureNotReady
            case _SuccessState(result=result):
                return result
            case _FailedState() as state:
                state.exc_retrieved = True
                raise state.exc
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

        if not isinstance(self._state, _PendingState):
            raise FutureFinishedError

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


@dataclass(frozen=True, kw_only=True)
class _BaseTaskState(_PendingState[T], Generic[T]):
    expect_coro_state: str
    coroutine: Coroutine[Future[Any], None, T]

    def __post_init__(self) -> None:
        coro_state = inspect.getcoroutinestate(self.coroutine)
        expect_state = self.expect_coro_state
        if coro_state != expect_state:
            raise RuntimeError(
                "Inconsistent task state: "
                f"coroutine in state {coro_state!r}, but {expect_state!r} expected"
            )


@dataclass(frozen=True, kw_only=True)
class _CreatedState(_BaseTaskState[T], Generic[T]):
    expect_coro_state: Literal["CORO_CREATED"] = "CORO_CREATED"


@dataclass(frozen=True, kw_only=True)
class _ScheduledState(_BaseTaskState[T], Generic[T]):
    handle: Handle
    expect_coro_state: Literal["CORO_CREATED"] = "CORO_CREATED"


@dataclass(frozen=True, kw_only=True)
class _RunningState(_BaseTaskState[T], Generic[T]):
    waiting_on: _PureFuture[Any]
    expect_coro_state: Literal["CORO_SUSPENDED"] = "CORO_SUSPENDED"


_TaskState = (
    _CreatedState[T] | _ScheduledState[T] | _RunningState[T] | _SuccessState[T] | _FailedState
)


class Task(_PureFuture[T]):
    def __init__(
        self, coroutine: Coroutine[Future[Any], None, T], loop: EventLoop, label: str | None = None
    ) -> None:
        super().__init__(loop, label, task=self, future=None)
        self._state: _TaskState = _CreatedState(result_callbacks={}, coroutine=coroutine)

    @property
    def state(self) -> Future.State:
        match self._state:
            case _CreatedState():
                return Future.State.created
            case _ScheduledState():
                return Future.State.scheduled
            case _RunningState():
                return Future.State.running
            case _:
                return super().state

    def _set_result(
        self, val: T | Literal[_Sentry.not_set] = _Sentry.not_set, exc: BaseException | None = None
    ) -> None:
        if isinstance(self._state, _RunningState) and is_coro_running(self._state.coroutine):
            raise RuntimeError(
                f"Attempt to finish task before it coroutine finished, task {self!r}. "
                "Setting task result allowed either when coroutine finished normally, or "
                "if it not started."
            )

        super()._set_result(val, exc)

    def _cancel(self, msg: str | None = None, exc: Cancelled | None = None) -> None:
        if not isinstance(self._state, _PendingState):
            raise FutureFinishedError

        match self._state:
            case _ScheduledState(handle=handle):
                assert not handle.executed, "Handle being executed, but state not changed"
                # Cancel scheduled first step
                handle.cancel()
                super()._cancel(msg, exc=exc)
            case _RunningState(waiting_on=waiting_on):
                # Recursively cancel all inner tasks
                waiting_on._cancel(msg=msg, exc=exc)
            case _CreatedState() | _:
                super()._cancel(msg=msg, exc=exc)

    def _schedule_first_step(self) -> None:
        if not isinstance(self._state, _CreatedState):
            raise RuntimeError("Only newly created tasks can be scheduled for first step")

        self._state = _ScheduledState(
            self._state.result_callbacks,
            coroutine=self._state.coroutine,
            handle=self._loop.call_soon(self._execute_coroutine_step),
        )

    def _schedule_step(self, _: Future[Any] | None = None) -> None:
        if not isinstance(self._state, _RunningState):
            raise RuntimeError("Could not schedule task step for non-running task")

        self._loop.call_soon(self._execute_coroutine_step)

    def _execute_coroutine_step(self) -> None:
        if not isinstance(self._state, _ScheduledState | _RunningState):
            raise RuntimeError("Trying to resume finished task")

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
            if isinstance(self._state, _RunningState):
                self._state.waiting_on.remove_callback(self._schedule_step)

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

        future.add_callback(self._schedule_step)
        self._state = _RunningState(
            result_callbacks=self._state.result_callbacks,
            coroutine=self._state.coroutine,
            waiting_on=future,
        )

    def _send_to_coroutine_within_new_context(self) -> _PureFuture[Any]:
        assert isinstance(self._state, _ScheduledState | _RunningState)

        reset_token = _current_task.set(self)
        try:
            maybe_feature = self._state.coroutine.send(None)
            if not isinstance(maybe_feature, _PureFuture):
                raise RuntimeError("All `aio` coroutines must yield and `Feature` instance")
            return maybe_feature
        finally:
            _current_task.reset(reset_token)

    def __repr__(self) -> str:
        return f"<Task label={self._label} state={self.state!r} >"


def _create_promise(
    label: str | None = None, *, _loop: EventLoop | None = None, **context: Any
) -> Promise[T]:
    if _loop is None:
        _loop = _get_running_loop()
    future: _PureFuture[T] = _PureFuture(_loop, label=label, **context)
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
    task._schedule_first_step()
    return task
