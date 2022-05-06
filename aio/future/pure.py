from __future__ import annotations

import contextvars
import inspect
import sys
import traceback
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import AbstractSet, Any, Coroutine, Generator, Generic, Literal, TypeVar

import aio
from aio.exceptions import (
    AlreadyCancelling,
    Cancelled,
    FutureFinishedError,
    FutureNotReady,
    SelfCancelForbidden,
)
from aio.future._priv import current_task_cv
from aio.future.utils import coerce_cancel_arg
from aio.interfaces import EventLoop
from aio.interfaces import Future as ABCFuture
from aio.interfaces import FutureResultCallback, Handle
from aio.interfaces import Promise as ABCPromise
from aio.interfaces import Task as ABCTask


class _Sentry(Enum):
    NOT_SET = "not-set"


T = TypeVar("T")


class FuturePromise(ABCPromise[T]):
    def __init__(self, future: Future[T]) -> None:
        if isinstance(future, Task):
            raise TypeError("Promise could not control task instance")
        self._fut = future

    def set_result(self, val: T) -> None:
        self._fut._set_result(val=val)

    def set_exception(self, exc: BaseException) -> None:
        if isinstance(exc, Cancelled):
            exc_type = type(exc)
            raise TypeError(
                f"Use cancellation API instead of passing exception "
                f"`{exc_type.__module__}.{exc_type.__qualname__}` manually"
            )

        self._fut._set_result(exc=exc)

    def cancel(self, exc: Cancelled | str | None = None, /) -> None:
        self._fut._cancel(exc)

    @property
    def future(self) -> ABCFuture[T]:
        return self._fut

    def __repr__(self) -> str:
        return f"<{type(self).__name__} future={self.future!r}>"


@dataclass(frozen=True)
class _PendingState(Generic[T]):
    result_callbacks: set[FutureResultCallback[T]]


@dataclass
class _SuccessState(Generic[T]):
    result: T
    scheduled_cbs: dict[FutureResultCallback[T], Handle]


@dataclass
class _FailedState:
    exc: BaseException
    exc_retrieved: bool
    scheduled_cbs: dict[FutureResultCallback[Any], Handle]


class Future(ABCFuture[T], Generic[T]):
    def __init__(self, loop: EventLoop, label: str | None = None) -> None:
        self._state: _PendingState[T] | _SuccessState[T] | _FailedState = _PendingState(
            result_callbacks=set()
        )

        self._label = label
        self._loop = loop

    @property
    def label(self) -> str | None:
        return self._label

    @property
    def state(self) -> ABCFuture.State:
        match self._state:
            case _PendingState():
                return ABCFuture.State.running
            case _SuccessState() | _FailedState():
                return ABCFuture.State.finished
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
                _, current_exc, _ = sys.exc_info()
                if current_exc:
                    raise state.exc from current_exc
                else:
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
                cbs.add(cb)
            case _SuccessState() | _FailedState():
                raise FutureFinishedError("Could not schedule callback for already finished future")
            case _:
                assert False

    def remove_callback(self, cb: FutureResultCallback[T]) -> None:
        match self._state:
            case _PendingState(result_callbacks=cbs):
                try:
                    cbs.remove(cb)
                except (KeyError, ValueError):
                    pass
            case _SuccessState(scheduled_cbs=cbs) | _FailedState(scheduled_cbs=cbs):
                try:
                    handle = cbs.pop(cb)
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

    def _schedule_callbacks(self) -> dict[FutureResultCallback[Any], Handle]:
        if not isinstance(self._state, _PendingState):
            raise RuntimeError("Future must finish before calling callbacks")

        return {cb: self._loop.call_soon(cb, self) for cb in self._state.result_callbacks}

    def _set_result(
        self,
        val: T | Literal[_Sentry.NOT_SET] = _Sentry.NOT_SET,
        exc: BaseException | None = None,
    ) -> None:
        if val is not _Sentry.NOT_SET and exc is not None:
            raise ValueError(
                "Both result value and exception given, but they are mutually exclusive"
            )

        if not isinstance(self._state, _PendingState):
            raise FutureFinishedError

        scheduled_cbs = self._schedule_callbacks()
        if val is not _Sentry.NOT_SET:
            self._state = _SuccessState(result=val, scheduled_cbs=scheduled_cbs)
        elif exc is not None:
            self._state = _FailedState(exc=exc, scheduled_cbs=scheduled_cbs, exc_retrieved=False)
        else:
            assert False

    def _cancel(self, exc: str | Cancelled | None = None) -> None:
        self._set_result(exc=coerce_cancel_arg(exc))

    def __await__(self) -> Generator[ABCFuture[Any], None, T]:
        if isinstance(self._state, _PendingState):
            yield self

        if isinstance(self._state, _PendingState):
            raise RuntimeError("Future being resumed after first yield, but still not finished!")

        return self.result()

    def __repr__(self) -> str:
        label = self._label or "UNNAMED"
        return f"<Future label={label!r} state={self.state.name} at {hex(id(self))}>"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self is other

    def __del__(self) -> None:
        if not self.is_finished:
            warnings.warn(
                (
                    f"Feature `{self}` is about to be destroyed, "
                    "but not finished, that normally should never occur "
                ),
                stacklevel=2,
            )
        if isinstance(self._state, _FailedState) and not self._state.exc_retrieved:
            exc_tb = "".join(traceback.format_exception(self._state.exc))

            warnings.warn(
                (
                    f"Feature `{self}` is about to be destroyed, but her exception was never "
                    f"retrieved. Please, `await` this feature, or call `result` or "
                    f"`exception` methods to prevent exception being ignored silently. "
                    f"Ignored exception:\n{exc_tb}"
                ),
                stacklevel=2,
            )


@dataclass(frozen=True, kw_only=True)
class _BaseTaskState(_PendingState[T], Generic[T]):
    expect_coro_state: AbstractSet[str]
    coroutine: Coroutine[ABCFuture[Any], None, T]

    def __post_init__(self) -> None:
        coro_state = inspect.getcoroutinestate(self.coroutine)
        expect_state = self.expect_coro_state
        if coro_state not in expect_state:
            raise RuntimeError(
                "Inconsistent task state: "
                f"coroutine in state {coro_state!r}, but {expect_state!r} expected"
            )


@dataclass(frozen=True, kw_only=True)
class _CreatedState(_BaseTaskState[T], Generic[T]):
    expect_coro_state: AbstractSet[str] = frozenset({"CORO_CREATED"})


@dataclass(frozen=True, kw_only=True)
class _ScheduledState(_BaseTaskState[T], Generic[T]):
    handle: Handle
    expect_coro_state: AbstractSet[str] = frozenset({"CORO_CREATED"})


@dataclass(frozen=True, kw_only=True)
class _CancellingState(_BaseTaskState[T], Generic[T]):
    handle: Handle
    inner_cancel: Cancelled
    expect_coro_state: AbstractSet[str] = frozenset({"CORO_SUSPENDED", "CORO_CREATED"})


@dataclass(frozen=True, kw_only=True)
class _RunningState(_BaseTaskState[T], Generic[T]):
    waiting_on: ABCFuture[Any]
    expect_coro_state: AbstractSet[str] = frozenset({"CORO_SUSPENDED"})


_TaskState = (
    _CreatedState[T]
    | _ScheduledState[T]
    | _CancellingState[T]
    | _RunningState[T]
    | _SuccessState[T]
    | _FailedState
)


class _TaskInnerError(Exception):
    pass


class Task(Future[T], ABCTask[T], Generic[T]):
    def __init__(
        self,
        coroutine: Coroutine[ABCFuture[Any], None, T],
        loop: EventLoop,
        label: str | None = None,
    ) -> None:
        super().__init__(loop, label)
        self._state: _TaskState[T] = _CreatedState(result_callbacks=set(), coroutine=coroutine)

    @property
    def state(self) -> ABCFuture.State:
        match self._state:
            case _CreatedState():
                return ABCFuture.State.created
            case _ScheduledState():
                return ABCFuture.State.scheduled
            case _RunningState():
                return ABCFuture.State.running
            case _CancellingState():
                return ABCFuture.State.cancelling
            case _:
                return super().state

    def _set_result(
        self, val: T | Literal[_Sentry.NOT_SET] = _Sentry.NOT_SET, exc: BaseException | None = None
    ) -> None:
        if isinstance(self._state, _BaseTaskState):
            coro_state = inspect.getcoroutinestate(self._state.coroutine)
            self_cancel_detected = isinstance(exc, Cancelled) and coro_state == "CORO_RUNNING"
            if self_cancel_detected:
                raise SelfCancelForbidden

            if isinstance(self._state, _BaseTaskState) and coro_state != "CORO_CLOSED":
                raise RuntimeError(
                    f"Attempt to finish task {self!r} before it coroutine finished. "
                    f"Current coroutine state {coro_state!r}. Setting task result "
                    "allowed only when coroutine finished normally."
                )

        super()._set_result(val, exc)

    def _go_to_inner_cancellation(self, exc: str | Cancelled | None = None) -> None:
        assert isinstance(self._state, _CreatedState | _ScheduledState | _RunningState)

        handle = self._loop.call_soon(self._step, None)
        self._state = _CancellingState(
            self._state.result_callbacks,
            coroutine=self._state.coroutine,
            handle=handle,
            inner_cancel=coerce_cancel_arg(exc),
        )

    def _cancel(self, exc: str | Cancelled | None = None) -> None:
        from aio.future._factories import cancel_future

        if not isinstance(self._state, _BaseTaskState):
            raise FutureFinishedError

        if inspect.getcoroutinestate(self._state.coroutine) == "CORO_RUNNING":
            raise coerce_cancel_arg(exc)

        match self._state:
            case _CreatedState():
                self._go_to_inner_cancellation(exc)
            case _ScheduledState(handle=handle):
                assert not handle.executed, "Handle being executed, but state not changed"
                # Cancel scheduled first step and set cancelled result
                handle.cancel()
                self._go_to_inner_cancellation(exc)
            case _RunningState(waiting_on=waiting_on) if not waiting_on.is_finished:
                # Recursively cancel all inner tasks
                cancel_future(waiting_on, exc)
            case _RunningState():
                # In case, if awaited future already finished, we can't cancel it, so there
                #  is workaround called "inner cancel", e.g. cancellation, which emitted right in
                #  coroutine via `Coroutine.throw` method, opposite to standard approach, when
                #  futures cancelled recursively, and then they propagate cancellation back
                self._state.waiting_on.remove_callback(self._step)
                self._go_to_inner_cancellation(exc)
            case _CancellingState():
                raise AlreadyCancelling
            case _:
                super()._cancel(exc)

    def _schedule_first_step(self) -> None:
        if not isinstance(self._state, _CreatedState):
            raise RuntimeError("Only newly created tasks can be scheduled for first step")

        self._state = _ScheduledState(
            self._state.result_callbacks,
            coroutine=self._state.coroutine,
            handle=self._loop.call_soon(self._step, None),
        )

    def _step(self, _: ABCFuture[Any] | None, /) -> None:
        if not isinstance(self._state, _ScheduledState | _RunningState | _CancellingState):
            raise RuntimeError("Trying to resume finished task")

        cv_context = contextvars.copy_context()
        try:
            try:
                future = cv_context.run(self._send_to_coroutine_within_new_context)
            except _TaskInnerError:
                raise
            except StopIteration as exc:
                val: T = exc.value
                self._set_result(val=val)
                return
            except (Exception, aio.Cancelled) as exc:
                self._set_result(exc=exc)
                return
        finally:
            if isinstance(self._state, _RunningState):
                self._state.waiting_on.remove_callback(self._step)

        if future is self:
            raise RuntimeError(
                "Task awaiting on itself, this will cause "
                "infinity awaiting that's why is forbidden"
            )

        if future.loop is not self._loop:
            raise RuntimeError(
                f"During processing task `{self!r}` another "
                f"feature has been `{future!r}` received, which "
                "does not belong to the same loop"
            )

        future.add_callback(self._step)
        self._state = _RunningState(
            result_callbacks=self._state.result_callbacks,
            coroutine=self._state.coroutine,
            waiting_on=future,
        )

    def _send_to_coroutine_within_new_context(self) -> ABCFuture[Any]:
        assert isinstance(self._state, _ScheduledState | _RunningState | _CancellingState)

        reset_token = current_task_cv.set(self)
        try:
            match self._state:
                case _RunningState() | _ScheduledState():
                    maybe_feature = self._state.coroutine.send(None)
                case _CancellingState(inner_cancel=inner_cancel):
                    maybe_feature = self._state.coroutine.throw(
                        type(inner_cancel), inner_cancel, inner_cancel.__traceback__
                    )
                case _:
                    raise _TaskInnerError("Unexpected future state")

            if not isinstance(maybe_feature, ABCFuture):
                raise _TaskInnerError("All `aio` coroutines must yield and `Feature` instance")
            return maybe_feature
        finally:
            current_task_cv.reset(reset_token)

    def __repr__(self) -> str:
        return f"<Task label={self._label!r} state={self.state.name} >"


def create_promise(
    loop: EventLoop, label: str | None = None, /, **context: Any
) -> FuturePromise[T]:
    future: Future[T] = Future(loop, label=label, **context)
    return FuturePromise(future)


def create_task(
    coroutine: Coroutine[ABCFuture[Any], None, T],
    loop: EventLoop,
    label: str | None = None,
    /,
) -> ABCTask[T]:
    task = Task(coroutine, loop, label)
    task._schedule_first_step()
    return task


def cancel_future(future: ABCFuture[object], msg: str | Cancelled | None = None) -> None:
    if not isinstance(future, Future):
        raise TypeError(f"Pure `{cancel_future.__name__}` can only cancel pure futures")
    future._cancel(msg)
