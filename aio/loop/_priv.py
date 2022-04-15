from __future__ import annotations

import contextvars
import threading
from typing import AsyncContextManager

from aio.interfaces import EventLoop, Executor, LoopPolicy, Networking

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


loop_global_cfg = threading.local()


def set_loop_policy(policy: LoopPolicy) -> None:
    loop_global_cfg.policy = policy


def get_loop_policy() -> LoopPolicy:
    try:
        policy = loop_global_cfg.policy
    except AttributeError:
        from aio.loop.pure.policy import BaseLoopPolicy

        set_loop_policy(BaseLoopPolicy())
        return get_loop_policy()

    if not isinstance(policy, LoopPolicy):
        raise TypeError("Invalid loop policy", policy)

    return policy


def networking() -> AsyncContextManager[Networking]:
    return get_loop_policy().create_networking()


def executor() -> AsyncContextManager[Executor]:
    return get_loop_policy().create_executor()
