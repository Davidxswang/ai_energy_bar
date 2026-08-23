from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from datetime import datetime
from typing import Final

from .models import JSONValue, LimitMetric, ProviderStatus
from .shell import command_environment, get_version, resolve_command_path
from .text import normalize_version_text, safe_percent_remaining

FIVE_HOUR_WINDOW_MINUTES: Final[int] = 5 * 60
WEEKLY_WINDOW_MINUTES: Final[int] = 7 * 24 * 60
WINDOW_KEY_BY_DURATION_MINUTES: Final[dict[int, str]] = {
    FIVE_HOUR_WINDOW_MINUTES: "five_hour",
    WEEKLY_WINDOW_MINUTES: "weekly",
}


def normalize_codex_rate_limits(
    payload: JSONValue | None,
) -> dict[str, JSONValue] | None:
    """Classify app-server rate-limit windows by duration.

    ``primary`` and ``secondary`` are nullable transport slots, not stable window
    identities. The app server can put a weekly-only limit in ``primary``, so the
    duration is the source of truth for whether a window is five-hour or weekly.
    """
    if not isinstance(payload, dict):
        return None

    def window(
        raw: JSONValue | None,
    ) -> tuple[str, dict[str, JSONValue]] | None:
        if not isinstance(raw, dict):
            return None
        duration = raw.get("windowDurationMins")
        if isinstance(duration, bool) or not isinstance(duration, int):
            return None
        window_key = WINDOW_KEY_BY_DURATION_MINUTES.get(duration)
        if window_key is None:
            return None

        bucket: dict[str, JSONValue] = {"window_duration_minutes": duration}
        used = raw.get("usedPercent")
        if isinstance(used, (int, float)) and not isinstance(used, bool):
            bucket["used_percent"] = float(used)
        resets = raw.get("resetsAt")
        if isinstance(resets, (int, float)) and not isinstance(resets, bool):
            bucket["resets_at"] = float(resets)
        return window_key, bucket

    normalized: dict[str, JSONValue] = {}
    for slot in ("primary", "secondary"):
        parsed = window(payload.get(slot))
        if parsed is not None:
            window_key, bucket = parsed
            normalized[window_key] = bucket
    return normalized or None


def fetch_codex_rate_limits(timeout: float = 10.0) -> dict[str, JSONValue] | None:
    # Codex 0.130+ writes rate_limits=null in session jsonl; live data only comes from
    # the app-server `account/rateLimits/read` RPC.
    codex_path = resolve_command_path("codex")
    if codex_path is None:
        return None

    try:
        proc = subprocess.Popen(
            [codex_path, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=command_environment(),
        )
    except OSError:
        return None

    if proc.stdin is None or proc.stdout is None:
        proc.kill()
        return None

    response_q: queue.Queue[str] = queue.Queue()

    def pump_stdout() -> None:
        # proc.stdout was non-None above; loop terminates when the pipe closes.
        assert proc.stdout is not None
        try:
            for raw_line in proc.stdout:
                response_q.put(raw_line)
        except (OSError, ValueError):
            pass

    threading.Thread(target=pump_stdout, daemon=True).start()

    try:
        for message in (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "ai_energy_bar", "version": "0.1.0"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "account/rateLimits/read",
                "params": {},
            },
        ):
            proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = response_q.get(timeout=remaining)
            except queue.Empty:
                return None
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict) or msg.get("id") != 2:
                continue
            result = msg.get("result")
            if not isinstance(result, dict):
                return None
            return normalize_codex_rate_limits(result.get("rateLimits"))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass


def codex_status() -> ProviderStatus:
    version = get_version("codex", ["-V"])
    if version is None:
        return ProviderStatus(
            key="codex",
            display_name="Codex",
            available=False,
            summary="Codex CLI not found",
            detail="Install `@openai/codex` and ensure `codex` is on PATH.",
            source="missing",
            compact="Cx --",
        )

    rate_limits = fetch_codex_rate_limits()
    five_hour_window = (
        rate_limits.get("five_hour") if isinstance(rate_limits, dict) else None
    )
    weekly_window = rate_limits.get("weekly") if isinstance(rate_limits, dict) else None

    now = datetime.now().timestamp()
    five_hour = None
    weekly = None

    if isinstance(five_hour_window, dict):
        resets_at = five_hour_window.get("resets_at")
        if isinstance(resets_at, (int, float)) and now >= resets_at:
            five_hour = 100.0
        else:
            used_percent = five_hour_window.get("used_percent")
            if isinstance(used_percent, (int, float)):
                five_hour = safe_percent_remaining(float(used_percent))

    if isinstance(weekly_window, dict):
        resets_at = weekly_window.get("resets_at")
        if isinstance(resets_at, (int, float)) and now >= resets_at:
            weekly = 100.0
        else:
            used_percent = weekly_window.get("used_percent")
            if isinstance(used_percent, (int, float)):
                weekly = safe_percent_remaining(float(used_percent))

    metrics: dict[str, LimitMetric] = {}
    if five_hour is not None:
        metrics["five_hour_limit"] = LimitMetric(
            label="5-hour limit",
            percent_remaining=five_hour,
            text=f"{five_hour:.0f}% left",
        )
    if weekly is not None:
        metrics["weekly_limit"] = LimitMetric(
            label="Weekly limit",
            percent_remaining=weekly,
            text=f"{weekly:.0f}% left",
        )

    warning: str | None = None
    if weekly is not None and weekly < 25.0:
        warning = "Weekly Codex quota is below 25%."

    summary_parts: list[str] = []
    missing_parts: list[str] = []
    if five_hour is not None:
        summary_parts.append(f"5-hour {five_hour:.0f}% left")
    else:
        missing_parts.append("5-hour")
    if weekly is not None:
        summary_parts.append(f"weekly {weekly:.0f}% left")
    else:
        missing_parts.append("weekly")

    if summary_parts:
        summary = " · ".join(summary_parts)
        detail = (
            f"Codex app-server did not report a {' or '.join(missing_parts)} limit."
            if missing_parts
            else ""
        )
        compact = (f"Cx {five_hour:.0f}" if five_hour is not None else "Cx --") + (
            f"/{weekly:.0f}" if weekly is not None else "/--"
        )
    else:
        summary = "Codex quota unavailable"
        detail = (
            "`codex app-server` did not return rate-limit data. Make sure you are "
            "signed in (`codex login`) and that `codex app-server` runs."
        )
        compact = "Cx --"

    return ProviderStatus(
        key="codex",
        display_name="Codex",
        available=True,
        summary=summary,
        detail=detail,
        source="codex-app-server",
        compact=compact,
        warning=warning,
        version=normalize_version_text(version, "codex-cli"),
        metrics=metrics,
    )
