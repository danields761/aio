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


aio.run_loop(main())
