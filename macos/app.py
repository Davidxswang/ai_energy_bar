"""macOS menu bar front-end for ai_energy_bar (rumps / NSStatusItem).

Reuses the exact same local probe as the GNOME extension by running
`extension/probe.py` as a subprocess and rendering its JSON snapshot — no probe
logic is duplicated here. macOS-only: depends on `rumps`, which only installs on
macOS (`uv sync --extra macos`). Launch from the repo root with:

    uv run python -m macos.app

The probe runs in a worker thread with a hard timeout so a slow Claude probe
never freezes the menu bar; completed results are applied on the main thread via
a fast drain timer (AppKit UI must be touched only from the main thread).
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import threading
from pathlib import Path

import rumps  # ty: ignore[unresolved-import]  # macOS-only optional dep, absent on Linux/CI

from extension._probe.models import JSONValue
from macos.render import (
    POLL_INTERVAL_SECONDS,
    PROBE_TIMEOUT_SECONDS,
    PROVIDER_ORDER,
    header_text,
    merge_with_last,
    provider_lines,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_PY = REPO_ROOT / "extension" / "probe.py"
ICON_PATH = Path(__file__).resolve().parent / "icon.png"
LOG_PATH = Path.home() / "Library" / "Logs" / "ai-energy-bar.log"

logger = logging.getLogger("ai_energy_bar.macos")


def configure_logging() -> None:
    handler: logging.Handler
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_PATH)
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s"
        )
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def run_probe() -> dict[str, JSONValue]:
    """Run extension/probe.py with the current interpreter and parse its JSON."""
    proc = subprocess.run(
        [sys.executable, str(PROBE_PY)],
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_SECONDS,
        check=True,
    )
    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise ValueError("probe did not return a JSON object")
    return data


class AiEnergyBar(rumps.App):
    def __init__(self) -> None:
        # Icon-only in the menu bar (template image tints for light/dark); all
        # quota values live in the dropdown. Falls back to the name text if the
        # icon file can't be loaded.
        super().__init__(
            "AI Energy Bar",
            icon=str(ICON_PATH),
            template=True,
            quit_button="Quit",
        )
        self._last_snapshot: dict[str, JSONValue] | None = None
        self._results: queue.Queue[tuple[str, object]] = queue.Queue()
        self._refresh_in_flight = False

        # Fixed menu skeleton with unique initial titles (rumps keys items by the
        # title at insertion). We hold references and only mutate `.title` later.
        self._header = rumps.MenuItem("Starting…")
        self._provider_items = {key: rumps.MenuItem(key) for key in PROVIDER_ORDER}
        self.menu = [
            self._header,
            None,
            *self._provider_items.values(),
            None,
            rumps.MenuItem("Refresh Now", callback=self._on_refresh_clicked),
        ]

        self._poll_timer = rumps.Timer(self._on_poll, POLL_INTERVAL_SECONDS)
        self._poll_timer.start()
        self._drain_timer = rumps.Timer(self._on_drain, 1)
        self._drain_timer.start()
        self._trigger_refresh()

    def _on_poll(self, _timer: object) -> None:
        self._trigger_refresh()

    def _on_refresh_clicked(self, _sender: object) -> None:
        self._trigger_refresh()

    def _on_drain(self, _timer: object) -> None:
        try:
            status, payload = self._results.get_nowait()
        except queue.Empty:
            return
        self._refresh_in_flight = False
        if status == "ok" and isinstance(payload, dict):
            self._apply_snapshot(payload)
        else:
            self._apply_error(str(payload))

    def _trigger_refresh(self) -> None:
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        self._header.title = "Refreshing local CLI status…"
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            self._results.put(("ok", run_probe()))
        except (
            subprocess.SubprocessError,
            OSError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            logger.exception("probe failed")
            self._results.put(("error", str(exc) or "probe failed"))

    def _apply_snapshot(self, snapshot: dict[str, JSONValue]) -> None:
        snapshot = merge_with_last(snapshot, self._last_snapshot)
        self._last_snapshot = snapshot
        self.title = ""  # icon only; details are in the dropdown
        self._header.title = header_text(snapshot)
        for key, item in self._provider_items.items():
            item.title = "\n".join(provider_lines(snapshot, key))

    def _apply_error(self, message: str) -> None:
        self.title = "!"  # short alert badge beside the icon
        self._header.title = f"Probe error: {message}"
        for item in self._provider_items.values():
            item.title = "Unavailable"
        logger.error("probe error: %s", message)


def main() -> int:
    configure_logging()
    logger.info("starting ai_energy_bar macOS menu bar app")
    AiEnergyBar().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
