from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from .models import JSONValue


def run_command(
    args: list[str],
    timeout: float = 5.0,
    env: dict[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    return stdout or stderr


def fallback_bin_dirs() -> list[Path]:
    home = Path.home()
    candidates = [
        home / ".local" / "bin",
        home / ".bun" / "bin",
    ]
    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.exists():
        candidates.extend(
            sorted(
                (version_dir / "bin" for version_dir in nvm_root.iterdir()),
                key=lambda path: path.name,
                reverse=True,
            )
        )
    candidates.extend(
        [
            Path("/usr/local/sbin"),
            Path("/usr/local/bin"),
            Path("/usr/sbin"),
            Path("/usr/bin"),
            Path("/sbin"),
            Path("/bin"),
            Path("/usr/games"),
            Path("/usr/local/games"),
            Path("/snap/bin"),
        ]
    )

    unique_dirs: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        text = str(candidate)
        if text in seen:
            continue
        seen.add(text)
        unique_dirs.append(candidate)
    return unique_dirs


def merge_path_entries(*path_values: str) -> str:
    entries: list[str] = []
    seen: set[str] = set()
    for path_value in path_values:
        for raw_entry in path_value.split(os.pathsep):
            entry = raw_entry.strip()
            if not entry or entry in seen:
                continue
            seen.add(entry)
            entries.append(entry)
    return os.pathsep.join(entries)


@lru_cache(maxsize=None)
def resolve_command_path(command: str) -> str | None:
    direct_path = shutil.which(command)
    if direct_path is not None:
        return direct_path

    shell = os.environ.get("SHELL")
    if shell:
        try:
            completed = subprocess.run(
                [shell, "-lic", f"command -v {shlex.quote(command)}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except (OSError, subprocess.SubprocessError):
            candidates: list[str] = []
        else:
            candidates = [
                line.strip() for line in completed.stdout.splitlines() if line.strip()
            ]

        for candidate in reversed(candidates):
            if candidate.startswith("/"):
                return candidate

        if candidates:
            return candidates[-1]

    for directory in fallback_bin_dirs():
        command_path = directory / command
        if command_path.is_file() and os.access(command_path, os.X_OK):
            return str(command_path)

    return None


@lru_cache(maxsize=None)
def resolve_login_shell_path() -> str | None:
    shell = os.environ.get("SHELL")
    if not shell:
        return None

    try:
        completed = subprocess.run(
            [shell, "-lic", 'printf "%s" "$PATH"'],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    resolved = completed.stdout.strip()
    return resolved or None


def command_environment() -> dict[str, str]:
    env = os.environ.copy()
    shell_path = resolve_login_shell_path()
    fallback_path = os.pathsep.join(str(path) for path in fallback_bin_dirs())
    if shell_path is not None:
        env["PATH"] = merge_path_entries(shell_path, fallback_path)
    else:
        env["PATH"] = merge_path_entries(env.get("PATH", ""), fallback_path)
    return env


def get_version(command: str, version_args: list[str]) -> str | None:
    command_path = resolve_command_path(command)
    if command_path is None:
        return None
    try:
        output = run_command([command_path, *version_args], env=command_environment())
    except (OSError, subprocess.SubprocessError):
        return None
    return output.strip()


def read_json_object(path: Path) -> dict[str, JSONValue] | None:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def read_json_value(path: Path) -> JSONValue | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
