from __future__ import annotations

import re
from typing import Final

from .models import JSONValue, LimitMetric
from .text import format_short_reset_time, safe_percent_remaining, short_model_label

GEMINI_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"Signed in with Google:\s*([^\s/]+@[^\s/]+)"
)
GEMINI_PLAN_RE: Final[re.Pattern[str]] = re.compile(
    r"Plan:\s*(.+?)(?:\s+/upgrade|\s*$)"
)
GEMINI_VISIBLE_MODEL_ORDER: Final[tuple[str, ...]] = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-pro-preview",
)


def parse_gemini_stats_transcript(text: str) -> dict[str, JSONValue] | None:
    tier_match = re.search(r"Tier:\s*(.+)", text)
    auth_match = re.search(r"Auth Method:\s*.*?\((.+?)\)", text)

    tier = tier_match.group(1).strip() if tier_match else None
    email = auth_match.group(1).strip() if auth_match else None

    # Model lines look like:
    # │  gemini-2.5-flash           -    ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬    4%  10:13 PM (19h 52m)  │
    # Note: Some names might be truncated with ellipsis

    buckets: list[dict[str, JSONValue]] = []

    for line in text.splitlines():
        model_name_match = re.search(
            r"│\s+(gemini-[a-z0-9.-]+(?:…)?)\s+-\s+.*?\s+(\d+)%\s+(.+?)\s+│", line
        )
        if model_name_match:
            truncated_name = model_name_match.group(1).strip()
            used_percent = float(model_name_match.group(2))
            reset_time = model_name_match.group(3).strip()

            actual_name = truncated_name
            if truncated_name.endswith("…"):
                prefix = truncated_name.rstrip("…")
                for candidate in GEMINI_VISIBLE_MODEL_ORDER:
                    if candidate.startswith(prefix):
                        actual_name = candidate
                        break

            buckets.append(
                {
                    "modelId": actual_name,
                    "remainingFraction": (100.0 - used_percent) / 100.0,
                    "resetTime": reset_time,
                }
            )

    if not buckets:
        return None

    return {"tier": tier, "email": email, "quota": {"buckets": buckets}}


def parse_gemini_quota_metrics(
    payload: dict[str, JSONValue] | None,
) -> tuple[dict[str, LimitMetric], str | None, str | None, str | None]:
    if not isinstance(payload, dict):
        return {}, None, None, None

    tier = payload.get("tier")
    tier_text = str(tier) if isinstance(tier, str) else None
    current_model = payload.get("model")
    current_model_text = str(current_model) if isinstance(current_model, str) else None
    quota = payload.get("quota")
    if not isinstance(quota, dict):
        return {}, tier_text, current_model_text, None

    buckets = quota.get("buckets")
    if not isinstance(buckets, list):
        return {}, tier_text, current_model_text, None

    metrics: dict[str, LimitMetric] = {}
    detail_rows: list[tuple[int, str]] = []
    priority_index = {
        model_id: index for index, model_id in enumerate(GEMINI_VISIBLE_MODEL_ORDER)
    }
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        model_id = bucket.get("modelId")
        remaining_fraction = bucket.get("remainingFraction")
        if not isinstance(model_id, str) or not isinstance(
            remaining_fraction, (int, float)
        ):
            continue
        if model_id not in priority_index:
            continue

        percent_remaining = max(0.0, min(100.0, float(remaining_fraction) * 100.0))
        metric_key = re.sub(r"[^a-z0-9]+", "_", model_id.lower()).strip("_")
        raw_reset_time = bucket.get("resetTime")
        reset_text = format_short_reset_time(
            raw_reset_time if isinstance(raw_reset_time, str) else None
        )
        metrics[metric_key] = LimitMetric(
            label=model_id,
            percent_remaining=percent_remaining,
            text=f"{percent_remaining:.0f}% left",
        )

        row = f"{short_model_label(model_id)}: {percent_remaining:.0f}% left"
        if reset_text is not None:
            row = f"{row}, resets {reset_text}"
        detail_rows.append((priority_index.get(model_id, len(priority_index)), row))

    ordered_rows = [row for _, row in sorted(detail_rows, key=lambda item: item[0])]
    detail_text = "\n".join(ordered_rows) if ordered_rows else None
    return metrics, tier_text, current_model_text, detail_text


def parse_gemini_startup_quota(
    screen_text: str,
) -> tuple[dict[str, LimitMetric], str | None, str | None, str | None]:
    tier_match = GEMINI_PLAN_RE.search(screen_text)
    tier_text = tier_match.group(1).strip() if tier_match is not None else None

    for line in screen_text.splitlines():
        if "% used" not in line:
            continue
        columns = [
            column.strip() for column in re.split(r"\s{2,}", line) if column.strip()
        ]
        if len(columns) < 2:
            continue
        used_match = re.fullmatch(r"(\d+(?:\.\d+)?)%\s*used", columns[-1])
        if used_match is None:
            continue

        used_percent = float(used_match.group(1))
        percent_remaining = safe_percent_remaining(used_percent)
        current_model = columns[-2]
        metrics = {
            "current_quota": LimitMetric(
                label="Current quota",
                percent_remaining=percent_remaining,
                text=(
                    f"{percent_remaining:.0f}% left"
                    if percent_remaining is not None
                    else None
                ),
            )
        }
        detail_parts = [f"{current_model}: {used_percent:.0f}% used"]
        if tier_text is not None:
            detail_parts.append(tier_text)
        return metrics, tier_text, current_model, "\n".join(detail_parts)

    return {}, tier_text, None, None
