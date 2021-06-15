from __future__ import annotations

import signal
import sys
import types
import warnings
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    Callable,
    Literal,
    Optional,
    Type,
    Union,
)
from weakref import WeakSet

if TYPE_CHECKING:
    from aio.interfaces import Clock


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


class WarnUndoneAsyncGens:
    def __init__(self):
        self.controlled_async_gens = WeakSet()
        self.already_emitted: set[int] = set()
        self._old_first_iter: Optional[Callable] = None
        self._old_finalize_iter: Optional[Callable] = None

    def _async_gen_first_iter(
        self, async_gen: AsyncGenerator[Any, Any]
    ) -> None:
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
        (
            self._old_first_iter,
            self._old_finalize_iter,
        ) = sys.get_asyncgen_hooks()
        sys.set_asyncgen_hooks(
            self._async_gen_first_iter, self._async_gen_finalize
        )
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


class SignalHandlerInstaller:
    def __init__(
        self,
        signal_to_handle: int,
        new_signal_handler: Union[
            Callable, Literal[signal.SIG_IGN, signal.SIG_DFL]
        ],
    ):
        self._signal_to_handle = signal_to_handle
        self._new_handler = new_signal_handler
        self._old_handler: Optional[Callable] = None

    def __enter__(self) -> None:
        self._old_handler = signal.signal(
            self._signal_to_handle, self._new_handler
        )

    def __exit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: types.TracebackType,
    ) -> Optional[bool]:
        signal.signal(self._signal_to_handle, self._old_handler)
        self._old_handler = None
        return None


class MeasureElapsed:
    def __init__(self, clock: Clock):
        self._last_enter_at: Optional[float] = None
        self._clock = clock

    def get_elapsed(self) -> float:
        if self._last_enter_at is None:
            raise RuntimeError('Measure not started')
        return self._clock.now() - self._last_enter_at

    def __enter__(self) -> MeasureElapsed:
        self._last_enter_at = self._clock.now()
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: types.TracebackType,
    ) -> Optional[bool]:
        return None
