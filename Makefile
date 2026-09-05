# Всё гоняется через `uv run`, чтобы окружение совпадало с локом.
# `check` — локальное зеркало CI-гейта.
.PHONY: sync run lint format typecheck test check app app-test \
        install install-tool install-app stop-app start-app pull update

# Порядок предпосылок install-app — часть контракта (сначала погасить приложение,
# потом ставить), а при -j make его не соблюдает.
.NOTPARALLEL:

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

# Гашение вынесено в отдельную цель и стоит первой предпосылкой, потому что
# гасить надо до install-tool, а не после: `uv tool install --force` переписывает
# venv, из которого прямо сейчас работает бот. Пока pkill сидел в рецепте
# install-app, предпосылка install-tool успевала отработать под живым процессом.
#
# Шаблон заякорён так же, как в BotSupervisor.swift (foreignBotPID): голое
# 'ClaudeRCMenu' ловит любой процесс, чья командная строка просто содержит
# это слово — на этом классе дефекта уже теряли время. `/ClaudeRCMenu$`
# требует, чтобы командная строка заканчивалась ровно на имя исполняемого
# файла бандла, а не на произвольный текст с этой подстрокой. Держится на
# допущении, что приложение стартует без аргументов (сейчас так и у Finder,
# и у SMAppService, и у запасного LaunchAgent) — если когда-нибудь появится
# аргумент запуска, этот `pgrep` перестанет находить процесс молча, и `cp`
# ляжет под живой процесс.
stop-app:
	@if pgrep -f '/ClaudeRCMenu$$' >/dev/null; then \
		echo "Гашу запущенное приложение: иначе установка идёт под работающим процессом."; \
		pkill -f '/ClaudeRCMenu$$'; sleep 2; \
	fi

start-app:
	open -a /Applications/ClaudeRC.app

install-app: stop-app install-tool app
	rm -rf /Applications/ClaudeRC.app
	cp -R app/build/ClaudeRC.app /Applications/ClaudeRC.app
	@echo "Готово: /Applications/ClaudeRC.app"

install: install-app

# Обновление установленной копии из origin. Грязное дерево — отказ, а не
# `git stash` за человека: спрятать чужие правки молча значит их потерять.
# Поставить локальный, ещё не закоммиченный код — это `make install`, не `update`.
pull:
	@git diff --quiet HEAD || { \
		echo "Рабочее дерево грязное — обновление отменено."; \
		echo "Закоммить или спрячь правки, либо поставь текущий код через make install."; \
		exit 1; \
	}
	git pull --rebase

# Одна команда на «обновиться»: подтянуть, прогнать гейт, переустановить, поднять
# приложение обратно. Гейт здесь не формальность — install-app копирует бандл в
# /Applications, и ставить туда красную сборку незачем.
#
# tmux-сессии (`rc-*`) всё это переживают: они живут в своём сервере, а ссылки бот
# после старта достаёт заново из опции tmux @rc_url. Гасится только процесс бота,
# и то на время установки.
update: pull check install-app start-app
	@echo "Обновлено до $$(git rev-parse --short HEAD). Живые сессии не тронуты."
