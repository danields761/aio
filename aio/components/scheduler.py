import heapq
import itertools
from collections import deque
from typing import Callable, Generic, Iterable, Optional, TypeVar, Union

from aio.interfaces import Handle

T = TypeVar('T')
P = TypeVar('P')


class MinPriority:
    pass


MIN_PRIORITY_SENTINEL = MinPriority()


class PriorityQueue(Generic[T, P]):
    def __init__(
        self,
        priority_fn: Callable[[T], Union[P, MinPriority]],
        _min_priority: Iterable[T] = (),
        _prioritized: Iterable[T] = (),
    ):
        self._priority_fn = priority_fn
        self._ids_generator = iter(itertools.count())
        self._min_priority = deque(_min_priority)
        self._prioritized = [self._wrap_item(item) for item in _prioritized]
        heapq.heapify(self._prioritized)

    def _wrap_item(self, item: T) -> tuple[P, int, T]:
        priority = self._priority_fn(item)
        if priority is MIN_PRIORITY_SENTINEL:
            raise ValueError(
                'Could not wrap item for priority queue '
                'due to it is have maximum priority'
            )
        return priority, next(self._ids_generator), item

    def enqueue(self, item: T) -> None:
        priority = self._priority_fn(item)
        if priority is MIN_PRIORITY_SENTINEL:
            self._min_priority.append(item)
        else:
            heapq.heappush(self._prioritized, self._wrap_item(item))

    def pop_below_priority(self, threshold: P) -> list[T]:
        old_max_priority, self._min_priority = self._min_priority, deque()
        return list(old_max_priority) + self._pop_prioritized_low(threshold)

    def get_items(self) -> list[T]:
        return list(self._min_priority) + [item for _, _, item in self._prioritized]

    def _pop_prioritized_low(self, threshold: P) -> list[T]:
        new_ready: list[T] = []
        while self._prioritized:
            priority, _, item = self._prioritized[0]
            if priority <= threshold:
                new_ready.append(item)
                heapq.heappop(self._prioritized)
            else:
                break
        return new_ready


def _handle_priority_getter(handle: Handle) -> Union[float, MinPriority]:
    if handle.when is None:
        return MIN_PRIORITY_SENTINEL
    return handle.when


class Scheduler(PriorityQueue[Handle, float]):
    def __init__(self, pending: Iterable[Handle] = (), enqueued: Iterable[Handle] = ()):
        if any(handle.when is not None for handle in pending):
            raise ValueError(
                'Scheduler receives pending handles and '
                'some of them have `when` attr which is not None'
            )
        super().__init__(_handle_priority_getter, pending, enqueued)

    def pop_pending(self, time_threshold: float) -> list[Handle]:
        return [
            handle
            for handle in self.pop_below_priority(time_threshold)
            if not handle.cancelled
        ]

    def next_event(self) -> Optional[float]:
        for _, _, handle in self._prioritized:
            assert handle.when is not None
            if not handle.cancelled:
                return handle.when

        return None
