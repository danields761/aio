from __future__ import annotations

import contextvars
from contextlib import contextmanager
from functools import partial
from typing import Any, Callable, ContextManager, Iterator

from aio.components.networking import create_selector_networking, create_selectors_event_selector
from aio.interfaces import EventLoop, IOSelector, LoopFactory, Networking
from aio.types import Logger
from aio.utils import get_logger

running_loop: contextvars.ContextVar[EventLoop] = contextvars.ContextVar("running-loop")


def get_running_loop() -> EventLoop:
    try:
        return running_loop.get()
    except LookupError:
        raise RuntimeError("Should be called inside event loop")


async def get_running() -> EventLoop:
    """Get event loop on which calling coroutine is running."""
    try:
        return running_loop.get()
    except LookupError:
        raise RuntimeError(
            "Current loop isn't accessible from current coroutine, "
            "probably it being run on non-`aio` event loop instance"
        )


@contextmanager
def default_loop_factory(
    *,
    selector_factory: Callable[[], ContextManager[IOSelector]] | None = None,
    networking_factory: Callable[[], ContextManager[Networking]] | None = None,
    logger: Logger | None = None,
    **loop_kwargs: Any,
) -> Iterator[EventLoop]:
    from aio.loop.pure import BaseEventLoop

    logger = logger or get_logger()

    if not selector_factory:
        selector_factory = partial(create_selectors_event_selector, logger=logger)

    with selector_factory() as selector:
        if not networking_factory:
            networking_factory = partial(create_selector_networking, selector, logger=logger)

        loop = BaseEventLoop(selector, networking_factory, **loop_kwargs)
        yield loop


loop_factory_cv: LoopFactory = default_loop_factory


def set_loop_factory(loop_factory: LoopFactory) -> None:
    global loop_factory_cv
    loop_factory_cv = loop_factory


def get_loop_factory() -> LoopFactory:
    return loop_factory_cv
