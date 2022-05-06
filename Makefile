SERVICE_NAME := aio


PYTHON ?= .venv/bin/python
PDM ?= pdm

mkfile_path := $(abspath $(lastword $(MAKEFILE_LIST)))
current_dir := $(notdir $(patsubst %/,%,$(dir $(mkfile_path))))

setup:
	$(PDM) plugin add pdm-venv
	$(PDM) config venv.in_project true
	$(PDM) venv create
	$(PDM) use -f
	$(PDM) install

lint:
	-$(PDM) run mypy $(SERVICE_NAME)/
	-$(PDM) run flake8 $(SERVICE_NAME)/ tests/

format:
	-$(PDM) run python scripts/gen_all.py aio/__init__.py
	-$(PDM) run python scripts/gen_all.py aio/loop/__init__.py
	-$(PDM) run python scripts/gen_all.py aio/loop/pure/__init__.py
	-$(PDM) run black $(SERVICE_NAME)/ tests/
	-$(PDM) run isort $(SERVICE_NAME)/ tests/

.PHONY: build
build:
	clang -pthread -Wno-unused-result -Wsign-compare -g -Og -Wall -O0 -fPIC -I/home/danields/workspace/aio-test/.venv/include -I/home/danields/.pyenv/versions/3.10.4-debug/include/python3.10d -c aio/future/cimpl.c -o build/temp.linux-x86_64-3.10-pydebug/aio/future/cimpl.o
	clang -pthread -shared -L/home/danields/.pyenv/versions/3.10.4-debug/lib -L/home/danields/.pyenv/versions/3.10.4-debug/lib build/temp.linux-x86_64-3.10-pydebug/aio/future/cimpl.o -o aio/future/_cimpl.cpython-310d-x86_64-linux-gnu.so


test:
	$(PDM) run pytest tests -vv
