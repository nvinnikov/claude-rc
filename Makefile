# Всё гоняется через `uv run`, чтобы окружение совпадало с локом.
# `check` — локальное зеркало CI-гейта.
.PHONY: install run lint format typecheck test check

install:
	uv sync --all-groups

# Цели зависят от install, чтобы на свежем клоне `make test` работал без
# отдельного шага: сам по себе `uv run` тянет только основные зависимости,
# и ruff/mypy/pytest не нашлись бы. Повторный `uv sync` — быстрый no-op,
# а make выполняет зависимость один раз за вызов.
run: install
	uv run claude-rc bot

lint: install
	uv run ruff format --check .
	uv run ruff check .

format: install
	uv run ruff format .
	uv run ruff check --fix .

typecheck: install
	uv run mypy

test: install
	uv run pytest

check: lint typecheck test
