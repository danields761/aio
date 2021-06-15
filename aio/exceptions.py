from typing import Any, Optional


class NetworkingError(Exception):
    pass


class SocketConfigurationError(NetworkingError):
    pass


class FutureError(Exception):
    pass


class FutureNotReady(FutureError):
    pass


class FutureFinishedError(FutureError):
    pass


class Cancelled(Exception):
    def __init__(self, msg: Optional[str] = None):
        self.msg = msg

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, type(self)) and self.args == other.args


class MultiError(Exception):
    def __new__(cls, msg: str, *children: Exception):
        if any(isinstance(child, Cancelled) for child in children):
            return CancelMultiError(msg, *children)
        else:
            return object.__new__(MultiError, msg, *children)

    def __init__(self, msg: str, *children: Exception):
        self.children = children
        self.msg = msg

    def __repr__(self) -> str:
        fl = f'{type(self).__name__}{f": {self.msg}" if self.msg else ""}'
        lines = tuple(
            f'\t{idx}: {exc!r}'
            for idx, exc in enumerate(self.children, start=1)
        )
        return '\n'.join((fl,) + lines)


class CancelMultiError(MultiError, Cancelled):
    def __new__(cls, msg: str, *children: Exception):
        return object.__new__(cls, msg, *children)

    def __init__(self, msg: str, *children: Exception):
        if not any(isinstance(child, Cancelled) for child in children):
            raise ValueError(
                f'`{self.__name__}` could only be created from child branch exceptions'
                f'where at least one of them is cancellation error'
            )

        MultiError.__init__(self, msg, *children)
