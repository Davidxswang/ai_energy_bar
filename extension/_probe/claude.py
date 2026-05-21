from __future__ import annotations

import io
import re
import time
from typing import Final

from .models import PROJECT_ROOT, LimitMetric, ProviderStatus
from .shell import command_environment, get_version, resolve_command_path
from .text import (
    compact_terminal_text,
    normalize_version_text,
    prettify_compact_claude_text,
    safe_percent_remaining,
    strip_terminal_noise,
)

CLAUDE_PERCENT_USED_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d+(?:\.\d+)?)%\s*used",
    re.IGNORECASE,
)


def capture_claude_usage_screen(timeout: float = 60.0) -> str | None:
    claude_path = resolve_command_path("claude")
    if claude_path is None:
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
            claude_path,
            ["--permission-mode", "plan"],
            cwd=str(PROJECT_ROOT),
            env=env,
            encoding="utf-8",
            timeout=timeout,
        )
        child.logfile_read = transcript
        # Increase initial prompt timeout specifically, as startup can be slow
        child.expect([r"❯", r"│ >", r"›", r"> "], timeout=max(timeout, 30.0))
        child.send("/usage")
        child.expect([r"/usage", r"usage"], timeout=timeout)
        child.send("\t")
        child.send("\r")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cleaned = strip_terminal_noise(transcript.getvalue())
            compact = compact_terminal_text(cleaned)
            if "currentweek(allmodels)" in compact and "%used" in compact:
                return cleaned
            try:
                child.expect(
                    [r"Current", r"Curre", r"Resets", r"Extra", pexpect.TIMEOUT],
                    timeout=2.0,
                )
            except pexpect.EOF:
                break

        return None
    except (OSError, pexpect.ExceptionPexpect, pexpect.TIMEOUT, pexpect.EOF):
        return None
    finally:
        if child is not None and child.isalive():
            child.close(force=True)


def parse_claude_usage_screen(
    screen_text: str,
) -> tuple[dict[str, LimitMetric], str | None, str | None, str | None]:
    lines = [line.strip() for line in screen_text.splitlines() if line.strip()]
    section_headers = {
        "currentsession",
        "curretsession",
        "currentweek(allmodels)",
        "currentweek(sonnetonly)",
        "extrausage",
        "status",
        "config",
        "usage",
        "pressesctoexit",
        "esctocancel",
        "what'scontributingtoyourlimitsusage?",
        "scanninglocalsessions…",
        "refreshing…",
    }

    def normalize_reset_text(raw_reset: str | None) -> str | None:
        if raw_reset is None:
            return None
        normalized = re.sub(r"\s+", " ", raw_reset).strip().rstrip(".")
        if not normalized:
            return None
        normalized = re.sub(
            r"^(?:resets?|reset|reses)\s*",
            "Resets ",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"^Resets\s+in(?=\S)", "Resets in ", normalized)
        normalized = re.sub(r"^Resets(?=\S)", "Resets ", normalized)
        normalized = prettify_compact_claude_text(normalized)
        return (
            normalized if normalized.startswith("Resets ") else f"Resets {normalized}"
        )

    def is_section_header(line: str) -> bool:
        compact = compact_terminal_text(line)
        return compact in section_headers

    def looks_like_reset_line(line: str) -> bool:
        squashed = re.sub(r"\s+", "", line).lower()
        return squashed.startswith(("resets", "reset", "reses"))

    def extract_usage_block(labels: set[str]) -> tuple[float | None, str | None]:
        for index, line in enumerate(lines):
            if compact_terminal_text(line) not in labels:
                continue
            block_lines: list[str] = []
            for candidate in lines[index + 1 : index + 8]:
                if is_section_header(candidate):
                    break
                block_lines.append(candidate)

            block_text = " ".join(block_lines)
            percent_used: float | None = None
            reset_text: str | None = None
            used_match = CLAUDE_PERCENT_USED_RE.search(block_text)
            if used_match is not None:
                percent_used = float(used_match.group(1))
            for reset_index, candidate in enumerate(block_lines):
                if not looks_like_reset_line(candidate):
                    continue
                reset_fragments = [candidate, *block_lines[reset_index + 1 :]]
                reset_text = normalize_reset_text(" ".join(reset_fragments))
                break
            return safe_percent_remaining(percent_used), reset_text
        return None, None

    def extract_extra_usage() -> str | None:
        for index, line in enumerate(lines):
            if compact_terminal_text(line) != "extrausage":
                continue
            for candidate in lines[index + 1 : index + 5]:
                compact = compact_terminal_text(candidate)
                if compact in section_headers or "%used" in compact:
                    continue
                if candidate:
                    return prettify_compact_claude_text(candidate)
        return None

    session_remaining, session_reset = extract_usage_block(
        {"currentsession", "curretsession"}
    )
    week_remaining, week_reset = extract_usage_block({"currentweek(allmodels)"})
    extra_usage = extract_extra_usage()
    metrics = {
        "current_session": LimitMetric(
            label="Current session",
            percent_remaining=session_remaining,
            text=(
                f"{session_remaining:.0f}% left"
                if session_remaining is not None
                else None
            ),
        ),
        "current_week": LimitMetric(
            label="Current week",
            percent_remaining=week_remaining,
            text=f"{week_remaining:.0f}% left" if week_remaining is not None else None,
        ),
    }
    return metrics, session_reset, week_reset, extra_usage


def claude_status() -> ProviderStatus:
    version = get_version("claude", ["-v"])
    if version is None:
        return ProviderStatus(
            key="claude",
            display_name="Claude Code",
            available=False,
            summary="Claude Code not found",
            detail="Install Claude Code and ensure `claude` is on PATH.",
            source="missing",
            compact="Cl --",
        )

    version_text = normalize_version_text(version, "claude")
    usage_screen = capture_claude_usage_screen()
    if usage_screen is not None:
        metrics, session_reset, week_reset, extra_usage = parse_claude_usage_screen(
            usage_screen
        )
        session_remaining = metrics["current_session"].percent_remaining
        week_remaining = metrics["current_week"].percent_remaining
        if session_remaining is not None and week_remaining is not None:
            warning: str | None = None
            if week_remaining < 25.0:
                warning = "Weekly Claude quota is below 25%."
            elif session_remaining < 25.0:
                warning = "Current Claude session quota is below 25%."

            detail_parts: list[str] = []
            if session_reset is not None:
                detail_parts.append(f"session {session_reset.removeprefix('Resets ')}")
            if week_reset is not None:
                detail_parts.append(f"week {week_reset.removeprefix('Resets ')}")
            if extra_usage is not None:
                if extra_usage == "Extra usage not enabled • /extra-usage to enable":
                    detail_parts.append("extra usage off")
                else:
                    detail_parts.append(f"extra usage: {extra_usage}")

            return ProviderStatus(
                key="claude",
                display_name="Claude Code",
                available=True,
                summary=(
                    f"session {session_remaining:.0f}% left · "
                    f"week {week_remaining:.0f}% left"
                ),
                detail="\n".join(detail_parts),
                source="claude-status-usage",
                compact=f"Cl {session_remaining:.0f}/{week_remaining:.0f}",
                warning=warning,
                version=version_text,
                metrics=metrics,
            )

    return ProviderStatus(
        key="claude",
        display_name="Claude Code",
        available=True,
        summary="Quota unavailable",
        detail="Live Claude quota was unavailable in this poll.",
        source="claude-auth-metadata",
        compact="Cl --",
        warning="Live Claude quota unavailable in this poll.",
        version=version_text,
    )
