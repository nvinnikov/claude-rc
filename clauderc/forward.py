"""ssh-туннели к портам, которые слушает сессия на другой машине.

Единственная команда, исполняемая на стороне того, кто сидит за клавиатурой:
туннель строится отсюда туда. pid каждого ssh — в файле, чтобы --stop умел снять.
"""

from __future__ import annotations

import os as os  # тесты подменяют forward.os.kill
import signal
import socket
import subprocess as subprocess  # тесты подменяют forward.subprocess.Popen
from dataclasses import dataclass
from pathlib import Path

PID_DIR_ENV = "CLAUDE_RC_FORWARDS"
_DEFAULT_DIR = "~/.claude-rc/forwards"


class ForwardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Forward:
    host: str
    port: int
    pid: int


def pid_dir() -> Path:
    return Path(os.environ.get(PID_DIR_ENV) or _DEFAULT_DIR).expanduser()


def _pid_file(host: str, port: int) -> Path:
    return pid_dir() / f"{host}-{port}.pid"


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def start(host: str, ports: list[int]) -> list[Forward]:
    busy = [p for p in ports if port_busy(p)]
    if busy:
        raise ForwardError("порт уже занят локально: " + ", ".join(map(str, busy)))
    pid_dir().mkdir(parents=True, exist_ok=True)
    started: list[Forward] = []
    for port in ports:
        argv = ["ssh", "-N", "-L", f"{port}:localhost:{port}", host]
        proc = subprocess.Popen(
            argv,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _pid_file(host, port).write_text(str(proc.pid))
        started.append(Forward(host=host, port=port, pid=proc.pid))
    return started


def _parse(path: Path) -> Forward | None:
    host, _, port = path.stem.rpartition("-")
    pid = path.read_text().strip()
    if not host or not port.isdigit() or not pid.isdigit():
        return None
    return Forward(host=host, port=int(port), pid=int(pid))


def active() -> list[Forward]:
    if not pid_dir().is_dir():
        return []
    found = [_parse(p) for p in sorted(pid_dir().glob("*.pid"))]
    return [f for f in found if f is not None]


def stop(host: str, ports: list[int] | None = None) -> list[Forward]:
    stopped: list[Forward] = []
    for f in active():
        if f.host != host or (ports is not None and f.port not in ports):
            continue
        _pid_file(f.host, f.port).unlink(missing_ok=True)
        try:
            os.kill(f.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue  # ssh уже умер сам; файл убрали
        stopped.append(f)
    return stopped
