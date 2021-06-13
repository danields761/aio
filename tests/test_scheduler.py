from unittest.mock import Mock

import pytest

from aio.interfaces import Handle
from aio.scheduler import PriorityQueue, Scheduler

CALLBACK_MARKER = Mock(name='CALLBACK_MARKER')


def h(*args, **kwargs):
    return Handle(*args, **kwargs)


@pytest.mark.parametrize(
    'init_pending, init_scheduled, now, result', [([h(), h()], [], 0, [])]
)
def test_pop_highest(init_pending, init_scheduled, now, result):
    assert Scheduler(init_pending, init_scheduled).pop_below_priority(now) == result
