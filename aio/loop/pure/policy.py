from contextlib import asynccontextmanager, contextmanager
from functools import partial
from typing import Any, AsyncIterator, Callable, ContextManager, Iterator

from aio.components.executor import concurrent_executor_factory
from aio.interfaces import EventLoop, Executor, IOSelector, LoopPolicy, Networking
from aio.loop.pure.impl import BaseEventLoop
from aio.loop.pure.networking import create_selector_networking, create_selectors_event_selector
from aio.types import Logger
from aio.utils import get_logger


class BaseLoopPolicy(LoopPolicy):
    def __init__(self) -> None:
        self._selector: IOSelector | None = None
        self._loop: BaseEventLoop | None = None
        self._cached_networking: Networking | None = None
        self._cached_executor: Executor | None = None

    @contextmanager
    def create_loop(
        self,
        selector_factory: Callable[[], ContextManager[IOSelector]] | None = None,
        logger: Logger | None = None,
        **loop_kwargs: Any,
    ) -> Iterator[EventLoop]:
        if self._selector is not None:
            raise RuntimeError("Attempt to create event loop on same thread twice")

        logger = logger or get_logger()

        if not selector_factory:
            selector_factory = partial(create_selectors_event_selector, logger=logger)

        with selector_factory() as selector:
            self._selector = selector
            self._loop = loop = BaseEventLoop(selector, **loop_kwargs)
            yield loop

    @asynccontextmanager
    async def create_networking(self) -> AsyncIterator[Networking]:
        assert self._selector
        if self._cached_networking:
            yield self._cached_networking
        else:
            with create_selector_networking(self._selector) as networking:
                self._cached_networking = networking
                try:
                    yield networking
                finally:
                    self._cached_networking = None

    @asynccontextmanager
    async def create_executor(self) -> AsyncIterator[Executor]:
        assert self._selector
        if self._cached_executor:
            yield self._cached_executor
        else:
            async with concurrent_executor_factory(self._selector) as executor:
                self._cached_executor = executor
                yield executor
