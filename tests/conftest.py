from unittest.mock import patch

import pytest

import aio


@pytest.fixture(autouse=True)
def guard_futures_cleanup():
    futures = []

    orig_init = aio.Future.__init__

    def future_init(fut, *args, **kwargs):
        orig_init(fut, *args, **kwargs)
        futures.append(fut)

    with patch.object(aio.future.Future, '__init__', future_init):
        yield futures

    for future in futures:
        assert future.is_finished()
