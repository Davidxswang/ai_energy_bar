from __future__ import annotations

import io
import re
import time

from .models import PROJECT_ROOT
from .shell import command_environment, resolve_command_path
from .text import strip_terminal_noise


def capture_gemini_startup_screen(timeout: float = 20.0) -> str | None:
    gemini_path = resolve_command_path("gemini")
    if gemini_path is None:
        return None

    try:
        import pexpect
    except ImportError:
        return None

    transcript = io.StringIO()
    child: pexpect.spawn[str] | None = None
    env = command_environment()
    env.setdefault("TERM", "xterm-256color")

    try:
        child = pexpect.spawn(
            gemini_path,
            cwd=str(PROJECT_ROOT),
            env=env,
            encoding="utf-8",
            timeout=timeout,
        )
        child.logfile_read = transcript
        child.expect(r"Type your message or @path/to/file", timeout=timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cleaned = strip_terminal_noise(transcript.getvalue())
            if re.search(r"\d+(?:\.\d+)?%\s*used", cleaned):
                return cleaned
            try:
                child.expect([r"\d+(?:\.\d+)?%\s*used", pexpect.TIMEOUT], timeout=1.0)
            except pexpect.EOF:
                break
        return strip_terminal_noise(transcript.getvalue())
    except (OSError, pexpect.ExceptionPexpect, pexpect.TIMEOUT, pexpect.EOF):
        return None
    finally:
        if child is not None and child.isalive():
            child.close(force=True)


def capture_gemini_stats_screen(timeout: float = 60.0) -> str | None:
    """Send `/stats` inside Gemini and return the rendered transcript."""
    gemini_path = resolve_command_path("gemini")
    if gemini_path is None:
        return None

    try:
        import pexpect
    except ImportError:
        return None

    transcript = io.StringIO()
    child: pexpect.spawn[str] | None = None
    env = command_environment()
    env.setdefault("TERM", "xterm-256color")

    try:
        child = pexpect.spawn(
            gemini_path,
            cwd=str(PROJECT_ROOT),
            env=env,
            encoding="utf-8",
            timeout=timeout,
        )
        child.logfile_read = transcript
        child.expect(
            [r"Type your message or @path/to/file", r"❯", r"›", r"> "],
            timeout=max(timeout, 30.0),
        )

        child.send("/stats")
        child.expect([r"/stats", r"stats"], timeout=timeout)
        child.send("\r")

        child.expect([r"Session Stats", r"Model usage"], timeout=timeout)

        try:
            child.expect([r"Type your message", r"❯", r"›", r"> "], timeout=15.0)
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass

        return strip_terminal_noise(transcript.getvalue())
    except (OSError, pexpect.ExceptionPexpect, pexpect.TIMEOUT, pexpect.EOF):
        return None
    finally:
        if child is not None and child.isalive():
            child.close(force=True)
