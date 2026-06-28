from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final

from .models import JSONValue, LimitMetric, ProviderStatus
from .shell import get_version
from .text import format_reset_time, normalize_version_text, safe_percent_remaining

CLAUDE_CREDENTIALS_PATH: Final[Path] = Path.home() / ".claude" / ".credentials.json"
CLAUDE_USAGE_URL: Final[str] = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_OAUTH_BETA: Final[str] = "oauth-2025-04-20"
USAGE_TIMEOUT_SECONDS: Final[float] = 6.0


def read_claude_oauth_token() -> str | None:
    """Return the Claude Code subscription OAuth access token, or None.

    Claude Code (Linux) persists its OAuth token at
    ``~/.claude/.credentials.json`` under ``claudeAiOauth.accessToken`` and
    refreshes it on use. We read it directly instead of driving the CLI's
    interactive ``/usage`` screen.
    """
    try:
        raw = CLAUDE_CREDENTIALS_PATH.read_text()
    except OSError:
        return None
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    oauth = loaded.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    return token if isinstance(token, str) and token else None


def fetch_claude_usage(
    timeout: float = USAGE_TIMEOUT_SECONDS,
    user_agent: str | None = None,
) -> dict[str, JSONValue] | None:
    """Fetch the live usage summary from the Claude OAuth usage endpoint.

    Mirrors what the interactive ``/usage`` screen calls under the hood:
    ``GET /api/oauth/usage`` with the subscription OAuth bearer token. Returns
    the decoded JSON object, or ``None`` on any auth/network/parse failure so
    the bar degrades to "unavailable" instead of hanging.
    """
    token = read_claude_oauth_token()
    if token is None:
        return None
    request = urllib.request.Request(
        CLAUDE_USAGE_URL,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": CLAUDE_OAUTH_BETA,
            "Content-Type": "application/json",
            "User-Agent": user_agent or "claude-cli (external, cli)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except (
        # Best-effort fetch: never let a flaky read crash the combined probe
        # (that would blank Codex/Gemini too). URLError covers HTTPError (e.g. a
        # 401 on an expired token) + DNS/SSL via OSError; HTTPException covers
        # IncompleteRead/BadStatusLine; ValueError covers a malformed token in
        # the header (and keeps it out of any propagated traceback/stderr).
        urllib.error.URLError,
        OSError,
        TimeoutError,
        http.client.HTTPException,
        ValueError,
    ):
        return None
    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _limit_metric(
    data: dict[str, JSONValue], key: str, label: str
) -> tuple[LimitMetric, str | None]:
    """Build a ``LimitMetric`` (+ formatted reset time) from one usage bucket.

    Each bucket looks like ``{"utilization": <percent_used 0-100>,
    "resets_at": <iso8601>}``; absent/null buckets yield an empty metric.
    """
    bucket = data.get(key)
    percent_remaining: float | None = None
    reset_text: str | None = None
    if isinstance(bucket, dict):
        utilization = bucket.get("utilization")
        if isinstance(utilization, (int, float)) and not isinstance(utilization, bool):
            percent_remaining = safe_percent_remaining(float(utilization))
        resets_at = bucket.get("resets_at")
        if isinstance(resets_at, str):
            reset_text = format_reset_time(resets_at)
    metric = LimitMetric(
        label=label,
        percent_remaining=percent_remaining,
        text=(
            f"{percent_remaining:.0f}% left" if percent_remaining is not None else None
        ),
    )
    return metric, reset_text


def _money(amount: JSONValue) -> str | None:
    """Format a ``{amount_minor, exponent, currency}`` money object as text."""
    if not isinstance(amount, dict):
        return None
    minor = amount.get("amount_minor")
    exponent = amount.get("exponent")
    if isinstance(minor, bool) or not isinstance(minor, (int, float)):
        return None
    if isinstance(exponent, bool) or not isinstance(exponent, int):
        return None
    value = minor / (10**exponent)
    digits = max(exponent, 0)
    currency = amount.get("currency")
    if currency == "USD":
        return f"${value:.{digits}f}"
    if isinstance(currency, str) and currency:
        return f"{value:.{digits}f} {currency}"
    return f"{value:.{digits}f}"


def _extra_usage_text(data: dict[str, JSONValue]) -> str | None:
    """Summarize the extra-usage (overage) state for the detail tooltip."""
    extra = data.get("extra_usage")
    if not isinstance(extra, dict):
        return None
    if extra.get("is_enabled") is not True:
        return "Extra usage not enabled"
    spend = data.get("spend")
    if isinstance(spend, dict):
        used = _money(spend.get("used"))
        limit = _money(spend.get("limit"))
        if used is not None and limit is not None:
            return f"extra usage {used} / {limit}"
    return "Extra usage enabled"


def parse_claude_usage(
    data: dict[str, JSONValue],
) -> tuple[dict[str, LimitMetric], str | None, str | None, str | None]:
    """Map ``/api/oauth/usage`` JSON to (metrics, session_reset, week_reset, extra).

    ``five_hour`` is the rolling session limit; ``seven_day`` the weekly
    all-models limit. Per-model weekly buckets (``seven_day_sonnet`` /
    ``seven_day_opus``) are included only when present.
    """
    session_metric, session_reset = _limit_metric(data, "five_hour", "Current session")
    week_metric, week_reset = _limit_metric(data, "seven_day", "Current week")
    metrics: dict[str, LimitMetric] = {
        "current_session": session_metric,
        "current_week": week_metric,
    }
    sonnet_metric, _ = _limit_metric(data, "seven_day_sonnet", "Current week (Sonnet)")
    if sonnet_metric.percent_remaining is not None:
        metrics["current_week_sonnet"] = sonnet_metric
    opus_metric, _ = _limit_metric(data, "seven_day_opus", "Current week (Opus)")
    if opus_metric.percent_remaining is not None:
        metrics["current_week_opus"] = opus_metric
    return metrics, session_reset, week_reset, _extra_usage_text(data)


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
    user_agent = f"claude-cli/{version_text} (external, cli)" if version_text else None
    usage = fetch_claude_usage(user_agent=user_agent)
    if usage is not None:
        metrics, session_reset, week_reset, extra_usage = parse_claude_usage(usage)
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
                detail_parts.append(f"session resets {session_reset}")
            if week_reset is not None:
                detail_parts.append(f"week resets {week_reset}")
            if extra_usage is not None:
                detail_parts.append(extra_usage)

            return ProviderStatus(
                key="claude",
                display_name="Claude Code",
                available=True,
                summary=(
                    f"session {session_remaining:.0f}% left · "
                    f"week {week_remaining:.0f}% left"
                ),
                detail="\n".join(detail_parts),
                source="claude-oauth-usage",
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
