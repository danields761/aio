import socket

import aio


async def client(host, port):
    try:
        loop = await aio.get_loop()
        async with loop.create_networking() as networking:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setblocking(False)
                while True:
                    try:
                        await networking.sock_connect(s, (host, port))
                        print(f"Connection to {host}:{port} established")
                        break
                    except ConnectionRefusedError:
                        print(
                            f'Unable to connect to "{host}:{port}", waiting 1 sec...'
                        )
                        await aio.sleep(1)

                while True:
                    data = await networking.sock_read(s, 1024)
                    print("Received", data)
                    await networking.sock_write(
                        s, b"Echoed back: " + data + b"\n"
                    )
    finally:
        print("Client has stopped")


aio.run_loop(client("127.0.0.1", 5000))
