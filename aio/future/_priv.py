from __future__ import annotations

import contextvars
from typing import Any

from aio.interfaces import Task

current_task_cv: contextvars.ContextVar[Task[Any]] = contextvars.ContextVar("current-task")


async def get_current_task() -> Task[Any]:
    try:
        return current_task_cv.get()
    except LookupError:
        raise RuntimeError(
            "Current task isn't accessible from current coroutine, "
            "probably it being run on non-`aio` event loop instance"
        )
