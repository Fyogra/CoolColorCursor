"""Окно сборок, детектор магазина, фоновые задачи Qt."""
from __future__ import annotations

import ctypes
import math
from PyQt5.QtCore import QObject, QPoint, QPointF, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import QToolTip, QApplication, QButtonGroup, QComboBox, QCompleter, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Tuple
from app.builds_data import BUILDS_DAYS, BuildsService, DotaConstants, ITEMS_PER_PHASE, PHASES, POSITIONS, POSITION_NAMES, SKILL_LEN, _is_int, fetch_image_bytes, format_age, is_talent
from app.config import ACCENT, APP_TITLE, BG, DANGER, DIM, FONT, FONT_SYMBOL, IS_WINDOWS, MUTED, TEXT, log
from app.sound import is_key_down
from app.trail import keep_on_top
from app.ui_common import DragHeader, caption, make_button


# ──────────────────────────────────────────────────────────────────────────────
#  Детектор магазина Dota 2 (без калибровки, по сетке иконок)
# ──────────────────────────────────────────────────────────────────────────────
VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B

SHOP_PROBE_WIDTH = 640       # кадр уменьшаем до такой ширины: быстро, и пороги не зависят от разрешения
SHOP_STABLE_FRAMES = 2       # столько кадров подряд должны согласованно показывать «магазин»
# Магазин у края экрана: тёмное ровное поле у самого края (A) и рядом сетка иконок (B).
# Доли ширины и высоты кадра; для левой стороны зеркально. Пороги сняты с живых кадров:
# магазин открыт — A≈14, B яркость≈56, строк с границами≈68%; игра без магазина — A≈124, B≈119, 9–14%.
SHOP_EDGE_A = (0.946, 0.25, 0.054, 0.50)   # x, y, ширина, высота
SHOP_GRID_B = (0.72, 0.14, 0.21, 0.34)
SHOP_A_LUM_MAX = 45
SHOP_B_LUM_RANGE = (20, 100)
SHOP_B_EDGE_MIN = 40         # % строк «иконочной» плотности границ


def _probe_edge_strip(image, x0: int, y0: int, w: int, h: int) -> Tuple[float, float, float]:
    """(средняя яркость, доля ярких пикселей, доля строк с «иконочной» плотностью границ) для области."""
    if image is None or w <= 2 or h <= 2:
        return 255.0, 100.0, 0.0
    total_lum, bright, edge_rows = 0.0, 0, 0
    sample_rows = max(10, h // 6)  # хватит каждого 6-го ряда — скорость важнее точности
    for yi in range(sample_rows):
        y = y0 + int(yi * h / sample_rows)
        prev = None
        edges = 0
        row_lum = 0
        for x in range(x0, x0 + w, 2):   # шаг 2 — вдвое быстрее, разницы для детектора нет
            r, g, b, _ = image.pixelColor(x, y).getRgb()
            lum = (r * 30 + g * 59 + b * 11) // 100
            row_lum += lum
            if lum > 215:
                bright += 1
            if prev is not None and abs(lum - prev) > 55:
                edges += 1
            prev = lum
        total_lum += row_lum / max(1, w // 2)
        if edges >= 8:  # ряд иконок даёт десятки резких границ
            edge_rows += 1
    rows = max(1, sample_rows)
    return (total_lum / rows,
            100.0 * bright / max(1, rows * (w // 2)),
            100.0 * edge_rows / rows)


class ShopDetector:
    """Смотрит, открыт ли магазин Dota 2 и с какой стороны. Без калибровки."""

    def __init__(self):
        self._history: deque = deque(maxlen=SHOP_STABLE_FRAMES)
        self._side: Optional[str] = None  # "left" | "right" | None
        self.last_metrics: dict = {}

    @property
    def side(self) -> Optional[str]:
        return self._side

    def reset(self):
        self._history.clear()
        self._side = None

    def _grab(self):
        """QImage всего экрана, или None если Qt недоступен."""
        try:
            screen = QApplication.primaryScreen()
            if screen is None:
                return None
            pixmap = screen.grabWindow(0)
            return pixmap.toImage()
        except Exception as e:  # noqa: BLE001
            log.warning("Снимок экрана не удался: %s", e)
            return None

    def probe(self) -> Optional[str]:
        img = self._grab()
        if img is None or img.width() < 100 or img.height() < 100:
            self._history.append(None)
            self._side = None
            return None
        img = img.scaled(SHOP_PROBE_WIDTH, int(SHOP_PROBE_WIDTH * img.height() / img.width()),
                         Qt.KeepAspectRatio, Qt.FastTransformation)
        w, h = img.width(), img.height()

        def measure(mirror: bool):
            def area(box):
                x, y, bw, bh = box
                x0 = int((1 - x - bw if mirror else x) * w)
                return _probe_edge_strip(img, x0, int(y * h), max(3, int(bw * w)), int(bh * h))
            edge, grid = area(SHOP_EDGE_A), area(SHOP_GRID_B)
            return (edge[0], grid[0], grid[2]), (edge[0] <= SHOP_A_LUM_MAX
                                                 and SHOP_B_LUM_RANGE[0] <= grid[0] <= SHOP_B_LUM_RANGE[1]
                                                 and grid[2] >= SHOP_B_EDGE_MIN)

        right, right_ok = measure(False)
        left, left_ok = measure(True)
        self.last_metrics = {"right": right, "left": left, "w": w, "h": h}
        side = "right" if right_ok else ("left" if left_ok else None)
        self._history.append(side)
        # Нужно, чтобы последние кадры стабильно показывали одно и то же
        if len(self._history) == SHOP_STABLE_FRAMES and len(set(self._history)) == 1:
            self._side = side
        return self._side


# ──────────────────────────────────────────────────────────────────────────────
#  Отслеживание клика по иконке золота Dota 2
# ──────────────────────────────────────────────────────────────────────────────
class GoldClickWatcher:
    """Срабатывает один раз, когда ЛКМ опустилась внутри заданной зоны (внизу по центру экрана).

    Отслеживаем переход «не нажата → нажата», чтобы долгий клик считался одним событием.
    """

    def __init__(self):
        self._prev_down = False

    @staticmethod
    def default_zone() -> Tuple[int, int, int, int]:
        """Прямоугольник вокруг иконки золота: нижняя полоса экрана, по центру."""
        try:
            geo = QApplication.primaryScreen().geometry()
            w, h = int(geo.width()), int(geo.height())
            if w < 100 or h < 100:
                return (900, 1000, 160, 80)
            zw, zh = int(w * 0.08), int(h * 0.08)
            return (w // 2 - zw // 2, h - zh, zw, zh)
        except Exception:  # noqa: BLE001
            return (900, 1000, 160, 80)

    def check(self, zone: Optional[Tuple[int, int, int, int]] = None) -> bool:
        """Вернёт True ровно один раз на каждое нажатие ЛКМ в зоне. Снаружи Windows — всегда False."""
        if not IS_WINDOWS:
            return False
        down = is_key_down(VK_LBUTTON)
        pressed_now = down and not self._prev_down
        self._prev_down = down
        if not pressed_now:
            return False
        try:
            pt = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            x, y = int(pt.x), int(pt.y)
        except Exception:  # noqa: BLE001
            return False
        zx, zy, zw, zh = zone or self.default_zone()
        return zx <= x <= zx + zw and zy <= y <= zy + zh


# ──────────────────────────────────────────────────────────────────────────────
#  Фоновые задачи для Qt
# ──────────────────────────────────────────────────────────────────────────────
def _safe_call(callback, *args):
    try:
        callback(*args)
    except RuntimeError:
        pass  # виджет уже удалён — это нормально


class AsyncRunner(QObject):
    """Выполняет функцию в пуле потоков, результат приходит сигналом уже в главном потоке."""

    done = pyqtSignal(object, object, object)  # token, результат, исключение

    def __init__(self, workers: int = 2, parent=None):
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ccc-net")
        self._closed = False

    def submit(self, token, fn, *args):
        if self._closed:
            return

        def job():
            result, error = None, None
            try:
                result = fn(*args)
            except Exception as e:  # noqa: BLE001 — ошибку отдаём в интерфейс
                error = e
            if not self._closed:
                try:
                    self.done.emit(token, result, error)
                except RuntimeError:
                    pass

        try:
            self._pool.submit(job)
        except RuntimeError:
            pass

    def shutdown(self):
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)


class ImageCache(QObject):
    """Иконки предметов, способностей и героев: диск → память, качаются в фоне."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmaps: Dict[str, QPixmap] = {}
        self._waiting: Dict[str, list] = {}
        self._runner = AsyncRunner(4, self)
        self._runner.done.connect(self._on_done)

    def request(self, url: str, callback):
        if not url:
            return
        pixmap = self._pixmaps.get(url)
        if pixmap is not None:
            _safe_call(callback, pixmap)
            return
        if url in self._waiting:
            self._waiting[url].append(callback)
            return
        self._waiting[url] = [callback]
        self._runner.submit(url, fetch_image_bytes, url)

    def _on_done(self, url, data, error):
        callbacks = self._waiting.pop(url, [])
        if error is not None or not data:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        self._pixmaps[url] = pixmap
        for cb in callbacks:
            _safe_call(cb, pixmap)

    def shutdown(self):
        self._runner.shutdown()


# ──────────────────────────────────────────────────────────────────────────────
#  Окно сборок
# ──────────────────────────────────────────────────────────────────────────────
TALENT_COLOR = "#e0b95b"


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


class ClickLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class IconTile(ClickLabel):
    """Квадратик с картинкой; пока картинка не скачалась — подпись-заглушка."""

    def __init__(self, w: int, h: int, placeholder: str = "", color: str = MUTED, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.setAlignment(Qt.AlignCenter)
        self.setText(placeholder)
        self.setStyleSheet(f"background: #1c1c28; color: {color}; border: 1px solid #2a2a3a;"
                           " border-radius: 4px; font-size: 9px; font-weight: bold;")

    def set_pixmap(self, pixmap: QPixmap):
        self.setText("")
        self.setPixmap(pixmap.scaled(self.width() - 2, self.height() - 2,
                                     Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))


class TalentTree(QWidget):
    """Древо талантов как в игре: на каждом из 4 уровней горит лист того таланта, который берут чаще."""

    W, H, SCALE = 74, 86, 2.1
    CX, CY, RING_R = 36.6, 43.0, 27.0
    LEAF_LEN, LEAF_HALF = 11.0, 2.5
    # Снизу вверх (уровни 10/15/20/25): высота ветки на стволе, угол листа над горизонталью, отступ основания
    TIERS = ((61.0, 18.0, 7.0), (50.0, 14.0, 7.0), (40.0, 26.0, 7.0), (31.0, 56.0, 3.0))
    DIM, DIM_EDGE, STEM_DIM = "#79859a", "#4b5362", "#566072"
    HOT_BASE, HOT_TIP = "#ff7a18", "#ffd27a"

    def __init__(self, talents: list, consts, parent=None):
        super().__init__(parent)
        self.talents, self.consts = talents, consts
        self.setFixedSize(int(self.W * self.SCALE), int(self.H * self.SCALE))
        self.setMouseTracking(True)

    def _leaf(self, tier: int, side: int) -> Tuple[QPointF, QPointF]:
        """Основание и кончик листа; side: -1 — левый, +1 — правый."""
        y, angle, off = self.TIERS[tier]
        a = math.radians(angle)
        base = QPointF(self.CX + side * off, y - 1.0)
        return base, QPointF(base.x() + side * self.LEAF_LEN * math.cos(a), base.y() - self.LEAF_LEN * math.sin(a))

    def _pick(self, tier: int) -> str:
        return (self.talents[tier].get("picked") or "") if tier < len(self.talents) else ""

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(self.SCALE, self.SCALE)
        lit_tiers = [i for i in range(4) if self._pick(i)]
        top_lit = max(lit_tiers) if lit_tiers else -1

        # Кольцо и засечки под ним
        p.setPen(QPen(QColor(110, 120, 138, 110), 0.8))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(self.CX, self.CY), self.RING_R, self.RING_R)
        p.setPen(QPen(QColor("#3d4452"), 1.6, Qt.SolidLine, Qt.FlatCap))
        for deg in (150, 128, 108, 90, 72, 52, 30):
            r = math.radians(deg)
            c = QPointF(self.CX + 31 * math.cos(r), self.CY + 31 * math.sin(r))
            d = QPointF(math.cos(r), math.sin(r)) * 1.4
            p.drawLine(c - d, c + d)

        # Ствол и ветки: тусклые целиком, а у горящих — оранжевые
        stem_top, stem_bottom = self.TIERS[3][0] - 2, 69.0
        p.setPen(QPen(QColor(self.STEM_DIM), 1.6, Qt.SolidLine, Qt.FlatCap))
        p.drawLine(QPointF(self.CX, stem_top), QPointF(self.CX, stem_bottom))
        for tier in range(4):
            y = self.TIERS[tier][0]
            for side in (-1, 1):
                p.drawLine(QPointF(self.CX, y), QPointF(self.CX + side * self.TIERS[tier][2], y - 1.0))
        hot = QColor(self.HOT_BASE)
        if top_lit >= 0:
            glow = QColor(255, 140, 40, 70)
            p.setPen(QPen(glow, 5.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(self.CX, self.TIERS[top_lit][0]), QPointF(self.CX, stem_bottom))
            p.setPen(QPen(hot, 1.8, Qt.SolidLine, Qt.FlatCap))
            p.drawLine(QPointF(self.CX, self.TIERS[top_lit][0]), QPointF(self.CX, stem_bottom))
        for tier in lit_tiers:
            side = -1 if self._pick(tier) == "left" else 1
            y, _a, off = self.TIERS[tier]
            p.setPen(QPen(hot, 1.6, Qt.SolidLine, Qt.FlatCap))
            p.drawLine(QPointF(self.CX, y), QPointF(self.CX + side * off, y - 1.0))

        # Листья
        for tier in range(4):
            for side, name in ((-1, "left"), (1, "right")):
                self._draw_leaf(p, tier, side, self._pick(tier) == name)

    def _draw_leaf(self, p: QPainter, tier: int, side: int, lit: bool):
        base, tip = self._leaf(tier, side)
        mid = QPointF((base.x() + tip.x()) / 2, (base.y() + tip.y()) / 2)
        ang = math.atan2(tip.y() - base.y(), tip.x() - base.x())
        n = QPointF(-math.sin(ang), math.cos(ang)) * self.LEAF_HALF
        path = QPainterPath(base)
        path.quadTo(mid + n, tip)
        path.quadTo(mid - n, base)
        if lit:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 140, 40, 70))
            glow = QPainterPath(base)
            g = n * 1.8
            glow.quadTo(mid + g, tip)
            glow.quadTo(mid - g, base)
            p.drawPath(glow)
            grad = QLinearGradient(base, tip)
            grad.setColorAt(0, QColor(self.HOT_BASE))
            grad.setColorAt(1, QColor(self.HOT_TIP))
            p.setBrush(QBrush(grad))
            p.setPen(QPen(QColor("#b34a00"), 0.4))
        else:
            p.setBrush(QColor(self.DIM))
            p.setPen(QPen(QColor(self.DIM_EDGE), 0.5))
        p.drawPath(path)

    def mouseMoveEvent(self, event):
        x, y = event.pos().x() / self.SCALE, event.pos().y() / self.SCALE
        for tier in range(min(4, len(self.talents))):
            t = self.talents[tier]
            for side, name in ((-1, "left"), (1, "right")):
                base, tip = self._leaf(tier, side)
                if math.hypot(x - (base.x() + tip.x()) / 2, y - (base.y() + tip.y()) / 2) <= 6.5 and t.get(name):
                    key, pct = t[name], t.get(name + "_pct", 0)
                    label = self.consts.ability_name(key) if self.consts else key
                    star = " · выбирают чаще" if self._pick(tier) == name else ""
                    QToolTip.showText(event.globalPos(), f"{t.get('level')} ур. · {label} · {pct}%{star}", self)
                    return
        QToolTip.hideText()


class BuildsWindow(QWidget):
    """Сборка на героя: стартовый закуп → поздняя игра с процентами, порядок прокачки."""

    positionChanged = pyqtSignal(int, int)
    roleChanged = pyqtSignal(int)
    heroChanged = pyqtSignal(int)
    closed = pyqtSignal()
    WIDTH, HEIGHT = 352, 690
    ITEM_W, ITEM_H = 38, 28

    def __init__(self, service: BuildsService, images: ImageCache, role: int = 0, hero_id: int = 0):
        super().__init__()
        self.service, self.images = service, images
        self.role = role if 0 <= role < len(POSITIONS) else 0
        self.hero_id = 0
        self.consts: Optional[DotaConstants] = None
        self.data: Optional[dict] = None
        self._pending_hero = hero_id if _is_int(hero_id) and hero_id > 0 else 0
        self._game_hero = 0
        self._token = 0
        self._filling = False
        self._status = ""

        self._runner = AsyncRunner(1, self)
        self._runner.done.connect(self._on_done)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setWindowTitle("Сборки — " + APP_TITLE)
        self._build_ui()
        self._set_status("Загружаю справочники OpenDota…")
        self._runner.submit(("consts",), self.service.constants)

    # ── интерфейс ───────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame(self)
        card.setObjectName("builds")
        card.setStyleSheet(f"#builds {{ background-color: rgba(17, 17, 24, 245); border: 1px solid {ACCENT};"
                           " border-radius: 14px; }")
        outer.addWidget(card)
        body = QVBoxLayout(card)
        body.setContentsMargins(12, 10, 12, 10)
        body.setSpacing(8)

        # Шапка: портрет, имя, позиция; за неё окно можно таскать
        header = DragHeader()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        self.portrait = IconTile(64, 36, "?")
        names = QVBoxLayout()
        names.setSpacing(0)
        self.lbl_hero = QLabel("Выбери героя")
        self.lbl_hero.setFont(QFont(FONT, 11, QFont.Bold))
        self.lbl_hero.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self.lbl_role = caption("", size=9, color=MUTED)
        names.addWidget(self.lbl_hero)
        names.addWidget(self.lbl_role)
        btn_close = make_button("✕", size=(22, 22), font=QFont(FONT_SYMBOL, 10, QFont.Bold), style=(
            "QPushButton { background: transparent; color: #777; border: none; }"
            f" QPushButton:hover {{ color: {DANGER}; }}"))
        btn_close.clicked.connect(self.close)
        hl.addWidget(self.portrait)
        hl.addLayout(names, 1)
        hl.addWidget(btn_close, 0, Qt.AlignTop)
        body.addWidget(header)

        # Поиск героя
        view_style = (f"background: {BG}; color: {TEXT}; selection-background-color: {ACCENT};"
                      " selection-color: #000; border: 1px solid #2a2a3a;")
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.NoInsert)
        self.combo.setMaxVisibleItems(15)
        self.combo.lineEdit().setPlaceholderText("Поиск героя…")
        self.combo.setStyleSheet(
            f"QComboBox {{ background: #1a1a24; color: {TEXT}; border: 1px solid #2a2a3a; border-radius: 6px;"
            " padding: 3px 8px; }"
            f" QComboBox:focus {{ border: 1px solid {ACCENT}; }}"
            f" QComboBox QAbstractItemView {{ {view_style} }}")
        completer = self.combo.completer()
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.popup().setStyleSheet(view_style)
        self.combo.currentIndexChanged.connect(self._on_combo)
        body.addWidget(self.combo)

        # Вкладки позиций
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self.role_group = QButtonGroup(self)
        self.role_group.setExclusive(True)
        self.role_buttons = []
        for i, name in enumerate(POSITIONS):
            btn = make_button(name, height=26, font=QFont(FONT, 9, QFont.Bold), style=(
                f"QPushButton {{ background: #1a1a24; color: {MUTED}; border: 1px solid #2a2a3a; border-radius: 6px; }}"
                f" QPushButton:hover {{ border: 1px solid {ACCENT}; }}"
                " QPushButton:checked { background: #f2f2f2; color: #000; border: 1px solid #f2f2f2; }"))
            btn.setCheckable(True)
            btn.setChecked(i == self.role)
            btn.setToolTip(f"Позиция {i + 1}: {POSITION_NAMES[i]}")
            btn.clicked.connect(lambda _=False, idx=i: self.set_role(idx))
            self.role_group.addButton(btn, i)
            self.role_buttons.append(btn)
            tabs.addWidget(btn)
        body.addLayout(tabs)

        # Содержимое с прокруткой
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)  # иначе Qt зальёт фон светло-серым
        scroll.setStyleSheet(
            "QScrollArea, QWidget#qt_scrollarea_viewport, QWidget#buildsContent { background: transparent; }"
            " QScrollBar:vertical { width: 6px; background: transparent; }"
            " QScrollBar::handle:vertical { background: #333344; border-radius: 3px; min-height: 30px; }"
            " QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }")
        content = QWidget()
        content.setObjectName("buildsContent")
        content.setAutoFillBackground(False)
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 4, 0)
        cl.setSpacing(4)

        self.phase_grids = {}
        for phase, title in PHASES:
            cl.addWidget(caption(title.upper(), size=10, color=TEXT))
            grid = QGridLayout()
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(2)
            holder = QWidget()
            holder.setLayout(grid)
            cl.addWidget(holder)
            self.phase_grids[phase] = grid

        cl.addWidget(caption("ПРОКАЧКА СПОСОБНОСТЕЙ", size=10, color=TEXT))
        self.skill_grid = QGridLayout()
        self.skill_grid.setHorizontalSpacing(3)
        self.skill_grid.setVerticalSpacing(2)
        holder = QWidget()
        holder.setLayout(self.skill_grid)
        cl.addWidget(holder)
        self.lbl_skills = caption("", size=9, color=DIM)
        self.lbl_skills.setWordWrap(True)
        cl.addWidget(self.lbl_skills)

        cl.addWidget(caption("ТАЛАНТЫ", size=10, color=TEXT))
        self.talents_grid = QGridLayout()
        self.talents_grid.setHorizontalSpacing(6)
        self.talents_grid.setVerticalSpacing(3)
        holder = QWidget()
        holder.setLayout(self.talents_grid)
        cl.addWidget(holder)

        hint = caption("Клик по предмету — название копируется, вставь его в поиск магазина (Ctrl+V).",
                       size=9, color=DIM)
        hint.setWordWrap(True)
        cl.addWidget(hint)
        cl.addStretch()
        scroll.setWidget(content)
        body.addWidget(scroll, 1)

        # Подвал: откуда данные и кнопка обновления
        footer = QHBoxLayout()
        self.lbl_status = caption("", size=9, color=DIM)
        self.lbl_status.setWordWrap(True)
        btn_refresh = make_button("⟳", size=(26, 26), font=QFont(FONT_SYMBOL, 11, QFont.Bold), style=(
            f"QPushButton {{ background: transparent; color: {ACCENT}; border: 1px solid #2a2a3a; border-radius: 6px; }}"
            f" QPushButton:hover {{ border: 1px solid {ACCENT}; }}"))
        btn_refresh.setToolTip("Скачать свежую сборку, не дожидаясь суточного обновления")
        btn_refresh.clicked.connect(lambda: self.refresh(force=True))
        footer.addWidget(self.lbl_status, 1)
        footer.addWidget(btn_refresh)
        body.addLayout(footer)

        self._render()

    def place(self, pos):
        if pos is not None:
            point = QPoint(int(pos[0]), int(pos[1]))
            if any(s.availableGeometry().contains(point) for s in QApplication.screens()):
                self.move(point)
                return
        area = QApplication.primaryScreen().availableGeometry()
        y = max(area.top(), min(area.top() + 90, area.bottom() - self.HEIGHT))  # низкий экран — прижимаем выше
        self.move(area.right() - self.WIDTH - 16, y)

    def dock_to_shop(self, side: str) -> None:
        """Ставит окно впритык к магазину (туда, где у Dota встроенные «Стандартные предметы»).

        side='right' — магазин справа, окно слева от него; side='left' — наоборот.
        """
        try:
            geo = QApplication.primaryScreen().geometry()
        except Exception:  # noqa: BLE001
            return
        shop_w = int(geo.width() * 0.31)  # магазин в Dota 2 ~31% ширины при 16:9
        y = max(geo.top() + 10, min(geo.top() + 70, geo.bottom() - self.HEIGHT))
        if side == "right":
            x = geo.right() - shop_w - self.WIDTH + 2
        else:
            x = geo.left() + shop_w - 2
        x = max(geo.left(), min(x, geo.right() - self.WIDTH))
        self.move(x, y)

    def showEvent(self, event):
        super().showEvent(event)
        keep_on_top(self)

    def moveEvent(self, event):
        super().moveEvent(event)
        if QApplication.mouseButtons() & Qt.LeftButton:
            self.positionChanged.emit(self.x(), self.y())

    def closeEvent(self, event):
        self._runner.shutdown()
        super().closeEvent(event)
        self.closed.emit()

    # ── управление ──────────────────────────────────────────────────────────
    def set_role(self, role: int):
        if not 0 <= role < len(POSITIONS):
            return
        self.role = role
        self.role_buttons[role].setChecked(True)
        self._render()
        self.roleChanged.emit(role)

    def follow_game_hero(self, hero_id: int):
        """Подхватывает героя из игры, когда он меняется. Ручной выбор держится до следующего пика."""
        if not _is_int(hero_id) or hero_id <= 0:
            self._game_hero = 0
            return
        if hero_id != self._game_hero:
            self._game_hero = hero_id
            self.set_hero(hero_id)

    def set_hero(self, hero_id: int, force: bool = False):
        if not _is_int(hero_id) or hero_id <= 0:
            return
        if self.consts is None:
            self._pending_hero = hero_id
            return
        if hero_id == self.hero_id and self.data is not None and not force:
            return
        changed = hero_id != self.hero_id
        self.hero_id = hero_id
        if changed:
            self.data = None
            self._select_combo(hero_id)
            self.lbl_hero.setText(self.consts.hero_name(hero_id))
            self.portrait.setText("?")  # заодно убирает портрет прошлого героя
            self.images.request(self.consts.hero_img(hero_id),
                                lambda pm, h=hero_id: self.hero_id == h and self.portrait.set_pixmap(pm))
            self.heroChanged.emit(hero_id)
        self._render()
        self._set_status("Загружаю сборку…")
        self._token += 1
        self._runner.submit(("builds", self._token, hero_id), self.service.builds, hero_id, force)

    def refresh(self, force: bool = True):
        if self.consts is None:
            self._set_status("Загружаю справочники OpenDota…")
            self._runner.submit(("consts",), self.service.constants, force)
        elif self.hero_id:
            self.set_hero(self.hero_id, force=force)

    def _select_combo(self, hero_id: int):
        idx = self.combo.findData(hero_id)
        if idx >= 0:
            self._filling = True
            self.combo.setCurrentIndex(idx)
            self._filling = False

    def _on_combo(self, index: int):
        if self._filling or index < 0:
            return
        hero_id = self.combo.itemData(index)
        if _is_int(hero_id):
            self.set_hero(hero_id)

    def _set_status(self, text: str):
        self._status = text
        self.lbl_status.setText(text)

    def _flash(self, text: str):
        self.lbl_status.setText(text)
        QTimer.singleShot(3000, lambda: _safe_call(self.lbl_status.setText, self._status))

    def _copy_item(self, key: str):
        name = self.consts.item_name(key) if self.consts else key
        QApplication.clipboard().setText(name)
        self._flash(f"Скопировано: {name}")

    # ── данные ──────────────────────────────────────────────────────────────
    def _on_done(self, token, result, error):
        if token[0] == "consts":
            if error is not None:
                self._set_status(f"Не удалось загрузить справочники: {error}. Нажми ⟳, чтобы повторить.")
                return
            self.consts = result
            self._fill_combo()
            hero = self._pending_hero or self._game_hero
            self._pending_hero = 0
            if hero:
                self.set_hero(hero)
            else:
                self._set_status("Выбери героя в поиске или начни матч — герой подхватится сам.")
            return

        _, tok, hero_id = token
        if tok != self._token or hero_id != self.hero_id:
            return  # пока грузилось, выбрали другого героя
        if error is not None:
            self._set_status(f"Не удалось загрузить сборку: {error}")
            return
        self.data = result
        self._render()
        self._set_status(self._source_text(result))

    def _fill_combo(self):
        self._filling = True
        self.combo.clear()
        for hid, h in sorted(self.consts.heroes.items(), key=lambda kv: kv[1]["localized_name"]):
            self.combo.addItem(h["localized_name"], hid)
        self.combo.setCurrentIndex(-1)
        self._filling = False

    @staticmethod
    def _source_text(data: dict) -> str:
        source = {"stratz": "Stratz · Divine–Immortal" + (" (на редких позициях и Legend–Ancient)" if data.get("wide") else ""),
                  "explorer": f"OpenDota · про-матчи за {BUILDS_DAYS} дн.",
                  "mixed": "OpenDota · предметы без разбивки по позициям",
                  "popularity": "OpenDota · предметы без разбивки по позициям, прокачки нет"}
        text = f"{source.get(data.get('source'), 'OpenDota')} · обновлено {format_age(data.get('age'))}"
        if data.get("stratz_error"):
            text += f" · Stratz недоступен ({data['stratz_error']}), показаны про-матчи"
        if data.get("stale"):
            text += f" · свежие данные не скачались ({data.get('error', 'ошибка')}), показан кэш"
        return text

    def _render(self):
        pos = None
        if self.data:
            pos = (self.data.get("positions") or {}).get(str(self.role + 1))
        games = pos.get("games", 0) if pos else 0

        role_text = f"{POSITIONS[self.role]} · {POSITION_NAMES[self.role]}"
        if self.data:
            role_text += f" · {games} игр" if games else " · игр на этой позиции нет"
        self.lbl_role.setText(role_text)
        for i, btn in enumerate(self.role_buttons):
            g = ((self.data or {}).get("positions") or {}).get(str(i + 1), {}).get("games", 0)
            btn.setToolTip(f"Позиция {i + 1}: {POSITION_NAMES[i]}" + (f" — {g} игр" if self.data else ""))

        for phase, grid in self.phase_grids.items():
            _clear_layout(grid)
            items = ((pos or {}).get("phases") or {}).get(phase) or []
            if not items:
                grid.addWidget(caption("—" if self.data else "", size=9, color=DIM), 0, 0)
                continue
            for i, it in enumerate(items[:ITEMS_PER_PHASE]):
                grid.addWidget(self._item_cell(it.get("key", ""), it.get("pct", 0)), i // 5, i % 5)

        _clear_layout(self.skill_grid)
        skills = (pos or {}).get("skills") or {}
        order = skills.get("order") or []
        # Таланты в прокачке и так рисуются в блоке «ТАЛАНТЫ» ниже — здесь их пропускаем
        regular = [(lvl, k) for lvl, k in enumerate(order[:SKILL_LEN], start=1) if not is_talent(k)]
        for i, (level, key) in enumerate(regular):
            self.skill_grid.addWidget(self._skill_cell(level, key), i // 9, i % 9)
        if order:
            note = f"Самый частый порядок — {skills.get('pct')}% из {skills.get('games')} игр" if skills.get("pct") \
                else f"По {skills.get('games')} играм" + (", порядок собран по уровням прокачки" if skills.get("approx") else "")
            self.lbl_skills.setText(note + ". Таланты показаны отдельно ниже.")
        else:
            self.lbl_skills.setText("Нет данных о прокачке." if self.data else "")

        _clear_layout(self.talents_grid)
        talents = (pos or {}).get("talents") or []
        if any(t.get("sided") and (t.get("left") or t.get("right")) for t in talents):
            self.talents_grid.addWidget(TalentTree(talents, self.consts), 0, 0, Qt.AlignHCenter)
        elif self.data:
            self.talents_grid.addWidget(caption("Данных о талантах нет (нужны данные Stratz).", size=9, color=DIM), 0, 0)

    def _item_cell(self, key: str, pct: int) -> QWidget:
        cell = QWidget()
        v = QVBoxLayout(cell)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        tile = IconTile(self.ITEM_W, self.ITEM_H, key[:4].upper())
        tile.setCursor(Qt.PointingHandCursor)
        if self.consts:
            tile.setToolTip(f"{self.consts.item_name(key)} — {pct}%")
            self.images.request(self.consts.item_img(key), tile.set_pixmap)
        tile.clicked.connect(lambda k=key: self._copy_item(k))
        lbl = QLabel(f"{pct}%")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 9px; background: transparent;")
        v.addWidget(tile, 0, Qt.AlignHCenter)
        v.addWidget(lbl)
        return cell

    def _skill_cell(self, level: int, key: str) -> QWidget:
        cell = QWidget()
        v = QVBoxLayout(cell)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        num = QLabel(str(level))
        num.setAlignment(Qt.AlignCenter)
        num.setStyleSheet(f"color: {DIM}; font-size: 8px; background: transparent;")
        name = self.consts.ability_name(key) if self.consts else key
        if "attribute" in key:
            tile = IconTile(30, 30, "+", color=TEXT)
            tile.setToolTip("Бонус к атрибутам")
        else:
            tile = IconTile(30, 30, name[:2].upper())
            tile.setToolTip(name)
            if self.consts:
                self.images.request(self.consts.ability_img(key), tile.set_pixmap)
        v.addWidget(num)
        v.addWidget(tile, 0, Qt.AlignHCenter)
        return cell
