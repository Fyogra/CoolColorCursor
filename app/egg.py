"""Маленький сюрприз: окошко с вращающейся фигуркой."""
from __future__ import annotations

import os
import time
from PyQt5.QtCore import QBuffer, QByteArray, Qt
from PyQt5.QtGui import QMovie
from PyQt5.QtWidgets import QDialog, QLabel, QVBoxLayout

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "bg_0.dat")


class EggDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        with open(_FILE, "rb") as f:
            self._data = QByteArray(f.read())
        self._buf = QBuffer(self._data, self)
        self._buf.open(QBuffer.ReadOnly)
        self._movie = QMovie(self._buf, QByteArray(b"gif"), self)
        label = QLabel(self)
        label.setMovie(self._movie)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(label)
        self._movie.start()
        self.adjustSize()

    def mousePressEvent(self, _event):
        self.close()


class EggTrigger:
    """Считает быстрые клики; пора показывать — возвращает True."""

    def __init__(self, count: int = 7, window: float = 4.0):
        self.count, self.window, self._times = count, window, []

    def hit(self) -> bool:
        now = time.monotonic()
        self._times = [t for t in self._times if now - t < self.window] + [now]
        if len(self._times) >= self.count:
            self._times = []
            return True
        return False
