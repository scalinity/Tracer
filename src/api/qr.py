"""The QR code for the public demo address (plan task P7).

    python -m src.api.qr                      # writes docs/demo/qr.png

The demo is a static page on GitHub Pages, published from the `docs` folder of `main`, so the project page at
https://scalinity.github.io/Tracer/ serves docs/demo/index.html at /demo/. The code is plain black on white
with a four-module quiet zone and medium error correction, so it scans from the back of a room and survives a
projector. It is deterministic: the same address always gives the same file, and a test regenerates it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_M

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_URL = "https://scalinity.github.io/Tracer/demo/"
OUT_PATH = ROOT / "docs" / "demo" / "qr.png"
BOX_PIXELS = 16  # a module is 16 pixels, so the whole code is about 640 pixels wide
BORDER_MODULES = 4


def build(url: str = PUBLIC_URL) -> qrcode.QRCode:
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=BOX_PIXELS, border=BORDER_MODULES)
    code.add_data(url)
    code.make(fit=True)
    return code


def write(path: Path = OUT_PATH, url: str = PUBLIC_URL) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    build(url).make_image(fill_color="black", back_color="white").save(path, format="PNG", optimize=False)
    return path


def main() -> int:
    path = write()
    code = build()
    print(f"wrote {path.relative_to(ROOT)}: {PUBLIC_URL} as a version {code.version} code, {len(code.get_matrix())} modules wide")
    return 0


if __name__ == "__main__":
    sys.exit(main())
