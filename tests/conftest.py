import weakref
from unittest.mock import patch

import pytest

import aio


@pytest.fixture(autouse=True)
def guard_futures_cleanup():
    futures = weakref.WeakSet()

    orig_init = aio.Future.__init__

    def future_init(fut, *args, **kwargs):
        orig_init(fut, *args, **kwargs)
        futures.add(fut)

    with patch.object(aio.future.Future, "__init__", future_init), patch.object(
        aio.future.Future, "__hash__", lambda fut: id(fut)
    ):
        yield futures

    unfinished_futures = {future for future in futures if not future.is_finished}
    if unfinished_futures:
        pytest.exit("One of the test doesnt cleanup instantiated futures")
