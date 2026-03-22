from extension import probe
from extension.probe import (
    JSONValue,
    parse_claude_usage_screen,
    parse_gemini_quota_metrics,
    safe_percent_remaining,
    short_plan,
)


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


def test_resolve_command_path_falls_back_to_login_shell(monkeypatch) -> None:
    probe.resolve_command_path.cache_clear()
    monkeypatch.setattr(probe.shutil, "which", lambda command: None)
    monkeypatch.setenv("SHELL", "/bin/sh")

    def fake_run(*args, **kwargs):
        return probe.subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="/mock/bin/claude\n",
            stderr="",
        )

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    assert probe.resolve_command_path("claude") == "/mock/bin/claude"
    probe.resolve_command_path.cache_clear()


def test_resolve_command_path_uses_fallback_bin_dirs(monkeypatch, tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_command = fake_bin / "gemini"
    fake_command.write_text("#!/bin/sh\n")
    fake_command.chmod(0o755)

    probe.resolve_command_path.cache_clear()
    monkeypatch.setattr(probe.shutil, "which", lambda command: None)
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(probe, "fallback_bin_dirs", lambda: [fake_bin])

    assert probe.resolve_command_path("gemini") == str(fake_command)
    probe.resolve_command_path.cache_clear()


def test_command_environment_includes_fallback_path(monkeypatch, tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    probe.resolve_login_shell_path.cache_clear()
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(probe, "fallback_bin_dirs", lambda: [fake_bin])

    env = probe.command_environment()

    assert env["PATH"] == str(fake_bin)
    probe.resolve_login_shell_path.cache_clear()
