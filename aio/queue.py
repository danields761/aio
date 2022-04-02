from __future__ import annotations

from collections import deque
from typing import AsyncIterable, AsyncIterator, Iterable, Tuple, TypeVar

from aio.future import Promise, _create_promise

T = TypeVar("T")


class QueueBaseError(Exception):
    pass


class QueueOverflowedError(QueueBaseError):
    pass


class QueueEmptyError(QueueBaseError):
    pass


class QueueClosed(QueueBaseError):
    pass


class QueueAsyncIterator(AsyncIterator[T]):
    def __init__(self, queue: Queue[T]) -> None:
        self._queue = queue

    async def __anext__(self) -> T:
        try:
            return await self._queue.get()
        except QueueClosed:
            raise StopAsyncIteration


class Queue(AsyncIterable[T]):
    def __init__(self, prepopulate: Iterable[T] = (), *, max_capacity: int | None = None) -> None:
        self._container = deque(prepopulate)
        self._read_waiters_q: deque[Promise[T]] = deque()
        self._write_waiters_q: deque[Tuple[T, Promise[None]]] = deque()
        self._max_capacity = max_capacity
        self._closed = False

    def __aiter__(self) -> AsyncIterator[T]:
        return QueueAsyncIterator(self)

    def get_no_wait(self) -> T:
        if self._closed and len(self._container) == 0:
            raise QueueClosed

        if len(self._container) == 0:
            raise QueueEmptyError
        assert len(self._read_waiters_q) == 0

        if len(self._write_waiters_q) > 0 and self._max_capacity is not None:
            assert len(self._container) == self._max_capacity

            write_elem, write_waiter = self._write_waiters_q.popleft()
            write_waiter.set_result(None)

            elem_to_return = self._container.popleft()
            self._container.append(write_elem)

            return elem_to_return

        return self._container.popleft()

    async def get(self) -> T:
        try:
            return self.get_no_wait()
        except QueueEmptyError:
            pass

        waiter_promise: Promise[T] = _create_promise()
        self._read_waiters_q.append(waiter_promise)

        try:
            return await waiter_promise.future
        finally:
            try:
                self._read_waiters_q.remove(waiter_promise)
            except ValueError:
                pass

    def put_no_wait(self, elem: T) -> None:
        if self._closed:
            raise QueueClosed

        if self._max_capacity is not None and len(self._container) >= self._max_capacity:
            assert len(self._read_waiters_q) == 0

            raise QueueOverflowedError

        if len(self._read_waiters_q) > 0:
            assert len(self._container) == 0

            waiter = self._read_waiters_q.popleft()
            waiter.set_result(elem)
            return

        self._container.append(elem)

    async def put(self, elem: T) -> None:
        try:
            self.put_no_wait(elem)
            return
        except QueueOverflowedError:
            pass

        assert self._max_capacity is not None

        waiter_promise: Promise[None] = _create_promise()
        waiters_q_elem = (elem, waiter_promise)
        self._write_waiters_q.append(waiters_q_elem)

        try:
            await waiter_promise.future
        finally:
            try:
                self._write_waiters_q.remove(waiters_q_elem)
            except ValueError:
                pass

    def close(self) -> None:
        if self._closed:
            raise RuntimeError("Queue already being closed")
        self._closed = True
        for _, write_promise in self._write_waiters_q:
            write_promise.set_exception(QueueClosed())
        for read_promise in self._read_waiters_q:
            read_promise.set_exception(QueueClosed())
