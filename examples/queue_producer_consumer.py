import sys

import aio


async def provider(q: aio.Queue) -> None:
    for i in range(10):
        await q.put(i)
        await aio.sleep(0.1)
    q.close()
    print('provider done')


async def consumer(q: aio.Queue) -> None:
    async for elem in q:
        print('consumed', elem)
    print('consumer done')


async def main() -> None:
    q = aio.Queue(max_capacity=1)
    async with aio.task_group() as tg:
        tg.spawn(provider(q))
        tg.spawn(consumer(q))

    print('main done')


async def main2() -> None:
    async def gen():
        yield 1
        yield 2

    async for _ in gen():
        break

    await aio.sleep(10)


def process_cb_error(msg, exc, context=None):
    import traceback

    print(msg, context, file=sys.stderr)
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    raise SystemExit(100)


aio.run_loop(main2(), exception_handler=process_cb_error)
