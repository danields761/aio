from __future__ import annotations

import contextlib
import dataclasses
import signal
from typing import TYPE_CHECKING, Callable, ContextManager, Iterator, Mapping, Sequence

from aio.loop.uv.utils import DEFAULT_REINSTALL_SIGNALS, collect_old_signal_handlers
from aio.loop.uv.uvcffi import ffi, invoke_uv_fn, lib

if TYPE_CHECKING:
    from aio.loop.uv.loop import UVEventLoop


@ffi.callback("uv_close_cb")
def _noop_close(handle: object) -> None:
    pass


@ffi.callback("uv_signal_cb")
def _signal_cb(uv_signal_ptr: object, signum: int) -> None:
    data = ffi.from_handle(uv_signal_ptr.data)
    assert isinstance(data, _SignalData)

    try:
        callback = data.callbacks[signum]
    except KeyError:
        data.exception_hook(
            Exception(
                f"No callback found for signal {signum} ({signal.strsignal(signum) or 'Unknown'})"
            )
        )
        return

    try:
        callback(signum, None)
    except BaseException as exc:
        data.exception_hook(exc)


@dataclasses.dataclass(frozen=True)
class _SignalData:
    callbacks: Mapping[int, Callable[[int, object], None]]
    exception_hook: Callable[[BaseException], None]


@contextlib.contextmanager
def install_signal_handlers(
    loop: UVEventLoop, signals: Mapping[signal.Signals, Callable[[int, object], None]]
) -> Iterator[None]:
    data = _SignalData(signals, exception_hook=loop.report_exception)
    # `data_ptr` ref must be keept to avoid being garbage collected
    data_ptr = ffi.new_handle(data)

    uv_signal_ptr = ffi.new("uv_signal_t*")
    uv_signal_ptr.data = data_ptr
    invoke_uv_fn(lib.uv_signal_init, loop.c_loop, uv_signal_ptr)

    for signum in signals:
        invoke_uv_fn(lib.uv_signal_start, uv_signal_ptr, _signal_cb, signum)

    try:
        yield
    finally:
        invoke_uv_fn(lib.uv_signal_stop, uv_signal_ptr)
        lib.uv_close(ffi.cast("uv_handle_t*", uv_signal_ptr), _noop_close)


def reinstall_std_signal_handlers(
    loop: UVEventLoop,
    lookup_signals: Sequence[signal.Signals] = DEFAULT_REINSTALL_SIGNALS,
) -> ContextManager[None]:
    old_handlers = collect_old_signal_handlers(lookup_signals)

    return install_signal_handlers(loop, old_handlers)
