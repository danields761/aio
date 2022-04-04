from typing import Any, AsyncContextManager, AsyncIterator, TypeVar, Union, overload

from aio import Future

T1 = TypeVar("T1")
T2 = TypeVar("T2")
T3 = TypeVar("T3")
T4 = TypeVar("T4")
T5 = TypeVar("T5")
T6 = TypeVar("T6")
T7 = TypeVar("T7")
T8 = TypeVar("T8")
T9 = TypeVar("T9")

@overload
def iter_done_futures(f1: Future[T1]) -> AsyncContextManager[AsyncIterator[Future[T1]]]: ...
@overload
def iter_done_futures(
    f1: Future[T1], f2: Future[T2]
) -> AsyncContextManager[AsyncIterator[Future[Union[T1, T2]]]]: ...
@overload
def iter_done_futures(
    f1: Future[T1], f2: Future[T2], f3: Future[T3]
) -> AsyncContextManager[AsyncIterator[Future[Union[T1, T2, T3]]]]: ...
@overload
def iter_done_futures(
    f1: Future[T1], f2: Future[T2], f3: Future[T3], f4: Future[T4]
) -> AsyncContextManager[AsyncIterator[Future[Union[T1, T2, T3, T4]]]]: ...
@overload
def iter_done_futures(
    f1: Future[T1],
    f2: Future[T2],
    f3: Future[T3],
    f4: Future[T4],
    f5: Future[T5],
) -> AsyncContextManager[AsyncIterator[Future[Union[T1, T2, T3, T4, T5]]]]: ...
@overload
def iter_done_futures(
    f1: Future[T1],
    f2: Future[T2],
    f3: Future[T3],
    f4: Future[T4],
    f5: Future[T5],
    f6: Future[T6],
) -> AsyncContextManager[AsyncIterator[Future[Union[T1, T2, T3, T4, T5, T6]]]]: ...
@overload
def iter_done_futures(
    f1: Future[T1],
    f2: Future[T2],
    f3: Future[T3],
    f4: Future[T4],
    f5: Future[T5],
    f6: Future[T6],
    f7: Future[T7],
) -> AsyncContextManager[AsyncIterator[Future[Union[T1, T2, T3, T4, T5, T6, T7]]]]: ...
@overload
def iter_done_futures(
    f1: Future[T1],
    f2: Future[T2],
    f3: Future[T3],
    f4: Future[T4],
    f5: Future[T5],
    f6: Future[T6],
    f7: Future[T7],
    f8: Future[T8],
) -> AsyncContextManager[AsyncIterator[Future[Union[T1, T2, T3, T4, T5, T6, T7, T8]]]]: ...
@overload
def iter_done_futures(
    f1: Future[T1],
    f2: Future[T2],
    f3: Future[T3],
    f4: Future[T4],
    f5: Future[T5],
    f6: Future[T6],
    f7: Future[T7],
    f8: Future[T8],
    f9: Future[T9],
) -> AsyncContextManager[AsyncIterator[Future[Union[T1, T2, T3, T4, T5, T6, T7, T8, T9]]]]: ...
@overload
def iter_done_futures(
    *futures: Future[Any],
) -> AsyncContextManager[AsyncIterator[Future[Any]]]: ...
