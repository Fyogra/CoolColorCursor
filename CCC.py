import sys
import time
import math
import ctypes
import random
import logging
import json
import os
import winreg
from collections import deque

from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QSlider, QPushButton, QFrame, QGraphicsDropShadowEffect,
                             QStyle, QStyleOptionSlider, QDialog, QSystemTrayIcon, QMenu, QAction)
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, QPoint, pyqtSignal
from PyQt5.QtGui import (QPainter, QPen, QColor, QCursor, QFont, QConicalGradient, 
                             QRadialGradient, QBrush, QIcon, QPixmap, QPainterPath, QLinearGradient)

# --- НАДЕЖНОЕ ОПРЕДЕЛЕНИЕ ПАПКИ ДЛЯ ФАЙЛОВ ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_FILE = os.path.join(BASE_DIR, "mouse_trail.log")

REG_AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "CCC"

# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
logging.basicConfig(
    filename=LOG_FILE,
    filemode='a',
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO,
    encoding='utf-8'
)


# --- ФУНКЦИИ АВТОЗАГРУЗКИ ---
def set_autostart(enable: bool):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_AUTOSTART_KEY, 0, winreg.KEY_ALL_ACCESS)
        if enable:
            exe_path = f'"{os.path.abspath(sys.argv[0])}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
            logging.info("Добавлено в автозагрузку")
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
                logging.info("Удалено из автозагрузки")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        logging.error(f"Ошибка настройки автозагрузки: {e}")


# --- ГЕНЕРАЦИЯ СТИЛИЗОВАННОЙ НЕОНОВОЙ ИКОНКИ ---
def create_tray_icon():
    size = 128
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    
    # 1. Темный округлый фон под стиль приложения
    bg_path = QPainterPath()
    bg_path.addRoundedRect(4, 4, 120, 120, 32, 32)
    painter.fillPath(bg_path, QColor("#111118"))
    
    # Тонкая рамка с легким свечением
    border_pen = QPen(QColor("#222238"), 3)
    painter.setPen(border_pen)
    painter.drawPath(bg_path)

    # 2. Неоновый шлейф (начинается прямо ИЗ ВЕРШИНЫ курсора: x=36, y=36)
    trail_path = QPainterPath()
    trail_path.moveTo(36, 36)
    
    # Плавная петелька вверх и вправо
    trail_path.cubicTo(36, 10, 85, 10, 85, 45)
    trail_path.cubicTo(85, 90, 20, 85, 105, 108)

    # Внешнее неоновое свечение шлейфа
    glow_pen = QPen(QColor(255, 0, 128, 110), 14, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(glow_pen)
    painter.drawPath(trail_path)

    # Основная яркая линия шлейфа с градиентом
    trail_grad = QLinearGradient(36, 36, 105, 108)
    trail_grad.setColorAt(0.0, QColor("#00ffc8"))
    trail_grad.setColorAt(0.5, QColor("#ff0080"))
    trail_grad.setColorAt(1.0, QColor("#9d00ff"))
    
    core_pen = QPen(QBrush(trail_grad), 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(core_pen)
    painter.drawPath(trail_path)

    # 3. Стильный неоновый курсор
    cursor_path = QPainterPath()
    cursor_path.moveTo(36, 36)      # Кончик
    cursor_path.lineTo(36, 80)      # Левый край
    cursor_path.lineTo(48, 68)      # Внутренний угол
    cursor_path.lineTo(60, 92)      # Ножка право
    cursor_path.lineTo(70, 87)      # Низ ножки
    cursor_path.lineTo(58, 62)      # Ножка лево
    cursor_path.lineTo(74, 62)      # Правый край
    cursor_path.closeSubpath()

    # Заливка курсора и неоновый контур
    painter.setBrush(QBrush(QColor("#161622")))
    cursor_pen = QPen(QColor("#00ffc8"), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(cursor_pen)
    painter.drawPath(cursor_path)

    # Яркая белая точка-источник прямо на вершине
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor("#ffffff")))
    painter.drawEllipse(QPointF(36, 36), 4.5, 4.5)

    painter.end()

    # Автоматически сохраняем файл иконки для сборки в EXE
    icon_path = os.path.join(BASE_DIR, "app_icon.ico")
    if not os.path.exists(icon_path):
        pixmap.save(icon_path, "ICO")

    return QIcon(pixmap)


# --- ДИАЛОГ АВТОЗАПУСКА ---
class AutostartDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(320, 180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        bg_frame = QFrame(self)
        bg_frame.setStyleSheet("""
            QFrame {
                background-color: #111118;
                border-radius: 16px;
                border: 2px solid #00ffc8;
            }
        """)
        frame_layout = QVBoxLayout(bg_frame)
        frame_layout.setContentsMargins(20, 20, 20, 20)
        frame_layout.setSpacing(15)

        title = QLabel("АВТОЗАПУСК")
        title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        title.setStyleSheet("color: #00ffc8; border: none;")
        title.setAlignment(Qt.AlignCenter)
        frame_layout.addWidget(title)

        text = QLabel("Хотите ли вы добавить приложение\nв автозапуск Windows?")
        text.setFont(QFont("Segoe UI", 9))
        text.setStyleSheet("color: #dddddd; border: none;")
        text.setAlignment(Qt.AlignCenter)
        frame_layout.addWidget(text)

        btns_layout = QHBoxLayout()
        btns_layout.setSpacing(10)

        btn_yes = QPushButton("ДА")
        btn_yes.setFixedHeight(32)
        btn_yes.setFont(QFont("Segoe UI", 9, QFont.Bold))
        btn_yes.setCursor(Qt.PointingHandCursor)
        btn_yes.setStyleSheet("""
            QPushButton {
                background: #00ffc8;
                color: #000000;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background: #00cc99;
            }
        """)
        btn_yes.clicked.connect(self.accept)

        btn_no = QPushButton("НЕТ")
        btn_no.setFixedHeight(32)
        btn_no.setFont(QFont("Segoe UI", 9, QFont.Bold))
        btn_no.setCursor(Qt.PointingHandCursor)
        btn_no.setStyleSheet("""
            QPushButton {
                background: #222233;
                color: #888888;
                border: 1px solid #444455;
                border-radius: 8px;
            }
            QPushButton:hover {
                background: #333344;
                color: #ffffff;
            }
        """)
        btn_no.clicked.connect(self.reject)

        btns_layout.addWidget(btn_yes)
        btns_layout.addWidget(btn_no)
        frame_layout.addLayout(btns_layout)

        layout.addWidget(bg_frame)


# --- КАСТОМНЫЙ ПОЛЗУНОК ---
class ClickSlider(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            sr = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
            
            if self.orientation() == Qt.Horizontal:
                slider_length = self.style().pixelMetric(QStyle.PM_SliderLength, opt, self)
                slider_min = sr.x()
                slider_max = sr.right() - slider_length + 1
                pos = event.pos().x() - slider_length // 2
                val = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), pos - slider_min, slider_max - slider_min, opt.upsideDown)
            else:
                slider_length = self.style().pixelMetric(QStyle.PM_SliderLength, opt, self)
                slider_min = sr.y()
                slider_max = sr.bottom() - slider_length + 1
                pos = event.pos().y() - slider_length // 2
                val = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), pos - slider_min, slider_max - slider_min, opt.upsideDown)
            
            self.setValue(val)
            event.accept()
        super().mousePressEvent(event)


# --- КАСТОМНОЕ ЦВЕТОВОЕ КОЛЕСО ---
class ColorWheel(QWidget):
    colorChanged = pyqtSignal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(180, 180)
        self.hue = 0.0          
        self.sat = 1.0          
        self.val = 1.0          
        self.current_color = QColor.fromHsvF(self.hue, self.sat, self.val)
        self.is_dragging = False

    def set_color(self, color: QColor):
        h, s, v, _ = color.getHsvF()
        if h >= 0:
            self.hue = h
        self.sat = s
        self.val = v
        self.current_color = QColor(color)
        self.update()

    def set_value(self, value_f: float):
        self.val = max(0.0, min(1.0, value_f))
        self.current_color = QColor.fromHsvF(self.hue, self.sat, self.val)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        center = QPointF(self.width() / 2, self.height() / 2)
        radius = min(self.width(), self.height()) / 2 - 8

        c_grad = QConicalGradient(center, 0)
        for deg in range(0, 360, 10):
            c_grad.setColorAt(deg / 360.0, QColor.fromHsvF(deg / 360.0, 1.0, self.val))
        c_grad.setColorAt(1.0, QColor.fromHsvF(0.0, 1.0, self.val))

        painter.setBrush(QBrush(c_grad))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, radius, radius)

        r_grad = QRadialGradient(center, radius)
        center_color = QColor.fromHsvF(0.0, 0.0, self.val)
        edge_color = QColor.fromHsvF(0.0, 0.0, self.val, 0)

        r_grad.setColorAt(0.0, center_color)
        r_grad.setColorAt(1.0, edge_color)

        painter.setBrush(QBrush(r_grad))
        painter.drawEllipse(center, radius, radius)

        angle_rad = math.radians(self.hue * 360.0)
        dist = self.sat * radius
        marker_x = center.x() + dist * math.cos(angle_rad)
        marker_y = center.y() - dist * math.sin(angle_rad)

        painter.setPen(QPen(QColor(0, 0, 0, 150), 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(marker_x, marker_y), 7, 7)

        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawEllipse(QPointF(marker_x, marker_y), 6, 6)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.update_color_from_pos(event.pos())

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            self.update_color_from_pos(event.pos())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False

    def update_color_from_pos(self, pos):
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = min(self.width(), self.height()) / 2 - 8

        dx = pos.x() - center.x()
        dy = center.y() - pos.y()

        dist = math.hypot(dx, dy)
        self.sat = min(1.0, dist / radius)

        angle_rad = math.atan2(dy, dx)
        if angle_rad < 0:
            angle_rad += 2 * math.pi

        self.hue = angle_rad / (2 * math.pi)
        self.current_color = QColor.fromHsvF(self.hue, self.sat, self.val)
        self.update()
        self.colorChanged.emit(self.current_color)


# --- ОКНО ПАЛИТРЫ ---
class ColorPickerWindow(QDialog):
    colorPicked = pyqtSignal(QColor)

    def __init__(self, initial_color, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(220, 260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        bg_frame = QFrame(self)
        bg_frame.setStyleSheet("""
            QFrame {
                background-color: #111118;
                border-radius: 12px;
                border: 1px solid #00ffc8;
            }
        """)
        frame_layout = QVBoxLayout(bg_frame)
        frame_layout.setAlignment(Qt.AlignCenter)

        header = QHBoxLayout()
        lbl = QLabel("ВЫБОР ЦВЕТА")
        lbl.setStyleSheet("color: #00ffc8; font-size: 10px; font-weight: bold; border: none;")
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(20, 20)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet("color: #aaa; border: none; font-weight: bold;")
        btn_close.clicked.connect(self.close)

        header.addWidget(lbl)
        header.addStretch()
        header.addWidget(btn_close)
        frame_layout.addLayout(header)

        self.wheel = ColorWheel()
        self.wheel.set_color(initial_color)
        self.wheel.colorChanged.connect(self.on_color_changed)
        frame_layout.addWidget(self.wheel)

        self.val_slider = ClickSlider(Qt.Horizontal)
        self.val_slider.setRange(0, 100)
        self.val_slider.setValue(int(initial_color.valueF() * 100))
        self.val_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 6px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #000, stop:1 #fff); border-radius: 3px; }
            QSlider::handle:horizontal { background: #fff; width: 12px; margin-top: -3px; margin-bottom: -3px; border-radius: 6px; }
        """)
        self.val_slider.valueChanged.connect(self.on_val_changed)
        frame_layout.addWidget(self.val_slider)

        layout.addWidget(bg_frame)

    def on_color_changed(self, color):
        self.colorPicked.emit(color)

    def on_val_changed(self, val):
        self.wheel.set_value(val / 100.0)
        self.colorPicked.emit(self.wheel.current_color)


# --- ДВИЖОК ОВЕРЛЕЯ С ПОДДЕРЖКОЙ ВСПЛЫВАЮЩЕЙ ПАНЕЛИ ЗАДАЧ ---
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED     = 0x00080000
WS_EX_NOACTIVATE  = 0x08000000
WS_EX_TOOLWINDOW  = 0x00000080
GWL_EXSTYLE       = -20

HWND_TOPMOST  = -1
SWP_NOSIZE    = 0x0001
SWP_NOMOVE    = 0x0002
SWP_NOACTIVATE = 0x0010

class MouseTrailOverlay(QWidget):
    def __init__(self, fade_time=0.4, max_width=12, color1=QColor(0, 204, 255), color2=QColor(255, 0, 128)):
        super().__init__()
        self.fade_time = fade_time
        self.max_width = max_width
        self.color1 = color1
        self.color2 = color2

        self.points = deque()
        self.last_raw_pos = None

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.Tool |
            Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        
        desktop_rect = QApplication.desktop().geometry()
        self.setGeometry(desktop_rect.x(), desktop_rect.y(), desktop_rect.width(), desktop_rect.height() - 2)

        self.refresh_rate = self.detect_max_refresh_rate()
        timer_interval = max(1, int(1000 / self.refresh_rate))
        self.step_distance = max(1.0, 3.0 * (60.0 / self.refresh_rate))

        logging.info(f"Определена герцовка: {self.refresh_rate} Гц. Интервал таймера: {timer_interval} мс, Шаг точек: {self.step_distance:.2f} px")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_trail)
        self.timer.start(timer_interval)

    def detect_max_refresh_rate(self):
        max_rate = 60.0
        try:
            screens = QApplication.screens()
            for screen in screens:
                rate = screen.refreshRate()
                if rate > max_rate:
                    max_rate = rate
        except Exception as e:
            logging.error(f"Ошибка определения герцовки: {e}")
        return max_rate

    def showEvent(self, event):
        super().showEvent(event)
        try:
            hwnd = int(self.winId())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            
            ctypes.windll.user32.SetWindowLongW(
                hwnd, 
                GWL_EXSTYLE, 
                style | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            )
            
            ctypes.windll.user32.SetWindowPos(
                hwnd, 
                HWND_TOPMOST, 
                0, 0, 0, 0, 
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )
        except Exception as e:
            logging.error(f"Ошибка стилей Windows: {e}")

    def set_colors_instant(self, c1, c2):
        self.color1 = QColor(c1)
        self.color2 = QColor(c2)
        self.points.clear()
        self.repaint()

    def update_trail(self):
        now = time.time()
        pos = QCursor.pos()
        
        current_pt = QPointF(pos.x() - self.x(), pos.y() - self.y())

        while self.points and (now - self.points[0][1]) > self.fade_time:
            self.points.popleft()

        if self.last_raw_pos is not None:
            dist = math.hypot(current_pt.x() - self.last_raw_pos.x(), current_pt.y() - self.last_raw_pos.y())
            
            if dist > self.step_distance:
                steps = int(dist / self.step_distance)
                dx = (current_pt.x() - self.last_raw_pos.x()) / steps
                dy = (current_pt.y() - self.last_raw_pos.y()) / steps
                
                for step in range(1, steps):
                    interp_x = self.last_raw_pos.x() + dx * step
                    interp_y = self.last_raw_pos.y() + dy * step
                    self.points.append((QPointF(interp_x, interp_y), now))

        self.points.append((current_pt, now))
        self.last_raw_pos = current_pt

        if self.points:
            min_x = min(pt[0].x() for pt in self.points) - self.max_width
            max_x = max(pt[0].x() for pt in self.points) + self.max_width
            min_y = min(pt[0].y() for pt in self.points) - self.max_width
            max_y = max(pt[0].y() for pt in self.points) + self.max_width
            
            rect = QRectF(min_x, min_y, max_x - min_x, max_y - min_y).toRect()
            self.update(rect)

    def paintEvent(self, event):
        num_points = len(self.points)
        if num_points < 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        now = time.time()
        c1_r, c1_g, c1_b = self.color1.red(), self.color1.green(), self.color1.blue()
        c2_r, c2_g, c2_b = self.color2.red(), self.color2.green(), self.color2.blue()

        for i in range(num_points - 1):
            p1, t1 = self.points[i]
            p2, _ = self.points[i + 1]

            progress = 1.0 - ((now - t1) / self.fade_time)
            if progress < 0.01:
                continue
            if progress > 1.0:
                progress = 1.0

            alpha = int(255 * (progress ** 1.5))
            width = max(1.0, self.max_width * progress)

            r = int(c2_r + (c1_r - c2_r) * progress)
            g = int(c2_g + (c1_g - c2_g) * progress)
            b = int(c2_b + (c1_b - c2_b) * progress)

            pen = QPen(QColor(r, g, b, alpha), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(p1, p2)


# --- ФОНОВЫЙ КОНТЕЙНЕР ---
class StarryBackgroundFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.stars = []
        self.init_stars()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate_stars)
        self.timer.start(30)

    def init_stars(self):
        for _ in range(100):
            self.stars.append({
                'x': random.randint(0, 400),
                'y': random.randint(0, 500),
                'size': random.uniform(1.0, 2.5),
                'alpha': random.randint(50, 255),
                'speed': random.uniform(0.2, 0.6),
                'delta': random.choice([-2, -1, 1, 2])
            })

    def animate_stars(self):
        for star in self.stars:
            star['y'] -= star['speed']
            if star['y'] < 0:
                star['y'] = 500
                star['x'] = random.randint(0, 400)

            star['alpha'] += star['delta']
            if star['alpha'] >= 255 or star['alpha'] <= 40:
                star['delta'] *= -1

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#111118"))

        for star in self.stars:
            color = QColor(255, 255, 255, max(0, min(255, star['alpha'])))
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(star['x'], star['y']), star['size'], star['size'])


# --- ГЛАВНАЯ ПАНЕЛЬ ---
class ModernControlPanel(QWidget):
    def __init__(self):
        super().__init__()
        logging.info("Инициализация интерфейса")
        self.overlay = None
        self.drag_position = QPoint()

        self.selected_c1 = QColor(0, 204, 255)
        self.selected_c2 = QColor(255, 0, 128)
        self.active_slot = 1
        self.fade_time = 0.4
        self.max_width = 12
        self.autostart_asked = False
        self.autostart_enabled = False
        
        self.custom_history = []
        self.history_buttons = []
        self.picker_dialog = None

        self.load_config()
        self.init_ui()
        self.init_tray()
        
        QTimer.singleShot(300, self.check_autostart_prompt)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    self.selected_c1 = QColor(cfg.get('color1', '#00ccff'))
                    self.selected_c2 = QColor(cfg.get('color2', '#ff0080'))
                    self.max_width = cfg.get('max_width', 12)
                    self.fade_time = cfg.get('fade_time', 0.4)
                    self.autostart_asked = cfg.get('autostart_asked', False)
                    self.autostart_enabled = cfg.get('autostart_enabled', False)
                    
                    hist_data = cfg.get('history', [])
                    self.custom_history = [(QColor(h[0]), QColor(h[1])) for h in hist_data]
                    logging.info("Конфигурация успешно загружена")
            except Exception as e:
                logging.error(f"Ошибка загрузки конфигурации: {e}")

    def save_config(self):
        try:
            cfg = {
                'color1': self.selected_c1.name(),
                'color2': self.selected_c2.name(),
                'max_width': self.max_width,
                'fade_time': self.fade_time,
                'autostart_asked': self.autostart_asked,
                'autostart_enabled': self.autostart_enabled,
                'history': [[c1.name(), c2.name()] for c1, c2 in self.custom_history]
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=4)
            logging.info("Конфигурация успешно сохранена")
        except Exception as e:
            logging.error(f"Ошибка сохранения конфигурации: {e}")

    def check_autostart_prompt(self):
        if not self.autostart_asked:
            dialog = AutostartDialog(self)
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )
            
            if dialog.exec_() == QDialog.Accepted:
                self.autostart_enabled = True
                set_autostart(True)
            else:
                self.autostart_enabled = False
                set_autostart(False)
                
            self.autostart_asked = True
            self.save_config()

    def init_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(380, 450)
        self.setWindowIcon(create_tray_icon())

        self.container = StarryBackgroundFrame(self)
        self.container.setGeometry(0, 0, 380, 450)
        self.container.setStyleSheet("""
            StarryBackgroundFrame {
                border-radius: 20px;
                border: 1px solid #222233;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 204, 255, 50))
        shadow.setOffset(0, 0)
        self.container.setGraphicsEffect(shadow)

        self.left_layout = QVBoxLayout(self.container)
        self.left_layout.setContentsMargins(20, 20, 20, 20)
        self.left_layout.setSpacing(8)

        # Шапка
        self.header_frame = QFrame()
        self.header_frame.setStyleSheet("background: transparent; border: none;")
        header_layout = QHBoxLayout(self.header_frame)
        header_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("CCC|CoolColorCursor")
        title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        title.setStyleSheet("color: #00ffc8;")

        btn_box = QHBoxLayout()
        btn_box.setSpacing(6)

        btn_minimize = QPushButton("─")
        btn_minimize.setFont(QFont("Segoe UI Symbol", 9, QFont.Bold))
        btn_minimize.setFixedSize(24, 24)
        btn_minimize.setCursor(Qt.PointingHandCursor)
        btn_minimize.setStyleSheet("""
            QPushButton { background: transparent; color: #777; border: none; }
            QPushButton:hover { color: #00ffc8; }
        """)
        btn_minimize.clicked.connect(self.hide_to_tray)

        btn_close = QPushButton("✕")
        btn_close.setFont(QFont("Segoe UI Symbol", 10, QFont.Bold))
        btn_close.setFixedSize(24, 24)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton { background: transparent; color: #777; border: none; }
            QPushButton:hover { color: #ff0055; }
        """)
        btn_close.clicked.connect(self.close_application)

        btn_box.addWidget(btn_minimize)
        btn_box.addWidget(btn_close)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addLayout(btn_box)
        self.left_layout.addWidget(self.header_frame)

        self.header_frame.mousePressEvent = self.header_press
        self.header_frame.mouseMoveEvent = self.header_move

        # Слайдеры
        self.lbl_width = QLabel(f"ТОЛЩИНА: {self.max_width} px")
        self.lbl_width.setStyleSheet("color: #aaa; font-size: 10px; font-weight: bold; background: transparent;")
        self.left_layout.addWidget(self.lbl_width)

        slider_w = ClickSlider(Qt.Horizontal)
        slider_w.setRange(2, 50)
        slider_w.setValue(self.max_width)
        slider_w.setTracking(True)
        slider_w.setStyleSheet(self.get_slider_style())
        slider_w.valueChanged.connect(self.change_width)
        self.left_layout.addWidget(slider_w)

        self.lbl_fade = QLabel(f"СКОРОСТЬ ИСЧЕЗНОВЕНИЯ: {int(self.fade_time * 1000)} мс")
        self.lbl_fade.setStyleSheet("color: #aaa; font-size: 10px; font-weight: bold; background: transparent;")
        self.left_layout.addWidget(self.lbl_fade)

        slider_f = ClickSlider(Qt.Horizontal)
        slider_f.setRange(100, 1500)
        slider_f.setValue(int(self.fade_time * 1000))
        slider_f.setTracking(True)
        slider_f.setStyleSheet(self.get_slider_style())
        slider_f.valueChanged.connect(self.change_fade)
        self.left_layout.addWidget(slider_f)

        # Выбор цвета
        lbl_colors = QLabel("ВЫБОР И НАСТРОЙКА ЦВЕТА")
        lbl_colors.setStyleSheet("color: #aaa; font-size: 10px; font-weight: bold; background: transparent;")
        self.left_layout.addWidget(lbl_colors)

        colors_layout = QHBoxLayout()
        self.btn_c1 = QPushButton("Основной")
        self.btn_c2 = QPushButton("Дополнительный")
        for b in (self.btn_c1, self.btn_c2):
            b.setFixedHeight(28)
            b.setFont(QFont("Segoe UI", 9))
            b.setCursor(Qt.PointingHandCursor)

        self.btn_c1.clicked.connect(lambda: self.set_active_slot(1))
        self.btn_c2.clicked.connect(lambda: self.set_active_slot(2))
        colors_layout.addWidget(self.btn_c1)
        colors_layout.addWidget(self.btn_c2)
        self.left_layout.addLayout(colors_layout)

        # Палитра
        picker_layout = QHBoxLayout()
        palette = [
            "#ff0055", "#ff6600", "#ffcc00", "#00ffc8", 
            "#00ccff", "#0055ff", "#9d00ff", "#ffffff"
        ]
        for hex_code in palette:
            p_btn = QPushButton()
            p_btn.setFixedSize(26, 22)
            p_btn.setCursor(Qt.PointingHandCursor)
            p_btn.setStyleSheet(f"background-color: {hex_code}; border-radius: 4px; border: 1px solid #333;")
            p_btn.clicked.connect(lambda _, c=hex_code: self.pick_inline_color(c))
            picker_layout.addWidget(p_btn)
        
        self.btn_arrow = QPushButton("▶")
        self.btn_arrow.setFixedSize(26, 22)
        self.btn_arrow.setCursor(Qt.PointingHandCursor)
        self.btn_arrow.setStyleSheet("""
            QPushButton {
                background: rgba(0, 255, 200, 0.15); 
                color: #00ffc8; 
                border: 1px solid #00ffc8; 
                border-radius: 4px; 
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #00ffc8;
                color: #000;
            }
        """)
        self.btn_arrow.clicked.connect(self.toggle_color_picker_window)
        picker_layout.addWidget(self.btn_arrow)

        self.left_layout.addLayout(picker_layout)

        # Кнопка применения
        self.btn_apply = QPushButton("✦ ПРИМЕНИТЬ ЦВЕТА")
        self.btn_apply.setFixedHeight(36)
        self.btn_apply.setFont(QFont("Segoe UI Symbol", 9, QFont.Bold))
        self.btn_apply.setCursor(Qt.PointingHandCursor)
        self.btn_apply.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a1b26, stop:1 #12131c);
                color: #00ffc8;
                border: 2px solid #00ffc8;
                border-radius: 18px;
            }
            QPushButton:hover {
                background: #00ffc8;
                color: #000000;
            }
        """)
        self.btn_apply.clicked.connect(self.apply_colors)
        self.left_layout.addWidget(self.btn_apply)

        # История
        lbl_hist = QLabel("ИСТОРИЯ ВАШИХ ЦВЕТОВ")
        lbl_hist.setStyleSheet("color: #666; font-size: 9px; font-weight: bold; background: transparent;")
        self.left_layout.addWidget(lbl_hist)

        hist_layout = QHBoxLayout()
        for i in range(5):
            btn = QPushButton()
            btn.setFixedSize(56, 22)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("background: rgba(24, 24, 34, 0.7); border: 1px dashed #333; border-radius: 6px;")
            btn.setEnabled(False)
            btn.clicked.connect(lambda _, idx=i: self.apply_history_color(idx))
            hist_layout.addWidget(btn)
            self.history_buttons.append(btn)
        self.left_layout.addLayout(hist_layout)

        # Пресеты
        lbl_presets = QLabel("ГОТОВЫЕ ПРЕСЕТЫ")
        lbl_presets.setStyleSheet("color: #aaa; font-size: 10px; font-weight: bold; background: transparent;")
        self.left_layout.addWidget(lbl_presets)

        presets_layout = QHBoxLayout()
        presets = [
            (QColor(0, 204, 255), QColor(255, 0, 128)),
            (QColor(157, 0, 255), QColor(0, 255, 240)),
            (QColor(255, 102, 0), QColor(255, 0, 0)),
            (QColor(0, 255, 0), QColor(0, 51, 0)),
            (QColor(255, 0, 255), QColor(75, 0, 130))
        ]

        for c1, c2 in presets:
            p_btn = QPushButton()
            p_btn.setFixedSize(56, 22)
            p_btn.setCursor(Qt.PointingHandCursor)
            p_btn.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c1.name()}, stop:1 {c2.name()});
                    border: 1px solid #333;
                    border-radius: 6px;
                }}
                QPushButton:hover {{ border: 1px solid #ffffff; }}
            """)
            p_btn.clicked.connect(lambda _, color1=c1, color2=c2: self.quick_preset_apply(color1, color2))
            presets_layout.addWidget(p_btn)
        self.left_layout.addLayout(presets_layout)

        self.left_layout.addStretch()

        # Кнопка переключения
        self.btn_toggle = QPushButton("▶ ВКЛЮЧИТЬ ЭФФЕКТ")
        self.btn_toggle.setFixedHeight(36)
        self.btn_toggle.setFont(QFont("Segoe UI Symbol", 9, QFont.Bold))
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.clicked.connect(self.toggle_trail)
        self.left_layout.addWidget(self.btn_toggle)

        self.update_toggle_button_style(is_active=False)
        self.update_buttons_ui()
        self.update_history_ui()

    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(create_tray_icon())
        self.tray_icon.setToolTip("CCC|CoolColorCursor")

        tray_menu = QMenu()
        tray_menu.setStyleSheet("""
            QMenu {
                background-color: #111118;
                color: #ffffff;
                border: 1px solid #00ffc8;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #00ffc8;
                color: #000000;
            }
        """)

        self.action_show = QAction("Открыть настройки", self)
        self.action_show.triggered.connect(self.show_from_tray)
        tray_menu.addAction(self.action_show)

        self.action_toggle = QAction("Включить эффект", self)
        self.action_toggle.triggered.connect(self.toggle_trail)
        tray_menu.addAction(self.action_toggle)

        tray_menu.addSeparator()

        action_quit = QAction("Выход", self)
        action_quit.triggered.connect(self.close_application)
        tray_menu.addAction(action_quit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide_to_tray()
            else:
                self.show_from_tray()

    def hide_to_tray(self):
        if self.picker_dialog:
            self.picker_dialog.close()
        self.hide()
        self.tray_icon.showMessage(
            "CCC|CoolColorCursor",
            "Приложение свернуто в трей.",
            create_tray_icon(),
            1500
        )

    def show_from_tray(self):
        self.show()
        self.activateWindow()

    def close_application(self):
        logging.info("Завершение работы приложения")
        self.save_config()
        self.tray_icon.hide()
        QApplication.quit()

    def toggle_color_picker_window(self):
        if self.picker_dialog and self.picker_dialog.isVisible():
            self.picker_dialog.close()
            self.btn_arrow.setText("▶")
            return

        current = self.selected_c1 if self.active_slot == 1 else self.selected_c2
        self.picker_dialog = ColorPickerWindow(current, self)
        
        pos = self.mapToGlobal(QPoint(self.width() + 10, 0))
        self.picker_dialog.move(pos)
        self.picker_dialog.colorPicked.connect(self.on_window_color_changed)
        
        self.btn_arrow.setText("◀")
        self.picker_dialog.finished.connect(lambda: self.btn_arrow.setText("▶"))
        self.picker_dialog.show()

    def update_picker_position(self):
        if self.picker_dialog and self.picker_dialog.isVisible():
            pos = self.mapToGlobal(QPoint(self.width() + 10, 0))
            self.picker_dialog.move(pos)

    def on_window_color_changed(self, color):
        if self.active_slot == 1:
            self.selected_c1 = color
        else:
            self.selected_c2 = color
        self.update_buttons_ui()

    def update_toggle_button_style(self, is_active):
        if is_active:
            self.btn_toggle.setText("⏹ ВЫКЛЮЧИТЬ ЭФФЕКТ")
            self.btn_toggle.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a1b26, stop:1 #12131c);
                    color: #ff0055;
                    border: 2px solid #ff0055;
                    border-radius: 18px;
                }
                QPushButton:hover {
                    background: #ff0055;
                    color: #ffffff;
                }
            """)
            if hasattr(self, 'action_toggle'):
                self.action_toggle.setText("Выключить эффект")
        else:
            self.btn_toggle.setText("▶ ВКЛЮЧИТЬ ЭФФЕКТ")
            self.btn_toggle.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a1b26, stop:1 #12131c);
                    color: #00ffc8;
                    border: 2px solid #00ffc8;
                    border-radius: 18px;
                }
                QPushButton:hover {
                    background: #00ffc8;
                    color: #000000;
                }
            """)
            if hasattr(self, 'action_toggle'):
                self.action_toggle.setText("Включить эффект")

    def header_press(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()

    def header_move(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self.drag_position)
            self.update_picker_position()

    def set_active_slot(self, slot):
        self.active_slot = slot
        if self.picker_dialog and self.picker_dialog.isVisible():
            current = self.selected_c1 if slot == 1 else self.selected_c2
            self.picker_dialog.wheel.set_color(current)
            self.picker_dialog.val_slider.setValue(int(current.valueF() * 100))
        self.update_buttons_ui()

    def pick_inline_color(self, hex_code):
        color = QColor(hex_code)
        if self.active_slot == 1:
            self.selected_c1 = color
        else:
            self.selected_c2 = color
        if self.picker_dialog and self.picker_dialog.isVisible():
            self.picker_dialog.wheel.set_color(color)
            self.picker_dialog.val_slider.setValue(int(color.valueF() * 100))
        self.update_buttons_ui()

    def update_buttons_ui(self):
        b1_border = "2px solid #00ffc8" if self.active_slot == 1 else "1px solid #333"
        b2_border = "2px solid #00ffc8" if self.active_slot == 2 else "1px solid #333"

        self.btn_c1.setStyleSheet(f"background-color: {self.selected_c1.name()}; color: #fff; border-radius: 6px; font-weight: bold; border: {b1_border};")
        self.btn_c2.setStyleSheet(f"background-color: {self.selected_c2.name()}; color: #fff; border-radius: 6px; font-weight: bold; border: {b2_border};")

    def apply_colors(self):
        logging.info(f"Применены цвета: C1={self.selected_c1.name()}, C2={self.selected_c2.name()}")
        if self.overlay:
            self.overlay.set_colors_instant(self.selected_c1, self.selected_c2)
        
        pair = (QColor(self.selected_c1), QColor(self.selected_c2))
        if pair in self.custom_history:
            self.custom_history.remove(pair)
        self.custom_history.insert(0, pair)
        if len(self.custom_history) > 5:
            self.custom_history.pop()

        self.update_history_ui()
        self.save_config()

    def quick_preset_apply(self, c1, c2):
        self.selected_c1 = c1
        self.selected_c2 = c2
        if self.picker_dialog and self.picker_dialog.isVisible():
            current = self.selected_c1 if self.active_slot == 1 else self.selected_c2
            self.picker_dialog.wheel.set_color(current)
            self.picker_dialog.val_slider.setValue(int(current.valueF() * 100))
        self.update_buttons_ui()
        self.apply_colors()

    def update_history_ui(self):
        for idx, btn in enumerate(self.history_buttons):
            if idx < len(self.custom_history):
                c1, c2 = self.custom_history[idx]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c1.name()}, stop:1 {c2.name()});
                        border: 1px solid #555;
                        border-radius: 6px;
                    }}
                    QPushButton:hover {{ border: 1px solid #fff; }}
                """)
                btn.setEnabled(True)

    def apply_history_color(self, idx):
        if idx < len(self.custom_history):
            c1, c2 = self.custom_history[idx]
            self.quick_preset_apply(c1, c2)

    def change_width(self, val):
        self.max_width = val
        self.lbl_width.setText(f"ТОЛЩИНА: {val} px")
        if self.overlay:
            self.overlay.max_width = val
            self.overlay.repaint()
        self.save_config()

    def change_fade(self, val):
        self.fade_time = val / 1000.0
        self.lbl_fade.setText(f"СКОРОСТЬ ИСЧЕЗНОВЕНИЯ: {val} мс")
        if self.overlay:
            self.overlay.fade_time = self.fade_time
            self.overlay.repaint()
        self.save_config()

    def toggle_trail(self):
        if self.overlay is None:
            logging.info("Эффект включен")
            self.overlay = MouseTrailOverlay(
                fade_time=self.fade_time,
                max_width=self.max_width,
                color1=self.selected_c1,
                color2=self.selected_c2
            )
            self.overlay.show()
            self.update_toggle_button_style(is_active=True)
        else:
            logging.info("Эффект выключен")
            self.overlay.close()
            self.overlay = None
            self.update_toggle_button_style(is_active=False)

    def get_slider_style(self):
        return """
            QSlider::groove:horizontal { height: 4px; background: rgba(28, 28, 43, 0.8); border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #00ffc8; border-radius: 2px; }
            QSlider::handle:horizontal { background: #ffffff; width: 14px; margin-top: -5px; margin-bottom: -5px; border-radius: 7px; }
        """

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    panel = ModernControlPanel()
    panel.show()
    sys.exit(app.exec_())