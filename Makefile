# Всё гоняется через `uv run`, чтобы окружение совпадало с локом.
# `check` — локальное зеркало CI-гейта.
.PHONY: sync run lint format typecheck test check app app-test install install-tool install-app

# Раньше называлась install, но это имя понадобилось под цель для конечного
# пользователя (поставить тулзу и приложение) — семантика другая, совмещать нельзя.
sync:
	uv sync --all-groups

# Цели зависят от sync, чтобы на свежем клоне `make test` работал без
# отдельного шага: сам по себе `uv run` тянет только основные зависимости,
# и ruff/mypy/pytest не нашлись бы. Повторный `uv sync` — быстрый no-op,
# а make выполняет зависимость один раз за вызов.
run: sync
	uv run claude-rc bot

lint: sync
	uv run ruff format --check .
	uv run ruff check .

format: sync
	uv run ruff format .
	uv run ruff check --fix .

typecheck: sync
	uv run mypy

test: sync
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

# Тулза нужна и сама по себе (например, на машине без графики), приложение без
# неё бессмысленно — отсюда разделение и зависимость.
install-tool:
	uv tool install --force .

install-app: install-tool app
	@if pgrep -f 'ClaudeRCMenu' >/dev/null; then \
		echo "Гашу запущенное приложение: иначе cp положит файлы под работающим процессом."; \
		pkill -f 'ClaudeRCMenu'; sleep 2; \
	fi
	rm -rf /Applications/ClaudeRC.app
	cp -R app/build/ClaudeRC.app /Applications/ClaudeRC.app
	@echo "Готово: /Applications/ClaudeRC.app"

install: install-app
