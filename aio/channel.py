from __future__ import annotations

import contextlib
from collections import deque
from typing import AsyncIterable, AsyncIterator, Iterable, Protocol, Tuple, TypeVar

from aio.future import create_promise
from aio.interfaces import Promise

T = TypeVar("T")
T_cov = TypeVar("T_cov", covariant=True)
T_contra = TypeVar("T_contra", contravariant=True)


class OverflowedError(Exception):
    pass


class EmptyError(Exception):
    pass


class Closed(Exception):
    pass


class Left(Protocol[T_contra]):
    def put_no_wait(self, elem: T_contra, /) -> None:
        raise NotImplementedError

    async def put(self, elem: T_contra, /) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class Right(AsyncIterable[T_cov], Protocol[T_cov]):
    def get_no_wait(self) -> T:
        raise NotImplementedError

    async def get(self) -> T:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class _QueueAsyncIterator(AsyncIterator[T]):
    def __init__(self, queue: _Queue[T]) -> None:
        self._queue = queue

    async def __anext__(self) -> T:
        try:
            return await self._queue.get()
        except Closed:
            raise StopAsyncIteration


class _Queue(AsyncIterable[T]):
    def __init__(self, prepopulate: Iterable[T] = (), *, max_capacity: int | None = None) -> None:
        self._container = deque(prepopulate)
        self._read_waiters_q: deque[Promise[T]] = deque()
        self._write_waiters_q: deque[Tuple[T, Promise[None]]] = deque()
        self._max_capacity = max_capacity
        self._closed = False

    def __aiter__(self) -> AsyncIterator[T]:
        return _QueueAsyncIterator(self)

    def get_no_wait(self) -> T:
        if self._closed and len(self._container) == 0:
            raise Closed

        if len(self._container) == 0:
            raise EmptyError
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
        except EmptyError:
            pass

        async with create_promise("get-waiter") as waiter_promise:
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
            raise Closed

        if self._max_capacity is not None and len(self._container) >= self._max_capacity:
            assert len(self._read_waiters_q) == 0

            raise OverflowedError

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
        except OverflowedError:
            pass

        assert self._max_capacity is not None
        async with create_promise("put-waiter") as waiter_promise:
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
        self._closed = True
        for _, write_promise in self._write_waiters_q:
            if write_promise.future.is_finished:
                continue
            write_promise.set_exception(Closed())
        for read_promise in self._read_waiters_q:
            if read_promise.future.is_finished:
                continue
            read_promise.set_exception(Closed())


@contextlib.asynccontextmanager
async def create(
    prepopulate: Iterable[T] = (), *, max_capacity: int | None = None
) -> AsyncIterator[tuple[Left, Right]]:
    queue = _Queue(prepopulate, max_capacity=max_capacity)
    try:
        yield (queue, queue)
    finally:
        queue.close()
