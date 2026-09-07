"""ssh-туннели к портам, которые слушает сессия на другой машине.

Единственная команда, исполняемая на стороне того, кто сидит за клавиатурой:
туннель строится отсюда туда. pid каждого ssh — в файле, чтобы --stop умел снять.
"""

from __future__ import annotations

import os as os  # тесты подменяют forward.os.kill
import re
import signal
import socket
import subprocess as subprocess  # тесты подменяют forward.subprocess.Popen
import time as time  # тесты подменяют forward.time.sleep
from dataclasses import dataclass
from pathlib import Path

PID_DIR_ENV = "CLAUDE_RC_FORWARDS"
_DEFAULT_DIR = "~/.claude-rc/forwards"
# Сколько ждать после Popen, прежде чем поверить, что ssh не умер сразу
# (например, из-за отказа авторизации).
_SETTLE_S = 0.5
# Имя хоста для ssh: без "/" и без ведущего "-" — иначе оно попадёт прямо в
# путь pid-файла (../../etc/x сбежал бы из pid_dir()) или сойдёт за опцию ssh.
# `user@host` разрешён: в ~/.ssh/config запись есть не всегда, а `@` в имени
# файла безопасен. Часть до `@` тоже обязана начинаться с буквы или цифры —
# иначе `-oProxyCommand=…@m1` уехал бы в argv ssh именно как опция.
_HOST_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9._-]*$")


class ForwardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Forward:
    host: str
    port: int
    pid: int


def _check_host(host: str) -> None:
    if not _HOST_RE.match(host):
        raise ForwardError(f"недопустимое имя хоста: {host}")


def _is_ssh(pid: int) -> bool:
    """Тот ли это процесс, чей pid мы записали.

    ssh мог умереть сам (оборвалась сеть), pid-файл при этом остаётся, а pid
    к следующему `--stop` занимает кто угодно. `ProcessLookupError` такой случай
    не ловит — процесс-то есть. Спрашиваем имя команды и снимаем только ssh;
    всё остальное значит, что туннеля давно нет, а файл протух.
    """
    try:
        done = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # ps нет или не ответил — уверенности, что это ssh, нет тем более.
        return False
    return os.path.basename(done.stdout.strip()) == "ssh"


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
    _check_host(host)
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
        time.sleep(_SETTLE_S)
        rc = proc.poll()
        if rc is not None:
            # Туннели, поднятые раньше в этом же вызове, уже работают и не
            # гасятся — неудача одного порта не должна рвать соседние.
            raise ForwardError(
                f"ssh -L {port} на {host} завершился сразу (код {rc}); проверь ssh {host}"
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
    _check_host(host)
    stopped: list[Forward] = []
    denied: list[int] = []
    for f in active():
        if f.host != host or (ports is not None and f.port not in ports):
            continue
        if not _is_ssh(f.pid):
            # pid занят не ssh: туннеля нет, а SIGTERM ушёл бы чужому процессу.
            # Файл убираем — он и есть протухшая запись.
            _pid_file(f.host, f.port).unlink(missing_ok=True)
            continue
        try:
            os.kill(f.pid, signal.SIGTERM)
        except ProcessLookupError:
            _pid_file(f.host, f.port).unlink(missing_ok=True)  # ssh уже умер сам
            continue
        except PermissionError:
            # pid, похоже, переиспользован чужим (привилегированным) процессом —
            # файл не трогаем, чтобы не потерять единственную нить к настоящему ssh.
            denied.append(f.port)
            continue
        _pid_file(f.host, f.port).unlink(missing_ok=True)
        stopped.append(f)
    if denied:
        raise ForwardError("не удалось снять (нет прав): " + ", ".join(map(str, denied)))
    return stopped
