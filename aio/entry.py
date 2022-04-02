from __future__ import annotations

from typing import Any, Coroutine, TypeVar

from aio.future import Future
from aio.loop import get_loop_runner_factory

T = TypeVar("T")


def run_loop(
    coroutine: Coroutine[Future[Any], None, T],
    **loop_kwargs: Any,
) -> T:
    with get_loop_runner_factory()(**loop_kwargs) as runner:
        return runner.run_coroutine(coroutine)
