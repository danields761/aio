from __future__ import annotations

from typing import Any


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


class AlreadyCancelling(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Task already being cancelled, but not finished")


class Cancelled(BaseException):
    def __init__(self, msg: str | None = None) -> None:
        if msg:
            super().__init__(msg)
        else:
            super().__init__()
        self.msg = msg

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, type(self)) and self.args == other.args


class KeyboardCancelled(Cancelled, KeyboardInterrupt):
    pass


class CancelledByChild(Cancelled):
    pass


class CancelledByParent(Cancelled):
    pass


class SelfCancelForbidden(RuntimeError):
    """
    It is forbidden to cancel current task via `cancel` method,
    raise `aio.Cancelled` subclass directly instead.
    """

    def __init__(self) -> None:
        super().__init__(
            "It is forbidden to cancel current task via `cancel` method, "
            "raise `aio.Cancelled` subclass directly instead."
        )
