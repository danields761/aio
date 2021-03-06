import functools
import inspect
import socket
import weakref
from unittest.mock import patch

import pytest

import aio
import aio.future.pure


@pytest.fixture(autouse=True)
def guard_futures_cleanup():
    futures = weakref.WeakSet()

    orig_init = aio.future.pure.Future.__init__

    def future_init(fut, *args, **kwargs):
        futures.add(fut)
        orig_init(fut, *args, **kwargs)

    with patch.object(aio.future.pure.Future, "__init__", future_init), patch.object(
        aio.future.pure.Future, "__hash__", lambda fut: id(fut)
    ):
        yield futures

        unfinished_futures = {future for future in futures if not future.is_finished}
        if unfinished_futures:
            pytest.fail(
                f"One of the test doesnt cleanup instantiated futures:\n{unfinished_futures}"
            )


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_pyfunc_call(pyfuncitem):
    if callable(pyfuncitem.obj) and inspect.iscoroutinefunction(pyfuncitem.obj):
        orig = pyfuncitem.obj

        @functools.wraps(orig)
        def coro_wrapper(*args, **kwargs):
            return aio.run(orig(*args, **kwargs))

        pyfuncitem.obj = coro_wrapper

    yield
