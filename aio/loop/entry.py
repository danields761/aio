from __future__ import annotations

import signal
from typing import Any, Coroutine, TypeVar

from aio.exceptions import KeyboardCancelled, SelfCancelForbidden
from aio.interfaces import Future, LoopStopped
from aio.loop._priv import get_loop_policy
from aio.utils import SignalHandlerInstaller, WarnUndoneAsyncGens

T = TypeVar("T")


def run(
    coroutine: Coroutine[Future[Any], None, T],
    **loop_kwargs: Any,
) -> T:
    from aio.future.factories import _create_task

    policy = get_loop_policy()
    with policy.create_loop(**loop_kwargs) as loop:
        runner = policy.create_loop_runner(loop)

        def receive_result(fut: Future[T]) -> None:
            nonlocal received_fut
            received_fut = fut
            runner.stop_loop()

        def on_keyboard_interrupt(*args: object) -> None:
            if root_task.is_finished:
                return

            try:
                # Cancel will schedule loop callback, and this should wakeup loop selector
                root_task.cancel(KeyboardCancelled())
            except SelfCancelForbidden as exc:
                raise KeyboardCancelled from exc

        received_fut: Future[T] | None = None
        root_task = _create_task(coroutine, _loop=loop, label="main-task")
        root_task.add_callback(receive_result)

        with SignalHandlerInstaller(signal.SIGINT, on_keyboard_interrupt), WarnUndoneAsyncGens():
            try:
                runner.run_loop()
            except LoopStopped:
                pass

        assert root_task.is_finished
        return received_fut.result()
