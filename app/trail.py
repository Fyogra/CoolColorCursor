"""Шлейф за курсором и WinAPI-хелперы оверлея."""
from __future__ import annotations

import math
import time
from PyQt5.QtCore import QPointF, QRect, QTimer, Qt
from PyQt5.QtGui import QColor, QCursor, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QWidget
from collections import deque
from app.config import IS_WINDOWS, log


# ──────────────────────────────────────────────────────────────────────────────
#  Платформа (WinAPI нужен только для «сквозного» окна)
# ──────────────────────────────────────────────────────────────────────────────
if IS_WINDOWS:
    import ctypes
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


def apply_overlay_style(widget: QWidget, *, click_through: bool) -> None:
    """Окно не берёт фокус, не светится в Alt+Tab и лежит поверх всех.

    click_through=True — клики проходят сквозь окно (шлейф); False — по окну можно кликать (панель таймеров).
    """
    if not IS_WINDOWS:
        return
    try:
        hwnd = int(widget.winId())
        style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        if click_through:
            style |= WS_EX_TRANSPARENT
        _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception as e:  # noqa: BLE001
        log.error("Ошибка стилей Windows: %s", e)


def keep_on_top(widget: QWidget) -> None:
    """Повторно поднимает окно наверх: игра в окне без рамки может перехватить верхний слой."""
    if not IS_WINDOWS:
        return
    try:
        _user32.SetWindowPos(int(widget.winId()), HWND_TOPMOST, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception as e:  # noqa: BLE001
        log.error("Ошибка поднятия окна: %s", e)


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
        apply_overlay_style(self, click_through=True)

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
