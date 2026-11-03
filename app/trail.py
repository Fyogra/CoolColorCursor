"""Шлейф за курсором и WinAPI-хелперы оверлея."""
from __future__ import annotations

import math
import time
from PyQt5.QtCore import QPointF, QRect, QTimer, Qt
from PyQt5.QtGui import QColor, QCursor, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QWidget
from collections import deque
from app.config import IS_WINDOWS, log

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
    """Прозрачное «сквозное» окно на весь рабочий стол, в котором рисуется шлейф.

    Курсор снимается часто (не реже SAMPLE_HZ), а перерисовка идёт не чаще кадров монитора (и не чаще MAX_PAINT_HZ):
    лишние кадры экран всё равно не покажет. Кривая между снимками считается один раз, когда снимок появился,
    и хранится до угасания, а не пересчитывается на каждом кадре."""

    MAX_POINTS = 400             # жёсткий лимит снятых положений курсора
    MAX_INTERP_STEPS = 80        # лимит точек кривой между двумя соседними положениями
    SAMPLE_HZ = 120              # как часто снимать положение курсора — не реже, чем 120 раз в секунду
    MAX_PAINT_HZ = 144           # перерисовка не чаще кадров монитора и не чаще этого
    CURVE_STEP = 2.0             # шаг точек кривой при рисовании, px
    FADE_LEVELS = 256            # ступеней угасания цвета и толщины
    FREEZE_GAP = 0.1             # пауза между тиками (с), после которой считаем, что система «подвисла»
    FULL_REPAINT = False         # True — перерисовывать весь экран (если вдруг появятся артефакты)
    KEEP_HEAD_WHEN_IDLE = False  # True — как в старой версии: «точка» остаётся под неподвижным курсором

    def __init__(self, fade_time: float, max_width: int, color1: QColor, color2: QColor):
        super().__init__()
        self.fade_time = fade_time
        self.max_width = max_width
        self.color1 = QColor(color1)
        self.color2 = QColor(color2)

        self.points = deque()  # снятые положения курсора: (x, y, момент)
        self._segs = deque()   # кривая от points[i] до points[i+1]: [(QPointF, момент), ...] без конечной точки
        self._last_pos = None
        self._last_tick = time.monotonic()
        self._next_paint = 0.0
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
        self._frame = 1.0 / min(self.refresh_rate, self.MAX_PAINT_HZ)
        log.info("Определена герцовка: %s Гц. Интервал таймера: %s мс, кадр %.1f мс",
                 self.refresh_rate, interval, self._frame * 1000)

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
        self._clear()
        self._dirty = QRect()
        self.update()

    def shutdown(self):
        self.timer.stop()
        self.close()
        self.deleteLater()

    # ── логика шлейфа ───────────────────────────────────────────────────────
    def _clear(self):
        self.points.clear()
        self._segs.clear()

    def _pop_oldest(self):
        self.points.popleft()
        if self._segs:
            self._segs.popleft()

    def _segment(self, i: int):
        """Кусок сплайна Катмулла–Рома от points[i] до points[i+1] с моментом времени в каждой точке:
        форма без углов, а цвет с толщиной угасают плавно, а не ступеньками по кадрам."""
        pts = self.points
        x1, y1, t1 = pts[i]
        x2, y2, t2 = pts[i + 1]
        x0, y0 = (pts[i - 1][0], pts[i - 1][1]) if i > 0 else (x1, y1)
        x3, y3 = (pts[i + 2][0], pts[i + 2][1]) if i + 2 < len(pts) else (x2, y2)
        steps = max(1, min(self.MAX_INTERP_STEPS, int(math.hypot(x2 - x1, y2 - y1) / self.CURVE_STEP)))
        bx, cx, dx = x2 - x0, 2 * x0 - 5 * x1 + 4 * x2 - x3, 3 * x1 - x0 - 3 * x2 + x3
        by, cy, dy = y2 - y0, 2 * y0 - 5 * y1 + 4 * y2 - y3, 3 * y1 - y0 - 3 * y2 + y3
        out = []
        for k in range(steps):
            u = k / steps
            u2, u3 = u * u, u * u * u
            out.append((QPointF(0.5 * (2 * x1 + bx * u + cx * u2 + dx * u3),
                                0.5 * (2 * y1 + by * u + cy * u2 + dy * u3)), t1 + (t2 - t1) * u))
        return out

    def _push(self, x: float, y: float, now: float):
        self.points.append((x, y, now))
        n = len(self.points)
        if n >= 3:                 # у предыдущего куска появилась следующая точка — его изгиб уточняется
            self._segs[-1] = self._segment(n - 3)
        if n >= 2:
            self._segs.append(self._segment(n - 2))

    def _tick(self):
        now = time.monotonic()
        if not self.focus(now):
            self._last_tick = now
            if self.points:
                self._clear()
                self._invalidate()
            return
        cur = QPointF(self.mapFromGlobal(QCursor.pos()))

        # Защита от фризов (UAC, нагрузка на систему): не тянем линию через весь экран
        if now - self._last_tick > self.FREEZE_GAP:
            self._last_tick = now
            self._last_pos = cur
            self._clear()
            self._invalidate()
            return
        self._last_tick = now

        while self.points and now - self.points[0][2] > self.fade_time:
            self._pop_oldest()

        last = self._last_pos
        moved = last is None or cur != last
        if moved or self.KEEP_HEAD_WHEN_IDLE:
            self._push(cur.x(), cur.y(), now)
        self._last_pos = cur

        while len(self.points) > self.MAX_POINTS:
            self._pop_oldest()
        if now >= self._next_paint:
            self._next_paint = max(self._next_paint + self._frame, now + self._frame * 0.5)
            self._invalidate()

    def _bounds(self) -> QRect:
        if not self.points:
            return QRect()
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
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

    def _paint_trail(self, p: QPainter, now: float) -> None:
        if len(self.points) < 2:
            return
        x, y, t = self.points[-1]
        points = [pt for seg in self._segs for pt in seg]
        points.append((QPointF(x, y), t))
        r1, g1, b1 = self.color1.red(), self.color1.green(), self.color1.blue()
        r2, g2, b2 = self.color2.red(), self.color2.green(), self.color2.blue()
        inv, width, levels = 1.0 / self.fade_time, self.max_width, self.FADE_LEVELS

        pen = QPen()
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        color = QColor()
        draw, set_pen = p.drawLine, p.setPen
        key = -1
        # Перо меняется, только когда угасание дошло до следующей ступени (их FADE_LEVELS — глазу не видно),
        # а не на каждом кусочке. Кусочки рисуются по одному: так полупрозрачные перекрытия те же, что и раньше
        for i in range(len(points) - 1):
            p1, t1 = points[i]
            progress = 1.0 - (now - t1) * inv
            if progress < 0.01:
                continue
            k = levels if progress >= 1.0 else int(progress * levels)
            if k != key:
                key = k
                q = k / levels
                color.setRgb(int(r2 + (r1 - r2) * q), int(g2 + (g1 - g2) * q), int(b2 + (b1 - b2) * q),
                             int(255 * q ** 1.5))
                pen.setColor(color)
                pen.setWidthF(max(1.0, width * q))
                set_pen(pen)
            draw(p1, points[i + 1][0])
