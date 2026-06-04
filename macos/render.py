"""Pure presentation logic shared by the macOS menu bar front-end.

This module has no macOS/rumps dependency so it can be unit-tested on any
platform. It mirrors the dropdown conventions of the GNOME front-end
(`extension/extension.js`): an "Updated <ts>" header, one block per provider,
and a "show the last live reading" fallback when a poll degrades. The macOS
menu bar itself shows an icon (not a text label), so quota values live in the
dropdown only.
"""

from __future__ import annotations

from typing import Final

from extension._probe.models import JSONValue

PROVIDER_ORDER: Final[tuple[str, ...]] = ("claude", "codex", "gemini")
POLL_INTERVAL_SECONDS: Final[int] = 900
# Wall-clock cap for one probe subprocess. The Claude probe alone drives the
# `claude` CLI with up to a 60s timeout; budget for all three plus startup.
PROBE_TIMEOUT_SECONDS: Final[int] = 180

# Sources that mean "this poll could not read live quota". Mirrors
# LIVE_FALLBACK_SOURCES in extension/extension.js.
LIVE_FALLBACK_SOURCES: Final[dict[str, frozenset[str]]] = {
    "claude": frozenset({"claude-auth-metadata"}),
    "gemini": frozenset({"gemini-startup", "gemini-auth-metadata"}),
}


def _as_dict(value: JSONValue | None) -> dict[str, JSONValue]:
    return value if isinstance(value, dict) else {}


def _as_text(value: JSONValue | None) -> str | None:
    return value if isinstance(value, str) and value else None


def _providers(snapshot: dict[str, JSONValue]) -> dict[str, JSONValue]:
    return _as_dict(snapshot.get("providers"))


def _provider(snapshot: dict[str, JSONValue], key: str) -> dict[str, JSONValue]:
    return _as_dict(_providers(snapshot).get(key))


def header_text(snapshot: dict[str, JSONValue]) -> str:
    return f"Updated {_as_text(snapshot.get('generated_at')) or 'unknown'}"


def provider_lines(snapshot: dict[str, JSONValue], key: str) -> list[str]:
    """Lines for one provider's menu entry: title, summary, detail, warning."""
    provider = _provider(snapshot, key)
    display = _as_text(provider.get("display_name")) or key
    version = _as_text(provider.get("version"))
    lines = [f"{display}  ·  v{version}" if version else display]
    lines.append(_as_text(provider.get("summary")) or "No status available")
    if detail := _as_text(provider.get("detail")):
        lines.extend(detail.splitlines())
    if warning := _as_text(provider.get("warning")):
        lines.append(f"⚠ {warning}")
    return lines


def merge_with_last(
    snapshot: dict[str, JSONValue],
    last: dict[str, JSONValue] | None,
) -> dict[str, JSONValue]:
    """Keep the last live reading when the current poll fell back to metadata.

    Mirrors `_mergeWithLastSnapshot` in extension/extension.js: if a provider's
    current source is a fallback source but its previous source was live, show
    the previous (live) reading with a note instead of flapping to "unavailable".
    """
    last_providers = _providers(last) if last else {}
    if not last_providers:
        return snapshot

    merged = dict(_providers(snapshot))
    generated_at = _as_text(snapshot.get("generated_at")) or "unknown"
    for key, fallback_sources in LIVE_FALLBACK_SOURCES.items():
        current = merged.get(key)
        previous = last_providers.get(key)
        if not isinstance(current, dict) or not isinstance(previous, dict):
            continue
        if _as_text(current.get("source")) not in fallback_sources:
            continue
        if _as_text(previous.get("source")) in fallback_sources:
            continue
        merged[key] = {
            **previous,
            "warning": (
                f"Showing the last live reading. Current poll fell back at "
                f"{generated_at}."
            ),
        }

    return {**snapshot, "providers": merged}
