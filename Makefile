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
	-$(PDM) run black $(SERVICE_NAME)/ tests/
	-$(PDM) run isort $(SERVICE_NAME)/ tests/

test:
	$(PDM) run pytest tests -vv
