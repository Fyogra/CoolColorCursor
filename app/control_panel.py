"""Главная панель управления и трей."""
from __future__ import annotations

import os
import threading
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget
from typing import Optional
from app.builds_data import BuildsService
from app.builds_window import BuildsWindow, GoldClickWatcher, ImageCache, ShopDetector
from app.config import ACCENT, APP_TITLE, BG, CONFIG_FILE, DANGER, DATA_DIR, DIM, FONT, FONT_SYMBOL, HISTORY_SIZE, IS_WINDOWS, Settings, log, set_autostart
from app.gsi import GSIServer, LIVE, OFFLINE, PREGAME, Snapshot, format_clock, install_gsi_config, refresh_installed_gsi_config
from app.hud import LauncherIcon, STATUS_TEXT, TimerHUD
from app.icon import app_icon
from app.sound import AlertTracker, SoundDialog, VK_ALT, ensure_bell, is_key_down, play_bell
from app.trail import MouseTrailOverlay, keep_on_top
from app.ui_common import AutostartDialog, ClickSlider, ColorPickerWindow, DragHeader, InfoDialog, SLIDER_STYLE, StarryBackground, caption, gradient_button_style, make_button, outline_button_style, readable_text_color


# ──────────────────────────────────────────────────────────────────────────────
#  Главная панель
# ──────────────────────────────────────────────────────────────────────────────
class ControlPanel(QWidget):
    WIDTH, HEIGHT = 380, 600

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
        self.hud = None          # панель таймеров Dota 2
        self.gsi = GSIServer()
        self.btn_timers = None
        self.act_timers = None
        self.act_autostart = None
        self.builds = None       # окно сборок
        self.launcher = None     # иконка в углу экрана
        self.btn_builds = None
        self.btn_sound = None
        self.lbl_gsi = None
        self.act_sound = None
        self.act_launcher = None
        self._gsi_text = None
        self._ticks = 0
        self.builds_service = BuildsService()
        self.images = ImageCache(self)
        self.alerts = AlertTracker()
        self.shop_detector = ShopDetector()
        self.gold_watcher = GoldClickWatcher()
        self._builds_auto_shown = False   # окно показали детектор или клик (не пользователь)
        self._shop_poll = 0
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
        self._restore_timers()
        self._restore_launcher()

        # Один общий тик на всё: таймеры, колокольчик, герой для сборок, статус GSI
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(200)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()

        threading.Thread(target=ensure_bell, name="bell", daemon=True).start()  # звук готовим заранее
        try:
            refresh_installed_gsi_config()
        except Exception as e:  # noqa: BLE001 — не критично
            log.warning("Проверка GSI-конфига не удалась: %s", e)
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

        # Dota 2
        root.addWidget(caption("DOTA 2"))
        dota = QHBoxLayout()
        self.btn_timers = make_button(height=30, font=QFont(FONT_SYMBOL, 9, QFont.Bold))
        self.btn_timers.clicked.connect(self.toggle_timers)
        btn_gsi = make_button("⚙ GSI-КОНФИГ", height=30, font=QFont(FONT_SYMBOL, 9, QFont.Bold),
                              style=outline_button_style(ACCENT, radius=15))
        btn_gsi.setToolTip("Установить в папку Dota 2 конфиг, по которому игра передаёт время матча и героя")
        btn_gsi.clicked.connect(self.install_gsi)
        dota.addWidget(self.btn_timers)
        dota.addWidget(btn_gsi)
        root.addLayout(dota)

        dota2 = QHBoxLayout()
        self.btn_builds = make_button(height=30, font=QFont(FONT_SYMBOL, 9, QFont.Bold))
        self.btn_builds.setToolTip("Что собирать на героя: предметы по стадиям игры и порядок прокачки")
        self.btn_builds.clicked.connect(self.toggle_builds)
        self.btn_sound = make_button(height=30, font=QFont(FONT_SYMBOL, 9, QFont.Bold))
        self.btn_sound.setToolTip("Колокольчик за 5 секунд до появления лотуса")
        self.btn_sound.clicked.connect(self.toggle_sound)
        btn_sound_settings = make_button("🎵", size=(32, 30), font=QFont(FONT_SYMBOL, 11),
                                         style=outline_button_style(ACCENT, radius=15))
        btn_sound_settings.setToolTip("Выбрать свой WAV и настроить громкость")
        btn_sound_settings.clicked.connect(self.open_sound_dialog)
        dota2.addWidget(self.btn_builds)
        dota2.addWidget(self.btn_sound)
        dota2.addWidget(btn_sound_settings)
        root.addLayout(dota2)

        self.lbl_gsi = caption("", size=9, color=DIM)
        root.addWidget(self.lbl_gsi)

        root.addStretch()

        self.btn_toggle = make_button(height=36, font=QFont(FONT_SYMBOL, 9, QFont.Bold))
        self.btn_toggle.clicked.connect(self.toggle_trail)
        root.addWidget(self.btn_toggle)

        self.btn_dota = make_button("🎮 ОТКРЫТЬ ДОТУ", height=36, font=QFont(FONT_SYMBOL, 9, QFont.Bold),
                                    style=outline_button_style(ACCENT))
        self.btn_dota.setToolTip("Запустить Dota 2 через Steam")
        self.btn_dota.clicked.connect(self.launch_dota)
        root.addWidget(self.btn_dota)

        self._refresh_toggle_ui()
        self._refresh_dota_ui()
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

        menu.addSeparator()
        menu.addAction("Сборки на героя").triggered.connect(self.open_builds)
        self.act_timers = menu.addAction("Таймеры Dota 2")
        self.act_timers.setCheckable(True)
        self.act_timers.setChecked(self.hud is not None)
        self.act_timers.toggled.connect(self.set_timers)
        self.act_hold_alt = menu.addAction("Таймеры только при зажатом Alt")
        self.act_hold_alt.setCheckable(True)
        self.act_hold_alt.setChecked(self.settings.hud_hold_alt)
        self.act_hold_alt.toggled.connect(self.set_hud_hold_alt)
        menu.addAction("Вернуть таймеры под часы игры").triggered.connect(self.reset_hud_position)
        self.act_auto = menu.addAction("Автооткрытие сборок при магазине")
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(self.settings.builds_auto)
        self.act_auto.toggled.connect(self.set_builds_auto)
        self.act_sound = menu.addAction("Колокольчик перед лотусом")
        self.act_sound.setCheckable(True)
        self.act_sound.setChecked(self.settings.sound_enabled)
        self.act_sound.toggled.connect(self.set_sound)
        menu.addAction("Настроить звук колокольчика…").triggered.connect(self.open_sound_dialog)
        self.act_launcher = menu.addAction("Иконка в углу экрана")
        self.act_launcher.setCheckable(True)
        self.act_launcher.setChecked(self.settings.launcher_enabled)
        self.act_launcher.toggled.connect(self.set_launcher)
        menu.addAction("Установить GSI-конфиг").triggered.connect(self.install_gsi)
        menu.addSeparator()

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
        self._tick_timer.stop()
        if self.overlay:
            self.overlay.shutdown()
            self.overlay = None
        if self.builds is not None:
            self.builds.close()
        self._close_launcher()
        self._close_hud()
        self.gsi.stop()
        self.images.shutdown()
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
            self._raise_overlays()  # шлейф показался позже и мог лечь поверх таймеров и иконки
        else:
            log.info("Эффект выключен")
            self.overlay.shutdown()
            self.overlay = None
        self._refresh_toggle_ui()

    def launch_dota(self):
        """Просим Steam запустить Dota 2 (app id 570). Если игра уже запущена, Steam просто её покажет."""
        url = "steam://rungameid/570"
        try:
            if IS_WINDOWS:
                os.startfile(url)  # noqa: S606 — открывает ссылку через установленный Steam
            else:
                import webbrowser
                if not webbrowser.open(url):
                    raise OSError("нет обработчика steam://")
            log.info("Запуск Dota 2 через Steam")
        except OSError as e:
            log.error("Не удалось запустить Dota 2: %s", e)
            dialog = InfoDialog("ОТКРЫТЬ ДОТУ", "Не получилось запустить Dota 2 через Steam.\n\n"
                                "Проверь, что Steam установлен, и запусти игру из него.", self, size=(400, 220))
            dialog.move(self.geometry().center() - dialog.rect().center())
            dialog.exec_()
            dialog.deleteLater()

    def _refresh_toggle_ui(self):
        active = self.overlay is not None
        if hasattr(self, "btn_toggle"):
            if active:
                self.btn_toggle.setText("⏹ ВЫКЛЮЧИТЬ ЭФФЕКТ")
                self.btn_toggle.setStyleSheet(outline_button_style(DANGER, hover_text="#ffffff"))
            else:
                self.btn_toggle.setText("▶ ВКЛЮЧИТЬ ЭФФЕКТ")
                self.btn_toggle.setStyleSheet(outline_button_style(ACCENT))
        if hasattr(self, "act_toggle"):
            self.act_toggle.setText("Выключить эффект" if active else "Включить эффект")

    # ── таймеры Dota 2 ──────────────────────────────────────────────────────
    def _restore_timers(self):
        if self.settings.timers_enabled:
            self.set_timers(True, persist=False)

    def toggle_timers(self):
        self.set_timers(self.hud is None)

    def set_timers(self, enabled: bool, persist: bool = True):
        if enabled and self.hud is None:
            self.hud = TimerHUD()  # покажется сам, когда начнётся матч
            self.hud.positionChanged.connect(self._on_hud_moved)
            self.hud.place(self.settings.hud_pos)
            self.alerts = AlertTracker()
            log.info("Таймеры включены")
        elif not enabled:
            self._close_hud()
            log.info("Таймеры выключены")
        self.settings.timers_enabled = enabled
        self._sync_gsi()
        if persist:
            self.schedule_save()
        self._refresh_dota_ui()

    def _close_hud(self):
        if self.hud is not None:
            self.hud.close()
            self.hud.deleteLater()
            self.hud = None

    def _on_hud_moved(self, x: int, y: int):
        self.settings.hud_pos = (x, y)
        self.schedule_save()

    def reset_hud_position(self):
        self.settings.hud_pos = None
        if self.hud is not None:
            self.hud.place(None)
        self.schedule_save()

    def _sync_gsi(self):
        """GSI-сервер нужен, пока включены таймеры или открыто окно сборок (герой берётся из игры)."""
        need = self.hud is not None or self.builds is not None
        if need and not self.gsi.running:
            self.gsi.start()  # если порт занят, причина появится в строке статуса
        elif not need and self.gsi.running:
            self.gsi.stop()

    # ── общий тик ───────────────────────────────────────────────────────────
    def _tick(self):
        self._ticks += 1
        snap = self.gsi.state.snapshot() if self.gsi.running else Snapshot(OFFLINE, None)
        if snap.status != getattr(self, "_last_status", None):
            log.info("GSI: статус %s, герой %s", snap.status, snap.hero_id)
            self._last_status = snap.status

        # Таймеры: по Alt (если включено) или всегда
        if self.hud is not None:
            visible = (not self.settings.hud_hold_alt) or is_key_down(VK_ALT)
            self.hud.update_from(snap, visible_override=visible)
            due = self.alerts.check(snap.clock)  # и с выключенным звуком — чтобы не звенеть задним числом
            if due and self.settings.sound_enabled:
                play_bell(self.settings.sound_file, self.settings.sound_volume)

        # Сборки: герой из игры подхватывается автоматически
        if self.builds is not None:
            self.builds.follow_game_hero(snap.hero_id)

        # Автодетект магазина: только в активном матче (не на паузе, не на базе загрузки)
        if self.settings.builds_auto and snap.status in (LIVE, PREGAME):
            # Клик по золоту — моментально открываем сборки, не дожидаясь детектора
            if self.gold_watcher.check(self.settings.builds_gold_zone):
                self._auto_show_builds("right")
            # Полная проба каждые ~400 мс (2 тика)
            self._shop_poll += 1
            if self._shop_poll >= 2:
                self._shop_poll = 0
                side = self.shop_detector.probe()
                if side != getattr(self, "_last_shop_side", None):
                    log.info("Магазин: %s %s", side or "закрыт", {k: tuple(round(x) for x in v) for k, v in
                                                                 self.shop_detector.last_metrics.items() if k in ("left", "right")})
                    self._last_shop_side = side
                if side:
                    self._auto_show_builds(side)
                elif self._builds_auto_shown and self.builds is not None:
                    self._auto_hide_builds()
        elif self._builds_auto_shown and self.builds is not None:
            self._auto_hide_builds()

        if self._ticks % 10 == 0:  # раз в ~2 секунды: игра в окне без рамки может перехватить верхний слой
            self._raise_overlays()
        self._refresh_gsi_status(snap)

    def _auto_show_builds(self, side: str):
        """Открывает окно сборок рядом с магазином. Запоминает, что открыли мы, не пользователь."""
        if self.builds is None:
            self._builds_auto_shown = True
            self.open_builds(dock_side=side)
        else:
            self.builds.dock_to_shop(side)
            if not self.builds.isVisible():
                self.builds.show()
            self._builds_auto_shown = True

    def _auto_hide_builds(self):
        if self.builds is not None and self._builds_auto_shown:
            self.builds.close()
            self._builds_auto_shown = False

    def _raise_overlays(self):
        for widget in (self.hud, self.launcher, self.builds):
            if widget is not None and widget.isVisible():
                keep_on_top(widget)

    def _refresh_gsi_status(self, snap: Snapshot):
        if not self.gsi.running:
            text = "GSI выключен: включи таймеры или открой сборки"
        elif self.gsi.error:
            text = self.gsi.error
        else:
            text = "Dota 2: " + STATUS_TEXT.get(snap.status, "")
            if snap.clock is not None:
                text += f" · {format_clock(snap.clock)}"
        if text != self._gsi_text and self.lbl_gsi is not None:
            self._gsi_text = text
            self.lbl_gsi.setText(text)

    # ── звук ────────────────────────────────────────────────────────────────
    def toggle_sound(self):
        self.set_sound(not self.settings.sound_enabled)

    def set_sound(self, enabled: bool):
        self.settings.sound_enabled = enabled
        self.schedule_save()
        self._refresh_dota_ui()
        if enabled:
            play_bell(self.settings.sound_file, self.settings.sound_volume)  # сразу слышно, как звучит

    def open_sound_dialog(self):
        dialog = SoundDialog(self.settings.sound_file, self.settings.sound_volume, self)
        dialog.changed.connect(self._on_sound_changed)
        dialog.move(self.geometry().center() - dialog.rect().center())
        dialog.exec_()
        dialog.deleteLater()

    def _on_sound_changed(self, path: str, volume: int):
        self.settings.sound_file = path
        self.settings.sound_volume = volume
        self.schedule_save()

    def set_hud_hold_alt(self, enabled: bool):
        self.settings.hud_hold_alt = enabled
        self.schedule_save()

    def set_builds_auto(self, enabled: bool):
        self.settings.builds_auto = enabled
        self.shop_detector.reset()
        if not enabled and self._builds_auto_shown:
            self._auto_hide_builds()
        self.schedule_save()

    # ── сборки ──────────────────────────────────────────────────────────────
    def toggle_builds(self):
        if self.builds is not None:
            self.builds.close()
        else:
            self.open_builds()

    def open_builds(self, dock_side: Optional[str] = None):
        new_window = self.builds is None
        if new_window:
            s = self.settings
            self.builds = BuildsWindow(self.builds_service, self.images, role=s.builds_role, hero_id=s.builds_hero)
            self.builds.positionChanged.connect(self._on_builds_moved)
            self.builds.roleChanged.connect(self._on_builds_role)
            self.builds.heroChanged.connect(self._on_builds_hero)
            self.builds.closed.connect(self._on_builds_closed)
            if dock_side:
                self.builds.dock_to_shop(dock_side)
            else:
                self.builds.place(s.builds_pos)
            self._sync_gsi()
        elif dock_side:
            self.builds.dock_to_shop(dock_side)
        self.builds.show()
        if not dock_side:
            # Автооткрытие возле магазина не ворует фокус у игры
            self.builds.raise_()
            self.builds.activateWindow()
        self._refresh_dota_ui()

    def _on_builds_closed(self):
        if self.builds is not None:
            self.builds.deleteLater()
            self.builds = None
        self._builds_auto_shown = False
        if not self._quitting:
            self._sync_gsi()
            self._refresh_dota_ui()

    def _on_builds_moved(self, x: int, y: int):
        self.settings.builds_pos = (x, y)
        self.schedule_save()

    def _on_builds_role(self, role: int):
        self.settings.builds_role = role
        self.schedule_save()

    def _on_builds_hero(self, hero_id: int):
        self.settings.builds_hero = hero_id
        self.schedule_save()

    # ── иконка в углу ───────────────────────────────────────────────────────
    def _restore_launcher(self):
        if self.settings.launcher_enabled:
            self.set_launcher(True, persist=False)

    def set_launcher(self, enabled: bool, persist: bool = True):
        if enabled and self.launcher is None:
            self.launcher = LauncherIcon()
            self.launcher.clicked.connect(self._on_launcher_clicked)
            self.launcher.positionChanged.connect(self._on_launcher_moved)
            self.launcher.menuRequested.connect(self._tray_menu.popup)
            self.launcher.place(self.settings.launcher_pos)
            self.launcher.show()
        elif not enabled:
            self._close_launcher()
        self.settings.launcher_enabled = enabled
        if persist:
            self.schedule_save()
        self._refresh_dota_ui()

    def _close_launcher(self):
        if self.launcher is not None:
            self.launcher.close()
            self.launcher.deleteLater()
            self.launcher = None

    def _on_launcher_clicked(self):
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.show_from_tray()

    def _on_launcher_moved(self, x: int, y: int):
        self.settings.launcher_pos = (x, y)
        self.schedule_save()

    # ── кнопки и галочки ────────────────────────────────────────────────────
    @staticmethod
    def _set_check(action, checked: bool):
        if action:
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)

    def _refresh_dota_ui(self):
        timers = self.hud is not None
        if self.btn_timers:
            self.btn_timers.setText("ТАЙМЕРЫ: ВКЛ" if timers else "ТАЙМЕРЫ: ВЫКЛ")
            self.btn_timers.setStyleSheet(outline_button_style(ACCENT if timers else DIM, radius=15))
        if self.btn_builds:
            opened = self.builds is not None
            self.btn_builds.setText("📖 СКРЫТЬ СБОРКИ" if opened else "📖 СБОРКИ НА ГЕРОЯ")
            self.btn_builds.setStyleSheet(outline_button_style(ACCENT, radius=15))
        if self.btn_sound:
            on = self.settings.sound_enabled
            self.btn_sound.setText("🔔 ЗВУК ЛОТУСА: ВКЛ" if on else "🔕 ЗВУК ЛОТУСА: ВЫКЛ")
            self.btn_sound.setStyleSheet(outline_button_style(ACCENT if on else DIM, radius=15))
        self._set_check(self.act_timers, timers)
        self._set_check(self.act_sound, self.settings.sound_enabled)
        self._set_check(self.act_launcher, self.launcher is not None)

    def install_gsi(self):
        installed, path = install_gsi_config(DATA_DIR)
        if installed:
            text = (f"Конфиг установлен:\n{path}\n\nПерезапусти Dota 2, если она открыта. "
                    "Если данные не появятся — добавь в Steam параметр запуска -gamestateintegration.")
        elif path:
            text = (f"Папку Dota 2 найти или записать в неё не удалось. Конфиг сохранён здесь:\n{path}\n\n"
                    "Скопируй его в папку «dota 2 beta\\game\\dota\\cfg\\gamestate_integration» "
                    "(создай её, если нет) и перезапусти Dota 2.")
        else:
            text = "Не удалось записать конфиг. Подробности — в файле лога рядом с программой."
        dialog = InfoDialog("GSI-КОНФИГ", text, self)
        dialog.move(self.geometry().center() - dialog.rect().center())
        dialog.exec_()
        dialog.deleteLater()
