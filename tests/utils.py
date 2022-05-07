import errno
import socket
from typing import Coroutine
from unittest.mock import Mock


def mock_wraps(cb, **mock_kwargs):
    if cb is not None:
        return Mock(wraps=cb, **mock_kwargs)
    else:
        return lambda cb: Mock(wraps=cb, **mock_kwargs)


def finalize_coro(coro_inst: Coroutine):
    async def wrapper():
        try:
            await coro_inst
        except Exception:
            pass

    w = wrapper()
    try:
        while True:
            w.send(None)
    except StopIteration:
        pass


def socketpair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
    try:
        return socket.socketpair(family, type, proto)
    except OSError as exc:
        if exc.errno != errno.EOPNOTSUPP:
            raise

    if family == socket.AF_INET:
        host = socket._LOCALHOST
    elif family == socket.AF_INET6:
        host = socket._LOCALHOST_V6
    else:
        raise ValueError("Only AF_INET and AF_INET6 socket address families " "are supported")
    if type != socket.SOCK_STREAM:
        raise ValueError("Only SOCK_STREAM socket type is supported")
    if proto != 0:
        raise ValueError("Only protocol zero is supported")

    # We create a connected TCP socket. Note the trick with
    # setblocking(False) that prevents us from having to create a thread.
    lsock = socket.socket(family, type, proto)
    try:
        lsock.bind((host, 0))
        lsock.listen()
        # On IPv6, ignore flow_info and scope_id
        addr, port = lsock.getsockname()[:2]
        csock = socket.socket(family, type, proto)
        try:
            csock.setblocking(False)
            try:
                csock.connect((addr, port))
            except (BlockingIOError, InterruptedError):
                pass
            csock.setblocking(True)
            ssock, _ = lsock.accept()
        except BaseException:
            csock.close()
            raise
    finally:
        lsock.close()
    return ssock, csock
