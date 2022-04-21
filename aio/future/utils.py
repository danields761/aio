from __future__ import annotations

from aio.exceptions import Cancelled


def coerce_cancel_arg(exc: str | Cancelled | None) -> Cancelled:
    match exc:
        case str():
            return Cancelled(exc)
        case None:
            return Cancelled()
        case _:
            return exc
