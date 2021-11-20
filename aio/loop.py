from __future__ import annotations

import contextvars
import os
import signal
from contextlib import asynccontextmanager, contextmanager
from functools import partial
from itertools import count
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Callable,
    ContextManager,
    Iterator,
    Mapping,
    TypeVar,
    ParamSpec,
)

import structlog

from aio.components.clock import MonotonicClock
from aio.components.networking import (
    create_selector_networking,
    create_selectors_event_selector,
)
from aio.components.scheduler import Scheduler
from aio.interfaces import (
    CallbackType,
    Clock,
    EventLoop,
    EventSelector,
    Handle,
    LoopRunner,
    LoopRunnerFactory,
    Networking,
    UnhandledExceptionHandler,
)
from aio.utils import (
    MeasureElapsed,
    SignalHandlerInstaller,
    WarnUndoneAsyncGens,
)

if TYPE_CHECKING:
    from aio.future import Coroutine, Future, Task
    from aio.types import Logger

_log = structlog.get_logger(__name__)


T = TypeVar('T')
CPS = ParamSpec("CPS")


def _report_loop_callback_error(
    exc: BaseException,
    cb: Callable[..., Any] | None = None,
    task: Task[Any] | None = None,
    future: Task[Any] | None = None,
    /,
    logger: Logger = _log,
    **context: Any,
) -> None:
    logger = logger.bind(**context)
    if cb:
        logger = logger.bind(callback=cb)

    if not isinstance(exc, Exception):
        logger.warning(
            'Callback raises non `Exception` derivative, which is forbidden',
            exc_info=exc,
        )

    if not task and not future:
        logger.error(f'Callback {cb} raised an unhandled exception', exc_info=exc)
        return

    if task:
        logger.error(
            f'While processing task {task!r} unhandled exception occurred',
            task=task,
            exc_info=exc,
        )
        return
    elif future:
        logger.error(
            (f'Unhandled error occurs while ' f'processing callback for future {future!r}'),
            callback=cb,
            future=future,
            exc_info=exc,
        )


_LOOP_DEBUG = bool(os.environ.get('AIO_DEBUG', __debug__))
_running_loop: contextvars.ContextVar[EventLoop] = contextvars.ContextVar('running-loop')


def _get_running_loop() -> EventLoop:
    try:
        return _running_loop.get()
    except LookupError:
        raise RuntimeError('Should be called inside event loop')


class BaseEventLoop(EventLoop):
    def __init__(
        self,
        selector: EventSelector,
        networking_factory: Callable[[], ContextManager[Networking]],
        *,
        clock: Clock = MonotonicClock(),
        scheduler: Scheduler | None = None,
        exception_handler: UnhandledExceptionHandler | None = None,
        logger: Logger | None = None,
        debug: bool = _LOOP_DEBUG,
    ) -> None:
        logger = logger or _log
        self._logger = logger.bind(component='event-loop', loop_id=id(self))

        self._scheduler = scheduler or Scheduler()
        self._selector = selector
        self._networking_factory = networking_factory
        self._clock = clock
        self._exception_handler = exception_handler or partial(
            _report_loop_callback_error, logger=self._logger
        )

        self._debug = debug

        self._cached_networking: Networking | None = None

    def run_step(self) -> None:
        self._logger.debug('Running loop step...')

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
        self._logger.debug(
            'Wait for IO',
            io_wait_time=(wait_events if wait_events is not None else 'wake-on-io'),
        )
        with MeasureElapsed(self._clock) as measure_io_wait:
            selector_callbacks = self._selector.select(
                wait_events,
            )
            self._logger.debug(
                'IO waiting completed',
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
            self._logger.debug(
                f'Invoking early callbacks',
                callbacks_num=len(early_callbacks),
            )
            with measure_callbacks:
                for handle in early_callbacks:
                    self._invoke_handle(cv_context, handle)
                self._logger.debug(
                    'Early callbacks invoked',
                    elapsed=measure_callbacks.get_elapsed(),
                )

        # Invoke IO callbacks
        self._logger.debug('Invoking IO callbacks', callbacks_num=len(selector_callbacks))
        with measure_callbacks:
            for callback, fd, events in selector_callbacks:
                self._invoke_callback(
                    cv_context,
                    callback,
                    fd,
                    events,
                    place='IO-callback',
                    fd=fd,
                    events=events,
                )
            self._logger.debug('IO callbacks invoked', elapsed=measure_callbacks.get_elapsed())

        # Pop late-callbacks and invoke them
        late_callbacks = self._scheduler.pop_pending(end_at)
        self._logger.debug(
            f'Invoking late callbacks',
            callbacks_num=len(early_callbacks),
        )
        with measure_callbacks:
            for handle in late_callbacks:
                self._invoke_handle(cv_context, handle)
            self._logger.debug(
                'Late callbacks invoked',
                elapsed=measure_callbacks.get_elapsed(),
            )

        self._logger.debug('Loop step done', total_elapsed=self._clock.now() - at_start)

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
        token = _running_loop.set(self)
        try:
            callback(*args)
        except Exception as err:
            self._exception_handler(err, cb=callback, **callback_context)
        except BaseException as err:
            self._exception_handler(err, cb=callback, **callback_context)
            raise
        finally:
            _running_loop.reset(token)

    def _invoke_handle(self, cv_context: contextvars.Context, handle: Handle) -> None:
        if handle.cancelled:
            self._logger.debug('Skipping cancelled handle', handle=handle)
            return

        self._invoke_callback(cv_context, handle.callback, *handle.args)

    @property
    def clock(self) -> Clock:
        return self._clock

    @asynccontextmanager
    async def create_networking(self) -> AsyncIterator[Networking]:
        if self._cached_networking:
            yield self._cached_networking
        else:
            with self._networking_factory() as networking:
                self._cached_networking = networking
                try:
                    yield networking
                finally:
                    self._cached_networking = None

    def call_soon(
        self,
        target: Callable[CPS, None],
        *args: CPS.args,
        context: Mapping[str, Any] | None = None,
    ) -> Handle:
        if self._debug:
            self._logger.debug(
                'Enqueuing callback for next cycle',
                callback=target,
                callback_args=args,
            )

        handle = Handle(None, target, args, False, context or {})
        self._scheduler.enqueue(handle)
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
            self._logger.debug(
                f'Enqueuing callback at',
                callback=target,
                callback_args=args,
                call_at=call_at,
            )

        handle = Handle(call_at, target, args, False, context or {})
        self._scheduler.enqueue(handle)
        return handle


class BaseLoopRunner(LoopRunner):
    def __init__(
        self,
        loop: BaseEventLoop,
        selector: EventSelector,
        *,
        logger: Logger | None = None,
    ) -> None:
        self._loop = loop
        self._selector = selector

        logger = logger or _log
        self._logger = logger.bind(component='event-loop-runner', runner_id=id(self))

    @property
    def loop(self) -> EventLoop:
        return self._loop

    def run_coroutine(self, coroutine: Coroutine[Future[Any], None, T]) -> T:
        logger = self._logger.bind(root_coroutine=coroutine)
        logger.info('Starting root coroutine')
        try:
            with WarnUndoneAsyncGens():
                return self._run_until_complete(coroutine)
            logger.info('Root coroutine finished')
        except BaseException as exc:
            logger.info('Root coroutine finished with an error', exc_info=exc)
            raise

    def _run_until_complete(self, coroutine: Coroutine[Future[Any], None, T]) -> T:
        from aio.future import _create_task

        root_task = _create_task(coroutine, _loop=self._loop)
        sigint_received = False
        received_fut: Future[T] | None = None

        def receive_result(fut: Future[T]) -> None:
            nonlocal received_fut
            received_fut = fut

        def on_keyboard_interrupt(*_: Any) -> None:
            nonlocal sigint_received
            sigint_received = True
            self._logger.debug('Keyboard interrupt request arrived')
            self._selector.wakeup_thread_safe()

        root_task.add_callback(receive_result)

        with SignalHandlerInstaller(signal.SIGINT, on_keyboard_interrupt):
            for _ in count():
                self._loop.run_step()
                # Delay cancelling of root task, otherwise `on_keyboard_interrupt`
                # might be called right inside coroutine and cause surprising behaviour
                if sigint_received and not root_task.is_finished():
                    self._logger.debug('Cancelling root task due to keyboard interrupt')
                    root_task._cancel('Keyboard interrupt')
                    sigint_received = False
                if received_fut:
                    break

        assert root_task.is_finished()
        assert received_fut is not None
        return received_fut.result


@contextmanager
def _default_loop_runner_factory(
    *,
    selector_factory: Callable[[], ContextManager[EventSelector]] | None = None,
    networking_factory: Callable[[], ContextManager[Networking]] | None = None,
    logger: Logger | None = None,
    **loop_kwargs: Any,
) -> Iterator[LoopRunner]:
    logger = logger or _log

    if not selector_factory:
        selector_factory = partial(create_selectors_event_selector, logger=logger)

    with selector_factory() as selector:
        if not networking_factory:
            networking_factory = partial(create_selector_networking, selector, logger=logger)

        loop = BaseEventLoop(selector, networking_factory, **loop_kwargs)
        runner = BaseLoopRunner(loop, selector)
        yield runner


_loop_runner_factory: LoopRunnerFactory = _default_loop_runner_factory


def set_loop_runner_factory(loop_factory: LoopRunnerFactory) -> None:
    global _loop_runner_factory
    _loop_runner_factory = loop_factory


def get_loop_runner_factory() -> LoopRunnerFactory:
    return _loop_runner_factory
