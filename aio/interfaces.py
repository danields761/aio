from __future__ import annotations

import abc
import dataclasses
import socket
from enum import IntEnum
from typing import (
    Any,
    AsyncContextManager,
    Callable,
    ContextManager,
    Generator,
    Generic,
    Literal,
    Mapping,
    NoReturn,
    ParamSpec,
    Protocol,
    TypeVar,
)

from aio import Cancelled
from aio.types import HasFileno

T = TypeVar("T")
CPS = ParamSpec("CPS")
CallbackType = Callable[..., None]


class Clock(abc.ABC):
    def now(self) -> float:
        raise NotImplementedError

    def resolution(self) -> float:
        raise NotImplementedError


class IOEventCallback(Protocol):
    def __call__(self, fd: int, events: int, /) -> None:
        raise NotImplementedError


class IOSelector(abc.ABC):
    def select(self, time_: float | None) -> list[tuple[IOEventCallback, int, int]]:
        raise NotImplementedError

    def add_watch(self, fd: int, events: int, cb: IOEventCallback) -> None:
        raise NotImplementedError

    def stop_watch(self, fd: int, events: int | None, cb: IOEventCallback | None) -> None:
        raise NotImplementedError

    def wakeup_thread_safe(self) -> None:
        raise NotImplementedError


class UnhandledExceptionHandler(Protocol):
    def __call__(self, exc: BaseException, /, **context: Any) -> None:
        raise NotImplementedError


@dataclasses.dataclass
class Handle:
    when: float | None
    callback: CallbackType
    args: tuple[Any, ...] = ()
    executed: bool = False
    cancelled: bool = False
    context: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def cancel(self) -> None:
        self.cancelled = True


LoopT = TypeVar("LoopT", bound="EventLoop")


class LoopPolicy(abc.ABC, Generic[LoopT]):
    @abc.abstractmethod
    def create_loop(self, **kwargs: Any) -> ContextManager[LoopT]:
        raise NotImplementedError

    @abc.abstractmethod
    def create_loop_runner(self, loop: LoopT) -> LoopRunner[LoopT]:
        raise NotImplementedError

    @abc.abstractmethod
    def create_networking(self) -> AsyncContextManager[Networking]:
        raise NotImplementedError

    @abc.abstractmethod
    def create_executor(self) -> AsyncContextManager[Executor]:
        raise NotImplementedError


class LoopStopped(Exception):
    pass


LoopT_cov = TypeVar("LoopT_cov", covariant=True, bound="EventLoop")


class LoopRunner(abc.ABC, Generic[LoopT_cov]):
    def get_loop(self) -> LoopT_cov:
        raise NotImplementedError

    def run_loop(self) -> NoReturn:
        raise NotImplementedError

    def stop_loop(self) -> None:
        raise NotImplementedError


class EventLoop(abc.ABC):
    def call_soon(
        self,
        target: Callable[CPS, None],
        *args: CPS.args,
        context: Mapping[str, Any] | None = None,
    ) -> Handle:
        raise NotImplementedError

    def call_later(
        self,
        timeout: float,
        target: Callable[CPS, None],
        *args: CPS.args,
        context: Mapping[str, Any] | None = None,
    ) -> Handle:
        raise NotImplementedError

    def call_soon_thread_safe(
        self,
        target: Callable[CPS, None],
        *args: CPS.args,
        context: Mapping[str, Any] | None = None,
    ) -> Handle:
        return self.call_soon(target, *args, context=context)

    def call_later_thread_safe(
        self,
        timeout: float,
        target: Callable[CPS, None],
        *args: CPS.args,
        context: Mapping[str, Any] | None = None,
    ) -> Handle:
        return self.call_later(timeout, target, *args, context=context)

    @property
    def clock(self) -> Clock:
        raise NotImplementedError


class FutureResultCallback(Protocol[T]):
    def __call__(self, fut: Future[T], /) -> None:
        raise NotImplementedError


class Promise(abc.ABC, Generic[T]):
    @abc.abstractmethod
    def set_result(self, val: T) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def set_exception(self, exc: BaseException, /) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def cancel(self, msg: Cancelled | str | None = None, /) -> None:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def future(self) -> Future[T]:
        raise NotImplementedError


class Future(abc.ABC, Generic[T]):
    class State(IntEnum):
        created = 0
        scheduled = 1
        running = 2
        finished = 4

    @property
    @abc.abstractmethod
    def state(self) -> Future.State:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def loop(self) -> EventLoop:
        raise NotImplementedError

    @abc.abstractmethod
    def result(self) -> T:
        raise NotImplementedError

    @abc.abstractmethod
    def exception(self) -> BaseException | None:
        raise NotImplementedError

    @abc.abstractmethod
    def add_callback(self, cb: FutureResultCallback[T]) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def remove_callback(self, cb: FutureResultCallback[T]) -> None:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def is_finished(self) -> bool:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def is_cancelled(self) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    def __await__(self) -> Generator[Future[Any], None, T]:
        raise NotImplementedError


class Task(Future[T], abc.ABC, Generic[T]):
    @abc.abstractmethod
    def cancel(self, exc: str | Cancelled | None = None) -> None:
        raise NotImplementedError


class Executor(Protocol):
    async def execute_sync_callable(
        self, fn: Callable[CPS, T], /, *args: CPS.args, **kwargs: CPS.kwargs
    ) -> T:
        raise NotImplementedError


#: Any socket like object, either FD, or file-like object or socket
SocketLike = int | HasFileno | socket.socket


#: Socket address type, which could be passed into `socket.connect` and
#: returned from `socket.accept` methods. Not well defined in std library,
#: so and we do so.
SocketAddress = str | bytes | tuple[Any, ...]


class Networking(abc.ABC):
    async def wait_sock_event(
        self, sock: SocketLike, *events: Literal["read", "write"], label: str | None = None
    ) -> None:
        """

        :param sock:
        :param events:
        :param label:
        :return:
        :raises SocketMustBeNonBlockingError: if given socket not in non-blocking mode
        """
        raise NotImplementedError

    async def sock_connect(self, sock: socket.socket, addr: SocketAddress) -> None:
        """

        :param sock:
        :param addr:
        :return:
        :raises SocketMustBeNonBlockingError: if given socket not in non-blocking mode
        """
        raise NotImplementedError

    async def sock_accept(self, sock: socket.socket) -> tuple[socket.socket, SocketAddress]:
        """

        :param sock:
        :return:
        :raises SocketMustBeNonBlockingError: if given socket not in non-blocking mode
        """
        raise NotImplementedError

    async def sock_read(self, sock: SocketLike, amount: int) -> bytes:
        """

        :param sock:
        :param amount:
        :return:
        :raises SocketMustBeNonBlockingError: if given socket not in non-blocking mode
        """
        raise NotImplementedError

    async def sock_write(self, sock: SocketLike, data: bytes) -> int:
        """

        :param sock:
        :param data:
        :return: written bytes before block
        :raises SocketMustBeNonBlockingError: if given socket not in non-blocking mode
        """
        raise NotImplementedError

    async def sock_write_all(self, sock: SocketLike, data: bytes) -> None:
        """

        :param sock:
        :param data:
        :return:
        :raises SocketMustBeNonBlockingError: if given socket not in non-blocking mode
        """
        raise NotImplementedError
