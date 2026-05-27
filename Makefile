.PHONY: install dev test lint fmt init backtest dashboard clean

install:
	uv sync

dev:
	uv sync --extra dev

test:
	uv run pytest

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests

init:
	uv run tt init-db

backtest:
	uv run tt backtest --days 365

dashboard:
	uv run tt dashboard

clean:
	rm -rf data/cache data/ledger.db .pytest_cache .ruff_cache
