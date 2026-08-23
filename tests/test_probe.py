from extension._probe import shell
from extension._probe.claude import parse_claude_usage
from extension._probe.gemini import gemini_compact_label
from extension._probe.gemini_parse import (
    parse_gemini_quota_metrics,
    parse_gemini_startup_quota,
)
from extension._probe.models import JSONValue, LimitMetric
from extension._probe.text import safe_percent_remaining, short_plan


def test_short_plan_normalizes_common_tiers() -> None:
    assert short_plan("Gemini Code Assist in Google One AI Pro") == "Pro"
    assert short_plan("Claude Max 20x") == "Max"
    assert short_plan("Enterprise") == "Ent"


def test_safe_percent_remaining_clamps_values() -> None:
    assert safe_percent_remaining(0.0) == 100.0
    assert safe_percent_remaining(12.5) == 87.5
    assert safe_percent_remaining(150.0) == 0.0


def test_parse_gemini_quota_metrics_extracts_model_rows() -> None:
    payload: dict[str, JSONValue] = {
        "tier": "Gemini Code Assist in Google One AI Pro",
        "model": "auto-gemini-3",
        "quota": {
            "buckets": [
                {
                    "modelId": "gemini-2.5-flash",
                    "remainingFraction": 0.88,
                    "resetTime": "2026-03-19T07:12:31Z",
                },
                {
                    "modelId": "gemini-3-pro-preview",
                    "remainingFraction": 0.5,
                    "resetTime": "2026-03-19T07:19:31Z",
                },
                {
                    "modelId": "gemini-2.5-pro",
                    "remainingFraction": 1.0,
                    "resetTime": "2026-03-19T07:20:51Z",
                },
            ]
        },
    }

    metrics, tier_text, current_model_text, detail_text = parse_gemini_quota_metrics(
        payload
    )

    assert tier_text == "Gemini Code Assist in Google One AI Pro"
    assert current_model_text == "auto-gemini-3"
    assert detail_text is not None
    assert "2.5-flash: 88% left" in detail_text
    assert "2.5-pro: 100% left" in detail_text
    assert "3-pro" not in detail_text
    assert metrics["gemini_2_5_flash"].percent_remaining == 88.0
    assert metrics["gemini_2_5_pro"].percent_remaining == 100.0
    assert "gemini_3_pro_preview" not in metrics


def test_gemini_compact_label_uses_lowest_two_remaining_values() -> None:
    metrics = {
        "gemini_2_5_flash": LimitMetric("gemini-2.5-flash", 89.0, "89% left"),
        "gemini_2_5_flash_lite": LimitMetric("gemini-2.5-flash-lite", 97.0, "97% left"),
        "gemini_2_5_pro": LimitMetric("gemini-2.5-pro", 100.0, "100% left"),
        "gemini_3_flash_preview": LimitMetric(
            "gemini-3-flash-preview", 92.0, "92% left"
        ),
    }

    assert gemini_compact_label(metrics) == "Ge 89/92"


def _usage_payload() -> dict[str, JSONValue]:
    """A representative ``GET /api/oauth/usage`` response (utilization = % used)."""
    return {
        "five_hour": {
            "utilization": 3.0,
            "resets_at": "2026-06-28T23:09:59.646359+00:00",
        },
        "seven_day": {
            "utilization": 12.0,
            "resets_at": "2026-07-03T20:59:59.646384+00:00",
        },
        "seven_day_oauth_apps": None,
        "seven_day_opus": None,
        "seven_day_sonnet": {
            "utilization": 9.0,
            "resets_at": "2026-07-03T20:59:59.646396+00:00",
        },
        "extra_usage": {
            "is_enabled": True,
            "monthly_limit": 20000,
            "used_credits": 0.0,
            "currency": "USD",
        },
        "spend": {
            "used": {"amount_minor": 0, "currency": "USD", "exponent": 2},
            "limit": {"amount_minor": 20000, "currency": "USD", "exponent": 2},
            "percent": 0,
        },
    }


def test_parse_claude_usage_maps_session_week_and_sonnet() -> None:
    metrics, session_reset, week_reset, extra_usage = parse_claude_usage(
        _usage_payload()
    )

    assert metrics["current_session"].percent_remaining == 97.0
    assert metrics["current_week"].percent_remaining == 88.0
    assert metrics["current_week_sonnet"].percent_remaining == 91.0
    # Opus bucket is null in the payload, so it is omitted entirely.
    assert "current_week_opus" not in metrics
    # Resets are formatted into non-empty localized strings, distinct per bucket
    # (a swapped/duplicated bucket mapping would also flip the percents above).
    assert session_reset is not None and session_reset != ""
    assert week_reset is not None and week_reset != ""
    assert session_reset != week_reset
    assert extra_usage == "extra usage $0.00 / $200.00"


def test_parse_claude_usage_handles_missing_and_null_buckets() -> None:
    metrics, session_reset, week_reset, extra_usage = parse_claude_usage(
        {"five_hour": None, "seven_day": {}}
    )

    assert metrics["current_session"].percent_remaining is None
    assert metrics["current_week"].percent_remaining is None
    assert "current_week_sonnet" not in metrics
    assert session_reset is None
    assert week_reset is None
    assert extra_usage is None


def test_parse_claude_usage_reports_extra_usage_disabled() -> None:
    payload = _usage_payload()
    payload["extra_usage"] = {"is_enabled": False}

    _metrics, _session_reset, _week_reset, extra_usage = parse_claude_usage(payload)

    assert extra_usage == "Extra usage not enabled"


def test_parse_claude_usage_reports_extra_usage_enabled_without_spend() -> None:
    payload = _usage_payload()
    payload["extra_usage"] = {"is_enabled": True}
    payload.pop("spend", None)

    _metrics, _session_reset, _week_reset, extra_usage = parse_claude_usage(payload)

    assert extra_usage == "Extra usage enabled"


def test_parse_gemini_startup_quota_extracts_footer_quota() -> None:
    screen_text = """
    Gemini CLI v0.39.1
    Signed in with Google /auth
    Plan: Gemini Code Assist in Google One AI Pro /upgrade

     workspace (/directory)      branch    sandbox       /model               quota
     ~/projects/ai_energy_bar    main      no sandbox    Auto (Gemini 3)    3% used
    """

    metrics, tier_text, current_model_text, detail_text = parse_gemini_startup_quota(
        screen_text
    )

    assert tier_text == "Gemini Code Assist in Google One AI Pro"
    assert current_model_text == "Auto (Gemini 3)"
    assert detail_text is not None
    assert "Auto (Gemini 3): 3% used" in detail_text
    assert metrics["current_quota"].percent_remaining == 97.0


def test_resolve_command_path_falls_back_to_login_shell(monkeypatch) -> None:
    shell.resolve_command_path.cache_clear()
    monkeypatch.setattr(shell.shutil, "which", lambda command: None)
    monkeypatch.setenv("SHELL", "/bin/sh")

    def fake_run(*args, **kwargs):
        return shell.subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="/mock/bin/claude\n",
            stderr="",
        )

    monkeypatch.setattr(shell.subprocess, "run", fake_run)

    assert shell.resolve_command_path("claude") == "/mock/bin/claude"
    shell.resolve_command_path.cache_clear()


def test_resolve_command_path_uses_fallback_bin_dirs(monkeypatch, tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_command = fake_bin / "gemini"
    fake_command.write_text("#!/bin/sh\n")
    fake_command.chmod(0o755)

    shell.resolve_command_path.cache_clear()
    monkeypatch.setattr(shell.shutil, "which", lambda command: None)
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(shell, "fallback_bin_dirs", lambda: [fake_bin])

    assert shell.resolve_command_path("gemini") == str(fake_command)
    shell.resolve_command_path.cache_clear()


def test_command_environment_includes_fallback_path(monkeypatch, tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    shell.resolve_login_shell_path.cache_clear()
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(shell, "fallback_bin_dirs", lambda: [fake_bin])

    env = shell.command_environment()

    assert env["PATH"] == str(fake_bin)
    shell.resolve_login_shell_path.cache_clear()
