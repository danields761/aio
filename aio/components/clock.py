import time

from aio.interfaces import Clock


class MonotonicClock(Clock):
    def __init__(self) -> None:
        resolution = time.get_clock_info('monotonic').resolution
        assert isinstance(resolution, float)
        self._resolution = resolution

    def now(self) -> float:
        return time.monotonic()

    def resolution(self) -> float:
        return self._resolution
