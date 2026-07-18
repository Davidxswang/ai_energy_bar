import pytest

from extension._probe import codex
from extension._probe.codex import normalize_codex_rate_limits
from extension._probe.models import JSONValue


def test_normalize_codex_rate_limits_remaps_app_server_payload() -> None:
    payload: dict[str, JSONValue] = {
        "limitId": "codex",
        "planType": "plus",
        "primary": {
            "usedPercent": 4,
            "windowDurationMins": 300,
            "resetsAt": 1779279053,
        },
        "secondary": {
            "usedPercent": 12,
            "windowDurationMins": 10080,
            "resetsAt": 1779865853,
        },
    }

    normalized = normalize_codex_rate_limits(payload)

    assert normalized == {
        "five_hour": {
            "window_duration_minutes": 300,
            "used_percent": 4.0,
            "resets_at": 1779279053.0,
        },
        "weekly": {
            "window_duration_minutes": 10080,
            "used_percent": 12.0,
            "resets_at": 1779865853.0,
        },
    }


def test_normalize_codex_rate_limits_classifies_weekly_primary() -> None:
    payload: dict[str, JSONValue] = {
        "primary": {
            "usedPercent": 1,
            "windowDurationMins": 10080,
            "resetsAt": 1784971363,
        },
        "secondary": None,
    }

    normalized = normalize_codex_rate_limits(payload)

    assert normalized == {
        "weekly": {
            "window_duration_minutes": 10080,
            "used_percent": 1.0,
            "resets_at": 1784971363.0,
        }
    }


def test_codex_status_renders_weekly_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex, "get_version", lambda *_args: "codex-cli 0.144.4")
    monkeypatch.setattr(
        codex,
        "fetch_codex_rate_limits",
        lambda: {"weekly": {"used_percent": 1.0}},
    )

    status = codex.codex_status()

    assert status.summary == "weekly 99% left"
    assert status.detail == "Codex app-server did not report a 5-hour limit."
    assert status.compact == "Cx --/99"
    assert "five_hour_limit" not in status.metrics
    assert status.metrics["weekly_limit"].percent_remaining == 99.0


def test_codex_status_renders_five_hour_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex, "get_version", lambda *_args: "codex-cli 0.144.4")
    monkeypatch.setattr(
        codex,
        "fetch_codex_rate_limits",
        lambda: {"five_hour": {"used_percent": 4.0}},
    )

    status = codex.codex_status()

    assert status.summary == "5-hour 96% left"
    assert status.detail == "Codex app-server did not report a weekly limit."
    assert status.compact == "Cx 96/--"
    assert status.metrics["five_hour_limit"].percent_remaining == 96.0
    assert "weekly_limit" not in status.metrics


def test_normalize_codex_rate_limits_returns_none_for_missing_payload() -> None:
    assert normalize_codex_rate_limits(None) is None
    assert normalize_codex_rate_limits({}) is None
    assert normalize_codex_rate_limits({"primary": None, "secondary": None}) is None
