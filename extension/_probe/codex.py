from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from datetime import datetime

from .models import JSONValue, LimitMetric, ProviderStatus
from .shell import command_environment, get_version, resolve_command_path
from .text import normalize_version_text, safe_percent_remaining


def normalize_codex_rate_limits(
    payload: JSONValue | None,
) -> dict[str, JSONValue] | None:
    """Reshape app-server rate limits into the snake_case form codex_status reads."""
    if not isinstance(payload, dict):
        return None

    def window(raw: JSONValue | None) -> dict[str, float] | None:
        if not isinstance(raw, dict):
            return None
        bucket: dict[str, float] = {}
        used = raw.get("usedPercent")
        if isinstance(used, (int, float)):
            bucket["used_percent"] = float(used)
        resets = raw.get("resetsAt")
        if isinstance(resets, (int, float)):
            bucket["resets_at"] = float(resets)
        return bucket or None

    normalized: dict[str, JSONValue] = {}
    primary = window(payload.get("primary"))
    if primary is not None:
        normalized["primary"] = primary
    secondary = window(payload.get("secondary"))
    if secondary is not None:
        normalized["secondary"] = secondary
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
    primary = rate_limits.get("primary") if isinstance(rate_limits, dict) else None
    secondary = rate_limits.get("secondary") if isinstance(rate_limits, dict) else None

    now = datetime.now().timestamp()
    five_hour = None
    weekly = None

    if isinstance(primary, dict):
        resets_at = primary.get("resets_at")
        if isinstance(resets_at, (int, float)) and now >= resets_at:
            five_hour = 100.0
        else:
            used_percent = primary.get("used_percent")
            if isinstance(used_percent, (int, float)):
                five_hour = safe_percent_remaining(float(used_percent))

    if isinstance(secondary, dict):
        resets_at = secondary.get("resets_at")
        if isinstance(resets_at, (int, float)) and now >= resets_at:
            weekly = 100.0
        else:
            used_percent = secondary.get("used_percent")
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

    if five_hour is not None and weekly is not None:
        summary = f"5-hour {five_hour:.0f}% left · weekly {weekly:.0f}% left"
        detail = ""
        compact = f"Cx {five_hour:.0f}/{weekly:.0f}"
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
