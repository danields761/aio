from __future__ import annotations

import contextvars
import datetime
import os
from functools import partial
from typing import Any, Callable, Mapping, NoReturn, ParamSpec, TypeVar

from aio.interfaces import (
    Clock,
    EventLoop,
    Handle,
    IOSelector,
    LoopRunner,
    LoopStopped,
    Task,
    UnhandledExceptionHandler,
)
from aio.loop._priv import running_loop
from aio.loop.pure.clock import MonotonicClock
from aio.loop.pure.scheduler import Scheduler
from aio.types import Logger
from aio.utils import MeasureElapsed, get_logger

T = TypeVar("T")
CPS = ParamSpec("CPS")


def _report_loop_callback_error(
    exc: BaseException,
    cb: Callable[..., Any] | None = None,
    task: Task[Any] | None = None,
    future: Task[Any] | None = None,
    /,
    logger: Logger = get_logger(),
    **context: Any,
) -> None:
    logger = logger.bind(**context)
    if cb:
        logger = logger.bind(callback=cb)

    if not task and not future:
        logger.error(f"Callback {cb} raised an unhandled exception", exc_info=exc)
        return

    if task:
        logger.error(
            f"While processing task {task!r} unhandled exception occurred",
            task=task,
            exc_info=exc,
        )
        return
    elif future:
        logger.error(
            f"Unhandled error occurs while processing callback for future {future!r}",
            callback=cb,
            future=future,
            exc_info=exc,
        )


_LOOP_DEBUG = bool(os.environ.get("AIO_DEBUG", __debug__))


class BaseEventLoop(EventLoop):
    def __init__(
        self,
        selector: IOSelector,
        *,
        clock: Clock = MonotonicClock(),
        scheduler: Scheduler | None = None,
        exception_handler: UnhandledExceptionHandler | None = None,
        logger: Logger | None = None,
        debug: bool = _LOOP_DEBUG,
    ) -> None:
        logger = logger or get_logger()
        self._logger = logger.bind(component="event-loop")

        self._scheduler = scheduler or Scheduler()
        self._selector = selector
        self._selector_in_poll = False
        self._clock = clock
        self._exception_handler = exception_handler or partial(
            _report_loop_callback_error, logger=self._logger
        )

        self._debug = debug

    def run_step(self) -> None:
        self._logger.trace("Running loop step...")

        at_start = self._clock.now()
        clock_resolution = self._clock.resolution()
        early_callbacks = self._scheduler.pop_pending(at_start + clock_resolution)

        # This logic a bit complicated, but overall idea is simple and acts like
        # `asyncio` do loop step
        next_event_at: float | None = self._scheduler.next_event()
        wait_events: float | None
        if len(early_callbacks) > 0:
            wait_events = 0
        elif next_event_at is None:
            wait_events = None
        else:
            wait_events = next_event_at - self._clock.now()
            if wait_events < 0:
                wait_events = 0

        #
        self._logger.trace(
            "Wait for IO",
            io_wait_time=(wait_events if wait_events is not None else "wake-on-io"),
        )
        with MeasureElapsed(self._clock) as measure_io_wait:
            try:
                self._selector_in_poll = True
                selector_callbacks = self._selector.select(
                    wait_events,
                )
            finally:
                self._selector_in_poll = False

            self._logger.trace(
                "IO waiting completed",
                triggered_events=len(selector_callbacks),
                select_poll_elapsed=measure_io_wait.get_elapsed(),
            )

        #
        after_select = self._clock.now()
        end_at = after_select + clock_resolution

        # Apply same nested context for all inner callbacks to make `_get_running_loop`
        # work
        cv_context = contextvars.copy_context()
        measure_callbacks = MeasureElapsed(self._clock)

        # Invoke early callbacks
        if early_callbacks:
            self._logger.trace("Invoking early callbacks", callbacks_num=len(early_callbacks))
            with measure_callbacks:
                for handle in early_callbacks:
                    self._invoke_handle(cv_context, handle)
                self._logger.trace(
                    "Early callbacks invoked", elapsed=measure_callbacks.get_elapsed()
                )

        # Invoke IO callbacks
        self._logger.trace("Invoking IO callbacks", callbacks_num=len(selector_callbacks))
        with measure_callbacks:
            for callback, fd, events, exc in selector_callbacks:
                self._invoke_callback(
                    cv_context,
                    callback,
                    fd,
                    events,
                    exc,
                    place="IO-callback",
                    fd=fd,
                    events=events,
                )
            self._logger.trace("IO callbacks invoked", elapsed=measure_callbacks.get_elapsed())

        # Pop late-callbacks and invoke them
        late_callbacks = self._scheduler.pop_pending(end_at)
        self._logger.trace(
            "Invoking late callbacks",
            callbacks_num=len(early_callbacks),
        )
        with measure_callbacks:
            for handle in late_callbacks:
                self._invoke_handle(cv_context, handle)
            self._logger.trace(
                "Late callbacks invoked",
                elapsed=measure_callbacks.get_elapsed(),
            )

        self._logger.trace(
            "Loop step done", total_elapsed=datetime.timedelta(seconds=self._clock.now() - at_start)
        )

    def _invoke_callback(
        self,
        cv_context: contextvars.Context,
        callback: Callable[CPS, None],
        *args: CPS.args,
        **callback_context: Any,
    ) -> None:
        cv_context.run(
            self._invoke_callback_within_context,
            callback,
            *args,
            **callback_context,
        )

    def _invoke_callback_within_context(
        self,
        callback: Callable[CPS, None],
        *args: CPS.args,
        **callback_context: Any,
    ) -> None:
        token = running_loop.set(self)
        try:
            callback(*args)
        except Exception as err:
            self._exception_handler(err, cb=callback, **callback_context)
        except BaseException as err:
            self._exception_handler(err, cb=callback, **callback_context)
            raise
        finally:
            running_loop.reset(token)

    def _invoke_handle(self, cv_context: contextvars.Context, handle: Handle) -> None:
        if handle.cancelled:
            self._logger.trace("Skipping cancelled handle", handle=handle)
            return

        self._invoke_callback(cv_context, handle.callback, *handle.args)
        # Mark handle as executed after actual execution despite result
        handle.executed = True

    @property
    def clock(self) -> Clock:
        return self._clock

    def _wakeup_selector_if_in_poll(self) -> None:
        if not self._selector_in_poll:
            return
        self._selector.wakeup_thread_safe()

    def call_soon(
        self,
        target: Callable[CPS, None],
        *args: CPS.args,
        context: Mapping[str, Any] | None = None,
    ) -> Handle:
        if self._debug:
            self._logger.trace(
                "Enqueuing callback for next cycle",
                callback=target,
                callback_args=args,
            )

        handle = Handle(None, target, args, False, False, context or {})
        self._scheduler.enqueue(handle)
        self._wakeup_selector_if_in_poll()
        return handle

    def call_later(
        self,
        timeout: float,
        target: Callable[CPS, None],
        *args: CPS.args,
        context: Mapping[str, Any] | None = None,
    ) -> Handle:
        if timeout == 0:
            return self.call_soon(target, *args, context=context)

        call_at = self._clock.now() + timeout
        if self._debug:
            self._logger.trace(
                "Enqueuing callback at",
                callback=target,
                callback_args=args,
                call_at=call_at,
            )

        handle = Handle(call_at, target, args, False, False, context or {})
        self._scheduler.enqueue(handle)
        self._wakeup_selector_if_in_poll()
        return handle


class BaseLoopRunner(LoopRunner[BaseEventLoop]):
    def __init__(self, loop: BaseEventLoop) -> None:
        self._loop = loop
        self._run = False

    def get_loop(self) -> BaseEventLoop:
        return self._loop

    def run_loop(self) -> NoReturn:
        if self._run:
            raise RuntimeError("`run_loop` being called twice")

        self._run = True
        while self._run or self._loop._scheduler.items_num():
            self._loop.run_step()

        raise LoopStopped

    def stop_loop(self) -> None:
        self._run = False
