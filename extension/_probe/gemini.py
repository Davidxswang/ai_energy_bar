from __future__ import annotations

from .gemini_capture import capture_gemini_startup_screen, capture_gemini_stats_screen
from .gemini_parse import (
    parse_gemini_quota_metrics,
    parse_gemini_startup_quota,
    parse_gemini_stats_transcript,
)
from .models import JSONValue, LimitMetric, ProviderStatus
from .shell import get_version
from .text import normalize_version_text


def load_gemini_live_quota(timeout: float = 60.0) -> dict[str, JSONValue] | None:
    """Capture the Gemini `/stats` screen and parse it into a quota payload."""
    screen = capture_gemini_stats_screen(timeout=timeout)
    if screen is None:
        return None
    return parse_gemini_stats_transcript(screen)


def gemini_compact_label(metrics: dict[str, LimitMetric]) -> str:
    remaining_values = sorted(
        metric.percent_remaining
        for metric in metrics.values()
        if metric.percent_remaining is not None
    )
    if len(remaining_values) >= 2:
        return f"Ge {remaining_values[0]:.0f}/{remaining_values[1]:.0f}"
    if remaining_values:
        return f"Ge {remaining_values[0]:.0f}"
    return "Ge --"


def gemini_status() -> ProviderStatus:
    version = get_version("gemini", ["-v"])
    if version is None:
        return ProviderStatus(
            key="gemini",
            display_name="Gemini CLI",
            available=False,
            summary="Gemini CLI not found",
            detail="Install Gemini CLI and ensure `gemini` is on PATH.",
            source="missing",
            compact="Ge --",
        )

    version_text = normalize_version_text(version, "gemini")
    startup_screen = capture_gemini_startup_screen()
    if startup_screen is not None:
        metrics, _tier_text, _current_model_text, detail_text = (
            parse_gemini_startup_quota(startup_screen)
        )
        quota_remaining = metrics.get("current_quota")
        percent_remaining = (
            quota_remaining.percent_remaining if quota_remaining is not None else None
        )
        if percent_remaining is not None:
            warning: str | None = None
            if percent_remaining < 25.0:
                warning = "Gemini quota is below 25%."

            return ProviderStatus(
                key="gemini",
                display_name="Gemini CLI",
                available=True,
                summary=f"{percent_remaining:.0f}% left",
                detail=detail_text or "",
                source="gemini-startup-quota",
                compact=f"Ge {percent_remaining:.0f}",
                warning=warning,
                version=version_text,
                metrics=metrics,
            )

    live_quota = load_gemini_live_quota(timeout=12.0)
    if isinstance(live_quota, dict):
        metrics, _tier_text, _current_model_text, detail_text = (
            parse_gemini_quota_metrics(live_quota)
        )
        if metrics:
            compact = gemini_compact_label(metrics)

            warning: str | None = None
            low_models = [
                metric.label
                for metric in metrics.values()
                if metric.percent_remaining is not None
                and metric.percent_remaining < 25.0
            ]
            if low_models:
                warning = f"Low Gemini quota for: {', '.join(low_models[:3])}."

            detail_parts: list[str] = []
            if detail_text is not None:
                detail_parts.append(detail_text)

            remaining_values = sorted(
                metric.percent_remaining
                for metric in metrics.values()
                if metric.percent_remaining is not None
            )
            summary = ""
            if len(remaining_values) >= 2:
                summary = (
                    f"{remaining_values[0]:.0f}% left · {remaining_values[1]:.0f}% next"
                )
            elif remaining_values:
                summary = f"{remaining_values[0]:.0f}% left"

            return ProviderStatus(
                key="gemini",
                display_name="Gemini CLI",
                available=True,
                summary=summary,
                detail="\n".join(detail_parts),
                source="gemini-cli-quota",
                compact=compact,
                warning=warning,
                version=version_text,
                metrics=metrics,
            )

    return ProviderStatus(
        key="gemini",
        display_name="Gemini CLI",
        available=True,
        summary="Quota unavailable",
        detail=(
            "Live Gemini quota was unavailable in this poll."
            if startup_screen is not None
            else "Gemini CLI quota was unavailable."
        ),
        source=(
            "gemini-startup" if startup_screen is not None else "gemini-auth-metadata"
        ),
        compact="Ge --",
        warning="Live Gemini quota unavailable via safe official CLI parsing.",
        version=version_text,
    )
