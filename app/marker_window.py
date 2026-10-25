"""Окно «Маркер в Dota 2»: два цвета (лепестки и остальное), превью и установка в игру."""
from __future__ import annotations

import math
import time
from typing import Callable, Optional, Tuple
from PyQt5.QtCore import QPointF, QTimer, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PyQt5.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QLabel, QWidget
from app.config import ACCENT, DIM, FONT, FONT_SYMBOL
from app.ui_common import ColorPickerWindow, FramelessCard, caption, make_button, outline_button_style

QUICK = ("#ff0055", "#ff6600", "#ffcc00", "#00ff41", "#00ccff", "#0055ff", "#9d00ff", "#ffffff")
Result = Tuple[bool, str]


class MarkerPreview(QWidget):
    """Примерный вид маркера в игре: светящееся кольцо и три маленьких лепестка, сходящихся к центру."""

    def __init__(self, petals: QColor, rest: QColor):
        super().__init__()
        self.petals, self.rest = petals, rest
        self.setFixedSize(180, 150)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(33)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(QColor(52, 49, 48))                           # фон — как земля в игре
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), 10, 10)
        cx, cy, unit = self.width() / 2, self.height() / 2, 62.0
        t = (time.monotonic() % 0.9) / 0.9                      # цикл: лепестки сходятся к центру и гаснут
        p.setCompositionMode(QPainter.CompositionMode_Plus)

        radius = unit * 0.52                                    # кольцо: яркий ободок и мягкое свечение
        for width, alpha in ((18, 0.10), (13, 0.16), (9, 0.30), (5, 0.80)):
            glow = QColor(self.rest)
            glow.setAlphaF(alpha)
            p.setPen(QPen(glow, width))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), radius, radius)
        inner = QColor(self.rest)
        inner.setAlphaF(0.10)
        p.setPen(Qt.NoPen)
        p.setBrush(inner)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        d = 0.30 - 0.26 * t                                     # три маленьких лепестка внутри кольца
        fade = 1.0 - max(0.0, (t - 0.55) / 0.45)
        size = 0.40
        for k in range(3):
            ang = -math.pi / 2 + k * 2 * math.pi / 3
            ex, ey = math.cos(ang), math.sin(ang)
            nx, ny = -ey, ex
            far = d + 0.36 * size
            poly = QPolygonF([
                QPointF(cx + ex * d * unit, cy + ey * d * unit),
                QPointF(cx + (ex * far + nx * 0.27 * size) * unit, cy + (ey * far + ny * 0.27 * size) * unit),
                QPointF(cx + ex * (d + 0.18 * size) * unit, cy + ey * (d + 0.18 * size) * unit),
                QPointF(cx + (ex * far - nx * 0.27 * size) * unit, cy + (ey * far - ny * 0.27 * size) * unit)])
            path = QPainterPath()
            path.addPolygon(poly)
            path.closeSubpath()
            for i in range(3, 0, -1):
                glow = QColor(self.petals)
                glow.setAlphaF(0.16 * fade)
                pen = QPen(glow, 2.5 * i)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
            body = QColor(self.petals)
            body.setAlphaF(fade)
            p.setPen(Qt.NoPen)
            p.setBrush(body)
            p.drawPolygon(poly)
        p.end()


class ColorRow(QWidget):
    """Подпись, быстрые цвета и кнопка выбора любого цвета."""

    def __init__(self, title: str, color: QColor, on_change: Callable[[QColor], None]):
        super().__init__()
        self._on_change = on_change
        self.color = QColor(color)
        self._picker: Optional[ColorPickerWindow] = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        lbl = caption(title, size=9)
        lbl.setFixedWidth(78)
        layout.addWidget(lbl)
        for hex_code in QUICK:
            btn = make_button(size=(20, 20), style=f"background-color: {hex_code}; border-radius: 4px; border: 1px solid #333;")
            btn.clicked.connect(lambda _=False, c=hex_code: self.set_color(QColor(c)))
            layout.addWidget(btn)
        more = make_button("…", size=(22, 20), style=(
            f"QPushButton {{ background: rgba(0,255,200,0.15); color: {ACCENT}; border: 1px solid {ACCENT};"
            " border-radius: 4px; font-weight: bold; } QPushButton:hover { background: " + ACCENT + "; color: #000; }"))
        more.setToolTip("Любой цвет")
        more.clicked.connect(self._pick)
        layout.addWidget(more)
        self.swatch = make_button(size=(26, 20))
        self.swatch.setEnabled(False)
        layout.addWidget(self.swatch)
        self._refresh()

    def _refresh(self):
        self.swatch.setStyleSheet(f"background-color: {self.color.name()}; border-radius: 4px; border: 2px solid #fff;")

    def set_color(self, color: QColor):
        self.color = QColor(color)
        self._refresh()
        self._on_change(self.color)

    def _pick(self):
        """Наш круг цветов, как для шлейфа: выезжает сбоку от окна, цвет меняется сразу."""
        if self._picker:
            self._picker.close()
            return
        win = self.window()
        self._picker = ColorPickerWindow(self.color, win)
        self._picker.colorPicked.connect(self.set_color)
        self._picker.finished.connect(self._picker_closed)
        frame = win.frameGeometry()
        area = (QApplication.screenAt(frame.center()) or QApplication.primaryScreen()).availableGeometry()
        x, direction = frame.right() + 10, -1
        if x + self._picker.width() > area.right():     # справа нет места — ставим слева
            x, direction = frame.left() - self._picker.width() - 10, 1
        y = max(area.top(), min(self.mapToGlobal(self.rect().topLeft()).y() - 100, area.bottom() - self._picker.height()))
        self._picker.set_anchor(x, y, direction)
        self._picker.slide_in()

    def _picker_closed(self, *_):
        self._picker = None


class MarkerDialog(FramelessCard):
    def __init__(self, petals: str, rest: str, chevron: bool, installed: bool,
                 on_apply: Callable[[str, str, bool], Result], on_revert: Callable[[], Result], parent=None):
        super().__init__((470, 475), radius=16, border_width=2, parent=parent, starry=True)
        self._on_apply, self._on_revert = on_apply, on_revert
        self.body.setContentsMargins(20, 18, 20, 18)
        self.body.setSpacing(10)

        title = QLabel("МАРКЕР ВНУТРИ DOTA 2")
        title.setFont(QFont(FONT, 11, QFont.Bold))
        title.setStyleSheet(f"color: {ACCENT};")
        title.setAlignment(Qt.AlignCenter)
        self.body.addWidget(title)

        self.preview = MarkerPreview(QColor(petals), QColor(rest))
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.preview)
        row.addStretch()
        self.body.addLayout(row)
        hint = caption("Примерный вид, настоящий рисует сама Dota", size=8, color=DIM)
        hint.setAlignment(Qt.AlignCenter)
        self.body.addWidget(hint)

        self.row_petals = ColorRow("Лепестки", QColor(petals), self._set_petals)
        self.row_rest = ColorRow("Остальное", QColor(rest), self._set_rest)
        self.body.addWidget(self.row_petals)
        self.body.addWidget(self.row_rest)

        self.chk_shape = QCheckBox("Наша форма лепестков (шевроны)")
        self.chk_shape.setChecked(chevron)
        self.chk_shape.setFont(QFont(FONT, 9))
        self.chk_shape.setStyleSheet("color: #ddd;")
        self.body.addWidget(self.chk_shape)

        self.lbl_status = caption("", size=9, color=DIM)
        self.lbl_status.setWordWrap(True)
        self.body.addWidget(self.lbl_status)
        self._set_status(installed)

        buttons = QHBoxLayout()
        btn_apply = make_button("✦ ПРИМЕНИТЬ В ИГРУ", height=34, font=QFont(FONT_SYMBOL, 9, QFont.Bold),
                                style=outline_button_style(ACCENT, radius=14))
        btn_apply.clicked.connect(self._apply)
        btn_revert = make_button("↺ ВЕРНУТЬ КАК БЫЛО", height=34, font=QFont(FONT_SYMBOL, 9, QFont.Bold),
                                 style=outline_button_style(DIM, radius=14))
        btn_revert.clicked.connect(self._revert)
        buttons.addWidget(btn_apply, 1)
        buttons.addWidget(btn_revert, 1)
        self.body.addLayout(buttons)

        btn_close = make_button("Закрыть", height=28, font=QFont(FONT, 9), style=outline_button_style(DIM, radius=12))
        btn_close.clicked.connect(self.accept)
        self.body.addWidget(btn_close)

    def _set_petals(self, color: QColor):
        self.preview.petals = QColor(color)

    def _set_rest(self, color: QColor):
        self.preview.rest = QColor(color)

    def _set_status(self, installed: bool, note: str = ""):
        text = "Сейчас в игре: свой маркер установлен" if installed else "Сейчас в игре: стандартный маркер"
        self.lbl_status.setText(text + (f"\n{note}" if note else ""))

    def _apply(self):
        ok, err = self._on_apply(self.row_petals.color.name(), self.row_rest.color.name(), self.chk_shape.isChecked())
        self._set_status(ok, "Перезапусти Dota 2, чтобы увидеть." if ok else err)

    def _revert(self):
        ok, err = self._on_revert()
        self._set_status(not ok, err if not ok else "Вернули стандартный маркер. Перезапусти Dota 2.")
