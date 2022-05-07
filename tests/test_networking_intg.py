import contextlib
import multiprocessing
import os
import signal
import socket
import time

import pytest

import aio.loop
from tests.utils import socketpair


@contextlib.contextmanager
def process(target, *args):
    def wrap(*args_):
        try:
            return target(*args_)
        except KeyboardInterrupt:
            pass

    proc_ = multiprocessing.Process(target=target, args=args)
    proc_.start()
    try:
        yield proc_
    finally:
        assert proc_.pid is not None
        os.kill(proc_.pid, signal.SIGINT)
        try:
            proc_.join(1)
        except multiprocessing.TimeoutError:
            proc_.terminate()
            proc_.join()
        else:
            assert proc_.exitcode == 1


def _echo(sock):
    with sock:
        while True:
            read = sock.recv(1024)
            written = 0
            while written != len(read):
                written += sock.send(read[written:])


def _serve_echo_from_pair(left, right):
    left.close()

    with right:
        _echo(right)


def _serve_echo(sock):
    with sock:
        sock.listen()
        peer, _ = sock.accept()
        with peer:
            _echo(peer)


def _echo_client(addr):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        for attempt in range(4):
            try:
                client.connect(addr)
                break
            except ConnectionRefusedError:
                time.sleep(0.1)

        _echo(client)


@pytest.fixture
def echo_socket():
    left, right = socketpair(socket.AF_INET, socket.SOCK_STREAM)

    with process(_serve_echo_from_pair, left, right):
        right.close()
        left.setblocking(False)
        yield left
        left.close()


@pytest.fixture
def echo_server_addr():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("0.0.0.0", 0))
    addr = server.getsockname()

    with process(_serve_echo, server):
        server.close()
        yield addr


async def do_test_send_recv(networking, peer):
    for i in range(1000):
        await networking.sock_write_all(peer, f"SOME TEST DATA {i}".encode("latin1"))
        response = await networking.sock_read(peer, 1024)
        assert (i, response) != (i, b""), "The connection should not be closed at this point"
        assert (i, response.decode("latin1")) == (i, f"SOME TEST DATA {i}")


async def test_send_receive(echo_socket):
    async with aio.loop.networking() as networking:
        await do_test_send_recv(networking, echo_socket)


async def test_connects(echo_server_addr):
    async with aio.loop.networking() as networking:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as peer:
            peer.setblocking(False)
            await networking.sock_connect(peer, echo_server_addr)

            await do_test_send_recv(networking, peer)


async def test_accept():
    async with aio.loop.networking() as networking:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setblocking(False)
            server.bind(("0.0.0.0", 0))
            server.listen()

            with process(_echo_client, server.getsockname()):
                peer, _ = await networking.sock_accept(server)
                with peer:
                    await do_test_send_recv(networking, peer)
