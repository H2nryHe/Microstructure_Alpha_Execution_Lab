.PHONY: install test lint smoke demo

install:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check src tests scripts

smoke:
	microalpha-smoke

demo: smoke
