"""Generate email_sender/assets/EmailSender.ico from the runtime-drawn icon.

Renders the envelope at several sizes with Qt and packs them into a single
multi-resolution .ico (PNG-compressed entries, valid on Windows Vista+), so we
don't need Pillow or an external asset. Run once (or when the icon changes):

    python -m email_sender.tools.make_icon
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

SIZES = (16, 32, 48, 64, 128, 256)


def _render(size: int) -> bytes:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    body = QRectF(8 * s, 16 * s, 48 * s, 32 * s)
    p.setBrush(QBrush(QColor("#2563eb")))
    p.setPen(QPen(QColor("#1e40af"), max(1.0, 2 * s)))
    p.drawRoundedRect(body, 4 * s, 4 * s)
    p.setPen(QPen(QColor("#ffffff"), max(1.0, 2 * s)))
    p.drawPolyline([QPointF(10 * s, 19 * s), QPointF(32 * s, 36 * s), QPointF(54 * s, 19 * s)])
    p.end()

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    pm.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def build_ico(path: Path) -> None:
    images = [(sz, _render(sz)) for sz in SIZES]
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)  # reserved, type=icon, count
    offset = 6 + count * 16
    entries = b""
    data = b""
    for sz, png in images:
        w = 0 if sz >= 256 else sz
        h = 0 if sz >= 256 else sz
        entries += struct.pack(
            "<BBBBHHII", w, h, 0, 0, 1, 32, len(png), offset
        )
        data += png
        offset += len(png)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + data)


def main() -> None:
    app = QApplication.instance() or QApplication([])
    out = Path(__file__).resolve().parents[1] / "assets" / "EmailSender.ico"
    build_ico(out)
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
