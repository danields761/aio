from types import TracebackType
from typing import (
    Any,
    Awaitable,
    Mapping,
    Optional,
    Protocol,
    Type,
    TypeVar,
    Union,
)

from aio import Future

T = TypeVar("T")

class Coroutine(Awaitable[T], Protocol[T]):
    @property
    def cr_running(self) -> bool: ...
    @property
    def cr_frame(self) -> Any: ...
    def send(self, value: None) -> Future[Any]: ...
    def throw(
        self,
        typ: Type[BaseException],
        val: Union[BaseException, object, None] = None,
        tb: Optional[TracebackType] = None,
    ) -> Future[Any]: ...
    def close(self) -> None: ...

class HasFileno(Protocol):
    def fileno(self) -> int: ...

FileDescriptorLike = Union[HasFileno, int]

class STDSelectorKey(Protocol[T]):
    fileobj: FileDescriptorLike
    fd: int
    events: int
    data: T

class STDSelector(Protocol[T]):
    """
    Fully mirrors `selectors.BaseSelector` interface, but make it more precise about
    selector key `data` filed type.
    """

    def register(
        self, fileobj: FileDescriptorLike, events: int, data: T = ...
    ) -> STDSelectorKey[T]: ...
    def unregister(self, fileobj: FileDescriptorLike) -> STDSelectorKey[T]: ...
    def modify(
        self, fileobj: FileDescriptorLike, events: int, data: T = ...
    ) -> STDSelectorKey[T]: ...
    def select(
        self, timeout: Optional[float] = ...
    ) -> list[tuple[STDSelectorKey[T], int]]: ...
    def close(self) -> None: ...
    def get_key(self, fileobj: FileDescriptorLike) -> STDSelectorKey[T]: ...
    def get_map(self) -> Mapping[FileDescriptorLike, STDSelectorKey[T]]: ...
    def __enter__(self) -> STDSelector[T]: ...
    def __exit__(self, *args: Any) -> None: ...

LoggerExcInfo = Union[None, BaseException, bool]

class Logger(Protocol):
    def debug(
        self, msg: str, exc_info: LoggerExcInfo = None, **params: Any
    ) -> None: ...
    def info(
        self, msg: str, exc_info: LoggerExcInfo = None, **params: Any
    ) -> None: ...
    def warning(
        self, msg: str, exc_info: LoggerExcInfo = None, **params: Any
    ) -> None: ...
    def error(
        self, msg: str, exc_info: LoggerExcInfo = None, **params: Any
    ) -> None: ...
    def bind(self, **params: Any) -> Logger: ...
