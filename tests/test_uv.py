import errno
import socket

import pytest

from aio.loop.uv.uvcffi import UVError, error_for_code, lib


@pytest.mark.parametrize(
    "uv_code, exp_cls, exp_errno, exc_strerror",
    [
        # Standard errors
        (
            lib.UV_EACCES,
            PermissionError,
            errno.EACCES,
            "Permission denied",
        ),
        (
            lib.UV_EINTR,
            InterruptedError,
            errno.EINTR,
            "Interrupted system call",
        ),
        (
            lib.UV_ETIMEDOUT,
            TimeoutError,
            errno.ETIMEDOUT,
            "Connection timed out",
        ),
        # Socket errors
        (
            lib.UV_EAI_ADDRFAMILY,
            OSError,
            socket.EAI_ADDRFAMILY,
            "Address family for hostname not supported",
        ),
        (lib.UV_EAI_AGAIN, OSError, socket.EAI_AGAIN, "Temporary failure in name resolution"),
        (lib.UV_EAI_BADFLAGS, OSError, socket.EAI_BADFLAGS, "Bad value for ai_flags"),
        (lib.UV_EAI_FAIL, OSError, socket.EAI_FAIL, "Non-recoverable failure in name resolution"),
        (lib.UV_EAI_FAMILY, OSError, socket.EAI_FAMILY, "ai_family not supported"),
        (lib.UV_EAI_NODATA, OSError, socket.EAI_NODATA, "No address associated with hostname"),
        (lib.UV_EAI_NONAME, OSError, socket.EAI_NONAME, "Name or service not known"),
        (lib.UV_EAI_SERVICE, OSError, socket.EAI_SERVICE, "Servname not supported for ai_socktype"),
        (lib.UV_EAI_SOCKTYPE, OSError, socket.EAI_SOCKTYPE, "ai_socktype not supported"),
        # Some unknown errors
        (lib.UV_EAI_OVERFLOW, UVError, lib.UV_EAI_OVERFLOW, "argument buffer overflow"),
    ],
)
def test_uv_errors(uv_code, exp_cls, exp_errno, exc_strerror):
    exc = error_for_code(uv_code)
    assert type(exc) is exp_cls
    assert (exc.errno, exc.strerror) == (exp_errno, exc_strerror)
