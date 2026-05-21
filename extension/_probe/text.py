from __future__ import annotations

import re
from datetime import datetime
from typing import Final

ANSI_ESCAPE_RE: Final[re.Pattern[str]] = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)
ANSI_OSC_RE: Final[re.Pattern[str]] = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")


def strip_terminal_noise(raw_text: str) -> str:
    cleaned = raw_text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    cleaned = ANSI_OSC_RE.sub("", cleaned)
    cleaned = ANSI_ESCAPE_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def compact_terminal_text(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def prettify_compact_claude_text(text: str) -> str:
    pretty = text.replace("spent·Resets", "spent · Resets ")
    pretty = re.sub(r"(?<=\d)spent", " spent", pretty)
    pretty = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", pretty)
    pretty = re.sub(r",(?=\d)", ", ", pretty)
    pretty = re.sub(r"(?<=\d)(am|pm)\(", r"\1 (", pretty, flags=re.IGNORECASE)
    pretty = re.sub(r"(?<=\d)\(", " (", pretty)
    return pretty


def normalize_version_text(version: str | None, prefix: str) -> str | None:
    if version is None:
        return None
    normalized = version.strip()
    if normalized.startswith(prefix):
        normalized = normalized.removeprefix(prefix).strip()
    return normalized or None


def safe_percent_remaining(used_percent: float | None) -> float | None:
    if used_percent is None:
        return None
    return max(0.0, min(100.0, 100.0 - used_percent))


def format_reset_time(reset_time: str | None) -> str | None:
    if reset_time is None:
        return None
    try:
        parsed = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
    except ValueError:
        return reset_time
    local_time = parsed.astimezone()
    return local_time.strftime("%b %d, %I:%M %p %Z")


def format_short_reset_time(reset_time: str | None) -> str | None:
    if reset_time is None:
        return None
    try:
        parsed = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
    except ValueError:
        return reset_time
    local_time = parsed.astimezone()
    return local_time.strftime("%I:%M %p %Z").lstrip("0")


def short_model_label(model_id: str) -> str:
    normalized = model_id.removeprefix("gemini-")
    normalized = normalized.removesuffix("-preview")
    return normalized


def short_plan(plan: str | None) -> str | None:
    if not plan:
        return None
    lowered = plan.lower()
    if "pro" in lowered:
        return "Pro"
    if "max" in lowered:
        return "Max"
    if "team" in lowered:
        return "Team"
    if "enterprise" in lowered:
        return "Ent"
    if "free" in lowered:
        return "Free"
    words = plan.split()
    return words[0] if words else None


def auth_mode_label(auth_mode: str | None) -> str | None:
    if auth_mode is None:
        return None
    labels = {
        "oauth-personal": "Google sign-in",
        "oauth-workspace": "Workspace sign-in",
        "api-key": "API key",
    }
    return labels.get(auth_mode, auth_mode)
