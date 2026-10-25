"""Общие хелперы и базовые виджеты интерфейса."""
from __future__ import annotations

import math
import random
from PyQt5.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QTimer, Qt, pyqtProperty, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QConicalGradient, QFont, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt5.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QStyleOptionSlider, QVBoxLayout, QWidget
from app.config import ACCENT, BG, BORDER, FONT, MUTED, TEXT


# ──────────────────────────────────────────────────────────────────────────────
#  Хелперы интерфейса
# ──────────────────────────────────────────────────────────────────────────────
SLIDER_STYLE = f"""
    QSlider::groove:horizontal {{ height: 4px; background: rgba(28, 28, 43, 0.8); border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: #ffffff; width: 14px; margin: -5px 0; border-radius: 7px; }}
"""

VALUE_SLIDER_STYLE = """
    QSlider::groove:horizontal { height: 6px; border-radius: 3px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #000, stop:1 #fff); }
    QSlider::handle:horizontal { background: #fff; width: 12px; margin: -3px 0; border-radius: 6px; }
"""


def outline_button_style(color: str, hover_text: str = "#000000", radius: int = 18) -> str:
    return (f"QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a1b26, stop:1 #12131c);"
            f" color: {color}; border: 2px solid {color}; border-radius: {radius}px; }}"
            f" QPushButton:hover {{ background: {color}; color: {hover_text}; }}")


def gradient_button_style(c1: str, c2: str, border: str = "#333") -> str:
    return (f"QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c1}, stop:1 {c2});"
            f" border: 1px solid {border}; border-radius: 6px; }}"
            f" QPushButton:hover {{ border: 1px solid #ffffff; }}")


def make_button(text="", *, size=None, height=None, font=None, style="") -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    if size:
        btn.setFixedSize(*size)
    if height:
        btn.setFixedHeight(height)
    if font:
        btn.setFont(font)
    if style:
        btn.setStyleSheet(style)
    return btn


def caption(text: str, size: int = 10, color: str = MUTED) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {color}; font-size: {size}px; font-weight: bold; background: transparent;")
    return lbl


def readable_text_color(bg: QColor) -> str:
    luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return "#000000" if luminance > 150 else "#ffffff"


# ──────────────────────────────────────────────────────────────────────────────
#  Базовые виджеты
# ──────────────────────────────────────────────────────────────────────────────
class FramelessCard(QDialog):
    """Безрамочный диалог с тёмной скруглённой карточкой. Содержимое — в self.body."""

    def __init__(self, size, radius, border_width, parent=None, starry: bool = False):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(*size)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        if starry:                  # фон со звёздами, как у главного окна
            card = StarryBackground(self)
            card.RADIUS = radius
        else:
            card = QFrame(self)
            card.setObjectName("card")  # селектор по имени, чтобы стиль не «протекал» на QLabel
            card.setStyleSheet(f"#card {{ background-color: {BG}; border-radius: {radius}px;"
                               f" border: {border_width}px solid {ACCENT}; }}")
        outer.addWidget(card)
        self.body = QVBoxLayout(card)


class ClickSlider(QSlider):
    """Ползунок, который прыгает в точку клика, а не двигается шагами."""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            groove = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
            handle = self.style().pixelMetric(QStyle.PM_SliderLength, opt, self)

            if self.orientation() == Qt.Horizontal:
                pos, start, span = event.pos().x(), groove.x(), groove.width()
            else:
                pos, start, span = event.pos().y(), groove.y(), groove.height()

            self.setValue(QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), pos - handle // 2 - start, span - handle, opt.upsideDown))
            event.accept()
        super().mousePressEvent(event)  # ручка уже под курсором — дальше работает обычное перетаскивание


class DragHeader(QFrame):
    """Шапка, за которую можно таскать всё окно."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._offset = event.globalPos() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._offset is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPos() - self._offset)

    def mouseReleaseEvent(self, event):
        self._offset = None


class ColorWheel(QWidget):
    """Цветовое колесо (оттенок + насыщенность); яркость задаётся снаружи."""

    colorChanged = pyqtSignal(QColor)
    MARGIN = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(180, 180)
        self.hue, self.sat, self.val = 0.0, 1.0, 1.0
        self.current_color = QColor.fromHsvF(self.hue, self.sat, self.val)
        self._dragging = False

    def _geometry(self):
        center = QPointF(self.width() / 2, self.height() / 2)
        return center, min(self.width(), self.height()) / 2 - self.MARGIN

    def set_color(self, color: QColor):
        h, s, v, _ = color.getHsvF()
        if h >= 0:  # у серых/чёрных/белых оттенок не определён — оставляем прежний
            self.hue = h
        self.sat, self.val = s, v
        self.current_color = QColor(color)
        self.update()

    def set_value(self, value: float):
        self.val = max(0.0, min(1.0, value))
        self.current_color = QColor.fromHsvF(self.hue, self.sat, self.val)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        center, radius = self._geometry()

        conical = QConicalGradient(center, 0)
        for deg in range(0, 360, 10):
            conical.setColorAt(deg / 360.0, QColor.fromHsvF(deg / 360.0, 1.0, self.val))
        conical.setColorAt(1.0, QColor.fromHsvF(0.0, 1.0, self.val))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(conical))
        p.drawEllipse(center, radius, radius)

        radial = QRadialGradient(center, radius)
        radial.setColorAt(0.0, QColor.fromHsvF(0.0, 0.0, self.val))
        radial.setColorAt(1.0, QColor.fromHsvF(0.0, 0.0, self.val, 0.0))
        p.setBrush(QBrush(radial))
        p.drawEllipse(center, radius, radius)

        angle = math.radians(self.hue * 360.0)
        dist = self.sat * radius
        marker = QPointF(center.x() + dist * math.cos(angle), center.y() - dist * math.sin(angle))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 150), 3))
        p.drawEllipse(marker, 7, 7)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawEllipse(marker, 6, 6)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._pick(event.pos())

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._pick(event.pos())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def _pick(self, pos):
        center, radius = self._geometry()
        dx, dy = pos.x() - center.x(), center.y() - pos.y()
        self.sat = min(1.0, math.hypot(dx, dy) / radius)
        angle = math.atan2(dy, dx)
        if angle < 0:
            angle += 2 * math.pi
        self.hue = angle / (2 * math.pi)
        self.current_color = QColor.fromHsvF(self.hue, self.sat, self.val)
        self.update()
        self.colorChanged.emit(self.current_color)


class ColorPickerWindow(FramelessCard):
    colorPicked = pyqtSignal(QColor)

    def __init__(self, initial_color: QColor, parent=None):
        super().__init__((220, 260), radius=12, border_width=1, parent=parent)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.body.setAlignment(Qt.AlignCenter)

        header = QHBoxLayout()
        btn_close = make_button("✕", size=(20, 20),
                                style="QPushButton { color: #aaa; border: none; font-weight: bold; }")
        btn_close.clicked.connect(self.close)
        header.addWidget(caption("ВЫБОР ЦВЕТА", color=ACCENT))
        header.addStretch()
        header.addWidget(btn_close)
        self.body.addLayout(header)

        self.wheel = ColorWheel()
        self.wheel.colorChanged.connect(self.colorPicked.emit)
        self.body.addWidget(self.wheel)

        self.val_slider = ClickSlider(Qt.Horizontal)
        self.val_slider.setRange(0, 100)
        self.val_slider.setStyleSheet(VALUE_SLIDER_STYLE)
        self.val_slider.valueChanged.connect(self._on_value_changed)
        self.body.addWidget(self.val_slider)

        self.set_color(initial_color)

        # Выезд: окно проезжает SLIDE_PX от панели на своё место и плавно проявляется
        self._anchor = (0, 0)
        self._dir = -1
        self._t = 0.0
        self._closing = False
        self.setWindowOpacity(0.0)
        self._anim = QPropertyAnimation(self, b"slide", self)
        self._anim.setDuration(self.SLIDE_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    SLIDE_PX, SLIDE_MS = 36, 220

    def _get_slide(self) -> float:
        return self._t

    def _set_slide(self, value: float):
        self._t = value
        self.setWindowOpacity(value)
        self._apply_pos()

    slide = pyqtProperty(float, _get_slide, _set_slide)

    def _apply_pos(self):
        x, y = self._anchor
        self.move(int(x + self._dir * self.SLIDE_PX * (1.0 - self._t)), y)

    def set_anchor(self, x: int, y: int, direction: int) -> None:
        """Место окна; direction: -1 — окно справа от панели (выезжает слева), +1 — слева (выезжает справа)."""
        self._anchor, self._dir = (x, y), direction
        self._apply_pos()

    def slide_in(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0)
        self.show()
        self._anim.start()

    def closeEvent(self, event):
        if self._closing or not self.isVisible():
            super().closeEvent(event)
            return
        event.ignore()  # сначала плавно задвигаем, потом закрываем по-настоящему
        self._closing = True
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.close)
        self._anim.start()

    def _on_value_changed(self, value: int):
        self.wheel.set_value(value / 100.0)
        self.colorPicked.emit(self.wheel.current_color)

    def set_color(self, color: QColor):
        """Программная смена цвета — без сигналов, чтобы выбранный цвет не «плыл» от округлений."""
        self.wheel.set_color(color)
        self.val_slider.blockSignals(True)
        self.val_slider.setValue(int(round(color.valueF() * 100)))
        self.val_slider.blockSignals(False)


class AutostartDialog(FramelessCard):
    def __init__(self, parent=None):
        super().__init__((320, 180), radius=16, border_width=2, parent=parent)
        self.body.setContentsMargins(20, 20, 20, 20)
        self.body.setSpacing(15)

        title = QLabel("АВТОЗАПУСК")
        title.setFont(QFont(FONT, 11, QFont.Bold))
        title.setStyleSheet(f"color: {ACCENT};")
        title.setAlignment(Qt.AlignCenter)

        text = QLabel("Хотите ли вы добавить приложение\nв автозапуск Windows?")
        text.setFont(QFont(FONT, 9))
        text.setStyleSheet(f"color: {TEXT};")
        text.setAlignment(Qt.AlignCenter)

        btn_yes = make_button("ДА", height=32, font=QFont(FONT, 9, QFont.Bold), style=(
            f"QPushButton {{ background: {ACCENT}; color: #000; border: none; border-radius: 8px; }}"
            " QPushButton:hover { background: #00cc99; }"))
        btn_no = make_button("НЕТ", height=32, font=QFont(FONT, 9, QFont.Bold), style=(
            "QPushButton { background: #222233; color: #888; border: 1px solid #444455; border-radius: 8px; }"
            " QPushButton:hover { background: #333344; color: #fff; }"))
        btn_yes.clicked.connect(self.accept)
        btn_no.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addWidget(btn_yes)
        buttons.addWidget(btn_no)

        self.body.addWidget(title)
        self.body.addWidget(text)
        self.body.addLayout(buttons)


# ──────────────────────────────────────────────────────────────────────────────
#  Информационное окно
# ──────────────────────────────────────────────────────────────────────────────
class InfoDialog(FramelessCard):
    def __init__(self, title: str, text: str, parent=None, size=(460, 290), ok: str = "ОК", cancel: str = ""):
        super().__init__(size, radius=16, border_width=2, parent=parent)
        self.body.setContentsMargins(20, 20, 20, 20)
        self.body.setSpacing(12)

        lbl_title = QLabel(title)
        lbl_title.setFont(QFont(FONT, 11, QFont.Bold))
        lbl_title.setStyleSheet(f"color: {ACCENT};")
        lbl_title.setAlignment(Qt.AlignCenter)

        lbl_text = QLabel(text)
        lbl_text.setFont(QFont(FONT, 9))
        lbl_text.setStyleSheet(f"color: {TEXT};")
        lbl_text.setWordWrap(True)
        lbl_text.setAlignment(Qt.AlignCenter)
        lbl_text.setTextInteractionFlags(Qt.TextSelectableByMouse)  # путь можно выделить и скопировать

        btn_ok = make_button(ok, height=32, font=QFont(FONT, 9, QFont.Bold), style=(
            f"QPushButton {{ background: {ACCENT}; color: #000; border: none; border-radius: 8px; }}"
            " QPushButton:hover { background: #00cc99; }"))
        btn_ok.clicked.connect(self.accept)

        self.body.addWidget(lbl_title)
        self.body.addWidget(lbl_text, 1)
        if cancel:  # вопрос: «Отмена» слева, подтверждение справа
            btn_cancel = make_button(cancel, height=32, font=QFont(FONT, 9), style=(
                f"QPushButton {{ background: transparent; color: {TEXT}; border: 1px solid {MUTED};"
                " border-radius: 8px; } QPushButton:hover { background: #222233; }"))
            btn_cancel.clicked.connect(self.reject)
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(btn_cancel)
            row.addWidget(btn_ok)
            self.body.addLayout(row)
        else:
            self.body.addWidget(btn_ok)


# ──────────────────────────────────────────────────────────────────────────────
#  Фон панели
# ──────────────────────────────────────────────────────────────────────────────
class Star:
    __slots__ = ("x", "y", "size", "alpha", "speed", "delta")

    def __init__(self):
        self.x = random.random()                 # координаты — доли от размера панели
        self.y = random.random()
        self.size = random.uniform(1.0, 2.5)
        self.alpha = random.randint(50, 255)
        self.speed = random.uniform(0.2, 0.6) / 450.0
        self.delta = random.choice((-2, -1, 1, 2))


class StarryBackground(QFrame):
    """Тёмная скруглённая панель с мерцающими звёздами. Анимация идёт только пока окно видно."""

    RADIUS = 20
    STAR_COUNT = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stars = [Star() for _ in range(self.STAR_COUNT)]
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._animate)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start(30)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()  # свёрнуты в трей — не тратим CPU

    def _animate(self):
        for s in self.stars:
            s.y -= s.speed
            if s.y < 0:
                s.y, s.x = 1.0, random.random()
            s.alpha += s.delta
            if s.alpha >= 255 or s.alpha <= 40:
                s.delta = -s.delta
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)
        p.setClipPath(shape)
        p.fillPath(shape, QColor(BG))

        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        for s in self.stars:
            p.setBrush(QColor(255, 255, 255, max(0, min(255, s.alpha))))
            p.drawEllipse(QPointF(s.x * w, s.y * h), s.size, s.size)

        p.setClipping(False)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawPath(shape)
