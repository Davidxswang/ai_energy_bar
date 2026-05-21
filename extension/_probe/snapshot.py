from __future__ import annotations

import argparse
import json
from datetime import datetime

from .claude import claude_status
from .codex import codex_status
from .gemini import gemini_status
from .models import JSONValue


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
