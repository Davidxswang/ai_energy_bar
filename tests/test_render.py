from extension._probe.models import JSONValue
from macos.render import (
    LIVE_FALLBACK_SOURCES,
    header_text,
    merge_with_last,
    provider_lines,
)


def _snapshot(**providers: dict[str, JSONValue]) -> dict[str, JSONValue]:
    return {"generated_at": "2026-06-04 09:30:00 PDT", "providers": dict(providers)}


def _provider(snapshot: dict[str, JSONValue], key: str) -> dict[str, JSONValue]:
    providers = snapshot["providers"]
    assert isinstance(providers, dict)
    provider = providers[key]
    assert isinstance(provider, dict)
    return provider


def test_header_text_uses_generated_at_with_fallback() -> None:
    assert header_text(_snapshot()) == "Updated 2026-06-04 09:30:00 PDT"
    assert header_text({}) == "Updated unknown"


def test_provider_lines_includes_version_detail_and_warning() -> None:
    snapshot = _snapshot(
        claude={
            "display_name": "Claude Code",
            "version": "2.0.1",
            "summary": "session 45% left · week 80% left",
            "detail": "session resets 5pm\nweek resets Mon",
            "warning": "Weekly Claude quota is below 25%.",
        }
    )
    assert provider_lines(snapshot, "claude") == [
        "Claude Code  ·  v2.0.1",
        "session 45% left · week 80% left",
        "session resets 5pm",
        "week resets Mon",
        "⚠ Weekly Claude quota is below 25%.",
    ]


def test_provider_lines_defaults_for_missing_provider() -> None:
    assert provider_lines({}, "gemini") == ["gemini", "No status available"]


def test_merge_keeps_last_live_reading_on_fallback() -> None:
    assert LIVE_FALLBACK_SOURCES["claude"] == frozenset({"claude-auth-metadata"})
    last = _snapshot(
        claude={
            "source": "claude-status-usage",
            "summary": "session 45% left · week 80% left",
            "compact": "Cl 45/80",
        }
    )
    current = _snapshot(
        claude={"source": "claude-auth-metadata", "summary": "Quota unavailable"}
    )
    merged = merge_with_last(current, last)
    claude = _provider(merged, "claude")
    assert claude["summary"] == "session 45% left · week 80% left"
    assert isinstance(claude["warning"], str)
    assert claude["warning"].startswith("Showing the last live reading")


def test_merge_keeps_current_when_poll_is_live() -> None:
    last = _snapshot(claude={"source": "claude-auth-metadata", "summary": "old"})
    current = _snapshot(claude={"source": "claude-status-usage", "summary": "new"})
    merged = merge_with_last(current, last)
    claude = _provider(merged, "claude")
    assert claude["summary"] == "new"
    assert "warning" not in claude
