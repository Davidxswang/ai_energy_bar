#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Final

JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
REPO_ROOT_HINT_FILE: Final[str] = ".repo-root"
ANSI_ESCAPE_RE: Final[re.Pattern[str]] = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)
ANSI_OSC_RE: Final[re.Pattern[str]] = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
CLAUDE_PERCENT_USED_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d+(?:\.\d+)?)%\s*used",
    re.IGNORECASE,
)
GEMINI_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"Signed in with Google:\s*([^\s/]+@[^\s/]+)"
)
GEMINI_PLAN_RE: Final[re.Pattern[str]] = re.compile(
    r"Plan:\s*(.+?)(?:\s+/upgrade|\s*$)"
)
GEMINI_JSON_MARKER: Final[str] = "__GEMINI_QUOTA__"
GEMINI_VISIBLE_MODEL_ORDER: Final[tuple[str, ...]] = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-pro-preview",
)


def resolve_project_root() -> Path:
    probe_path = Path(__file__).resolve()
    hint_path = probe_path.with_name(REPO_ROOT_HINT_FILE)
    if hint_path.exists():
        try:
            hinted_root = Path(hint_path.read_text().strip()).expanduser().resolve()
        except OSError:
            hinted_root = None
        if hinted_root is not None and hinted_root.exists():
            return hinted_root
    return probe_path.parent.parent


PROJECT_ROOT = resolve_project_root()


@dataclasses.dataclass(slots=True)
class LimitMetric:
    label: str
    percent_remaining: float | None = None
    text: str | None = None

    def to_json(self) -> dict[str, JSONValue]:
        return {
            "label": self.label,
            "percent_remaining": self.percent_remaining,
            "text": self.text,
        }


@dataclasses.dataclass(slots=True)
class ProviderStatus:
    key: str
    display_name: str
    available: bool
    summary: str
    detail: str
    source: str
    compact: str | None = None
    warning: str | None = None
    version: str | None = None
    metrics: dict[str, LimitMetric] = dataclasses.field(default_factory=dict)

    def to_json(self) -> dict[str, JSONValue]:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "available": self.available,
            "summary": self.summary,
            "detail": self.detail,
            "source": self.source,
            "compact": self.compact,
            "warning": self.warning,
            "version": self.version,
            "metrics": {
                name: metric.to_json() for name, metric in self.metrics.items()
            },
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect local status signals for Claude Code, Codex, and Gemini CLI."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output.",
    )
    return parser.parse_args()


def run_command(
    args: list[str],
    timeout: float = 5.0,
    env: dict[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    return stdout or stderr


def fallback_bin_dirs() -> list[Path]:
    home = Path.home()
    candidates = [
        home / ".local" / "bin",
        home / ".bun" / "bin",
    ]
    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.exists():
        candidates.extend(
            sorted(
                (version_dir / "bin" for version_dir in nvm_root.iterdir()),
                key=lambda path: path.name,
                reverse=True,
            )
        )
    candidates.extend(
        [
            Path("/usr/local/sbin"),
            Path("/usr/local/bin"),
            Path("/usr/sbin"),
            Path("/usr/bin"),
            Path("/sbin"),
            Path("/bin"),
            Path("/usr/games"),
            Path("/usr/local/games"),
            Path("/snap/bin"),
        ]
    )

    unique_dirs: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        text = str(candidate)
        if text in seen:
            continue
        seen.add(text)
        unique_dirs.append(candidate)
    return unique_dirs


def merge_path_entries(*path_values: str) -> str:
    entries: list[str] = []
    seen: set[str] = set()
    for path_value in path_values:
        for raw_entry in path_value.split(os.pathsep):
            entry = raw_entry.strip()
            if not entry or entry in seen:
                continue
            seen.add(entry)
            entries.append(entry)
    return os.pathsep.join(entries)


@lru_cache(maxsize=None)
def resolve_command_path(command: str) -> str | None:
    direct_path = shutil.which(command)
    if direct_path is not None:
        return direct_path

    shell = os.environ.get("SHELL")
    if shell:
        try:
            completed = subprocess.run(
                [shell, "-lic", f"command -v {shlex.quote(command)}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except (OSError, subprocess.SubprocessError):
            candidates: list[str] = []
        else:
            candidates = [
                line.strip() for line in completed.stdout.splitlines() if line.strip()
            ]

        for candidate in reversed(candidates):
            if candidate.startswith("/"):
                return candidate

        if candidates:
            return candidates[-1]

    for directory in fallback_bin_dirs():
        command_path = directory / command
        if command_path.is_file() and os.access(command_path, os.X_OK):
            return str(command_path)

    return None


@lru_cache(maxsize=None)
def resolve_login_shell_path() -> str | None:
    shell = os.environ.get("SHELL")
    if not shell:
        return None

    try:
        completed = subprocess.run(
            [shell, "-lic", 'printf "%s" "$PATH"'],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    resolved = completed.stdout.strip()
    return resolved or None


def command_environment() -> dict[str, str]:
    env = os.environ.copy()
    shell_path = resolve_login_shell_path()
    fallback_path = os.pathsep.join(str(path) for path in fallback_bin_dirs())
    if shell_path is not None:
        env["PATH"] = merge_path_entries(shell_path, fallback_path)
    else:
        env["PATH"] = merge_path_entries(env.get("PATH", ""), fallback_path)
    return env


def get_version(command: str, version_args: list[str]) -> str | None:
    command_path = resolve_command_path(command)
    if command_path is None:
        return None
    try:
        output = run_command([command_path, *version_args], env=command_environment())
    except (OSError, subprocess.SubprocessError):
        return None
    return output.strip()


def read_json_object(path: Path) -> dict[str, JSONValue] | None:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def read_json_value(path: Path) -> JSONValue | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def short_plan(plan: str | None) -> str | None:
    if not plan:
        return None
    lowered = plan.lower()
    if "pro" in lowered:
        return "Pro"
    if "max" in lowered:
        return "Max"
    if "team" in lowered:
        return "Team"
    if "enterprise" in lowered:
        return "Ent"
    if "free" in lowered:
        return "Free"
    words = plan.split()
    return words[0] if words else None


def auth_mode_label(auth_mode: str | None) -> str | None:
    if auth_mode is None:
        return None
    labels = {
        "oauth-personal": "Google sign-in",
        "oauth-workspace": "Workspace sign-in",
        "api-key": "API key",
    }
    return labels.get(auth_mode, auth_mode)


def strip_terminal_noise(raw_text: str) -> str:
    cleaned = raw_text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    cleaned = ANSI_OSC_RE.sub("", cleaned)
    cleaned = ANSI_ESCAPE_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def capture_claude_usage_screen(timeout: float = 30.0) -> str | None:
    claude_path = resolve_command_path("claude")
    if claude_path is None:
        return None

    try:
        import pexpect
    except ImportError:
        return None

    transcript = io.StringIO()
    child: pexpect.spawn[str] | None = None
    env = command_environment()
    env.setdefault("TERM", "xterm-256color")

    try:
        child = pexpect.spawn(
            claude_path,
            ["--permission-mode", "plan"],
            cwd=str(PROJECT_ROOT),
            env=env,
            encoding="utf-8",
            timeout=timeout,
        )
        child.logfile_read = transcript
        child.expect([r"❯", r"│ >", r"›", r"> "], timeout=timeout)
        child.send("/status")
        child.expect([r"/status", r"status"], timeout=timeout)
        child.send("\t")
        child.send("\r")
        child.expect(
            [r"Status", r"Loading usage data", r"Current session"],
            timeout=timeout,
        )
        child.send("\x1b[C")
        child.expect([r"Search settings", r"Config"], timeout=timeout)
        child.send("\x1b[C")
        child.expect(
            [r"Loading usage data", r"Current week \(all models\)"],
            timeout=timeout,
        )
        if child.after == "Loading usage data":
            child.expect(r"Current week \(all models\)", timeout=timeout)
        child.expect(r"Extra usage", timeout=timeout)
        return strip_terminal_noise(transcript.getvalue())
    except (OSError, pexpect.ExceptionPexpect, pexpect.TIMEOUT, pexpect.EOF):
        return None
    finally:
        if child is not None and child.isalive():
            child.close(force=True)


def parse_claude_usage_screen(
    screen_text: str,
) -> tuple[dict[str, LimitMetric], str | None, str | None, str | None]:
    lines = [line.strip() for line in screen_text.splitlines() if line.strip()]
    section_headers = {
        "Current session",
        "Current week (all models)",
        "Extra usage",
        "Status",
        "Config",
        "Usage",
        "Press Esc to exit",
        "Esc to cancel",
    }

    def normalize_reset_text(raw_reset: str | None) -> str | None:
        if raw_reset is None:
            return None
        normalized = re.sub(r"\s+", " ", raw_reset).strip().rstrip(".")
        if not normalized:
            return None
        normalized = re.sub(
            r"^(?:resets?|reset|reses)\s*",
            "Resets ",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"^Resets\s+in(?=\S)", "Resets in ", normalized)
        normalized = re.sub(r"^Resets(?=\S)", "Resets ", normalized)
        return (
            normalized if normalized.startswith("Resets ") else f"Resets {normalized}"
        )

    def is_section_header(line: str) -> bool:
        return line in section_headers

    def looks_like_reset_line(line: str) -> bool:
        squashed = re.sub(r"\s+", "", line).lower()
        return squashed.startswith(("resets", "reset", "reses"))

    def extract_usage_block(label: str) -> tuple[float | None, str | None]:
        for index, line in enumerate(lines):
            if label not in line:
                continue
            block_lines: list[str] = []
            for candidate in lines[index + 1 : index + 8]:
                if is_section_header(candidate):
                    break
                block_lines.append(candidate)

            block_text = " ".join(block_lines)
            percent_used: float | None = None
            reset_text: str | None = None
            used_match = CLAUDE_PERCENT_USED_RE.search(block_text)
            if used_match is not None:
                percent_used = float(used_match.group(1))
            for reset_index, candidate in enumerate(block_lines):
                if not looks_like_reset_line(candidate):
                    continue
                reset_fragments = [candidate, *block_lines[reset_index + 1 :]]
                reset_text = normalize_reset_text(" ".join(reset_fragments))
                break
            return safe_percent_remaining(percent_used), reset_text
        return None, None

    def extract_extra_usage() -> str | None:
        for index, line in enumerate(lines):
            if line != "Extra usage":
                continue
            for candidate in lines[index + 1 : index + 5]:
                if candidate not in {
                    "Usage",
                    "Status",
                    "Config",
                    "Press Esc to exit",
                }:
                    return candidate
        return None

    session_remaining, session_reset = extract_usage_block("Current session")
    week_remaining, week_reset = extract_usage_block("Current week (all models)")
    extra_usage = extract_extra_usage()
    metrics = {
        "current_session": LimitMetric(
            label="Current session",
            percent_remaining=session_remaining,
            text=(
                f"{session_remaining:.0f}% left"
                if session_remaining is not None
                else None
            ),
        ),
        "current_week": LimitMetric(
            label="Current week",
            percent_remaining=week_remaining,
            text=f"{week_remaining:.0f}% left" if week_remaining is not None else None,
        ),
    }
    return metrics, session_reset, week_reset, extra_usage


def capture_gemini_startup_screen(timeout: float = 20.0) -> str | None:
    gemini_path = resolve_command_path("gemini")
    if gemini_path is None:
        return None

    try:
        import pexpect
    except ImportError:
        return None

    transcript = io.StringIO()
    child: pexpect.spawn[str] | None = None
    env = command_environment()
    env.setdefault("TERM", "xterm-256color")

    try:
        child = pexpect.spawn(
            gemini_path,
            cwd=str(PROJECT_ROOT),
            env=env,
            encoding="utf-8",
            timeout=timeout,
        )
        child.logfile_read = transcript
        child.expect(r"Type your message or @path/to/file", timeout=timeout)
        return strip_terminal_noise(transcript.getvalue())
    except (OSError, pexpect.ExceptionPexpect, pexpect.TIMEOUT, pexpect.EOF):
        return None
    finally:
        if child is not None and child.isalive():
            child.close(force=True)


def load_gemini_live_quota(timeout: float = 30.0) -> dict[str, JSONValue] | None:
    gemini_path = resolve_command_path("gemini")
    node_path = resolve_command_path("node")
    if gemini_path is None or node_path is None:
        return None

    gemini_package_root = Path(gemini_path).resolve().parent.parent
    settings_js = gemini_package_root / "dist" / "src" / "config" / "settings.js"
    config_js = gemini_package_root / "dist" / "src" / "config" / "config.js"
    if not settings_js.exists() or not config_js.exists():
        return None

    node_source = textwrap.dedent(
        f"""
        import {{ loadSettings }} from {json.dumps(str(settings_js))};
        import {{ loadCliConfig }} from {json.dumps(str(config_js))};

        const cwd = {json.dumps(str(Path.home()))};
        const loadedSettings = loadSettings(cwd);
        const argv = {{
          query: undefined,
          model: undefined,
          sandbox: false,
          debug: false,
          prompt: undefined,
          promptInteractive: undefined,
          yolo: false,
          approvalMode: 'default',
          policy: undefined,
          allowedMcpServerNames: undefined,
          allowedTools: undefined,
          acp: false,
          experimentalAcp: false,
          extensions: undefined,
          listExtensions: false,
          resume: undefined,
          listSessions: false,
          deleteSession: undefined,
          includeDirectories: undefined,
          screenReader: false,
          useWriteTodos: undefined,
          outputFormat: 'text',
          fakeResponses: undefined,
          recordResponses: undefined,
          startupMessages: [],
          rawOutput: false,
          acceptRawOutputRisk: false,
          isCommand: false,
        }};

        const config = await loadCliConfig(
          loadedSettings.merged,
          `probe-${{Date.now()}}`,
          argv,
          {{ cwd }},
        );
        await config.storage.initialize();
        const authType = loadedSettings.merged.security?.auth?.selectedType;
        if (authType) {{
          await config.refreshAuth(authType);
        }}
        await config.initialize();
        const quota = await config.refreshUserQuota();
        const out = {{
          authType,
          tier: config.getUserTierName(),
          paidTier: config.getUserPaidTier(),
          quota,
          pooledRemaining: config.getQuotaRemaining(),
          pooledLimit: config.getQuotaLimit(),
          pooledResetTime: config.getQuotaResetTime(),
          model: config.getModel(),
        }};
        console.log({json.dumps(GEMINI_JSON_MARKER)} + JSON.stringify(out));
        """
    ).strip()

    env = command_environment()
    env.setdefault("TERM", "xterm-256color")
    completed = subprocess.run(
        [node_path, "--input-type=module", "-e", node_source],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )

    output = "\n".join(
        part for part in (completed.stdout, completed.stderr) if part.strip()
    )
    for line in output.splitlines():
        if not line.startswith(GEMINI_JSON_MARKER):
            continue
        try:
            loaded = json.loads(line.removeprefix(GEMINI_JSON_MARKER))
        except json.JSONDecodeError:
            return None
        return loaded if isinstance(loaded, dict) else None
    return None


def latest_file(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    return max(paths, key=lambda path: path.stat().st_mtime)


def find_latest_codex_rate_limits() -> tuple[str | None, dict[str, JSONValue] | None]:
    sessions_root = Path.home() / ".codex" / "sessions"
    if not sessions_root.exists():
        return None, None

    session_files = sorted(
        sessions_root.rglob("*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:20]

    best_timestamp: str | None = None
    best_rate_limits: dict[str, JSONValue] | None = None

    for path in session_files:
        try:
            for line in path.read_text(errors="ignore").splitlines():
                if '"rate_limits"' not in line:
                    continue
                loaded = json.loads(line)
                if not isinstance(loaded, dict):
                    continue

                timestamp = loaded.get("timestamp")
                payload = loaded.get("payload")
                if not isinstance(timestamp, str) or not isinstance(payload, dict):
                    continue

                rate_limits = payload.get("rate_limits")
                if not isinstance(rate_limits, dict):
                    continue

                if best_timestamp is None or timestamp > best_timestamp:
                    best_timestamp = timestamp
                    best_rate_limits = rate_limits
        except (OSError, json.JSONDecodeError):
            continue

    return best_timestamp, best_rate_limits


def normalize_version_text(version: str | None, prefix: str) -> str | None:
    if version is None:
        return None
    normalized = version.strip()
    if normalized.startswith(prefix):
        normalized = normalized.removeprefix(prefix).strip()
    return normalized or None


def safe_percent_remaining(used_percent: float | None) -> float | None:
    if used_percent is None:
        return None
    return max(0.0, min(100.0, 100.0 - used_percent))


def format_reset_time(reset_time: str | None) -> str | None:
    if reset_time is None:
        return None
    try:
        parsed = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
    except ValueError:
        return reset_time
    local_time = parsed.astimezone()
    return local_time.strftime("%b %d, %I:%M %p %Z")


def format_short_reset_time(reset_time: str | None) -> str | None:
    if reset_time is None:
        return None
    try:
        parsed = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
    except ValueError:
        return reset_time
    local_time = parsed.astimezone()
    return local_time.strftime("%I:%M %p %Z").lstrip("0")


def short_model_label(model_id: str) -> str:
    normalized = model_id.removeprefix("gemini-")
    normalized = normalized.removesuffix("-preview")
    return normalized


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

    _, rate_limits = find_latest_codex_rate_limits()
    primary = rate_limits.get("primary") if isinstance(rate_limits, dict) else None
    secondary = rate_limits.get("secondary") if isinstance(rate_limits, dict) else None

    five_hour = None
    weekly = None
    if isinstance(primary, dict):
        used_percent = primary.get("used_percent")
        if isinstance(used_percent, (int, float)):
            five_hour = safe_percent_remaining(float(used_percent))
    if isinstance(secondary, dict):
        used_percent = secondary.get("used_percent")
        if isinstance(used_percent, (int, float)):
            weekly = safe_percent_remaining(float(used_percent))

    metrics: dict[str, LimitMetric] = {
        "five_hour_limit": LimitMetric(
            label="5-hour limit",
            percent_remaining=five_hour,
            text=f"{five_hour:.0f}% left" if five_hour is not None else None,
        ),
        "weekly_limit": LimitMetric(
            label="Weekly limit",
            percent_remaining=weekly,
            text=f"{weekly:.0f}% left" if weekly is not None else None,
        ),
    }

    warning: str | None = None
    if weekly is not None and weekly < 25.0:
        warning = "Weekly Codex quota is below 25%."

    if five_hour is not None and weekly is not None:
        summary = f"5-hour {five_hour:.0f}% left · weekly {weekly:.0f}% left"
        detail = ""
        compact = f"Cx {five_hour:.0f}/{weekly:.0f}"
    else:
        summary = "No recent Codex rate-limit event found"
        detail = (
            "Open Codex once in this account so the local session log contains "
            "rate-limit state."
        )
        compact = "Cx --"

    return ProviderStatus(
        key="codex",
        display_name="Codex",
        available=True,
        summary=summary,
        detail=detail,
        source="codex-session-jsonl",
        compact=compact,
        warning=warning,
        version=normalize_version_text(version, "codex-cli"),
        metrics=metrics,
    )


def claude_status() -> ProviderStatus:
    version = get_version("claude", ["-v"])
    if version is None:
        return ProviderStatus(
            key="claude",
            display_name="Claude Code",
            available=False,
            summary="Claude Code not found",
            detail="Install Claude Code and ensure `claude` is on PATH.",
            source="missing",
            compact="Cl --",
        )

    version_text = normalize_version_text(version, "claude")
    usage_screen = capture_claude_usage_screen()
    if usage_screen is not None:
        metrics, session_reset, week_reset, extra_usage = parse_claude_usage_screen(
            usage_screen
        )
        session_remaining = metrics["current_session"].percent_remaining
        week_remaining = metrics["current_week"].percent_remaining
        if session_remaining is not None and week_remaining is not None:
            warning: str | None = None
            if week_remaining < 25.0:
                warning = "Weekly Claude quota is below 25%."
            elif session_remaining < 25.0:
                warning = "Current Claude session quota is below 25%."

            detail_parts: list[str] = []
            if session_reset is not None:
                detail_parts.append(f"session {session_reset.removeprefix('Resets ')}")
            if week_reset is not None:
                detail_parts.append(f"week {week_reset.removeprefix('Resets ')}")
            if extra_usage is not None:
                if extra_usage == "Extra usage not enabled • /extra-usage to enable":
                    detail_parts.append("extra usage off")
                else:
                    detail_parts.append(f"extra usage: {extra_usage}")

            return ProviderStatus(
                key="claude",
                display_name="Claude Code",
                available=True,
                summary=(
                    f"session {session_remaining:.0f}% left · "
                    f"week {week_remaining:.0f}% left"
                ),
                detail="\n".join(detail_parts),
                source="claude-status-usage",
                compact=f"Cl {session_remaining:.0f}/{week_remaining:.0f}",
                warning=warning,
                version=version_text,
                metrics=metrics,
            )

    return ProviderStatus(
        key="claude",
        display_name="Claude Code",
        available=True,
        summary="Quota unavailable",
        detail="Live Claude quota was unavailable in this poll.",
        source="claude-auth-metadata",
        compact="Cl --",
        warning="Live Claude quota unavailable in this poll.",
        version=version_text,
    )


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

    live_quota = load_gemini_live_quota()
    if isinstance(live_quota, dict):
        metrics, _tier_text, _current_model_text, detail_text = (
            parse_gemini_quota_metrics(live_quota)
        )
        if metrics:
            flash = metrics.get("gemini_2_5_flash")
            pro = metrics.get("gemini_2_5_pro")
            compact = "Ge --"
            if flash is not None and pro is not None:
                flash_remaining = flash.percent_remaining
                pro_remaining = pro.percent_remaining
                if flash_remaining is not None and pro_remaining is not None:
                    compact = f"Ge {flash_remaining:.0f}/{pro_remaining:.0f}"

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

            return ProviderStatus(
                key="gemini",
                display_name="Gemini CLI",
                available=True,
                summary="",
                detail="\n".join(detail_parts),
                source="gemini-cli-quota",
                compact=compact,
                warning=warning,
                version=version_text,
                metrics=metrics,
            )

    startup_screen = capture_gemini_startup_screen()

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


def build_snapshot() -> dict[str, JSONValue]:
    providers = {
        "claude": claude_status(),
        "codex": codex_status(),
        "gemini": gemini_status(),
    }
    return {
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "providers": {name: provider.to_json() for name, provider in providers.items()},
    }


def main() -> int:
    args = parse_args()
    snapshot = build_snapshot()
    indent = 2 if args.pretty else None
    print(json.dumps(snapshot, indent=indent, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
