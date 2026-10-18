# -*- coding: utf-8 -*-
"""
CCC | CoolColorCursor
Неоновый шлейф за курсором мыши поверх всех окон. Windows, PyQt5.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import sys
import tempfile
import time
from collections import deque
from functools import lru_cache
from logging.handlers import RotatingFileHandler

from PyQt5.QtCore import QPointF, QRect, QRectF, QSharedMemory, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QConicalGradient, QCursor, QFont, QIcon,
                         QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                         QRadialGradient)
from PyQt5.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMenu,
                             QPushButton, QSlider, QStyle, QStyleOptionSlider,
                             QSystemTrayIcon, QVBoxLayout, QWidget)

# ──────────────────────────────────────────────────────────────────────────────
#  Платформа (WinAPI нужен только для «сквозного» окна и автозапуска)
# ──────────────────────────────────────────────────────────────────────────────
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    import winreg
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    _user32.SetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.SetWindowPos.restype = wintypes.BOOL

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

# ──────────────────────────────────────────────────────────────────────────────
#  Константы, пути, логирование
# ──────────────────────────────────────────────────────────────────────────────
APP_NAME = "CCC"
APP_TITLE = "CCC|CoolColorCursor"
REG_AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# Тема
BG = "#111118"
BORDER = "#222233"
ACCENT = "#00ffc8"
DANGER = "#ff0055"
TEXT = "#dddddd"
MUTED = "#aaaaaa"
DIM = "#666666"
FONT = "Segoe UI"
FONT_SYMBOL = "Segoe UI Symbol"

DEFAULT_C1 = "#00ccff"
DEFAULT_C2 = "#ff0080"
HISTORY_SIZE = 5

log = logging.getLogger("ccc")


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _data_dir() -> str:
    """Папка для конфига и лога: рядом с программой, а если туда нельзя писать — в %APPDATA%."""
    base = _app_dir()
    if os.access(base, os.W_OK):
        return base
    fallback = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    try:
        os.makedirs(fallback, exist_ok=True)
        return fallback
    except OSError:
        return tempfile.gettempdir()


DATA_DIR = _data_dir()
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LOG_FILE = os.path.join(DATA_DIR, "mouse_trail.log")
ICON_FILE = os.path.join(DATA_DIR, "app_icon.ico")


def setup_logging() -> None:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)

    def log_uncaught(exc_type, exc, tb):
        # Без своего excepthook PyQt5 роняет процесс на любом исключении внутри слота
        log.critical("Необработанное исключение", exc_info=(exc_type, exc, tb))

    sys.excepthook = log_uncaught


# ──────────────────────────────────────────────────────────────────────────────
#  Автозапуск
# ──────────────────────────────────────────────────────────────────────────────
def autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # Запуск .py напрямую открыл бы его в ассоциированной программе, поэтому — pythonw
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    interpreter = pythonw if os.path.exists(pythonw) else sys.executable
    return f'"{interpreter}" "{os.path.abspath(__file__)}"'


def set_autostart(enable: bool) -> bool:
    """Добавляет/убирает программу из автозапуска. Возвращает True при успехе."""
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_AUTOSTART_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
                log.info("Добавлено в автозагрузку")
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    log.info("Удалено из автозагрузки")
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        log.error("Ошибка настройки автозагрузки: %s", e)
        return False


# ──────────────────────────────────────────────────────────────────────────────
#  Настройки
# ──────────────────────────────────────────────────────────────────────────────
def _clamp(value, lo, hi, default, scale=1.0):
    try:
        return max(lo, min(hi, int(round(float(value) * scale))))
    except (TypeError, ValueError):
        return default


def _color(value, default) -> QColor:
    color = QColor(str(value))
    return color if color.isValid() else QColor(default)


class Settings:
    """Настройки приложения. Формат config.json совместим со старой версией."""

    def __init__(self):
        self.colors = [QColor(DEFAULT_C1), QColor(DEFAULT_C2)]
        self.max_width = 12
        self.fade_ms = 400
        self.autostart_asked = False
        self.autostart_enabled = False
        self.history = []  # [(QColor, QColor), ...], свежие — в начале

    @classmethod
    def load(cls, path: str) -> "Settings":
        s = cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("корень конфигурации — не объект")
        except FileNotFoundError:
            return s
        except (OSError, ValueError) as e:
            log.error("Ошибка загрузки конфигурации: %s", e)
            return s

        s.colors = [_color(raw.get("color1"), DEFAULT_C1), _color(raw.get("color2"), DEFAULT_C2)]
        s.max_width = _clamp(raw.get("max_width", 12), 2, 50, 12)
        s.fade_ms = _clamp(raw.get("fade_time", 0.4), 100, 1500, 400, scale=1000)
        s.autostart_asked = bool(raw.get("autostart_asked", False))
        s.autostart_enabled = bool(raw.get("autostart_enabled", False))

        history = raw.get("history")
        if isinstance(history, list):
            for item in history[:HISTORY_SIZE]:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    a, b = QColor(str(item[0])), QColor(str(item[1]))
                    if a.isValid() and b.isValid():
                        s.history.append((a, b))
        log.info("Конфигурация загружена")
        return s

    def save(self, path: str) -> None:
        data = {
            "color1": self.colors[0].name(),
            "color2": self.colors[1].name(),
            "max_width": self.max_width,
            "fade_time": self.fade_ms / 1000.0,
            "autostart_asked": self.autostart_asked,
            "autostart_enabled": self.autostart_enabled,
            "history": [[a.name(), b.name()] for a, b in self.history],
        }
        tmp = path + ".tmp"
        try:
            # Пишем во временный файл и подменяем — конфиг не побьётся при сбое посреди записи
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            os.replace(tmp, path)
            log.info("Конфигурация сохранена")
        except OSError as e:
            log.error("Ошибка сохранения конфигурации: %s", e)


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


def outline_button_style(color: str, hover_text: str = "#000000") -> str:
    return (f"QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a1b26, stop:1 #12131c);"
            f" color: {color}; border: 2px solid {color}; border-radius: 18px; }}"
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
#  Иконка приложения
# ──────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Рисует неоновую иконку (один раз) и при необходимости сохраняет её как .ico."""
    size = 128
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing, True)

    bg = QPainterPath()
    bg.addRoundedRect(4, 4, 120, 120, 32, 32)
    p.fillPath(bg, QColor(BG))
    p.setPen(QPen(QColor("#222238"), 3))
    p.drawPath(bg)

    trail = QPainterPath()
    trail.moveTo(36, 36)
    trail.cubicTo(36, 10, 85, 10, 85, 45)
    trail.cubicTo(85, 90, 20, 85, 105, 108)

    p.setPen(QPen(QColor(255, 0, 128, 110), 14, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(trail)

    grad = QLinearGradient(36, 36, 105, 108)
    grad.setColorAt(0.0, QColor(ACCENT))
    grad.setColorAt(0.5, QColor("#ff0080"))
    grad.setColorAt(1.0, QColor("#9d00ff"))
    p.setPen(QPen(QBrush(grad), 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(trail)

    cursor = QPainterPath()
    cursor.moveTo(36, 36)
    for x, y in ((36, 80), (48, 68), (60, 92), (70, 87), (58, 62), (74, 62)):
        cursor.lineTo(x, y)
    cursor.closeSubpath()

    p.setBrush(QBrush(QColor("#161622")))
    p.setPen(QPen(QColor(ACCENT), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(cursor)

    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor("#ffffff")))
    p.drawEllipse(QPointF(36, 36), 4.5, 4.5)
    p.end()

    if not os.path.exists(ICON_FILE):
        try:
            pixmap.save(ICON_FILE, "ICO")
        except Exception as e:  # noqa: BLE001 — иконка не критична
            log.warning("Не удалось сохранить иконку: %s", e)
    return QIcon(pixmap)


# ──────────────────────────────────────────────────────────────────────────────
#  Базовые виджеты
# ──────────────────────────────────────────────────────────────────────────────
class FramelessCard(QDialog):
    """Безрамочный диалог с тёмной скруглённой карточкой. Содержимое — в self.body."""

    def __init__(self, size, radius, border_width, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(*size)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

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
#  Оверлей со шлейфом
# ──────────────────────────────────────────────────────────────────────────────
class MouseTrailOverlay(QWidget):
    """Прозрачное «сквозное» окно на весь рабочий стол, в котором рисуется шлейф."""

    MAX_POINTS = 400             # жёсткий лимит точек шлейфа
    MAX_INTERP_STEPS = 80        # лимит промежуточных точек за один тик
    FREEZE_GAP = 0.1             # пауза между тиками (с), после которой считаем, что система «подвисла»
    FULL_REPAINT = False         # True — перерисовывать весь экран (если вдруг появятся артефакты)
    KEEP_HEAD_WHEN_IDLE = False  # True — как в старой версии: «точка» остаётся под неподвижным курсором

    def __init__(self, fade_time: float, max_width: int, color1: QColor, color2: QColor):
        super().__init__()
        self.fade_time = fade_time
        self.max_width = max_width
        self.color1 = QColor(color1)
        self.color2 = QColor(color2)

        self.points = deque()  # (QPointF, момент создания)
        self._last_pos = None
        self._last_tick = time.monotonic()
        self._dirty = QRect()  # где шлейф был нарисован в прошлый раз

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowTransparentForInput
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setGeometry(self._desktop_rect())

        self.refresh_rate = self._detect_refresh_rate()
        interval = max(1, int(1000 / self.refresh_rate))
        self.step_distance = max(1.0, 3.0 * (60.0 / self.refresh_rate))
        log.info("Определена герцовка: %s Гц. Интервал таймера: %s мс", self.refresh_rate, interval)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(interval)

    @staticmethod
    def _desktop_rect() -> QRect:
        rect = QRect()
        for screen in QApplication.screens():
            rect = rect.united(screen.geometry())
        # -2 px: окно не должно выглядеть для Windows как полноэкранное приложение
        rect.setHeight(rect.height() - 2)
        return rect

    @staticmethod
    def _detect_refresh_rate() -> float:
        try:
            return max([60.0] + [float(s.refreshRate()) for s in QApplication.screens()])
        except Exception as e:  # noqa: BLE001
            log.error("Ошибка определения герцовки: %s", e)
            return 60.0

    def showEvent(self, event):
        super().showEvent(event)
        if not IS_WINDOWS:
            return
        try:
            hwnd = int(self.winId())
            style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_LAYERED
                                   | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception as e:  # noqa: BLE001
            log.error("Ошибка стилей Windows: %s", e)

    # ── управление ──────────────────────────────────────────────────────────
    def set_colors(self, c1: QColor, c2: QColor):
        self.color1, self.color2 = QColor(c1), QColor(c2)
        self.points.clear()
        self._dirty = QRect()
        self.update()

    def shutdown(self):
        self.timer.stop()
        self.close()
        self.deleteLater()

    # ── логика шлейфа ───────────────────────────────────────────────────────
    def _tick(self):
        now = time.monotonic()
        cur = QPointF(self.mapFromGlobal(QCursor.pos()))

        # Защита от фризов (UAC, нагрузка на систему): не тянем линию через весь экран
        if now - self._last_tick > self.FREEZE_GAP:
            self._last_tick = now
            self._last_pos = cur
            self.points.clear()
            self._invalidate()
            return
        self._last_tick = now

        while self.points and now - self.points[0][1] > self.fade_time:
            self.points.popleft()

        last = self._last_pos
        moved = last is None or cur != last
        if moved and last is not None:
            dist = math.hypot(cur.x() - last.x(), cur.y() - last.y())
            if dist > self.step_distance:
                steps = min(int(dist / self.step_distance), self.MAX_INTERP_STEPS)
                dx, dy = (cur.x() - last.x()) / steps, (cur.y() - last.y()) / steps
                for i in range(1, steps):
                    self.points.append((QPointF(last.x() + dx * i, last.y() + dy * i), now))
        if moved or self.KEEP_HEAD_WHEN_IDLE:
            self.points.append((cur, now))
        self._last_pos = cur

        while len(self.points) > self.MAX_POINTS:
            self.points.popleft()
        self._invalidate()

    def _bounds(self) -> QRect:
        if not self.points:
            return QRect()
        xs = [p.x() for p, _ in self.points]
        ys = [p.y() for p, _ in self.points]
        margin = int(self.max_width) + 3
        left, top = int(min(xs)) - margin, int(min(ys)) - margin
        return QRect(left, top, int(max(xs)) + margin + 1 - left, int(max(ys)) + margin + 1 - top)

    def _invalidate(self):
        """Перерисовываем только ту область, где шлейф был и где он теперь."""
        if self.FULL_REPAINT:
            self.update()
            return
        new = self._bounds()
        region = new.united(self._dirty)
        self._dirty = new
        if not region.isNull():
            self.update(region)

    def paintEvent(self, event):
        p = QPainter(self)
        # По-настоящему очищаем область (в режиме SourceOver заливка прозрачным ничего не делает)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(event.rect(), QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        points = self.points
        if len(points) < 2:
            return
        p.setRenderHint(QPainter.Antialiasing, True)

        now = time.monotonic()
        r1, g1, b1 = self.color1.red(), self.color1.green(), self.color1.blue()
        r2, g2, b2 = self.color2.red(), self.color2.green(), self.color2.blue()

        pen = QPen()
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        color = QColor()

        for i in range(len(points) - 1):
            p1, t1 = points[i]
            p2 = points[i + 1][0]

            progress = max(0.0, min(1.0, 1.0 - (now - t1) / self.fade_time))
            if progress < 0.01:
                continue

            color.setRgb(int(r2 + (r1 - r2) * progress),
                         int(g2 + (g1 - g2) * progress),
                         int(b2 + (b1 - b2) * progress),
                         int(255 * progress ** 1.5))
            pen.setColor(color)
            pen.setWidthF(max(1.0, self.max_width * progress))
            p.setPen(pen)
            p.drawLine(p1, p2)


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


# ──────────────────────────────────────────────────────────────────────────────
#  Главная панель
# ──────────────────────────────────────────────────────────────────────────────
class ControlPanel(QWidget):
    WIDTH, HEIGHT = 380, 450

    QUICK_COLORS = ("#ff0055", "#ff6600", "#ffcc00", "#00ffc8", "#00ccff", "#0055ff", "#9d00ff", "#ffffff")
    PRESETS = (
        ("#00ccff", "#ff0080"),
        ("#9d00ff", "#00fff0"),
        ("#ff6600", "#ff0000"),
        ("#00ff00", "#003300"),
        ("#ff00ff", "#4b0082"),
    )

    def __init__(self):
        super().__init__()
        log.info("Инициализация интерфейса")
        self.picker = None
        self.overlay = None
        self.act_autostart = None
        self.active = 0  # какой цвет редактируем: 0 — основной, 1 — дополнительный
        self.history_buttons = []
        self._quitting = False
        self._tray_hint_shown = False

        self.settings = Settings.load(CONFIG_FILE)

        # Слайдеры дёргают сохранение на каждый пиксель — склеиваем записи в одну
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save_now)

        self._build_ui()
        self._build_tray()
        QTimer.singleShot(300, self._maybe_ask_autostart)

    # ── сохранение ──────────────────────────────────────────────────────────
    def schedule_save(self):
        self._save_timer.start()

    def _save_now(self):
        self._save_timer.stop()
        self.settings.save(CONFIG_FILE)

    # ── построение интерфейса ───────────────────────────────────────────────
    def _build_ui(self):
        s = self.settings
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())

        container = StarryBackground(self)
        container.setGeometry(0, 0, self.WIDTH, self.HEIGHT)
        root = QVBoxLayout(container)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(8)

        # Шапка
        header = DragHeader()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(APP_TITLE)
        title.setFont(QFont(FONT, 11, QFont.Bold))
        title.setStyleSheet(f"color: {ACCENT}; background: transparent;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self._window_button("─", 9, ACCENT, self.hide_to_tray))
        header_layout.addWidget(self._window_button("✕", 10, DANGER, self.quit_app))
        root.addWidget(header)

        # Слайдеры
        self.lbl_width = caption(f"ТОЛЩИНА: {s.max_width} px")
        root.addWidget(self.lbl_width)
        root.addWidget(self._make_slider(2, 50, s.max_width, self.change_width))

        self.lbl_fade = caption(f"СКОРОСТЬ ИСЧЕЗНОВЕНИЯ: {s.fade_ms} мс")
        root.addWidget(self.lbl_fade)
        root.addWidget(self._make_slider(100, 1500, s.fade_ms, self.change_fade))

        # Выбор цвета
        root.addWidget(caption("ВЫБОР И НАСТРОЙКА ЦВЕТА"))

        slots = QHBoxLayout()
        self.btn_c1 = make_button("Основной", height=28, font=QFont(FONT, 9))
        self.btn_c2 = make_button("Дополнительный", height=28, font=QFont(FONT, 9))
        self.btn_c1.clicked.connect(lambda: self.set_active_slot(0))
        self.btn_c2.clicked.connect(lambda: self.set_active_slot(1))
        slots.addWidget(self.btn_c1)
        slots.addWidget(self.btn_c2)
        root.addLayout(slots)

        quick = QHBoxLayout()
        for hex_code in self.QUICK_COLORS:
            btn = make_button(size=(26, 22), style=f"background-color: {hex_code};"
                                                   " border-radius: 4px; border: 1px solid #333;")
            btn.clicked.connect(lambda _=False, c=hex_code: self.pick_quick_color(c))
            quick.addWidget(btn)
        self.btn_arrow = make_button("▶", size=(26, 22), style=(
            f"QPushButton {{ background: rgba(0, 255, 200, 0.15); color: {ACCENT}; border: 1px solid {ACCENT};"
            " border-radius: 4px; font-size: 11px; font-weight: bold; }"
            f" QPushButton:hover {{ background: {ACCENT}; color: #000; }}"))
        self.btn_arrow.clicked.connect(self.toggle_picker)
        quick.addWidget(self.btn_arrow)
        root.addLayout(quick)

        btn_apply = make_button("✦ ПРИМЕНИТЬ ЦВЕТА", height=36, font=QFont(FONT_SYMBOL, 9, QFont.Bold),
                                style=outline_button_style(ACCENT))
        btn_apply.clicked.connect(self.apply_colors)
        root.addWidget(btn_apply)

        # История
        root.addWidget(caption("ИСТОРИЯ ВАШИХ ЦВЕТОВ", size=9, color=DIM))
        history = QHBoxLayout()
        for i in range(HISTORY_SIZE):
            btn = make_button(size=(56, 22))
            btn.clicked.connect(lambda _=False, idx=i: self.apply_history(idx))
            history.addWidget(btn)
            self.history_buttons.append(btn)
        root.addLayout(history)

        # Пресеты
        root.addWidget(caption("ГОТОВЫЕ ПРЕСЕТЫ"))
        presets = QHBoxLayout()
        for c1, c2 in self.PRESETS:
            btn = make_button(size=(56, 22), style=gradient_button_style(c1, c2))
            btn.clicked.connect(lambda _=False, a=c1, b=c2: self.apply_preset(a, b))
            presets.addWidget(btn)
        root.addLayout(presets)

        root.addStretch()

        self.btn_toggle = make_button(height=36, font=QFont(FONT_SYMBOL, 9, QFont.Bold))
        self.btn_toggle.clicked.connect(self.toggle_trail)
        root.addWidget(self.btn_toggle)

        self._refresh_toggle_ui()
        self._refresh_color_buttons()
        self._refresh_history_ui()

    @staticmethod
    def _window_button(text, font_size, hover_color, slot) -> QPushButton:
        btn = make_button(text, size=(24, 24), font=QFont(FONT_SYMBOL, font_size, QFont.Bold), style=(
            "QPushButton { background: transparent; color: #777; border: none; }"
            f" QPushButton:hover {{ color: {hover_color}; }}"))
        btn.clicked.connect(slot)
        return btn

    @staticmethod
    def _make_slider(lo, hi, value, on_change) -> ClickSlider:
        slider = ClickSlider(Qt.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(value)
        slider.setStyleSheet(SLIDER_STYLE)
        slider.valueChanged.connect(on_change)
        return slider

    # ── трей ────────────────────────────────────────────────────────────────
    def _build_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_TITLE)

        menu = QMenu()
        menu.setStyleSheet(
            f"QMenu {{ background-color: {BG}; color: #fff; border: 1px solid {ACCENT};"
            " border-radius: 8px; padding: 4px; }"
            " QMenu::item { padding: 6px 20px; border-radius: 4px; }"
            f" QMenu::item:selected {{ background-color: {ACCENT}; color: #000; }}")

        menu.addAction("Открыть настройки").triggered.connect(self.show_from_tray)
        self.act_toggle = menu.addAction("Включить эффект")
        self.act_toggle.triggered.connect(self.toggle_trail)

        if IS_WINDOWS:
            self.act_autostart = menu.addAction("Запускать вместе с Windows")
            self.act_autostart.setCheckable(True)
            self.act_autostart.setChecked(self.settings.autostart_enabled)
            self.act_autostart.toggled.connect(self._on_autostart_toggled)

        menu.addSeparator()
        menu.addAction("Выход").triggered.connect(self.quit_app)

        self._tray_menu = menu  # у меню нет родителя — держим ссылку, иначе его соберёт GC
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        # Реагируем только на левый клик; правый открывает меню и окно показывать не должен
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide_to_tray()
            else:
                self.show_from_tray()

    def hide_to_tray(self):
        if self.picker:
            self.picker.close()
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage(APP_TITLE, "Приложение свернуто в трей.", app_icon(), 1500)

    def show_from_tray(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        log.info("Завершение работы приложения")
        self._quitting = True
        self._save_now()
        if self.overlay:
            self.overlay.shutdown()
            self.overlay = None
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        if self._quitting:
            event.accept()
        else:  # Alt+F4 не должен убивать приложение — сворачиваем в трей
            event.ignore()
            self.hide_to_tray()

    # ── автозапуск ──────────────────────────────────────────────────────────
    def _set_autostart_check(self, checked: bool):
        if self.act_autostart:
            self.act_autostart.blockSignals(True)
            self.act_autostart.setChecked(checked)
            self.act_autostart.blockSignals(False)

    def _maybe_ask_autostart(self):
        if not IS_WINDOWS or self.settings.autostart_asked:
            return
        dialog = AutostartDialog(self)
        dialog.move(self.geometry().center() - dialog.rect().center())
        wanted = dialog.exec_() == QDialog.Accepted
        dialog.deleteLater()

        enabled = set_autostart(wanted) and wanted
        self.settings.autostart_enabled = enabled
        self.settings.autostart_asked = True
        self._set_autostart_check(enabled)
        self.schedule_save()

    def _on_autostart_toggled(self, checked: bool):
        if not set_autostart(checked):
            self._set_autostart_check(not checked)  # не получилось — возвращаем галочку
            return
        self.settings.autostart_enabled = checked
        self.settings.autostart_asked = True
        self.schedule_save()

    # ── окно выбора цвета ───────────────────────────────────────────────────
    def toggle_picker(self):
        if self.picker:
            self.picker.close()
            return

        self.picker = ColorPickerWindow(self.settings.colors[self.active], self)
        self.picker.colorPicked.connect(self.on_picker_color)
        self.picker.finished.connect(self._on_picker_closed)
        self.btn_arrow.setText("◀")
        self._place_picker()
        self.picker.show()

    def _on_picker_closed(self):
        self.picker = None
        self.btn_arrow.setText("▶")

    def _place_picker(self):
        if not self.picker:
            return
        frame = self.frameGeometry()
        screen = QApplication.screenAt(frame.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()

        x = frame.right() + 10
        if x + self.picker.width() > area.right():  # справа нет места — ставим слева
            x = frame.left() - self.picker.width() - 10
        y = max(area.top(), min(frame.top(), area.bottom() - self.picker.height()))
        self.picker.move(x, y)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._place_picker()

    def _sync_picker(self):
        if self.picker:
            self.picker.set_color(self.settings.colors[self.active])

    def on_picker_color(self, color: QColor):
        self.settings.colors[self.active] = QColor(color)
        self._refresh_color_buttons()

    # ── цвета ───────────────────────────────────────────────────────────────
    def set_active_slot(self, slot: int):
        self.active = slot
        self._sync_picker()
        self._refresh_color_buttons()

    def pick_quick_color(self, hex_code: str):
        self.settings.colors[self.active] = QColor(hex_code)
        self._sync_picker()
        self._refresh_color_buttons()

    def apply_colors(self):
        c1, c2 = self.settings.colors
        log.info("Применены цвета: C1=%s, C2=%s", c1.name(), c2.name())
        if self.overlay:
            self.overlay.set_colors(c1, c2)

        pair = (QColor(c1), QColor(c2))
        if pair in self.settings.history:
            self.settings.history.remove(pair)
        self.settings.history.insert(0, pair)
        del self.settings.history[HISTORY_SIZE:]

        self._refresh_history_ui()
        self.schedule_save()

    def apply_preset(self, c1, c2):
        self.settings.colors = [QColor(c1), QColor(c2)]
        self._sync_picker()
        self._refresh_color_buttons()
        self.apply_colors()

    def apply_history(self, idx: int):
        if idx < len(self.settings.history):
            self.apply_preset(*self.settings.history[idx])

    def _refresh_color_buttons(self):
        for idx, btn in enumerate((self.btn_c1, self.btn_c2)):
            color = self.settings.colors[idx]
            border = f"2px solid {ACCENT}" if idx == self.active else "1px solid #333"
            btn.setStyleSheet(f"background-color: {color.name()}; color: {readable_text_color(color)};"
                              f" border-radius: 6px; font-weight: bold; border: {border};")

    def _refresh_history_ui(self):
        for idx, btn in enumerate(self.history_buttons):
            if idx < len(self.settings.history):
                a, b = self.settings.history[idx]
                btn.setStyleSheet(gradient_button_style(a.name(), b.name(), border="#555"))
                btn.setEnabled(True)
            else:
                btn.setStyleSheet("background: rgba(24, 24, 34, 0.7); border: 1px dashed #333; border-radius: 6px;")
                btn.setEnabled(False)

    # ── эффект ──────────────────────────────────────────────────────────────
    def change_width(self, value: int):
        self.settings.max_width = value
        self.lbl_width.setText(f"ТОЛЩИНА: {value} px")
        if self.overlay:
            self.overlay.max_width = value
            self.overlay.update()
        self.schedule_save()

    def change_fade(self, value: int):
        self.settings.fade_ms = value
        self.lbl_fade.setText(f"СКОРОСТЬ ИСЧЕЗНОВЕНИЯ: {value} мс")
        if self.overlay:
            self.overlay.fade_time = value / 1000.0
            self.overlay.update()
        self.schedule_save()

    def toggle_trail(self):
        if self.overlay is None:
            log.info("Эффект включен")
            s = self.settings
            self.overlay = MouseTrailOverlay(s.fade_ms / 1000.0, s.max_width, *s.colors)
            self.overlay.show()
        else:
            log.info("Эффект выключен")
            self.overlay.shutdown()
            self.overlay = None
        self._refresh_toggle_ui()

    def _refresh_toggle_ui(self):
        active = self.overlay is not None
        if active:
            self.btn_toggle.setText("⏹ ВЫКЛЮЧИТЬ ЭФФЕКТ")
            self.btn_toggle.setStyleSheet(outline_button_style(DANGER, hover_text="#ffffff"))
        else:
            self.btn_toggle.setText("▶ ВКЛЮЧИТЬ ЭФФЕКТ")
            self.btn_toggle.setStyleSheet(outline_button_style(ACCENT))
        if hasattr(self, "act_toggle"):
            self.act_toggle.setText("Выключить эффект" if active else "Включить эффект")


# ──────────────────────────────────────────────────────────────────────────────
def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Защита от второго экземпляра (например, автозапуск + ручной запуск)
    guard = QSharedMemory(f"{APP_NAME}_single_instance")
    if guard.attach():
        guard.detach()  # на Unix чистит «осиротевший» сегмент после падения
    if not guard.create(1):
        log.warning("Приложение уже запущено — выход")
        return 0

    panel = ControlPanel()
    panel.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
