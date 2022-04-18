import aio


async def provider(q: aio.channel.Left[int]) -> None:
    for i in range(10):
        await q.put(i)
        await aio.sleep(0.1)
    q.close()
    print("provider done")


async def consumer(q: aio.channel.Right[int]) -> None:
    try:
        async for elem in q:
            print("consumed", elem)
    finally:
        print("consumer done")


async def main() -> None:
    async with aio.channel.create(max_capacity=1) as (left, right):
        async with aio.create_task(provider(left), label="provider"), aio.create_task(
            consumer(right), label="consumer"
        ):
            pass

    print("main done")


aio.run(main())
