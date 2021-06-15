from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from aio.loop import get_loop_runner_factory

if TYPE_CHECKING:
    from aio.types import Coroutine


T = TypeVar('T')


def run_loop(
    coroutine: Coroutine[T],
    **loop_kwargs: Any,
) -> T:
    with get_loop_runner_factory()(**loop_kwargs) as runner:
        return runner.run_coroutine(coroutine)
