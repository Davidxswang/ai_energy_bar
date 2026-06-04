"""Generate the menu-bar template icon (`macos/icon.png`).

One-off asset generator with no third-party dependencies (pure stdlib PNG
writer). The icon is an ascending "levels" glyph drawn black-on-transparent so
macOS template rendering tints it for light/dark menu bars. rumps resizes the
image to 20x20pt, so we render at 40x40 for crisp @2x retina display.

Regenerate with:

    uv run python macos/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Final

ICON_PATH: Final[Path] = Path(__file__).resolve().parent / "icon.png"
SIZE: Final[int] = 40
BAR_WIDTH: Final[int] = 5
BASELINE: Final[int] = 33  # bottom row of every bar (inclusive)
# (left x, top y) of each ascending bar; all share BAR_WIDTH and BASELINE.
BARS: Final[tuple[tuple[int, int], ...]] = ((4, 22), (13, 16), (22, 10), (31, 4))


def _write_png(path: Path, width: int, height: int, pixels: bytes) -> None:
    """Write 8-bit RGBA `pixels` (row-major, width*height*4 bytes) as a PNG."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter type 0 (none)
        raw.extend(pixels[y * stride : (y + 1) * stride])
    idat = zlib.compress(bytes(raw), 9)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def _is_filled(x: int, y: int) -> bool:
    return any(bx <= x < bx + BAR_WIDTH and ty <= y <= BASELINE for bx, ty in BARS)


def render() -> bytes:
    pixels = bytearray(SIZE * SIZE * 4)
    for y in range(SIZE):
        for x in range(SIZE):
            if _is_filled(x, y):
                pixels[(y * SIZE + x) * 4 + 3] = 255  # opaque black (RGB stays 0)
    return bytes(pixels)


def ascii_preview(pixels: bytes) -> str:
    rows = []
    for y in range(SIZE):
        row = "".join(
            "#" if pixels[(y * SIZE + x) * 4 + 3] else "." for x in range(SIZE)
        )
        rows.append(row)
    return "\n".join(rows)


def main() -> int:
    pixels = render()
    _write_png(ICON_PATH, SIZE, SIZE, pixels)
    print(ascii_preview(pixels))
    print(f"\nwrote {ICON_PATH} ({ICON_PATH.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
