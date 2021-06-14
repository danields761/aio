from typing import Coroutine
from unittest.mock import Mock, call

import pytest

import aio
from aio.future import _create_promise, _create_task


class SpecialExc(Exception):
    pass


@pytest.fixture
def loop_last_enqueued():
    return []


@pytest.fixture
def loop_call_last_enqueued(loop_last_enqueued):
    def caller():
        while loop_last_enqueued:
            target, args = loop_last_enqueued.pop()
            target(*args)

    return caller


@pytest.fixture
def loop(loop_last_enqueued):
    def call_soon(target, *args, context=None):
        loop_last_enqueued.append((target, args))

    loop = Mock(aio.EventLoop)
    loop.call_soon = call_soon
    loop.call_later.side_effect = NotImplementedError('"call_later" is forbidden here')
    return loop


@pytest.fixture
def create_promise(loop):
    return lambda th=None: _create_promise(th or 'test-future', _loop=loop)


@pytest.fixture
def create_task(loop):
    return lambda coro, th=None: _create_task(coro, label='test-task', _loop=loop)


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


class TestFuture:
    def test_result_set_get(self, create_promise, loop_call_last_enqueued):
        promise = create_promise()
        future = promise.future

        assert future.state == aio.Future.State.running
        with pytest.raises(aio.FutureNotReady):
            _ = future.result

        with pytest.raises(aio.FutureNotReady):
            _ = future.exception

        promise.set_result('test result')
        loop_call_last_enqueued()

        assert future.state == aio.Future.State.finished
        assert future.is_finished()
        assert future.result == 'test result'
        assert future.exception is None

    def test_get_result_after_exc(self, create_promise, loop_call_last_enqueued):
        test_err = Exception('test exception description')

        promise = create_promise()
        future = promise.future

        assert future.state == aio.Future.State.running

        promise.set_exception(test_err)
        loop_call_last_enqueued()

        assert future.is_finished()
        assert future.state == aio.Future.State.finished
        assert future.exception is test_err

        with pytest.raises(Exception) as exc_info:
            _ = future.result
        assert exc_info.value is test_err

    def test_on_done_cbs_set_res(self, create_promise, loop_call_last_enqueued):
        cb = Mock()
        promise = create_promise()
        future = promise.future
        future.add_callback(cb)

        promise.set_result('test result')
        loop_call_last_enqueued()

        assert cb.mock_calls == [call(future)]
        assert future.is_finished()
        assert future.result == 'test result'

    def test_on_done_cbs_set_exc(self, create_promise, loop_call_last_enqueued):
        test_err = Exception('test exception description')

        cb = Mock()
        promise = create_promise()
        future = promise.future
        future.add_callback(cb)

        promise.set_exception(test_err)
        loop_call_last_enqueued()

        assert cb.mock_calls == [call(future)]
        assert future.is_finished()
        assert future.exception is test_err

    def test_on_cancel_calls_cb(self, create_promise, loop_call_last_enqueued):
        cb = Mock()
        promise = create_promise()
        future = promise.future
        future.add_callback(cb)

        promise.cancel()
        loop_call_last_enqueued()

        assert cb.mock_calls == [call(future)]
        assert future.is_finished()
        assert isinstance(future.exception, aio.Cancelled)

    def test_finished_state_in_res_cb(self, create_promise, loop_call_last_enqueued):
        promise = create_promise()
        future = promise.future

        def cb_callable(res_fut):
            assert res_fut is future
            assert res_fut.state == aio.Future.State.finished
            assert res_fut.result == 'test result'
            assert res_fut.exception is None

        cb = Mock(wraps=cb_callable)

        future.add_callback(cb)
        promise.set_result('test result')
        loop_call_last_enqueued()

        assert cb.mock_calls == [call(future)]

    def test_cant_add_same_cb_twice(self, create_promise, loop_call_last_enqueued):
        cb = Mock()

        promise = create_promise()
        future = promise.future
        future.add_callback(cb)
        future.add_callback(cb)

        promise.set_result('test result')
        loop_call_last_enqueued()

        assert cb.mock_calls == [call(future)]

    def test_coro_awaits(self, create_promise, loop_call_last_enqueued):
        result_mock = Mock(name='result')
        promise = create_promise()
        future = promise.future

        async def coro():
            return await future

        coro_inst = coro()
        assert coro_inst.send(None) is future

        promise.set_result(result_mock)
        loop_call_last_enqueued()

        with pytest.raises(StopIteration) as exc_info:
            coro_inst.send(None)
        assert exc_info.value.value is result_mock

    def test_coro_cant_await_same_future_twice(self, create_promise):
        promise = create_promise()
        future = promise.future
        should_never_be_called = Mock(name='should_never_be_called')

        async def coro():
            await future
            should_never_be_called()

        coro_inst = coro()
        assert coro_inst.send(None) is future
        with pytest.raises(
            aio.FutureNotReady,
            match='The future object resumed before result has been set',
        ):
            coro_inst.send(None)

        # Assert that `should_never_be_called` mock actually hasn't being called
        assert should_never_be_called.mock_calls == []

        # Set future result to prevent warnings
        promise.set_result(None)

    def test_coro_raises_exception_being_set(self, create_promise):
        promise = create_promise()
        future = promise.future
        should_never_be_called = Mock(name='should_never_be_called')

        async def coro():
            await future
            should_never_be_called()

        coro_inst = coro()
        assert coro_inst.send(None) is future

        exc_inst = SpecialExc()
        promise.set_exception(exc_inst)

        with pytest.raises(SpecialExc) as exc_info:
            coro_inst.send(None)
        assert exc_info.value is exc_inst
        assert should_never_be_called.mock_calls == []

    def test_multiple_awaiters(self, create_promise):
        future_result = Mock(name='future_result')
        promise = create_promise()
        future = promise.future

        async def coro():
            return await future

        awaiters = [coro() for _ in range(10)]

        for awaiter in awaiters:
            assert awaiter.send(None) is future

        promise.set_result(future_result)
        for awaiter in awaiters:
            with pytest.raises(StopIteration) as exc_info:
                awaiter.send(None)
            assert exc_info.value.value is future_result

    def test_await_completed_future(self, create_promise):
        future_result = Mock(name='future-result')
        promise = create_promise()
        future = promise.future
        promise.set_result(future_result)

        async def coro():
            return await future

        coro_inst = coro()

        with pytest.raises(StopIteration) as exc_info:
            coro_inst.send(None)
        assert exc_info.value.value is future_result

    def test_await_completed_future_with_exc(self, create_promise):
        should_never_be_called = Mock(name='should_never_be_called')
        future_exc = SpecialExc()
        promise = create_promise()
        future = promise.future
        promise.set_exception(future_exc)

        async def coro():
            await future
            should_never_be_called()

        coro_inst = coro()

        with pytest.raises(SpecialExc) as exc_info:
            coro_inst.send(None)
        assert exc_info.value is future_exc
        assert should_never_be_called.mock_calls == []


class TestTask:
    def test_task_starting_state(self):
        async def coro():
            pass

        coro_inst = coro()
        task = aio.Task(coro_inst, Mock(name='loop'))
        assert not task.is_finished()
        assert task.state == aio.Future.State.created

        task._cancel('Test end clean-up')
        finalize_coro(coro_inst)

    def test_single_step_coro(
        self, create_task, loop_call_last_enqueued, loop_last_enqueued
    ):
        task_result = Mock(name='coro-result')

        async def coro():
            return task_result

        task = create_task(coro())
        assert (task._execute_coroutine_step, ()) in loop_last_enqueued
        loop_call_last_enqueued()

        assert task.is_finished()
        assert task.result is task_result

    def test_single_step_coro_raises_exc(self, create_task, loop_call_last_enqueued):
        task_exc = Exception('special exception')

        async def coro():
            raise task_exc

        task = create_task(coro())

        loop_call_last_enqueued()
        assert task.is_finished()
        assert task.exception is task_exc

    def test_coro_with_single_step_awaits_future(
        self, create_promise, create_task, loop_call_last_enqueued
    ):
        promise = create_promise()
        future = promise.future
        future_result = Mock(name='future_result')

        async def coro():
            return await future

        task = create_task(coro())
        assert not task.is_finished()

        promise.set_result(future_result)
        loop_call_last_enqueued()

        assert task.is_finished()
        assert task.result is future_result

    def test_coro_with_single_step_awaits_future_raise_exc(
        self, create_task, create_promise, loop_call_last_enqueued
    ):
        promise = create_promise()
        future = promise.future

        async def coro():
            return await future

        task = create_task(coro())
        assert not task.is_finished()

        exc_inst = SpecialExc()
        promise.set_exception(exc_inst)
        loop_call_last_enqueued()

        assert task.is_finished()
        assert task.exception is exc_inst

    def test_task_two_steps(self, create_promise, create_task, loop_call_last_enqueued):
        future_result0 = Mock(name='future-result-0')
        future_result1 = Mock(name='future-result-1')

        promise0 = create_promise()
        promise1 = create_promise()

        async def coro():
            r0 = await promise0.future
            r1 = await promise1.future
            return r0, r1

        task = create_task(coro())

        assert not task.is_finished()
        promise0.set_result(future_result0)
        loop_call_last_enqueued()

        assert not task.is_finished()
        promise1.set_result(future_result1)
        loop_call_last_enqueued()

        assert task.is_finished()
        assert task.result == (future_result0, future_result1)

    def test_task_two_steps_first_raises_second_returns(
        self, create_promise, create_task, loop_call_last_enqueued
    ):
        future_exc0 = SpecialExc('future 0 exc')
        future_result1 = Mock(name='future_result1')

        promise0 = create_promise()
        promise1 = create_promise()

        async def coro():
            try:
                await promise0.future
            except SpecialExc as err:
                r0 = err
            else:
                raise Exception('should never occurs')

            r1 = await promise1.future
            return r0, r1

        task = create_task(coro())

        assert not task.is_finished()
        promise0.set_exception(future_exc0)
        loop_call_last_enqueued()

        assert not task.is_finished()
        promise1.set_result(future_result1)
        loop_call_last_enqueued()

        assert task.is_finished()
        assert task.result == (future_exc0, future_result1)

    def test_cancel_do_not_await_inner_coro(self, loop_call_last_enqueued):
        should_never_be_called = Mock(name='should_never_be_called')

        async def coro():
            should_never_be_called()

        coro_inst = coro()
        task = aio.Task(coro_inst, Mock(name='loop'))
        task._cancel()

        assert should_never_be_called.mock_calls == []

        finalize_coro(coro_inst)

    def test_cancel_also_cancels_inner_future(
        self, create_promise, create_task, loop_call_last_enqueued
    ):
        inner_future_cb = Mock(name='inner_future_cb')
        should_be_called = Mock(name='should_be_called')
        should_never_be_called = Mock(name='should_never_be_called')

        inner_promise = create_promise()
        inner_future = inner_promise.future
        inner_future.add_callback(inner_future_cb)

        async def coro():
            should_be_called()
            await inner_future
            should_never_be_called()

        task = create_task(coro())
        loop_call_last_enqueued()

        assert should_be_called.mock_calls == [call()]

        task._cancel()
        loop_call_last_enqueued()

        assert inner_future.is_finished()
        assert inner_future.is_cancelled()
        assert inner_future_cb.mock_calls == [call(inner_future)]
        assert task.is_finished()
        assert task.is_cancelled()
        assert should_never_be_called.mock_calls == []
