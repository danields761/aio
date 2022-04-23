from typing import Coroutine
from unittest.mock import Mock


def mock_wraps(cb, **mock_kwargs):
    if cb is not None:
        return Mock(wraps=cb, **mock_kwargs)
    else:
        return lambda cb: Mock(wraps=cb, **mock_kwargs)


def finalize_coro(coro_inst: Coroutine):
    async def wrapper():
        try:
            await coro_inst
        except Exception:
            pass

    w = wrapper()
    try:
        while True:
            w.send(None)
    except StopIteration:
        pass
