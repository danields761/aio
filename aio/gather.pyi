from typing import Any, AsyncIterator, TypeVar, Union, overload

from aio import Future

T1 = TypeVar('T1')
T2 = TypeVar('T2')
T3 = TypeVar('T3')
T4 = TypeVar('T4')
T5 = TypeVar('T5')
T6 = TypeVar('T6')
T7 = TypeVar('T7')
T8 = TypeVar('T8')
T9 = TypeVar('T9')
@overload
async def iter_done_futures(f1: Future[T1]) -> AsyncIterator[Future[T1]]: ...
@overload
async def iter_done_futures(
    f1: Future[T1], f2: Future[T2]
) -> AsyncIterator[Future[Union[T1, T2]]]: ...
@overload
async def iter_done_futures(
    f1: Future[T1], f2: Future[T2], f3: Future[T3]
) -> AsyncIterator[Future[Union[T1, T2, T3]]]: ...
@overload
async def iter_done_futures(
    f1: Future[T1], f2: Future[T2], f3: Future[T3], f4: Future[T4]
) -> AsyncIterator[Future[Union[T1, T2, T3, T4]]]: ...
@overload
async def iter_done_futures(
    f1: Future[T1], f2: Future[T2], f3: Future[T3], f4: Future[T4], f5: Future[T5]
) -> AsyncIterator[Future[Union[T1, T2, T3, T4, T5]]]: ...
@overload
async def iter_done_futures(
    f1: Future[T1],
    f2: Future[T2],
    f3: Future[T3],
    f4: Future[T4],
    f5: Future[T5],
    f6: Future[T6],
) -> AsyncIterator[Future[Union[T1, T2, T3, T4, T5, T6]]]: ...
@overload
async def iter_done_futures(
    f1: Future[T1],
    f2: Future[T2],
    f3: Future[T3],
    f4: Future[T4],
    f5: Future[T5],
    f6: Future[T6],
    f7: Future[T7],
) -> AsyncIterator[Future[Union[T1, T2, T3, T4, T5, T6, T7]]]: ...
@overload
async def iter_done_futures(
    f1: Future[T1],
    f2: Future[T2],
    f3: Future[T3],
    f4: Future[T4],
    f5: Future[T5],
    f6: Future[T6],
    f7: Future[T7],
    f8: Future[T8],
) -> AsyncIterator[Future[Union[T1, T2, T3, T4, T5, T6, T7, T8]]]: ...
@overload
async def iter_done_futures(
    f1: Future[T1],
    f2: Future[T2],
    f3: Future[T3],
    f4: Future[T4],
    f5: Future[T5],
    f6: Future[T6],
    f7: Future[T7],
    f8: Future[T8],
    f9: Future[T9],
) -> AsyncIterator[Future[Union[T1, T2, T3, T4, T5, T6, T7, T8, T9]]]: ...
@overload
async def iter_done_futures(*futures: Future[Any]) -> AsyncIterator[Future[Any]]: ...
