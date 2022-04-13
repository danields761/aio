from __future__ import annotations

from typing import Any, Coroutine, TypeVar

from aio.interfaces import Future
from aio.loop._priv import get_loop_factory
from aio.utils import WarnUndoneAsyncGens

T = TypeVar("T")


def run(
    coroutine: Coroutine[Future[Any], None, T],
    **loop_kwargs: Any,
) -> T:
    with get_loop_factory()(**loop_kwargs) as runner:
        with WarnUndoneAsyncGens():
            return runner.run(coroutine)
