from __future__ import annotations

import sys
import types
import warnings
from contextlib import asynccontextmanager, contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Mapping,
    Optional,
    Type,
    TypeVar,
)
from weakref import WeakSet

import structlog
from structlog import BoundLogger

from aio.base_components import (
    MonotonicClock,
    SelectorNetworking,
    SelectorsSelectorImpl,
)
from aio.interfaces import (
    CallbackType,
    Clock,
    EventCallback,
    EventLoop,
    EventSelector,
    Handle,
    LoopFactory,
    LoopRunner,
    Networking,
    UnhandledExceptionHandler,
)
from aio.scheduler import Scheduler

if TYPE_CHECKING:
    from aio.future import Coroutine, Future, Task

logger = structlog.get_logger(__name__)


T = TypeVar('T')


def _report_future_callback_error(
    exc: Exception,
    logger_: BoundLogger = logger,
    cb: Optional[Callable] = None,
    task: Optional[Task] = None,
    future: Optional[Task] = None,
    /,
    **context: Any,
):
    if cb:
        logger_ = logger_.bind(**context, callback=cb)

    if not task and not future:
        logger_.error(f'Callback "{cb}" raised an unhandled exception', exc=exc)
        return

    if task:
        logger_.error(
            f'While processing task {task!r} unhandled exception occurred',
            task=task,
            exc=exc,
        )
        return
    elif future:
        logger_.error(
            (
                f'Unhandled error occurs while '
                f'processing callback for future {future!r}'
            ),
            callback=cb,
            future=future,
            exc=exc,
        )


class BaseEventLoop(EventLoop):
    def __init__(
        self,
        selector: EventSelector,
        *,
        clock: Clock = MonotonicClock(),
        scheduler: Optional[Scheduler] = None,
        exception_handler: Optional[UnhandledExceptionHandler] = None,
    ):
        self._scheduler = scheduler or Scheduler()
        self._selector = selector
        self._clock = clock
        self._exception_handler = exception_handler or _report_future_callback_error
        self._logger = logger.bind(loop_id=id(self))

    def run_step(self) -> None:
        # TODO Guard from KeyboardInterrupt

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

        if __debug__:
            if max_wait_event is None or max_wait_event > 1:
                max_wait_event = 1

        self._logger.debug(
            'Selector will poll',
            poll_time=max_wait_event if max_wait_event is not None else 'until event',
        )

        #
        self._selector.select(max_wait_event)

        #
        after_select = self._clock.now()
        end_at = after_select + clock_resolution

        #
        pending_after_select = self._scheduler.pop_pending(end_at)

        self._logger.debug(
            f'After select some callbacks will be invoked',
            elapsed=after_select - before_select,
            callbacks_num=len(pending_after_select + pending_before_select),
        )

        #
        for handle in pending_before_select + pending_after_select:
            self._invoke_handle(handle)

        after_cbs_invoke = self._clock.now()
        self._logger.debug('Loop step done', elapsed=after_cbs_invoke - before_select)

    def _invoke_handle(self, handle: Handle) -> None:
        logger_ = self._logger.bind(handle=handle)

        if handle.cancelled:
            logger_.info('Skipping cancelled handle')
            return

        try:
            handle.callback(*handle.args)
        except Exception as err:
            self._exception_handler(err, logger_, cb=handle.callback, **handle.context)

    @property
    def clock(self) -> Clock:
        return self._clock

    def call_soon(
        self,
        target: CallbackType,
        *args: Any,
        context: Optional[Mapping[str, Any]] = None,
    ) -> Handle:
        self._logger.debug('Enqueuing callback for next cycle')

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

        enqueue_at = self._clock.now() + timeout
        self._logger.debug(f'Enqueuing cb at {enqueue_at}')

        handle = Handle(enqueue_at, target, args, False, context or {})
        self._scheduler.enqueue(handle)
        return handle

    @asynccontextmanager
    async def create_networking(self) -> AsyncIterator[Networking]:
        networking = SelectorNetworking(selector=self._selector, logger=self._logger)
        try:
            yield networking
        finally:
            networking.finalize()


class BaseLoopRunner(LoopRunner):
    def __init__(self, loop: BaseEventLoop, selector: EventSelector):
        self._loop = loop
        self._selector = selector

    @property
    def loop(self) -> EventLoop:
        return self._loop

    def run_coroutine(self, coroutine: Coroutine[T]) -> T:
        try:
            with _loop_context(self._loop):
                return self._run_until_complete(coroutine)
        finally:
            self._selector.finalize()

    def _run_until_complete(self, coroutine: Coroutine[T]) -> T:
        from aio.future import _create_task

        task = _create_task(coroutine, _loop=self._loop)
        received_fut: Optional[Future[T]] = None

        def receive_result(fut: Future[T]) -> None:
            nonlocal received_fut
            received_fut = fut

        task.add_callback(receive_result)

        while not received_fut:
            self._loop.run_step()

        assert task.is_finished()
        return received_fut.result


def _selector_default_callback_invoker(
    loop: BaseEventLoop, callback: EventCallback, fd: int, events: int
) -> None:
    try:
        callback(fd, events)
    except Exception as exc:
        loop._exception_handler(
            exc, cb=callback, place='processing IO callback', fd=fd, events=events
        )


def _base_loop_factory(
    selector: Optional[EventSelector] = None, **loop_kwargs: Any
) -> tuple[LoopRunner, EventLoop]:
    if not selector:
        selector = SelectorsSelectorImpl(
            lambda cb, fd, events: _selector_default_callback_invoker(
                loop, cb, fd, events
            )
        )
    loop = BaseEventLoop(selector=selector, **loop_kwargs)
    runner = BaseLoopRunner(loop, selector)
    return runner, loop


_loop_factory: LoopFactory = _base_loop_factory
_running_loop: Optional[EventLoop] = None


def set_loop_factory(loop_factory: _loop_factory) -> None:
    global _loop_factory
    _loop_factory = loop_factory


def get_loop_factory() -> LoopFactory:
    return _loop_factory


def _get_loop_inner() -> EventLoop:
    """Get event loop currently in use."""
    if _running_loop is None:
        raise RuntimeError('No instances of an event loop has been created')
    return _running_loop


async def get_loop() -> EventLoop:
    return _get_loop_inner()


def _emit_undone_async_gen_warn(
    async_gen: AsyncGenerator[Any, Any], stack_level: int = 0
) -> None:
    warnings.warn(
        f'Async-generator shutdown request income for `{async_gen}`, but this '
        'event loop doesn\'t supports such behaviour. '
        'Please, consider either close async-generator manually via `aclose` method '
        'or use `aio.guard_async_gen` context manager instead.',
        stacklevel=stack_level + 2,
    )


class _WarnUndoneAsyncGens:
    def __init__(self):
        self.controlled_async_gens = WeakSet()
        self.already_emitted: set[int] = set()
        self._old_first_iter: Optional[Callable] = None
        self._old_finalize_iter: Optional[Callable] = None

    def _async_gen_first_iter(self, async_gen: AsyncGenerator[Any, Any]) -> None:
        self.controlled_async_gens.add(async_gen)

    def _async_gen_finalize(self, async_gen: AsyncGenerator[Any, Any]) -> None:
        self._emit_warning(async_gen, stack_level=1)

    def _emit_warning(
        self, async_gen: AsyncGenerator[Any, Any], stack_level: int = 0
    ) -> None:
        if id(async_gen) in self.already_emitted:
            return
        self.already_emitted.add(id(async_gen))
        _emit_undone_async_gen_warn(async_gen, stack_level=stack_level + 1)

    def __enter__(self) -> None:
        self._old_first_iter, self._old_finalize_iter = sys.get_asyncgen_hooks()
        sys.set_asyncgen_hooks(self._async_gen_first_iter, self._async_gen_finalize)
        return None

    def __exit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: types.TracebackType,
    ) -> Optional[bool]:
        for async_gen in self.controlled_async_gens:
            self._emit_warning(async_gen)
        self.controlled_async_gens = []
        sys.set_asyncgen_hooks(self._old_first_iter, self._old_finalize_iter)
        return None


@contextmanager
def _loop_context(loop: EventLoop) -> None:
    global _running_loop

    if _running_loop:
        raise Exception('Nested loop creating detected')

    _running_loop = loop

    try:
        with _WarnUndoneAsyncGens():
            yield
    finally:
        _running_loop = None


def run_loop(
    coroutine: Coroutine[T],
    **loop_kwargs: Any,
) -> T:
    runner, loop = _loop_factory(**loop_kwargs)
    return runner.run_coroutine(coroutine)
