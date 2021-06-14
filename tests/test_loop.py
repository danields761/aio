import gc
import os
import signal
from unittest.mock import DEFAULT, MagicMock, Mock, call

import pytest

import aio
from aio.components.scheduler import Scheduler
from aio.interfaces import Clock, Handle
from aio.loop import BaseEventLoop, BaseLoopRunner


def process_handle_exception(exc, logger_, /, **context) -> None:
    import traceback

    traceback.print_exception(type(exc), exc, exc.__traceback__)
    pytest.fail('No unhandled exceptions is allowed inside callbacks during testing')


@pytest.fixture
def clock():
    clock = MagicMock(Clock, name='clock')
    clock.now.return_value = 50.0
    clock.resolution.return_value = 0.1
    return clock


@pytest.fixture
def selector(clock):
    selector = Mock(name='selector')

    def selector_select(time_):
        if time_ is None:
            return
        clock.now.return_value += time_

    selector.select = Mock(wraps=selector_select)
    return selector


class TestLoopStepping:
    @pytest.fixture
    def make_loop(self, selector, clock):
        return lambda scheduler: BaseEventLoop(
            selector,
            networking_factory=Mock(side_effect=Exception('Not exists.')),
            clock=clock,
            scheduler=scheduler,
            exception_handler=process_handle_exception,
        )

    def test_runs_only_expired_cbs(self, clock, selector, make_loop):
        parent_cb = Mock()
        cb0 = parent_cb.cb0
        cb1 = parent_cb.cb1
        scheduler = Scheduler([], [Handle(55.0, cb0), Handle(60.0, cb1)])

        make_loop(scheduler).run_step()

        assert selector.mock_calls == [call.select(5.0)]
        assert scheduler.get_items() == [Handle(60.0, cb1)]
        assert parent_cb.mock_calls == [
            call.cb0(),
        ]
        assert clock.now() == 55.0

    def test_dont_runs_pending_if_cancelled(self, clock, selector, make_loop):
        parent_cb = Mock()
        cb1 = parent_cb.cb1
        cb2 = parent_cb.cb2
        handle1 = Handle(None, cb1, cancelled=True)
        handle2 = Handle(60.0, cb2)
        scheduler = Scheduler([handle1], [handle2])

        make_loop(scheduler).run_step()

        assert selector.mock_calls == [call.select(10.0)]
        assert scheduler.get_items() == []
        assert parent_cb.mock_calls == [
            call.cb2(),
        ]
        assert clock.now() == 60.0

    def test_dont_runs_enqueued_if_cancelled(self, clock, selector, make_loop):
        parent_cb = Mock()
        cb1 = parent_cb.cb1
        cb2 = parent_cb.cb2
        handle1 = Handle(55.0, cb1, cancelled=True)
        handle2 = Handle(60.0, cb2)
        scheduler = Scheduler([], [handle1, handle2])

        make_loop(scheduler).run_step()

        assert selector.mock_calls == [call.select(10.0)]
        assert scheduler.get_items() == []
        assert parent_cb.mock_calls == [
            call.cb2(),
        ]
        assert clock.now() == 60.0

    def test_dont_runs_pending_if_cancelled_during_select(
        self, clock, selector, make_loop
    ):
        parent_cb = Mock()
        cb1 = parent_cb.cb1
        cb2 = parent_cb.cb2
        handle1 = Handle(55.0, cb1)
        handle2 = Handle(60.0, cb2)
        scheduler = Scheduler([], [handle1, handle2])
        selector.select.side_effect = (
            # returning DEFAULT force mock to proceed to call 'wraps' object
            lambda *_: (handle1.cancel() or DEFAULT)
        )

        make_loop(scheduler).run_step()

        assert selector.mock_calls == [call.select(5.0)]
        assert scheduler.get_items() == [handle2]
        assert parent_cb.mock_calls == []
        assert clock.now() == 55.0

    @pytest.mark.parametrize('same_time_events_count', [2, 3, 4, 5])
    def test_runs_only_expired_cbs_have_same_time_events(
        self, clock, selector, make_loop, same_time_events_count
    ):
        parent_cb = Mock()
        cbs = [getattr(parent_cb, f'cb{i}') for i in range(same_time_events_count)]
        last_cb = parent_cb.cb2
        scheduler = Scheduler(
            [], [Handle(55.0, cb) for cb in cbs] + [Handle(60.0, last_cb)]
        )

        make_loop(scheduler).run_step()

        assert scheduler.get_items() == [Handle(60.0, last_cb)]
        assert parent_cb.mock_calls == [
            getattr(call, f'cb{i}')() for i in range(same_time_events_count)
        ]
        assert selector.mock_calls == [call.select(5.0)]
        assert clock.now() == 55.0

    def test_runs_only_expired_cbs2(self, clock, selector, make_loop):
        parent_cb = Mock()
        cb0 = parent_cb.cb0
        cb1 = parent_cb.cb1
        scheduler = Scheduler([], [Handle(55.0, cb0), Handle(60.0, cb1)])
        loop = make_loop(scheduler)

        loop.run_step()
        assert selector.mock_calls == [call.select(5.0)]
        assert clock.now() == 55.0
        assert parent_cb.mock_calls == [
            call.cb0(),
        ]

        loop.run_step()
        assert scheduler.get_items() == []
        assert parent_cb.mock_calls == [
            call.cb0(),
            call.cb1(),
        ]
        assert selector.mock_calls == [call.select(5.0), call.select(5.0)]
        assert clock.now() == 60.0

    def test_runs_only_expired_cbs3(self, clock, selector, make_loop):
        parent_cb = Mock()
        cb0 = parent_cb.cb0
        cb1 = parent_cb.cb1
        cb2 = parent_cb.cb2
        scheduler = Scheduler(
            [], [Handle(55.0, cb0), Handle(60.0, cb1), Handle(65.0, cb2)]
        )
        loop = make_loop(scheduler)

        for i in range(3):
            loop.run_step()

        assert scheduler.get_items() == []
        assert parent_cb.mock_calls == [
            call.cb0(),
            call.cb1(),
            call.cb2(),
        ]
        assert selector.mock_calls == [
            call.select(5.0),
            call.select(5.0),
            call.select(5.0),
        ]
        assert clock.now() == 65.0

    def test_runs_pending_cbs(self, clock, selector, make_loop):
        parent_cb = Mock()
        cb0 = parent_cb.cb0
        cb1 = parent_cb.cb1
        scheduler = Scheduler([Handle(None, cb0), Handle(None, cb1)])
        loop = make_loop(scheduler)

        loop.run_step()

        assert scheduler.get_items() == []
        assert parent_cb.mock_calls == [call.cb0(), call.cb1()]
        assert selector.mock_calls == [call.select(0)]
        assert clock.now() == 50.0

    def test_zero_time_select_if_pending_cbs(self, selector, clock, make_loop):
        cb1 = Mock()
        cb2 = Mock()
        scheduler = Scheduler([Handle(None, cb1)], [Handle(60, cb2)])
        loop = make_loop(scheduler)

        loop.run_step()

        assert scheduler.get_items() == [Handle(60, cb2)]
        assert cb1.mock_calls == [call()]
        assert cb2.mock_calls == []
        assert selector.mock_calls == [call.select(0)]
        assert clock.now() == 50

    @pytest.mark.parametrize('now', [0.0, 15.0])
    def test_executes_handle_eagerly_if_below_clock_resolution(
        self, selector, clock, make_loop, now
    ):
        clock.now.return_value = now
        cb1 = Mock()
        cb2 = Mock()
        h1 = Handle(now + clock.resolution() / 2, cb1)
        h2 = Handle(now + clock.resolution() * 2, cb2)
        scheduler = Scheduler([], [h1, h2])
        loop = make_loop(scheduler)

        loop.run_step()

        assert scheduler.get_items() == [h2]
        assert cb1.mock_calls == [call()]
        assert selector.mock_calls == [call.select(0)]
        assert clock.now() == now

    def test_run_both_pending_and_scheduled(self, clock, selector, make_loop):
        parent_cb = Mock()
        rcb0 = parent_cb.rcb0
        rcb1 = parent_cb.rcb1
        scb0 = parent_cb.scb0
        scb1 = parent_cb.scb1

        scheduler = Scheduler(
            [Handle(None, rcb0), Handle(None, rcb1)],
            [Handle(55.0, scb0), Handle(60.0, scb1)],
        )
        loop = make_loop(scheduler)

        # must consume all pending cbs and first scheduled on second step
        for i in range(2):
            loop.run_step()

        assert scheduler.get_items() == [Handle(60.0, scb1)]
        assert selector.mock_calls == [call.select(0), call.select(5.0)]

        # call order isn't guarantied
        assert sorted(parent_cb.mock_calls) == sorted(
            [call.rcb0(), call.rcb1(), call.scb0()]
        )

    def test_enqueue_pending_during_select(self, selector, clock, make_loop):
        first_cb = Mock(name='first-cb')
        enqueued_cb = Mock(name='enqueued-cb')

        scheduler = Scheduler(enqueued=[Handle(55.0, first_cb)])
        loop = make_loop(scheduler)

        selector.select.side_effect = (
            # returning DEFAULT force mock to proceed to call 'wraps' object
            lambda *_: (scheduler.enqueue(Handle(None, enqueued_cb)) or DEFAULT)
        )

        loop.run_step()

        assert selector.mock_calls == [call.select(5.0)]
        assert first_cb.mock_calls == [call()]
        assert enqueued_cb.mock_calls == [call()]
        assert scheduler.get_items() == []

    def test_enqueue_later_during_select(self, selector, clock, make_loop):
        first_cb = Mock(name='first-cb')
        enqueued_cb = Mock(name='enqueued-cb')

        scheduler = Scheduler(enqueued=[Handle(55.0, first_cb)])
        loop = make_loop(scheduler)

        selector.select.side_effect = (
            # returning DEFAULT force mock to proceed to call 'wraps' object
            lambda *_: (scheduler.enqueue(Handle(clock.now(), enqueued_cb)) or DEFAULT)
        )

        loop.run_step()

        assert selector.mock_calls == [call.select(5.0)]
        assert first_cb.mock_calls == [call()]
        assert enqueued_cb.mock_calls == [call()]
        assert scheduler.get_items() == []

    def test_enqueue_much_later_during_select(self, selector, clock, make_loop):
        first_cb = Mock(name='first-cb')
        enqueued_cb = Mock(name='enqueued-cb')
        enqueued_handle = Handle(100, enqueued_cb)

        scheduler = Scheduler(enqueued=[Handle(55.0, first_cb)])
        loop = make_loop(scheduler)

        selector.select.side_effect = (
            # returning DEFAULT force mock to proceed to call 'wraps' object
            lambda *_: (scheduler.enqueue(enqueued_handle) or DEFAULT)
        )

        loop.run_step()

        assert selector.mock_calls == [call.select(5.0)]
        assert first_cb.mock_calls == [call()]
        assert enqueued_cb.mock_calls == []
        assert scheduler.get_items() == [enqueued_handle]


class TestLoopRunner:
    @pytest.fixture
    def runner(self, clock, selector):
        loop = BaseEventLoop(
            selector, Mock(side_effect=Exception('Not exists.')), clock=clock
        )
        runner = BaseLoopRunner(loop, selector)
        return runner

    @pytest.fixture
    def loop(self, runner):
        return runner.loop

    def test_runs_simple_coroutine(self, runner):
        should_be_called = Mock()

        async def root():
            should_be_called()

        runner.run_coroutine(root())

        assert should_be_called.mock_calls == [call()]

    def test_propagates_coroutine_exception(self, runner):
        async def root():
            raise Exception('Some exception')

        with pytest.raises(Exception, match='Some exception'):
            runner.run_coroutine(root())

    def test_runs_multi_suspend_coroutine(self, loop, runner, clock):
        should_be_called = Mock()

        async def root():
            at_start = clock.now()

            for i in range(1, 11):
                await aio.sleep(1)
                assert clock.now() - at_start == 1.0 * i

            should_be_called()

        runner.run_coroutine(root())
        assert should_be_called.mock_calls == [call()]

    def test_changes_sigint_to_cancelled(self, runner):
        should_be_called = Mock()
        should_not_be_called = Mock()

        async def root():
            should_be_called()
            # Probably wont work in a lot of cases and may cause wired behaviour
            os.kill(os.getpid(), signal.SIGINT)
            # Suspend coroutine to initialize further processing
            await aio.sleep(10)

            should_not_be_called()

        with pytest.raises(aio.Cancelled):
            runner.run_coroutine(root())

        assert should_be_called.mock_calls == [call()]
        assert should_not_be_called.mock_calls == []

    def test_should_warn_if_async_gen_being_gc_while_not_finished(self, runner):
        should_be_called_in_root = Mock()
        should_be_called_in_gen = Mock()

        async def async_gen():
            should_be_called_in_gen()
            yield 1
            yield 2

        async def root():
            should_be_called_in_root()
            async for _ in async_gen():
                break
            gc.collect()

        with pytest.warns(UserWarning) as warn_info:
            runner.run_coroutine(root())

        assert should_be_called_in_root.mock_calls == [call()]
        assert should_be_called_in_gen.mock_calls == [call()]
        assert any(
            'Async-generator shutdown request income for' in warn.message.args[0]
            and warn.filename == __file__
            for warn in warn_info.list
        )
