#!/usr/bin/env python3
"""Thin launcher for the probe; provider logic lives in the `_probe` subpackage.

The GNOME extension spawns this file directly (`python probe.py`), in which case
there is no enclosing package — we add the extension directory to `sys.path` so
`_probe` is importable as a top-level package. Under pytest the same file is
loaded as `extension.probe`, and relative imports work normally.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__:
    from ._probe.snapshot import main
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    # `_probe` is only importable at runtime (after the sys.path insert above);
    # ty can't see it statically. Same module as the package path used in the if branch.
    from _probe.snapshot import main  # noqa: E402  # ty: ignore[unresolved-import]


if __name__ == "__main__":
    raise SystemExit(main())
