"""Шлейф за курсором и WinAPI-хелперы оверлея."""
from __future__ import annotations

import math
import time
from PyQt5.QtCore import QPointF, QRect, QTimer, Qt
from PyQt5.QtGui import QColor, QCursor, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QWidget
from collections import deque
from app.config import IS_WINDOWS, log

VK_RBUTTON = 0x02
WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004+: окно видно на мониторе, но не попадает в снимки экрана
GAME_EXES = ("dota2.exe",)


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
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                     ctypes.POINTER(wintypes.DWORD)]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


def foreground_exe() -> str:
    """Имя exe окна, которое сейчас в фокусе (нижний регистр), или пустая строка."""
    if not IS_WINDOWS:
        return ""
    return window_exe(_user32.GetForegroundWindow())


def dota_window():
    """Окно запущенной Dota 2 (даже свёрнутой) или None. Класс окна SDL — плюс сверяем exe, чтобы не принять
    за игру что-то другое с таким же заголовком."""
    if not IS_WINDOWS:
        return None
    hwnd = _user32.FindWindowW("SDL_app", "Dota 2")
    return hwnd if hwnd and window_exe(hwnd) in GAME_EXES else None


def close_window(hwnd) -> None:
    """Просим окно закрыться, как по крестику (WM_CLOSE): игра выходит сама, без убийства процесса."""
    if IS_WINDOWS and hwnd:
        _user32.PostMessageW(hwnd, 0x0010, 0, 0)


def window_exe(hwnd) -> str:
    """Имя exe, которому принадлежит окно (нижний регистр), или пустая строка."""
    try:
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        handle = _kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf, size = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
            if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        finally:
            _kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — не критично: считаем, что игры нет
        pass
    return ""


def right_button_down() -> bool:
    return IS_WINDOWS and bool(_user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)


class GameFocus:
    """В фокусе только Dota 2: ни в панели настроек, ни в других окнах эффекта нет.
    Проверка не чаще 4 раз в секунду; only_in_dota=False — считаем, что фокус есть всегда."""

    def __init__(self):
        self.only_in_dota = True
        self._ok, self._at = True, 0.0

    def __call__(self, now: float) -> bool:
        if not self.only_in_dota:
            return True
        if now - self._at >= 0.25:
            self._at = now
            exe = foreground_exe()
            self._ok = exe in GAME_EXES
        return self._ok


def exclude_from_capture(widget: QWidget) -> None:
    """Окно остаётся на экране, но пропадает из снимков экрана и записи."""
    if not IS_WINDOWS:
        return
    try:
        if not _user32.SetWindowDisplayAffinity(int(widget.winId()), WDA_EXCLUDEFROMCAPTURE):
            log.warning("Не удалось скрыть окно от захвата экрана (нужна Windows 10 2004 или новее)")
    except Exception as e:  # noqa: BLE001
        log.warning("Скрытие окна от захвата: %s", e)


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

    MAX_POINTS = 400             # жёсткий лимит снятых положений курсора
    MAX_INTERP_STEPS = 80        # лимит точек кривой между двумя соседними положениями
    SAMPLE_HZ = 120              # как часто снимать положение курсора — не реже, чем 120 раз в секунду
    CURVE_STEP = 2.0             # шаг точек кривой при рисовании, px
    FREEZE_GAP = 0.1             # пауза между тиками (с), после которой считаем, что система «подвисла»
    FULL_REPAINT = False         # True — перерисовывать весь экран (если вдруг появятся артефакты)
    KEEP_HEAD_WHEN_IDLE = False  # True — как в старой версии: «точка» остаётся под неподвижным курсором

    def __init__(self, fade_time: float, max_width: int, color1: QColor, color2: QColor):
        super().__init__()
        self.fade_time = fade_time
        self.max_width = max_width
        self.color1 = QColor(color1)
        self.color2 = QColor(color2)

        self.points = deque()  # снятые положения курсора: (QPointF, момент); кривая строится по ним при рисовании
        self._last_pos = None
        self._last_tick = time.monotonic()
        self._dirty = QRect()  # где шлейф был нарисован в прошлый раз
        self.focus = GameFocus()  # рисовать только пока в фокусе Dota 2 (если включено)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowTransparentForInput
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setGeometry(self._desktop_rect())

        self.refresh_rate = self._detect_refresh_rate()
        # На 60 Гц курсор за кадр успевает пролететь десятки пикселей — снимаем его чаще кадров
        interval = max(1, int(1000 / max(self.refresh_rate, self.SAMPLE_HZ)))
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
        if not self.focus(now):
            self._last_tick = now
            if self.points:
                self.points.clear()
                self._invalidate()
            return
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
        margin = int(self.max_width) + 12      # кривая может чуть выйти за снятые точки на поворотах
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

        p.setRenderHint(QPainter.Antialiasing, True)
        now = time.monotonic()
        self._paint_trail(p, now)

    def _curve(self):
        """Плавная кривая через снятые положения курсора (сплайн Катмулла–Рома) с моментом времени в каждой
        точке: так и форма без углов, и цвет с толщиной угасают плавно, а не ступеньками по кадрам."""
        raw = list(self.points)
        n = len(raw)
        out = []
        for i in range(n - 1):
            p1, t1 = raw[i]
            p2, t2 = raw[i + 1]
            p0 = raw[i - 1][0] if i > 0 else p1
            p3 = raw[i + 2][0] if i + 2 < n else p2
            steps = max(1, min(self.MAX_INTERP_STEPS,
                               int(math.hypot(p2.x() - p1.x(), p2.y() - p1.y()) / self.CURVE_STEP)))
            for k in range(steps):
                u = k / steps
                u2, u3 = u * u, u * u * u
                x = 0.5 * (2 * p1.x() + (p2.x() - p0.x()) * u + (2 * p0.x() - 5 * p1.x() + 4 * p2.x() - p3.x()) * u2
                           + (3 * p1.x() - p0.x() - 3 * p2.x() + p3.x()) * u3)
                y = 0.5 * (2 * p1.y() + (p2.y() - p0.y()) * u + (2 * p0.y() - 5 * p1.y() + 4 * p2.y() - p3.y()) * u2
                           + (3 * p1.y() - p0.y() - 3 * p2.y() + p3.y()) * u3)
                out.append((QPointF(x, y), t1 + (t2 - t1) * u))
        if raw:
            out.append(raw[-1])
        return out

    def _paint_trail(self, p: QPainter, now: float) -> None:
        points = self._curve()
        if len(points) < 2:
            return
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
