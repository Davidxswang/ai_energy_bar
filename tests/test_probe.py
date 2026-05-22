from extension._probe import shell
from extension._probe.claude import parse_claude_usage_screen
from extension._probe.codex import normalize_codex_rate_limits
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


def test_parse_claude_usage_screen_keeps_session_and_week_resets_separate() -> None:
    screen_text = """
    Current session
    ██▌                                               5% used
    Resets in
    3h 25m (America/Los_Angeles)
    Current week (all models)
    ██                                                4% used
    Resets
    Mar 24, 11pm (America/Los_Angeles)
    Extra usage
    Extra usage not enabled • /extra-usage to enable
    """

    metrics, session_reset, week_reset, extra_usage = parse_claude_usage_screen(
        screen_text
    )

    assert metrics["current_session"].percent_remaining == 95.0
    assert metrics["current_week"].percent_remaining == 96.0
    assert session_reset == "Resets in 3h 25m (America/Los_Angeles)"
    assert week_reset == "Resets Mar 24, 11pm (America/Los_Angeles)"
    assert extra_usage == "Extra usage not enabled • /extra-usage to enable"


def test_parse_claude_usage_screen_handles_mangled_session_reset_line() -> None:
    screen_text = """
    Current session
    ████████████████████████████████████████████████  96%used
    Reses10pm (America/Los_Angeles)
    Current week (all models)
    ██████████                                        20%used
    Resets Mar 24, 11pm (America/Los_Angeles)
    Extra usage
    Extra usage not enabled • /extra-usage to enable
    Esc to cancel
    """

    metrics, session_reset, week_reset, extra_usage = parse_claude_usage_screen(
        screen_text
    )

    assert metrics["current_session"].percent_remaining == 4.0
    assert metrics["current_week"].percent_remaining == 80.0
    assert session_reset == "Resets 10pm (America/Los_Angeles)"
    assert week_reset == "Resets Mar 24, 11pm (America/Los_Angeles)"
    assert extra_usage == "Extra usage not enabled • /extra-usage to enable"


def test_parse_claude_usage_screen_handles_compact_usage_dialog_text() -> None:
    screen_text = """
    Curretsession
    ██4%used
    Reses1:20am(America/Los_Angeles)

    Currentweek(allmodels)
    ██████████████████████44%used
    ResetsMay1,2pm(America/Los_Angeles)

    Currentweek(Sonnetonly)
    █████████████26%used
    ResetsMay1,2pm(America/Los_Angeles)

    Extrausage
    █▎2%used
    $4.89/$200.00spent·ResetsMay1(America/Los_Angeles)
    """

    metrics, session_reset, week_reset, extra_usage = parse_claude_usage_screen(
        screen_text
    )

    assert metrics["current_session"].percent_remaining == 96.0
    assert metrics["current_week"].percent_remaining == 56.0
    assert session_reset == "Resets 1:20am (America/Los_Angeles)"
    assert week_reset == "Resets May 1, 2pm (America/Los_Angeles)"
    assert extra_usage == "$4.89/$200.00 spent · Resets May 1 (America/Los_Angeles)"


def test_parse_claude_usage_screen_stops_week_reset_at_contribution_section() -> None:
    screen_text = """
    Currentsession
    ██25%used
    Resets2:10am(America/Los_Angeles)

    Currentweek(allmodels)
    █████████████████████████50%used
    ResetsMay1,2pm(America/Los_Angeles)

    What'scontributingtoyourlimitsusage?
    Approximate,basedonlocalsessionsonthismachine—doesnotinclude
    otherdevicesorclaude.ai
    Scanninglocalsessions…
    Refreshing…
    """

    metrics, session_reset, week_reset, extra_usage = parse_claude_usage_screen(
        screen_text
    )

    assert metrics["current_session"].percent_remaining == 75.0
    assert metrics["current_week"].percent_remaining == 50.0
    assert session_reset == "Resets 2:10am (America/Los_Angeles)"
    assert week_reset == "Resets May 1, 2pm (America/Los_Angeles)"
    assert extra_usage is None


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
        "primary": {"used_percent": 4.0, "resets_at": 1779279053.0},
        "secondary": {"used_percent": 12.0, "resets_at": 1779865853.0},
    }


def test_normalize_codex_rate_limits_returns_none_for_missing_payload() -> None:
    assert normalize_codex_rate_limits(None) is None
    assert normalize_codex_rate_limits({}) is None
    assert normalize_codex_rate_limits({"primary": None, "secondary": None}) is None


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
