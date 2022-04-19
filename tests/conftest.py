import weakref
from unittest.mock import patch

import pytest

import aio
import aio.future.pure


@pytest.fixture(autouse=True)
def guard_futures_cleanup():
    futures = weakref.WeakSet()

    orig_init = aio.future.pure._Future.__init__

    def future_init(fut, *args, **kwargs):
        futures.add(fut)
        orig_init(fut, *args, **kwargs)

    with patch.object(aio.future.pure._Future, "__init__", future_init), patch.object(
        aio.future.pure._Future, "__hash__", lambda fut: id(fut)
    ):
        yield futures

        unfinished_futures = {future for future in futures if not future.is_finished}
        if unfinished_futures:
            pytest.fail("One of the test doesnt cleanup instantiated futures")
