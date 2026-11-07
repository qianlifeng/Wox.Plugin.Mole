.PHONY: help install lint format test build publish

help:
	@echo "make install | lint | format | test | build"

install:
	uv sync --all-extras

lint:
	uv run ruff check src tests scripts
	uv run mypy src

format:
	uv run ruff format src tests scripts

test:
	uv run python -m unittest

build: lint test
	uv run python scripts/package.py

# Compatibility alias: this only creates a local archive; it does not upload.
package: build
