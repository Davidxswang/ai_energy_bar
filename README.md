# ai_energy_bar

Native GNOME Shell extension for Ubuntu 24.04 that surfaces local usage signals for:

- Claude Code
- Codex CLI
- Gemini CLI

This repository now contains a working GNOME Shell 46 extension skeleton plus a local probe helper.

## Current v1 behavior

- Codex: live quota probe from the latest local session JSONL rate-limit events.
- Claude Code: live quota probe by launching the official CLI and parsing the `/status` usage view.
- Gemini CLI: live quota probe from the installed official Gemini CLI quota path, matching the data surfaced by `/stats`.

The extension avoids private hosted APIs. Exact remaining quota is currently available for Codex, Claude, and Gemini using local session state or official CLI-backed quota data.

## Layout

- `extension/metadata.json`: GNOME Shell extension manifest
- `extension/extension.js`: top-bar widget and dropdown UI
- `extension/probe.py`: local status collector
- `extension/stylesheet.css`: extension styles
- `scripts/install-dev.sh`: local install script

## Install for local development

```bash
./scripts/install-dev.sh
```

Then log out and back in on Wayland if GNOME does not reload the extension automatically.

## Uninstall

```bash
./scripts/uninstall-dev.sh
```

## Probe manually

```bash
python3 extension/probe.py --pretty
```

## Development setup

```bash
uv sync --group dev
uv run python extension/probe.py --pretty
uv run ty check
```

When installed via `./scripts/install-dev.sh`, the GNOME extension prefers the repo-local `.venv` interpreter at `<repo>/.venv/bin/python3` and falls back to system `python3` if that environment is unavailable.

## Next technical steps

1. Add preferences for poll interval and which providers appear in the top-bar label.
2. Add optional click actions for opening each CLI or its local logs.
3. Tune the Gemini dropdown presentation so the per-model quota table is easier to scan in a compact menu.
