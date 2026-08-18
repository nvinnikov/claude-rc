# Всё гоняется через `uv run`, чтобы окружение совпадало с локом.
# `check` — локальное зеркало CI-гейта.
.PHONY: install run lint format typecheck test check app app-test

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

app:
	./app/make-app.sh

# Testing.framework живёт в каталоге Command Line Tools, про который SPM не знает.
# Флаги добавляются, только если каталог есть, — на машине с полным Xcode он не нужен.
app-test:
	@FW="$$(xcode-select -p)/Library/Developer/Frameworks"; \
	if [ -d "$$FW" ]; then \
		swift test --package-path app \
			-Xswiftc -F"$$FW" -Xlinker -F"$$FW" -Xlinker -rpath -Xlinker "$$FW" \
			-Xswiftc -Xfrontend -Xswiftc -disable-cross-import-overlays; \
	else \
		swift test --package-path app; \
	fi
