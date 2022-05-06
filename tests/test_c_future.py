import gc
import re
import weakref
from unittest.mock import Mock

import pytest

from aio import EventLoop, Future, FutureNotReady, Task
from aio.future.cimpl import Future as CFuture
from aio.future.cimpl import Task as CTask
from tests.utils import finalize_coro


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


class TestFuture:
    def test_constructs(self, loop):
        fut = CFuture(loop, "TEST-LABEL")
        assert fut.loop is loop
        assert fut.label == "TEST-LABEL"

    def test_type(self, fut):
        type_ = type(fut)
        assert type_.__module__ == "aio.future.cimpl"
        assert type_.__qualname__ == "Future"

    def test_fut_inheritance(self, fut):
        assert isinstance(fut, Future)

    def test_repr(self, fut):
        assert repr(fut) == '<Future label="TEST-FUTURE" state=running>'

    def test_docs(self):
        docs = {
            entity.__name__: entity.__doc__
            for entity in [
                CFuture,
                CFuture.label,
                CFuture.loop,
                CFuture.state,
                CFuture.is_finished,
                CFuture.is_cancelled,
                CFuture.add_callback,
                CFuture.remove_callback,
                CFuture._set_result,
                CFuture._set_exception,
                CFuture.result,
                CFuture.exception,
            ]
        }
        assert docs == {
            "Future": "Future C implementation",
            "loop": "Future event loop",
            "label": "Future label",
            "state": "Current future state",
            "is_finished": "Is future finished",
            "is_cancelled": "Is future finished due to cancellation",
            "result": "Get future result or raise `aio.FutureNotReady`",
            "exception": "Get future exception or raise `aio.FutureNotReady`",
            "add_callback": "Get future exception or raise `aio.FutureFinishedError`",
            "remove_callback": "Get future exception, or try to cancel already scheduled callback",
            "_set_exception": (
                "Set future exception and schedule "
                "pending callbacks or raise `aio.FutureFinishedError`"
            ),
            "_set_result": (
                "Set future result and schedule "
                "pending callbacks or raise `aio.FutureFinishedError`"
            ),
        }

    @pytest.mark.parametrize(
        "input_, error_msg",
        [
            (None, "argument 1 must be `aio.EventLoop` instance, not NoneType"),
            (1, "argument 1 must be `aio.EventLoop` instance, not int"),
            (Mock(), "argument 1 must be `aio.EventLoop` instance, not Mock"),
            (object(), "argument 1 must be `aio.EventLoop` instance, not object"),
        ],
    )
    def test_rejects_non_ev_loop_instance(self, input_, error_msg):
        with pytest.raises(TypeError) as exc_info:
            CFuture(input_, "")

        assert str(exc_info.value) == error_msg

    def test_running_state(self, fut):
        assert not fut.is_finished
        assert not fut.is_cancelled
        assert fut.state == Future.State.running
        with pytest.raises(FutureNotReady):
            fut.result()

        with pytest.raises(FutureNotReady):
            fut.exception()

    def test_set_result(self, fut, loop):
        fut._set_result(1)
        assert fut.is_finished
        assert fut.exception() is None
        assert fut.result() == 1
        assert loop.call_soon.mock_calls == []

    def test_set_exception(self, fut, loop):
        test_exc = Exception("Test exception")
        fut._set_exception(test_exc)
        assert fut.is_finished
        assert not fut.is_cancelled
        assert fut.exception() is test_exc
        assert loop.call_soon.mock_calls == []
        with pytest.raises(Exception, match="Test exception"):
            fut.result()

    def test_await_method_iters_till_result(self, fut):
        gen = fut.__await__()
        assert gen.send(None) is fut
        fut._set_result(10)

        with pytest.raises(StopIteration) as exc_info:
            gen.send(None)

        assert exc_info.value.args == (10,)

    def test_await_method_iters_till_exception(self, fut):
        gen = fut.__await__()
        assert gen.send(None) is fut

        test_exc = Exception("TEST EXCEPTION")
        fut._set_exception(test_exc)

        with pytest.raises(Exception) as exc_info:
            gen.send(None)

        assert exc_info.value is test_exc


class TestFutureCommon:
    @pytest.fixture(params=["task", "future"])
    def fut(self, request):
        loop = request.getfixturevalue("loop")
        match request.param:
            case "task":
                return CTask(coroutine(), loop, "TEST-TASK")
            case "future":
                return CFuture(loop, "TEST-FUTURE")
            case _:
                assert False

    @pytest.mark.parametrize(
        "args, expect_exc, expect_err_str",
        [
            ((), TypeError, "add_callback() takes exactly one argument (0 given)"),
            ((1, 2), TypeError, "add_callback() takes exactly one argument (2 given)"),
            ((1,), TypeError, "Callback must be callable object"),
        ],
    )
    def test_add_callback_exc(self, fut, args, expect_exc, expect_err_str):
        with pytest.raises(expect_exc, match=re.escape(expect_err_str)):
            fut.add_callback(*args)

    def test_add_callback_keeps_single_ref(self, fut):
        cb = lambda f: None
        cb_weak = weakref.ref(cb)
        fut.add_callback(cb)
        del cb
        assert gc.collect() >= 1
        assert cb_weak() is not None

    @pytest.mark.parametrize(
        "args, expect_exc, expect_err_str",
        [
            ((), TypeError, "remove_callback() takes exactly one argument (0 given)"),
            ((1, 2), TypeError, "remove_callback() takes exactly one argument (2 given)"),
        ],
    )
    def test_remove_callback_exc(self, fut, args, expect_exc, expect_err_str):
        with pytest.raises(expect_exc, match=re.escape(expect_err_str)):
            fut.remove_callback(*args)

    def test_set_callback(self, fut, loop):
        cb = Mock()
        fut.add_callback(cb)
        assert cb.mock_calls == []
        assert loop.call_soon.mock_calls == []

    def test_remove_callback(self, fut, loop):
        cb = Mock()
        fut.remove_callback(cb)
        assert cb.mock_calls == []
        assert loop.call_soon.mock_calls == []

    def test_set_remove_callback(self, fut, loop):
        cb = Mock()
        fut.add_callback(cb)
        fut.remove_callback(cb)
        assert cb.mock_calls == []
        assert loop.call_soon.mock_calls == []


class TestTask:
    @pytest.fixture
    def task(self, loop):
        coro = coroutine()
        task = CTask(coro, loop, "TEST-LABEL")
        yield task
        finalize_coro(coro)

    def test_constructs(self, loop):
        coro = coroutine()
        task = CTask(coro, loop, "TEST-LABEL")
        assert task.loop is loop
        assert task.label == "TEST-LABEL"

        finalize_coro(coro)

    def test_running_state(self, task):
        assert not task.is_finished
        assert not task.is_cancelled
        assert task.state == Future.State.created
        with pytest.raises(FutureNotReady):
            task.result()

        with pytest.raises(FutureNotReady):
            task.exception()

    def test_inheritance(self, task):
        assert isinstance(task, Future)
        assert isinstance(task, Task)
        assert isinstance(task, CFuture)


async def coroutine():
    pass
