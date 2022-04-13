import gc
import inspect
import sys
from typing import Coroutine
from unittest.mock import ANY, Mock, call

import pytest

import aio
import aio.future
import aio.interfaces
from aio.exceptions import CancelledByChild, CancelledByParent, SelfCancelForbidden
from aio.future import _create_promise, _create_task, _guard_task, _Task
from aio.loop._priv import running_loop


class SpecialExc(Exception):
    pass


@pytest.fixture
def loop_queue():
    return []


@pytest.fixture
def loop_make_step(loop_queue):
    def caller():
        old_last_enqueued = list(loop_queue)
        loop_queue[:] = []

        for target, args in old_last_enqueued:
            target(*args)

    return caller


@pytest.fixture
def loop(loop_queue):
    def call_soon(target, *args, context=None):
        loop_queue.append((target, args))

        def cancel():
            try:
                loop_queue.remove((target, args))
            except ValueError:
                pass
            handle.cancelled = True

        handle = Mock(name="handle")
        handle.cancel = Mock(wraps=cancel)
        handle.cancelled = False
        handle.executed = False
        return handle

    loop = Mock(aio.EventLoop, name="loop")
    loop.call_soon = Mock(wraps=call_soon)
    loop.call_later.side_effect = NotImplementedError('"call_later" is forbidden here')

    token = running_loop.set(loop)
    yield loop
    running_loop.reset(token)


def test_loop_mock(loop, loop_make_step):
    root = Mock()

    loop.call_soon(root.first)
    loop.call_soon(root.second, 1)
    loop.call_soon(root.third, 1, 2)

    loop_make_step()

    assert root.mock_calls == [
        call.first(),
        call.second(1),
        call.third(1, 2),
    ]


@pytest.fixture
def create_promise(loop):
    return lambda th=None: _create_promise(th or "test-future", _loop=loop)


@pytest.fixture
def create_task(loop):
    return lambda coro, th=None: _create_task(coro, label=th or "test-task", _loop=loop)


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
    def test_result_set_get(self, create_promise, loop_make_step):
        promise = create_promise()
        future = promise.future

        assert future.state == aio.interfaces.Future.State.running
        with pytest.raises(aio.FutureNotReady):
            _ = future.result()

        with pytest.raises(aio.FutureNotReady):
            _ = future.exception()

        promise.set_result("test result")
        loop_make_step()

        assert future.state == aio.interfaces.Future.State.finished
        assert future.is_finished
        assert future.result() == "test result"
        assert future.exception() is None

    def test_get_result_after_exc(self, create_promise, loop_make_step):
        test_err = Exception("test exception description")

        promise = create_promise()
        future = promise.future

        assert future.state == aio.interfaces.Future.State.running

        promise.set_exception(test_err)
        loop_make_step()

        assert future.is_finished
        assert future.state == aio.interfaces.Future.State.finished
        assert future.exception() is test_err

        with pytest.raises(Exception) as exc_info:
            _ = future.result()
        assert exc_info.value is test_err

    def test_warns_if_exc_not_retrieved(self, create_promise):
        promise = create_promise()
        promise.set_exception(Exception("unretrieved exception"))

        with pytest.warns() as warn_cap:
            del promise
            gc.collect()

        assert len(warn_cap.list) == 1
        assert (
            "is about to be destroyed, but her exception was never retrieved"
            in warn_cap.list[0].message.args[0]
        )

    def test_on_done_cbs_set_res(self, create_promise, loop_make_step):
        cb = Mock()
        promise = create_promise()
        future = promise.future
        future.add_callback(cb)

        promise.set_result("test result")
        loop_make_step()

        assert cb.mock_calls == [call(future)]
        assert future.is_finished
        assert future.result() == "test result"

    def test_on_done_cbs_set_exc(self, create_promise, loop_make_step):
        test_err = Exception("test exception description")

        cb = Mock()
        promise = create_promise()
        future = promise.future
        future.add_callback(cb)

        promise.set_exception(test_err)
        loop_make_step()

        assert cb.mock_calls == [call(future)]
        assert future.is_finished
        assert future.exception() is test_err

    def test_on_cancel_calls_cb(self, create_promise, loop_make_step):
        cb = Mock()
        promise = create_promise()
        future = promise.future
        future.add_callback(cb)

        promise.cancel()
        loop_make_step()

        assert cb.mock_calls == [call(future)]
        assert future.is_finished
        assert future.is_cancelled
        assert isinstance(future.exception(), aio.Cancelled)

    def test_finished_state_in_res_cb(self, create_promise, loop_make_step):
        promise = create_promise()
        future = promise.future

        def cb_callable(res_fut):
            assert res_fut is future
            assert res_fut.state == aio.interfaces.Future.State.finished
            assert res_fut.result() == "test result"
            assert res_fut.exception() is None

        cb = Mock(wraps=cb_callable)

        future.add_callback(cb)
        promise.set_result("test result")
        loop_make_step()

        assert cb.mock_calls == [call(future)]

    def test_cant_add_same_cb_twice(self, create_promise, loop_make_step):
        cb = Mock()

        promise = create_promise()
        future = promise.future
        future.add_callback(cb)
        future.add_callback(cb)

        promise.set_result("test result")
        loop_make_step()

        assert cb.mock_calls == [call(future)]

    def test_coro_awaits(self, create_promise, loop_make_step):
        result_mock = Mock(name="result")
        promise = create_promise()
        future = promise.future

        async def coro():
            return await future

        coro_inst = coro()
        assert coro_inst.send(None) is future

        promise.set_result(result_mock)
        loop_make_step()

        with pytest.raises(StopIteration) as exc_info:
            coro_inst.send(None)
        assert exc_info.value.value is result_mock

    def test_coro_cant_await_same_future_twice(self, create_promise):
        promise = create_promise()
        future = promise.future
        should_never_be_called = Mock(name="should_never_be_called")

        async def coro():
            await future
            should_never_be_called()

        coro_inst = coro()
        assert coro_inst.send(None) is future
        with pytest.raises(
            RuntimeError,
            match="Future being resumed after first yield, but still not finished!",
        ):
            coro_inst.send(None)

        # Assert that `should_never_be_called` mock actually hasn't being called
        assert should_never_be_called.mock_calls == []

        # Set future result to prevent warnings
        promise.set_result(None)

    def test_coro_raises_exception_being_set(self, create_promise):
        promise = create_promise()
        future = promise.future
        should_never_be_called = Mock(name="should_never_be_called")

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
        future_result = Mock(name="future_result")
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
        future_result = Mock(name="future-result")
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
        should_never_be_called = Mock(name="should_never_be_called")
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


class SpecialCancel(aio.Cancelled):
    pass


class TestTask:
    def test_task_starting_state(self):
        async def coroutine():
            pass

        coro = coroutine()
        task = _Task(coro, Mock(name="loop"))
        assert not task.is_finished
        assert task.state == aio.interfaces.Future.State.created

        assert inspect.getcoroutinestate(task._state.coroutine) == "CORO_CREATED"
        task._cancel("Test end clean-up")
        # Retrieve exception to prevent unretrieved exception warning
        with pytest.raises(aio.Cancelled):
            task.result()
        # Prevent un-awaited coroutine warning
        finalize_coro(coro)

    def test_create_task_schedules_first_step(self, create_task, loop_make_step, loop_queue):
        task_result = Mock(name="coro-result")

        async def coroutine():
            return task_result

        coro = coroutine()
        task = create_task(coro)
        assert (task._execute_coroutine_step, ()) in loop_queue
        assert task.state == aio.interfaces.Future.State.scheduled

        task._cancel("Test end clean-up")
        # Retrieve exception to prevent unretrieved exception warning
        with pytest.raises(aio.Cancelled):
            task.result()
        # Prevent un-awaited coroutine warning
        finalize_coro(coro)

    def test_single_step_coro(self, create_task, loop_make_step, loop_queue):
        task_result = Mock(name="coro-result")

        async def coro():
            return task_result

        task = create_task(coro())
        assert (task._execute_coroutine_step, ()) in loop_queue
        loop_make_step()

        assert task.is_finished
        assert task.state == aio.interfaces.Future.State.finished
        assert task.result() is task_result

    def test_single_step_coro_raises_exc(self, create_task, loop_make_step):
        task_exc = Exception("special exception")

        async def coro():
            raise task_exc

        task = create_task(coro())
        loop_make_step()

        assert task.is_finished
        assert task.state == aio.interfaces.Future.State.finished
        assert task.exception() is task_exc

    def test_coro_with_single_step_awaits_future(self, create_promise, create_task, loop_make_step):
        promise = create_promise()
        future = promise.future
        future_result = Mock(name="future_result")

        async def coro():
            return await future

        task = create_task(coro())
        assert not task.is_finished

        promise.set_result(future_result)
        loop_make_step()

        assert task.is_finished
        assert task.result() is future_result

    def test_coro_with_single_step_awaits_future_raise_exc(
        self, create_task, create_promise, loop_make_step
    ):
        promise = create_promise()
        future = promise.future

        async def coro():
            return await future

        task = create_task(coro())
        assert not task.is_finished

        exc_inst = SpecialExc()
        promise.set_exception(exc_inst)
        loop_make_step()

        assert task.is_finished
        assert task.exception() is exc_inst

    def test_task_two_steps(self, create_promise, create_task, loop_make_step):
        root = Mock()

        future_result0 = Mock(name="future-result-0")
        future_result1 = Mock(name="future-result-1")

        promise0 = create_promise("promise-0")
        promise1 = create_promise("promise-1")

        async def coro():
            root.before_first()
            r0 = await promise0.future
            root.after_first()
            root.before_second()
            r1 = await promise1.future
            root.after_second()
            return r0, r1

        task = create_task(coro())
        loop_make_step()
        assert task._state.waiting_on is promise0.future
        assert root.mock_calls == [call.before_first()]
        assert not task.is_finished

        promise0.set_result(future_result0)
        assert root.mock_calls == [call.before_first()]

        loop_make_step()
        assert task._state.waiting_on is promise1.future
        assert root.mock_calls == [
            call.before_first(),
            call.after_first(),
            call.before_second(),
        ]
        assert not task.is_finished

        promise1.set_result(future_result1)
        assert root.mock_calls == [
            call.before_first(),
            call.after_first(),
            call.before_second(),
        ]
        loop_make_step()
        assert root.mock_calls == [
            call.before_first(),
            call.after_first(),
            call.before_second(),
            call.after_second(),
        ]

        assert task.is_finished
        assert task.result() == (future_result0, future_result1)

    def test_task_two_steps_first_raises_second_returns(
        self, create_promise, create_task, loop_make_step
    ):
        future_exc0 = SpecialExc("future 0 exc")
        future_result1 = Mock(name="future_result1")

        promise0 = create_promise()
        promise1 = create_promise()

        async def coro():
            try:
                await promise0.future
            except SpecialExc as err:
                r0 = err
            else:
                raise Exception("should never occurs")

            r1 = await promise1.future
            return r0, r1

        task = create_task(coro())
        loop_make_step()

        assert not task.is_finished
        promise0.set_exception(future_exc0)
        loop_make_step()

        assert not task.is_finished
        promise1.set_result(future_result1)
        loop_make_step()

        assert task.is_finished
        assert task.result() == (future_exc0, future_result1)

    def test_cancel_do_not_await_unscheduled_inner_coro(self):
        should_never_be_called = Mock(name="should_never_be_called")

        async def coro():
            should_never_be_called()

        coro_inst = coro()
        task = _Task(coro_inst, Mock(name="loop"))
        task._cancel()

        assert should_never_be_called.mock_calls == []

        # Retrieve exception to prevent unretrieved exception warning
        with pytest.raises(aio.Cancelled):
            task.result()
        # Avoid un-awaited coroutine warning
        finalize_coro(coro_inst)

    def test_cancel_also_cancels_inner_future(
        self, create_promise, create_task, loop_make_step, loop_queue
    ):
        inner_future_cb = Mock(name="inner_future_cb")
        should_be_called = Mock(name="should_be_called")
        should_never_be_called = Mock(name="should_never_be_called")

        inner_promise = create_promise()
        inner_future = inner_promise.future
        inner_future.add_callback(inner_future_cb)

        async def coro():
            should_be_called()
            await inner_future
            should_never_be_called()

        task = create_task(coro())
        loop_make_step()

        assert should_be_called.mock_calls == [call()]

        cancel_exc = SpecialCancel()
        task.cancel(cancel_exc)
        loop_make_step()
        assert loop_queue == []

        assert inner_future.is_finished
        assert inner_future.is_cancelled
        assert inner_future.exception() is cancel_exc
        assert inner_future_cb.mock_calls == [call(inner_future)]

        assert task.is_finished
        assert task.is_cancelled
        assert task.exception() is cancel_exc

        assert should_never_be_called.mock_calls == []

    def test_inner_cancel_when_awaited_future_finish(
        self, create_promise, create_task, loop_make_step, loop
    ):
        inner_promise = create_promise("inner-future")

        async def coroutine():
            await inner_promise.future

        task = create_task(coroutine())
        loop_make_step()

        assert task._state.waiting_on is inner_promise.future
        inner_promise.set_result(None)
        assert inner_promise.future._state.scheduled_cbs == {task._execute_coroutine_step: ANY}
        handle = inner_promise.future._state.scheduled_cbs[task._execute_coroutine_step]
        assert loop.mock_calls == [
            call.call_soon(task._execute_coroutine_step),
            call.call_soon(task._execute_coroutine_step, inner_promise.future, context=ANY),
        ]

        task.cancel()
        assert loop.mock_calls == [
            call.call_soon(task._execute_coroutine_step),
            call.call_soon(task._execute_coroutine_step, inner_promise.future, context=ANY),
            call.call_soon(task._execute_coroutine_step, None, aio.Cancelled()),
        ]
        assert inner_promise.future._state.scheduled_cbs == {}
        assert handle.cancelled

        loop_make_step()

        assert task.is_finished
        with pytest.raises(aio.Cancelled):
            task.result()

    def test_self_cancel_is_forbidden(self, create_task, loop_make_step):
        async def coroutine():
            self_task = await aio.get_current_task()
            self_task.cancel()

        task = create_task(coroutine())
        loop_make_step()

        assert task.is_finished
        assert isinstance(task.exception(), SelfCancelForbidden)

    def test_after_task_step_completed_future_dont_hold_task_ref(
        self, create_task, create_promise, loop_make_step
    ):
        promise = create_promise()

        async def coroutine():
            await promise.future

        task = create_task(coroutine())
        loop_make_step()

        assert sys.getrefcount(task) == 4

        promise.set_result(None)
        loop_make_step()

        del promise
        gc.collect()

        assert task.is_finished
        assert sys.getrefcount(task) == 3

    def test_current_task_accessible_from_coro(self, create_task, loop_make_step):
        should_be_called = Mock(name="should_be_called")

        async def coro():
            should_be_called()
            assert await aio.get_current_task() is task

        task = create_task(coro())
        loop_make_step()

        assert should_be_called.mock_calls == [call()]

    def test_current_task_accessible_from_coro_multiple_tasks(self, create_task, loop_make_step):
        should_be_called = Mock(name="should_be_called")

        async def coro1():
            should_be_called()
            assert await aio.get_current_task() is task1

        async def coro2():
            should_be_called()
            assert await aio.get_current_task() is task2

        task1 = create_task(coro1())
        task2 = create_task(coro2())
        loop_make_step()

        assert should_be_called.mock_calls == [call(), call()]


class TestScopedTask:
    def test_parent_ok_child_ok(self, loop_make_step, loop_queue, create_task):
        root = Mock()

        async def child():
            root.child_started()
            try:
                return 2
            finally:
                root.child_finished()

        async def parent():
            root.parent_started()
            async with _guard_task(child_task):
                pass
            root.parent_finished()
            return 1

        parent_task = create_task(parent(), "parent")
        child_task = create_task(child(), "child")

        loop_make_step()
        assert child_task.is_finished
        assert child_task.result() == 2

        # 2 loop step required because parent will wait for child to finish via shield
        loop_make_step()
        loop_make_step()
        assert parent_task.is_finished
        assert parent_task.result() == 1

        assert root.mock_calls == [
            call.parent_started(),
            call.child_started(),
            call.child_finished(),
            call.parent_finished(),
        ]

    def test_parent_exception_cancels_child(self, loop_make_step, loop_queue, create_task):
        root = Mock()

        async def child():
            root.child_started()
            try:
                await aio.sleep(0)
                return 2
            except BaseException as exc:
                root.child_exception(exc)
                raise
            finally:
                root.child_finished()

        async def parent():
            root.parent_started()
            try:
                async with _guard_task(child_task):
                    raise Exception("Parent exception")
            finally:
                root.parent_finished()

        # Gibe the child task first turn to execute to make it in running (not scheduled) state
        #  before parent will cancel it, otherwise child will not be executed at all
        child_task = create_task(child(), "child")
        parent_task = create_task(parent(), "parent")

        loop_make_step()
        assert root.mock_calls == [
            call.child_started(),
            call.parent_started(),
        ]
        loop_make_step()
        assert root.mock_calls == [
            call.child_started(),
            call.parent_started(),
            call.child_exception(CancelledByParent("Parent task being aborted with an exception")),
            call.child_finished(),
        ]

        with pytest.raises(CancelledByParent):
            child_task.result()

        loop_make_step()
        loop_make_step()
        assert root.mock_calls == [
            call.child_started(),
            call.parent_started(),
            call.child_exception(CancelledByParent("Parent task being aborted with an exception")),
            call.child_finished(),
            call.parent_finished(),
        ]
        with pytest.raises(Exception, match="Parent exception"):
            parent_task.result()

    def test_child_exception_cancels_parent(self, loop_make_step, loop_queue, create_task):
        root = Mock()
        child_exc = Exception("Child exception")

        async def child():
            root.child_started()
            try:
                raise child_exc
            finally:
                root.child_finished()

        async def parent():
            root.parent_started()
            try:
                async with _guard_task(child_task):
                    try:
                        await aio.sleep(0)
                    except BaseException as exc:
                        root.parent_in_sleep_exception(exc)
                        raise
            except BaseException as exc:
                root.parent_exception(exc)
                raise
            finally:
                root.parent_finished()

        parent_task = create_task(parent(), "parent")
        child_task = create_task(child(), "child")

        loop_make_step()
        assert root.mock_calls == [
            call.parent_started(),
            call.child_started(),
            call.child_finished(),
        ]
        with pytest.raises(Exception, match="Child exception"):
            child_task.result()

        loop_make_step()
        loop_make_step()
        assert root.mock_calls == [
            call.parent_started(),
            call.child_started(),
            call.child_finished(),
            call.parent_in_sleep_exception(
                CancelledByChild("Child task finished with an exception")
            ),
            call.parent_exception(CancelledByChild("Child task finished with an exception")),
            call.parent_finished(),
        ]
        with pytest.raises(CancelledByChild):
            parent_task.result()

        assert parent_task.exception().__cause__ is child_exc
