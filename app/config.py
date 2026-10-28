"""Константы, пути, логирование, настройки, автозапуск."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from PyQt5.QtGui import QColor
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple


IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import winreg  # нужен только для автозапуска

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
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # папка с main.py


def _data_dir() -> str:
    """Папка для конфига, лога и своих наборов: %APPDATA%/CCC — одна на все версии, куда бы ни скачали exe.
    Без APPDATA — рядом с программой, а если и туда нельзя писать — во временную папку."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        path = os.path.join(appdata, APP_NAME)
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except OSError:
            pass
    base = _app_dir()
    return base if os.access(base, os.W_OK) else tempfile.gettempdir()


DATA_DIR = _data_dir()
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

# До v.2.0.5 данные лежали рядом с exe, и новая версия в другой папке начинала с чистого листа
LEGACY_DATA = ("config.json", "shop_state.json", "abilities.json", "rank_sets", "backup")


def _migrate_legacy() -> None:
    """Один раз копирует настройки старой версии из папки программы в общую папку данных."""
    old = _app_dir()
    if (os.path.normcase(os.path.abspath(old)) == os.path.normcase(os.path.abspath(DATA_DIR))
            or os.path.exists(CONFIG_FILE) or not os.path.isfile(os.path.join(old, "config.json"))):
        return
    for name in LEGACY_DATA:
        src, dst = os.path.join(old, name), os.path.join(DATA_DIR, name)
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            elif os.path.isfile(src):
                shutil.copy2(src, dst)
        except OSError:
            pass                  # не скопировалось — эта часть начнётся заново, остальное перенесётся


_migrate_legacy()

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
    return f'"{interpreter}" "{os.path.abspath(sys.argv[0])}"'


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


def _point(value) -> Optional[Tuple[int, int]]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


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
        self.timers_enabled = True   # показывать таймеры Dota 2
        self.hud_pos = None          # (x, y), если таймеры перетащили; None — под часами игры
        self.sound_enabled = True    # колокольчик перед лотусом
        self.sound_file = ""         # свой WAV; пустая строка — встроенный звон
        self.sound_volume = 70       # громкость звонка, 0..100
        self.lotus_alert = 5         # за сколько секунд до лотуса звенеть, 0..30 (0 — в момент появления)
        self.hud_hold_alt = True     # True — HUD виден только пока зажат Alt (поведение по умолчанию)
        self.only_in_dota = True     # эффект только пока в фокусе Dota 2
        self.ingame_marker = False       # свой маркер установлен в игру подменой файлов (свой пак в dota_russian)
        self.ingame_petals = "#ff0055"   # цвет лепестков маркера в игре
        self.ingame_rest = "#00ccff"     # цвет остального (центр, свечение, кольца)
        self.ingame_chevron = True       # наша форма лепестков вместо родной стрелки
        self.rank_mod = False            # свой вид ранга установлен в игру (свой пак в dota_russian)
        self.rank_real = "5"             # настоящий ранг: медаль и звёзды
        self.rank_real_stars = 1
        self.rank_show = "8"             # каким ранг показывать
        self.rank_show_stars = 1
        self.rank_game = ""              # версия игры, из которой собран пак рангов
        self.rank_set = ""               # свой набор иконок рангов ("" — родные)
        self.builds_auto = True      # True — сборки автоматически появляются при открытом магазине
        self.builds_gold_zone = None # (x, y, w, h) зоны кнопки золота; None — автозона 20% справа снизу
        self.launcher_enabled = True # иконка-кнопка в углу экрана
        self.launcher_pos = None
        self.builds_pos = None
        self.builds_role = 0         # вкладка позиции в окне сборок: 0..4
        self.builds_hero = 0         # последний герой в окне сборок
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
        s.timers_enabled = bool(raw.get("timers_enabled", True))
        # Ключ новый: старая позиция («правый верхний угол») не должна мешать встать под часы игры
        s.hud_pos = _point(raw.get("hud_custom_pos"))
        s.sound_enabled = bool(raw.get("sound_enabled", True))
        s.sound_file = str(raw.get("sound_file") or "")
        s.sound_volume = _clamp(raw.get("sound_volume", 70), 0, 100, 70)
        s.lotus_alert = _clamp(raw.get("lotus_alert", 5), 0, 30, 5)
        s.hud_hold_alt = bool(raw.get("hud_hold_alt", True))
        s.only_in_dota = bool(raw.get("only_in_dota", True))
        s.ingame_marker = bool(raw.get("ingame_marker", False))
        s.ingame_petals = str(raw.get("ingame_petals", s.ingame_petals))
        s.ingame_rest = str(raw.get("ingame_rest", s.ingame_rest))
        s.ingame_chevron = bool(raw.get("ingame_chevron", True))
        s.rank_mod = bool(raw.get("rank_mod", False))
        s.rank_real = str(raw.get("rank_real", s.rank_real))
        s.rank_real_stars = _clamp(raw.get("rank_real_stars", 1), 1, 5, 1)
        s.rank_show = str(raw.get("rank_show", s.rank_show))
        s.rank_show_stars = _clamp(raw.get("rank_show_stars", 1), 1, 5, 1)
        s.rank_game = str(raw.get("rank_game", ""))
        s.rank_set = str(raw.get("rank_set", ""))
        s.builds_auto = bool(raw.get("builds_auto", True))
        gold = raw.get("builds_gold_zone")
        if isinstance(gold, (list, tuple)) and len(gold) == 4:
            try:
                s.builds_gold_zone = tuple(int(v) for v in gold)
            except (TypeError, ValueError):
                pass
        s.launcher_enabled = bool(raw.get("launcher_enabled", True))
        s.launcher_pos = _point(raw.get("launcher_pos"))
        s.builds_pos = _point(raw.get("builds_pos"))
        s.builds_role = _clamp(raw.get("builds_role", 0), 0, 4, 0)
        s.builds_hero = _clamp(raw.get("builds_hero", 0), 0, 10000, 0)

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
            "timers_enabled": self.timers_enabled,
            "hud_custom_pos": list(self.hud_pos) if self.hud_pos else None,
            "sound_enabled": self.sound_enabled,
            "sound_file": self.sound_file,
            "sound_volume": self.sound_volume,
            "lotus_alert": self.lotus_alert,
            "hud_hold_alt": self.hud_hold_alt,
            "only_in_dota": self.only_in_dota,
            "ingame_marker": self.ingame_marker,
            "ingame_petals": self.ingame_petals,
            "ingame_rest": self.ingame_rest,
            "ingame_chevron": self.ingame_chevron,
            "rank_mod": self.rank_mod,
            "rank_real": self.rank_real,
            "rank_real_stars": self.rank_real_stars,
            "rank_show": self.rank_show,
            "rank_show_stars": self.rank_show_stars,
            "rank_game": self.rank_game,
            "rank_set": self.rank_set,
            "builds_auto": self.builds_auto,
            "builds_gold_zone": list(self.builds_gold_zone) if self.builds_gold_zone else None,
            "launcher_enabled": self.launcher_enabled,
            "launcher_pos": list(self.launcher_pos) if self.launcher_pos else None,
            "builds_pos": list(self.builds_pos) if self.builds_pos else None,
            "builds_role": self.builds_role,
            "builds_hero": self.builds_hero,
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
