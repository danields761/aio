import signal
from typing import Callable, Sequence


def sec_to_msec(sec: float) -> int:
    return int(sec * 1000)


def msec_to_sec(msec: int) -> float:
    return msec / 1000


DEFAULT_REINSTALL_SIGNALS = (signal.Signals.SIGINT, signal.Signals.SIGTERM)


def collect_old_signal_handlers(
    lookup_signals: Sequence[signal.Signals] = DEFAULT_REINSTALL_SIGNALS,
) -> dict[signal.Signals, Callable[[int, object], None]]:
    signals = ((signum, signal.getsignal(signum)) for signum in lookup_signals)
    return {signum: handler for signum, handler in signals if callable(handler)}
