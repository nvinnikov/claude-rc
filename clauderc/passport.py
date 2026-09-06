"""Паспорт сессии: все входы в неё разом, одинаковые на всех поверхностях.

Одна структура и три рендера (текст, HTML, dict), чтобы карточка бота, вывод
CLI и `--json` для агента не расходились: раньше карточка носила только имя
tmux, CLI — команду подсадки для этой же машины, а агенту не доставалось
ничего.
"""

from __future__ import annotations

import html
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clauderc import worktrees as worktrees  # тесты подменяют passport.worktrees.inspect
from clauderc.remote import RemoteSession, attach_command
from clauderc.worktrees import Worktree

_SESSION_PREFIX = "session_"


@dataclass(frozen=True)
class Passport:
    label: str
    name: str
    host: str
    cwd: str
    branch: str
    blockers: tuple[str, ...]
    url: str
    session_id: str
    tmux_name: str
    created_at: int
    uptime_s: int
    mode: str
    attach: str
    cli: str
    state: str = ""
    last_lines: tuple[str, ...] = ()
    listening: tuple[int, ...] = field(default_factory=tuple)


def attach_line(tmux_name: str, host: str) -> str:
    """Команда подсадки: локальная, а при `host` — обёрнутая в ssh.

    `-t` обязателен: без tty tmux отвечает «open terminal failed». Внутренняя
    команда уходит одной квотированной строкой — удалённый shell разобрал бы
    её на слова заново.
    """
    local = attach_command(tmux_name)
    if not host:
        return local
    return shlex.join(["ssh", host, "-t", local])


def cli_prefix(host: str) -> str:
    return shlex.join(["claude-rc", "--host", host]) if host else "claude-rc"


def build(session: RemoteSession, *, host: str, tree: Worktree | None) -> Passport:
    tmux_name = session.tmux_name
    session_id = tmux_name if tmux_name.startswith(_SESSION_PREFIX) else ""
    return Passport(
        label=session.label or session.name,
        name=session.name,
        host=host,
        cwd=session.cwd,
        branch=tree.branch if tree is not None else "",
        blockers=tuple(tree.blockers) if tree is not None else (),
        url=session.url,
        session_id=session_id,
        tmux_name=tmux_name,
        created_at=session.created_at,
        uptime_s=int(session.uptime_s()),
        mode=getattr(session, "mode", ""),  # поле появляется в remote в задаче 11
        attach=attach_line(tmux_name, host),
        cli=cli_prefix(host),
    )


async def collect(sessions: list[RemoteSession], *, host: str) -> list[Passport]:
    """Паспорта для списка сессий; ветка — из git по каталогу каждой."""
    result: list[Passport] = []
    for session in sessions:
        tree = await worktrees.inspect(Path(session.cwd))
        result.append(build(session, host=host, tree=tree))
    return result


def as_dict(p: Passport) -> dict[str, Any]:
    return {
        "name": p.name,
        "label": p.label,
        "host": p.host,
        "tmux_name": p.tmux_name,
        "session_id": p.session_id,
        "cwd": p.cwd,
        "branch": p.branch,
        "blockers": list(p.blockers),
        "url": p.url,
        "uptime_s": p.uptime_s,
        "mode": p.mode,
        "attach": p.attach,
        "cli": p.cli,
    }


def _uptime(seconds: int) -> str:
    minutes = seconds // 60
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин"
    return f"{minutes // 60} ч {minutes % 60} мин"


def _tree_line(p: Passport) -> str:
    if not p.branch:
        return ""
    state = "; ".join(p.blockers) or "чисто"
    mode = f" · {p.mode}" if p.mode else ""
    return f"🌿 {p.branch} · {state}{mode}"


def as_text(p: Passport) -> str:
    head = f"{p.label} · {_uptime(p.uptime_s)}" + (f" · {p.host}" if p.host else "")
    lines = [head, p.cwd, _tree_line(p), f"🔗 {p.url or 'ссылка неизвестна'}", f"🖥 {p.attach}"]
    lines.append(f"🤖 {p.cli} send {shlex.quote(p.label)} '/mcp' --tail")
    return "\n".join(line for line in lines if line)


def as_html(p: Passport) -> str:
    e = html.escape
    head = f"<b>{e(p.label)}</b> · {_uptime(p.uptime_s)}" + (f" · {e(p.host)}" if p.host else "")
    lines = [
        head,
        f"<code>{e(p.cwd)}</code>",
        e(_tree_line(p)),
        e(p.url) if p.url else "ссылка неизвестна",
        f"🖥 <code>{e(p.attach)}</code>",
        f"🤖 <code>{e(p.cli)} send {e(shlex.quote(p.label))} '/mcp' --tail</code>",
    ]
    return "\n".join(line for line in lines if line)
