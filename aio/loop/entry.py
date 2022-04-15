from __future__ import annotations

from typing import Any, Coroutine, TypeVar

from aio.interfaces import Future
from aio.loop._priv import get_loop_policy
from aio.utils import WarnUndoneAsyncGens

T = TypeVar("T")


def run(
    coroutine: Coroutine[Future[Any], None, T],
    **loop_kwargs: Any,
) -> T:
    policy = get_loop_policy()
    with policy.create_loop(**loop_kwargs) as runner:
        with WarnUndoneAsyncGens():
            return runner.run(coroutine)
