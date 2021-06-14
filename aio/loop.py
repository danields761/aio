from __future__ import annotations

import os
import signal
from contextlib import asynccontextmanager, closing, contextmanager
from itertools import count
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Callable,
    ContextManager,
    Iterator,
    Mapping,
    Optional,
    TypeVar,
)

import structlog

from aio.components.clock import MonotonicClock
from aio.components.networking import (
    SelectorNetworking,
    SelectorsEventsSelector,
)
from aio.components.scheduler import Scheduler
from aio.interfaces import (
    CallbackType,
    Clock,
    EventCallback,
    EventLoop,
    EventSelector,
    Handle,
    LoopRunner,
    LoopRunnerFactory,
    Networking,
    UnhandledExceptionHandler,
)
from aio.utils import SignalHandler, WarnUndoneAsyncGens

if TYPE_CHECKING:
    from aio.future import Coroutine, Future, Task
    from aio.types import Logger

_log = structlog.get_logger(__name__)


T = TypeVar('T')


def _report_loop_callback_error(
    exc: Exception,
    logger: Logger = _log,
    cb: Optional[Callable] = None,
    task: Optional[Task] = None,
    future: Optional[Task] = None,
    /,
    **context: Any,
):
    if cb:
        logger = logger.bind(**context, callback=cb)

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
            (
                f'Unhandled error occurs while '
                f'processing callback for future {future!r}'
            ),
            callback=cb,
            future=future,
            exc_info=exc,
        )


_LOOP_DEBUG = bool(os.environ.get('AIO_DEBUG', __debug__))


class BaseEventLoop(EventLoop):
    def __init__(
        self,
        selector: EventSelector,
        networking_factory: Callable[[], ContextManager[Networking]],
        *,
        clock: Clock = MonotonicClock(),
        scheduler: Optional[Scheduler] = None,
        exception_handler: Optional[UnhandledExceptionHandler] = None,
        logger: Optional[Logger] = None,
        debug: bool = _LOOP_DEBUG,
    ):
        self._scheduler = scheduler or Scheduler()
        self._selector = selector
        self._networking_factory = networking_factory
        self._clock = clock
        self._exception_handler = exception_handler or _report_loop_callback_error

        logger = logger or _log
        self._logger = logger.bind(component='event-loop', loop_id=id(self))
        self._debug = debug

        self._cached_networking: Optional[Networking] = None

    def run_step(self) -> None:
        if self._debug:
            self._logger.debug('Running loop step...')

        clock_resolution = self._clock.resolution()
        before_select = self._clock.now()
        pending_before_select = self._scheduler.pop_pending(
            before_select + clock_resolution
        )

        max_wait_event: Optional[float] = self._scheduler.next_event()
        if len(pending_before_select) > 0:
            max_wait_event = 0
        if max_wait_event and max_wait_event >= before_select:
            max_wait_event -= before_select

        if self._debug:
            self._logger.debug(
                'Selector will poll',
                poll_time=(
                    max_wait_event if max_wait_event is not None else 'until event'
                ),
            )

        #
        self._selector.select(max_wait_event)

        #
        after_select = self._clock.now()
        end_at = after_select + clock_resolution

        #
        pending_after_select = self._scheduler.pop_pending(end_at)

        if self._debug:
            self._logger.debug(
                f'Invoking callbacks after select',
                elapsed=after_select - before_select,
                callbacks_num=len(pending_after_select + pending_before_select),
            )

        #
        for handle in pending_before_select + pending_after_select:
            self._invoke_handle(handle)

        if self._debug:
            after_cbs_invoke = self._clock.now()
            self._logger.debug(
                'Loop step done', elapsed=after_cbs_invoke - before_select
            )

    def _invoke_handle(self, handle: Handle) -> None:
        logger = self._logger.bind(handle=handle)

        if handle.cancelled:
            logger.info('Skipping cancelled handle')
            return

        try:
            handle.callback(*handle.args)
        except Exception as err:
            self._exception_handler(err, logger, cb=handle.callback, **handle.context)

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
        target: CallbackType,
        *args: Any,
        context: Optional[Mapping[str, Any]] = None,
    ) -> Handle:
        if self._debug:
            self._logger.debug(
                'Enqueuing callback for next cycle', callback=target, callback_args=args
            )

        handle = Handle(None, target, args, False, context or {})
        self._scheduler.enqueue(handle)
        return handle

    def call_later(
        self,
        timeout: float,
        target: CallbackType,
        *args: Any,
        context: Optional[Mapping[str, Any]] = None,
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
        logger: Optional[Logger] = None,
    ):
        self._loop = loop
        self._selector = selector

        logger = logger or _log
        self._logger = logger.bind(component='event-loop-runner', runner_id=id(self))

    @property
    def loop(self) -> EventLoop:
        return self._loop

    def run_coroutine(self, coroutine: Coroutine[T]) -> T:
        logger = self._logger.bind(root_coroutine=coroutine)
        logger.info('Starting root coroutine')

        try:
            with _loop_context(self._loop):
                return self._run_until_complete(coroutine)
        except BaseException as exc:
            logger.info('Root coroutine finished with an error', exc_info=exc)
            raise
        else:
            logger.info('Root coroutine finished')

    def _run_until_complete(self, coroutine: Coroutine[T]) -> T:
        from aio.future import _create_task

        root_task = _create_task(coroutine, _loop=self._loop)
        sigint_received = False
        received_fut: Optional[Future[T]] = None

        def receive_result(fut: Future[T]) -> None:
            nonlocal received_fut
            received_fut = fut

        def on_keyboard_interrupt(*_) -> None:
            nonlocal sigint_received
            sigint_received = True
            self._logger.debug('Keyboard interrupt request arrived')
            self._selector.wakeup_thread_safe()

        root_task.add_callback(receive_result)

        with SignalHandler(signal.SIGINT, on_keyboard_interrupt):
            for _ in count():
                self._loop.run_step()
                # Delay cancelling of root task, otherwise `on_keyboard_interrupt`
                # might be called right inside coroutine and cause surprising behaviour
                if sigint_received and not root_task.is_finished():
                    self._logger.debug('Cancelling root task')
                    root_task._cancel('Keyboard interrupt')
                    sigint_received = False
                if received_fut:
                    break

        assert root_task.is_finished()
        return received_fut.result


def _selector_default_callback_invoker(
    exception_handler: UnhandledExceptionHandler,
    logger: Logger,
    callback: EventCallback,
    fd: int,
    events: int,
) -> None:
    try:
        callback(fd, events)
    except Exception as exc:
        exception_handler(
            exc,
            logger,
            cb=callback,
            place='processing IO callback',
            fd=fd,
            events=events,
        )


@contextmanager
def _default_loop_runner_factory(
    *,
    selector_factory: Optional[Callable[[], ContextManager[EventSelector]]] = None,
    networking_factory: Optional[Callable[[], ContextManager[Networking]]] = None,
    exception_handler: Optional[UnhandledExceptionHandler] = None,
    logger: Optional[Logger] = None,
    **loop_kwargs: Any,
) -> Iterator[LoopRunner]:
    logger = logger or _log
    exception_handler = exception_handler or _report_loop_callback_error

    if not selector_factory:

        def selector_factory():
            return closing(
                SelectorsEventsSelector(
                    lambda cb, fd, events: _selector_default_callback_invoker(
                        exception_handler, logger, cb, fd, events
                    ),
                )
            )

    if not networking_factory:

        def networking_factory():
            return closing(SelectorNetworking(selector, logger=logger))

    with selector_factory() as selector:
        loop = BaseEventLoop(selector, networking_factory, **loop_kwargs)
        runner = BaseLoopRunner(loop, selector)
        yield runner


_loop_runner_factory: LoopRunnerFactory = _default_loop_runner_factory
_running_loop: Optional[EventLoop] = None


def set_loop_runner_factory(loop_factory: _loop_runner_factory) -> None:
    global _loop_runner_factory
    _loop_runner_factory = loop_factory


def get_loop_runner_factory() -> LoopRunnerFactory:
    return _loop_runner_factory


def _get_loop_inner() -> EventLoop:
    if _running_loop is None:
        raise RuntimeError('No instances of an event loop has been created')
    return _running_loop


async def get_loop() -> EventLoop:
    """Get event loop on which calling coroutine is running."""
    try:
        return _get_loop_inner()
    except RuntimeError:
        assert False, 'Loop not defined'


@contextmanager
def _loop_context(loop: EventLoop) -> Iterator[None]:
    global _running_loop

    if _running_loop:
        raise Exception('Nested loop creating detected')

    _running_loop = loop

    try:
        with WarnUndoneAsyncGens():
            yield
    finally:
        _running_loop = None


def run_loop(
    coroutine: Coroutine[T],
    **loop_kwargs: Any,
) -> T:
    with _loop_runner_factory(**loop_kwargs) as runner:
        return runner.run_coroutine(coroutine)
