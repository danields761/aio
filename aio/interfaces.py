from __future__ import annotations

import dataclasses
import socket
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncContextManager,
    Callable,
    ContextManager,
    Coroutine,
    Mapping,
    ParamSpec,
    Protocol,
    TypeVar,
)

if TYPE_CHECKING:
    from aio.future import Future

T = TypeVar("T")
CPS = ParamSpec("CPS")
CallbackType = Callable[..., None]


class Clock(Protocol):
    def now(self) -> float:
        raise NotImplementedError

    def resolution(self) -> float:
        raise NotImplementedError


class IOEventCallback(Protocol):
    def __call__(self, fd: int, events: int, /) -> None:
        raise NotImplementedError


class IOSelector(Protocol):
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


class LoopFactory(Protocol):
    def __call__(self, **kwargs: Any) -> ContextManager[EventLoop]:
        raise NotImplementedError


class EventLoop(Protocol):
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

    def create_networking(self) -> AsyncContextManager[Networking]:
        raise NotImplementedError

    def create_executor(self) -> AsyncContextManager[Executor]:
        raise NotImplementedError

    def run(self, coroutine: Coroutine[Future[Any], None, T]) -> T:
        raise NotImplementedError


class Executor(Protocol):
    async def execute_sync_callable(
        self, fn: Callable[CPS, T], /, *args: CPS.args, **kwargs: CPS.kwargs
    ) -> T:
        raise NotImplementedError


#: Socket address type, which could be passed into `socket.connect` and
#: returned from `socket.accept` methods. Not well defined in std library,
#: so and we do so.
SocketAddress = str | bytes | tuple[Any, ...]


class Networking(Protocol):
    async def wait_sock_ready_to_read(self, sock: socket.socket) -> None:
        """

        :param sock:
        :return:
        :raises SocketMustBeNonBlockingError: if given socket not in non-blocking mode
        """
        raise NotImplementedError

    async def wait_sock_ready_to_write(self, sock: socket.socket) -> None:
        """

        :param sock:
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

    async def sock_read(self, sock: socket.socket, amount: int) -> bytes:
        """

        :param sock:
        :param amount:
        :return:
        :raises SocketMustBeNonBlockingError: if given socket not in non-blocking mode
        """
        raise NotImplementedError

    async def sock_write(self, sock: socket.socket, data: bytes) -> None:
        """

        :param sock:
        :param data:
        :return:
        :raises SocketMustBeNonBlockingError: if given socket not in non-blocking mode
        """
        raise NotImplementedError
