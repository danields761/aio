from aio.interfaces import Clock
from aio.loop.uv.utils import msec_to_sec
from aio.loop.uv.uvcffi import lib


class UVClock(Clock):
    def __init__(self, c_loop: object) -> None:
        self._c_loop = c_loop

    def now(self) -> float:
        lib.uv_update_time(self._c_loop)
        return msec_to_sec(lib.uv_now(self._c_loop))

    def resolution(self) -> float:
        return 1 / 1000
