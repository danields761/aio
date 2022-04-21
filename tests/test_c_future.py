import re
from unittest.mock import Mock

import pytest

from aio import EventLoop, Future, FutureNotReady
from aio.future.cfuture import Future as CFuture


class MockLoop(EventLoop):
    def call_soon(self, target, *args, **context):
        raise NotImplementedError

    def call_later(self, timeout, target, *args, **context):
        raise NotImplementedError

    @property
    def clock(self):
        raise NotImplementedError


@pytest.fixture
def loop():
    mock_loop = MockLoop()
    mock_loop.call_soon = Mock()
    return mock_loop


@pytest.fixture
def fut(loop):
    return CFuture(loop, "TEST-FUTURE")


def test_constructs(loop):
    fut = CFuture(loop, "TEST-LABEL")
    assert fut.loop is loop
    assert fut.label == "TEST-LABEL"


def test_type(fut):
    type_ = type(fut)
    assert type_.__module__ == "aio.future.cfuture"
    assert type_.__qualname__ == "Future"


def test_running_state(loop):
    fut = CFuture(loop, "")

    assert not fut.is_finished
    assert not fut.is_cancelled
    assert fut.state == Future.State.running
    with pytest.raises(FutureNotReady):
        fut.result()

    with pytest.raises(FutureNotReady):
        fut.exception()


def test_repr(fut):
    assert repr(fut) == "<Future label='TEST-FUTURE' state=running>"


@pytest.mark.parametrize(
    "input_, error_msg",
    [
        (None, "argument 1 must be EventLoop, not None"),
        (1, "argument 1 must be EventLoop, not int"),
        (Mock(), "argument 1 must be EventLoop, not Mock"),
        (object(), "argument 1 must be EventLoop, not object"),
    ],
)
def test_rejects_non_ev_loop_instance(input_, error_msg):
    with pytest.raises(TypeError) as exc_info:
        CFuture(input_, "")

    assert str(exc_info.value) == error_msg


@pytest.mark.parametrize(
    "args, expect_exc, expect_err_str",
    [
        ((), TypeError, "Future.add_callback() takes exactly one argument (0 given)"),
        ((1, 2), TypeError, "Future.add_callback() takes exactly one argument (2 given)"),
        ((1,), TypeError, "Callback must be callable object"),
    ],
)
def test_add_callback_exc(fut, args, expect_exc, expect_err_str):
    with pytest.raises(expect_exc, match=re.escape(expect_err_str)):
        fut.add_callback(*args)


@pytest.mark.parametrize(
    "args, expect_exc, expect_err_str",
    [
        ((), TypeError, "Future.remove_callback() takes exactly one argument (0 given)"),
        ((1, 2), TypeError, "Future.remove_callback() takes exactly one argument (2 given)"),
    ],
)
def test_remove_callback_exc(fut, args, expect_exc, expect_err_str):
    with pytest.raises(expect_exc, match=re.escape(expect_err_str)):
        fut.remove_callback(*args)


def test_set_result(fut, loop):
    fut._set_result(1)
    assert fut.is_finished
    assert fut.exception() is None
    assert fut.result() == 1
    assert loop.call_soon.mock_calls == []


def test_set_exception(fut, loop):
    test_exc = Exception("Test exception")
    fut._set_exception(test_exc)
    assert fut.is_finished
    assert not fut.is_cancelled
    assert fut.exception() is test_exc
    assert loop.call_soon.mock_calls == []
    with pytest.raises(Exception, match="Test exception"):
        fut.result()


def test_set_callback(fut, loop):
    cb = Mock()
    fut.add_callback(cb)
    assert cb.mock_calls == []
    assert loop.call_soon.mock_calls == []


def test_remove_callback(fut, loop):
    cb = Mock()
    fut.remove_callback(cb)
    assert cb.mock_calls == []
    assert loop.call_soon.mock_calls == []


def test_set_remove_callback(fut, loop):
    cb = Mock()
    fut.add_callback(cb)
    fut.remove_callback(cb)
    assert cb.mock_calls == []
    assert loop.call_soon.mock_calls == []


def test_await_method_iters_till_result(fut):
    gen = fut.__await__()
    assert gen.send(None) is fut
    fut._set_result(10)

    with pytest.raises(StopIteration) as exc_info:
        gen.send(None)

    assert exc_info.value.args == (10,)


def test_await_method_iters_till_exception(fut):
    gen = fut.__await__()
    assert gen.send(None) is fut

    test_exc = Exception("TEST EXCEPTION")
    fut._set_exception(test_exc)

    with pytest.raises(Exception) as exc_info:
        gen.send(None)

    assert exc_info.value is test_exc
