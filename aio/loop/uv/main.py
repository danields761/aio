import signal

import aio.loop
from aio.loop.uv.policy import UVPolicy
from aio.loop.uv.uvcffi import ffi

aio.loop.set_loop_policy(UVPolicy())


@ffi.callback("uv_signal_cb")
def signal_cb(uv_signal_ptr: int, signum: int) -> None:
    old_cb = ffi.from_handle(uv_signal_ptr.data)
    old_cb(signum)


async def main():
    # loop = get_running_loop()
    # old_signal = signal.signal(signal.SIGINT, signal.SIG_DFL)
    # assert callable(old_signal)
    # uv_signal_prt = ffi.new("uv_signal_t*")
    # data = uv_signal_prt.data = ffi.new_handle(old_signal)
    # invoke_uv_fn(lib.uv_signal_init, loop.c_loop, uv_signal_prt)
    # invoke_uv_fn(lib.uv_signal_start, uv_signal_prt, signal_cb, signal.SIGTERM)
    async with aio.create_promise() as promise:
        promise.set_result(10)
        try:
            await aio.sleep(10)
        except BaseException as exc:
            print(exc)
            raise
        return await promise.future


try:
    print(aio.run(main()))
finally:
    print("DONE")
