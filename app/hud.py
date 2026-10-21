"""Таймеры под часами игры и иконка в углу."""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from app.config import ACCENT, APP_TITLE, FONT, MUTED, TEXT
from app.gsi import IDLE, LIVE, OFFLINE, PAUSED, PREGAME, STALE, Snapshot, TIMERS, TimerDef, WARN_SECONDS, format_mmss, seconds_until
from app.icon import app_icon
from app.trail import apply_overlay_style


# ──────────────────────────────────────────────────────────────────────────────
#  Таймеры под часами игры (как у Dota Plus)
# ──────────────────────────────────────────────────────────────────────────────
URGENT = "#ff3030"  # цвет таймера, когда до появления осталось WARN_SECONDS секунд и меньше
TIMER_COLORS = {"lotus": "#ff7ad9", "bounty": "#ffd400", "wisdom": "#4da6ff", "power": "#b36bff"}
HUD_CLOCK_OFFSET = 0.068  # доля высоты экрана: сразу под игровыми часами
STATUS_TEXT = {
    OFFLINE: "Жду данные от Dota 2…",
    STALE: "Нет связи с игрой",
    IDLE: "Матч не идёт",
    PREGAME: "Подготовка к матчу",
    LIVE: "Матч идёт",
    PAUSED: "Пауза",
}
IN_MATCH = (PREGAME, LIVE, PAUSED)


class TimerCell(QWidget):
    """Название объекта и обратный отсчёт."""

    def __init__(self, timer: TimerDef, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # таскаем панель за любую точку
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        name = QLabel(timer.short or timer.label)
        name.setFont(QFont(FONT, 8, QFont.Bold))
        name.setStyleSheet(f"color: {TIMER_COLORS.get(timer.key, MUTED)}; background: transparent;")
        self.value = QLabel("--:--")
        self.value.setFont(QFont("Consolas", 10, QFont.Bold))
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value.setMinimumWidth(40)
        layout.addWidget(name)
        layout.addStretch()
        layout.addWidget(self.value)

        self._text = None
        self._urgent = None
        self.set("--:--", False)

    def set(self, text: str, urgent: bool):
        # Вызывается 5 раз в секунду: трогаем виджет, только если что-то изменилось
        if text != self._text:
            self._text = text
            self.value.setText(text)
        if urgent != self._urgent:
            self._urgent = urgent
            self.value.setStyleSheet(f"color: {URGENT if urgent else TEXT}; background: transparent;")


class TimerHUD(QWidget):
    """Компактная плашка 2×2 под игровыми часами. Видна только во время матча, фокус у игры не берёт."""

    positionChanged = pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        self._drag = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setToolTip("Таймеры. Можно перетащить; вернуть под часы — в меню трея.")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame(self)
        card.setObjectName("hud")
        card.setStyleSheet("#hud { background-color: rgba(8, 8, 14, 205); border: 1px solid rgba(0, 255, 200, 90);"
                           " border-radius: 7px; }")
        outer.addWidget(card)

        grid = QGridLayout(card)
        grid.setContentsMargins(10, 4, 10, 4)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(1)
        self.cells = [TimerCell(t) for t in TIMERS]
        for i, cell in enumerate(self.cells):
            grid.addWidget(cell, i // 2, i % 2)
        self.adjustSize()

    def place(self, pos):
        """Сохранённое место, а если его нет (или того экрана больше нет) — по центру под часами игры."""
        self.adjustSize()
        if pos is not None:
            point = QPoint(int(pos[0]), int(pos[1]))
            if any(s.geometry().contains(point) for s in QApplication.screens()):
                self.move(point)
                return
        geo = QApplication.primaryScreen().geometry()
        self.move(geo.center().x() - self.width() // 2, geo.top() + int(round(geo.height() * HUD_CLOCK_OFFSET)))

    def showEvent(self, event):
        super().showEvent(event)
        apply_overlay_style(self, click_through=False)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.positionChanged.emit(self.x(), self.y())

    def update_from(self, snap: Snapshot, visible_override: bool = True):
        clock = snap.clock
        for cell, timer in zip(self.cells, TIMERS):
            if clock is None:
                cell.set("--:--", False)
            else:
                left = seconds_until(clock, timer.first, timer.interval)
                cell.set(format_mmss(left), left <= WARN_SECONDS)
        visible = snap.status in IN_MATCH and visible_override
        if visible != self.isVisible():
            self.setVisible(visible)


# ──────────────────────────────────────────────────────────────────────────────
#  Иконка-кнопка в левом верхнем углу
# ──────────────────────────────────────────────────────────────────────────────
class LauncherIcon(QWidget):
    """Маленькая кнопка поверх игры: клик — открыть/скрыть приложение, перетаскивание — переставить."""

    clicked = pyqtSignal()
    positionChanged = pyqtSignal(int, int)
    menuRequested = pyqtSignal(QPoint)
    SIZE = 38
    DEFAULT_OFFSET = (8, 10)  # левее шестерёнки настроек Dota 2

    def __init__(self):
        super().__init__()
        self._press = None
        self._origin = None
        self._moved = False
        self._hover = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{APP_TITLE}: клик — открыть, правый клик — меню, можно перетащить")

    def place(self, pos):
        if pos is not None:
            point = QPoint(int(pos[0]), int(pos[1]))
            if any(s.geometry().contains(point) for s in QApplication.screens()):
                self.move(point)
                return
        geo = QApplication.primaryScreen().geometry()
        self.move(geo.left() + self.DEFAULT_OFFSET[0], geo.top() + self.DEFAULT_OFFSET[1])

    def showEvent(self, event):
        super().showEvent(event)
        apply_overlay_style(self, click_through=False)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(ACCENT if self._hover else "#2f6f63"), 1.5))
        p.setBrush(QColor(17, 17, 24, 235 if self._hover else 200))
        p.drawRoundedRect(rect, 9, 9)
        icon = app_icon().pixmap(self.SIZE - 10, self.SIZE - 10)
        p.drawPixmap((self.width() - icon.width()) // 2, (self.height() - icon.height()) // 2, icon)

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.globalPos()
            self._origin = self.pos()
            self._moved = False

    def mouseMoveEvent(self, event):
        if self._press is None or not event.buttons() & Qt.LeftButton:
            return
        delta = event.globalPos() - self._press
        if not self._moved and delta.manhattanLength() < 5:
            return  # дрожание руки при клике — не перетаскивание
        self._moved = True
        self.move(self._origin + delta)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._press is None:
            return
        self._press = None
        if self._moved:
            self.positionChanged.emit(self.x(), self.y())
        else:
            self.clicked.emit()

    def contextMenuEvent(self, event):
        self.menuRequested.emit(event.globalPos())
