"""A simple, dependency-free envelope icon drawn at runtime.

Phase 5 can swap in a proper .ico asset; this keeps the app self-contained for
development and tray use. Requires a running QApplication.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap

_ICO_PATH = Path(__file__).resolve().parents[1] / "assets" / "EmailSender.ico"


def app_icon() -> QIcon:
    # Prefer the bundled multi-resolution .ico when present.
    if _ICO_PATH.exists():
        icon = QIcon(str(_ICO_PATH))
        if not icon.isNull():
            return icon

    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    # Envelope body.
    body = QRectF(8, 16, 48, 32)
    p.setBrush(QBrush(QColor("#2563eb")))
    p.setPen(QPen(QColor("#1e40af"), 2))
    p.drawRoundedRect(body, 4, 4)

    # Flap.
    p.setPen(QPen(QColor("#ffffff"), 2))
    p.drawPolyline([QPointF(10, 19), QPointF(32, 36), QPointF(54, 19)])

    p.end()
    return QIcon(pm)
