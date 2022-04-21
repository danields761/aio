from setuptools import Extension, setup

setup(name="aio", ext_modules=[Extension("aio.future._cimpl", ["aio/future/cimpl.c"])])
