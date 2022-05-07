from __future__ import annotations

from typing import Any, Mapping, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class HasFileno(Protocol):
    def fileno(self) -> int:
        raise NotImplementedError


FileDescriptorLike = HasFileno | int


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

    def register(self, fileobj: FileDescriptorLike, events: int, data: T) -> STDSelectorKey[T]:
        raise NotImplementedError

    def unregister(self, fileobj: FileDescriptorLike) -> STDSelectorKey[T]:
        raise NotImplementedError

    def modify(self, fileobj: FileDescriptorLike, events: int, data: T) -> STDSelectorKey[T]:
        raise NotImplementedError

    def select(self, timeout: float | None = None) -> list[tuple[STDSelectorKey[T], int]]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def get_key(self, fileobj: FileDescriptorLike) -> STDSelectorKey[T]:
        raise NotImplementedError

    def get_map(self) -> Mapping[FileDescriptorLike, STDSelectorKey[T]]:
        raise NotImplementedError

    def __enter__(self) -> STDSelector[T]:
        raise NotImplementedError

    def __exit__(self, *args: Any) -> None:
        raise NotImplementedError


LoggerExcInfo = BaseException | bool


class Logger(Protocol):
    def trace(self, msg: str, exc_info: LoggerExcInfo | None = None, **params: Any) -> None:
        raise NotImplementedError

    def debug(self, msg: str, exc_info: LoggerExcInfo | None = None, **params: Any) -> None:
        raise NotImplementedError

    def info(self, msg: str, exc_info: LoggerExcInfo | None = None, **params: Any) -> None:
        raise NotImplementedError

    def warning(self, msg: str, exc_info: LoggerExcInfo | None = None, **params: Any) -> None:
        raise NotImplementedError

    def error(self, msg: str, exc_info: LoggerExcInfo | None = None, **params: Any) -> None:
        raise NotImplementedError

    def bind(self, **params: Any) -> Logger:
        raise NotImplementedError
