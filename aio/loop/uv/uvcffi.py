# -*- coding: utf-8 -*-

# Copyright (C) 2016, Maximilian Köhl <mail@koehlma.de>
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License version 3 as published by
# the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License along
# with this program. If not, see <http://www.gnu.org/licenses/>.

__project__ = "Python libuv CFFI Bindings"
__author__ = "Maximilian Köhl"
__email__ = "mail@koehlma.de"

import importlib.resources
import os
import socket
from typing import Callable, ParamSpec

import cffi

declarations = importlib.resources.read_text(__package__, "cffi_declarations.c")
source = importlib.resources.read_text(__package__, "cffi_source.c")

try:
    from aio.loop.uv._uvcffi import ffi, lib
except ImportError:
    ffi = cffi.FFI()
    ffi.cdef(declarations)
    try:
        ffi.set_source(f"{__package__}._uvcffi", source, libraries=["uv"])
        ffi.compile()
        from aio.loop.uv._uvcffi import ffi, lib
    except AttributeError or ImportError:
        lib = ffi.verify(source, modulename=f"{__package__}._uvcffi", libraries=["uv"])


CPS = ParamSpec("CPS")

UV_TO_STD = {
    lib.UV_EAI_ADDRFAMILY: socket.EAI_ADDRFAMILY,
    lib.UV_EAI_AGAIN: socket.EAI_AGAIN,
    lib.UV_EAI_BADFLAGS: socket.EAI_BADFLAGS,
    lib.UV_EAI_FAIL: socket.EAI_FAIL,
    lib.UV_EAI_FAMILY: socket.EAI_FAMILY,
    lib.UV_EAI_NODATA: socket.EAI_NODATA,
    lib.UV_EAI_NONAME: socket.EAI_NONAME,
    lib.UV_EAI_SERVICE: socket.EAI_SERVICE,
    lib.UV_EAI_SOCKTYPE: socket.EAI_SOCKTYPE,
}


class UVError(OSError):
    pass


def error_for_code(uv_code: int) -> Exception:
    if uv_code == lib.UV_EAI_MEMORY:
        return MemoryError()

    try:
        stdcode = UV_TO_STD[uv_code]
    except KeyError:
        pass
    else:
        err_ptr = lib.gai_strerror(stdcode)
        err_str = ffi.string(err_ptr).decode("ascii")
        return OSError(stdcode, err_str)

    err_str = os.strerror(-uv_code)
    if "Unknown" not in err_str:
        return OSError(-uv_code, err_str)

    err_ptr = lib.uv_strerror(uv_code)
    err_str = ffi.string(err_ptr).decode("ascii")
    if err_ptr != ffi.NULL:
        return UVError(uv_code, err_str)

    return OSError(uv_code, os.strerror(uv_code))


def invoke_uv_fn(fn: Callable[CPS, int], *args: CPS.args, **kwargs: CPS.kwargs) -> int:
    res = fn(*args, **kwargs)
    if res >= 0:
        return res
    raise error_for_code(res)


@ffi.callback("uv_walk_cb")
def _walk_cb(uv_handle_ptr, arg):
    data = ffi.from_handle(uv_handle_ptr.data)
    res = ffi.from_handle(arg)
    res.append(data)


def collect_loop_handles(c_loop: object) -> list[object]:
    res = []
    res_ptr = ffi.new_handle(res)
    lib.uv_walk(c_loop, _walk_cb, res_ptr)
    return res
