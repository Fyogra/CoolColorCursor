"""GSI-сервер Dota 2, состояние матча, таймеры."""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional, Tuple
from app.config import log


# ──────────────────────────────────────────────────────────────────────────────
#  Настройки
# ──────────────────────────────────────────────────────────────────────────────
HOST = "127.0.0.1"      # только локальный адрес: наружу сервер не торчит и фаервол не спрашивает
PORT = 3000
WARN_SECONDS = 15       # столько секунд и меньше до появления — таймер красный
STALE_AFTER = 5.0       # столько секунд без пакетов — считаем, что связь с игрой потеряна
MAX_BODY = 2 * 1024 * 1024

GSI_CFG_NAME = "gamestate_integration_ccc_timers.cfg"
GSI_CFG_TEXT = f'''"CCC Dota Timers"
{{
    "uri"         "http://{HOST}:{PORT}/"
    "timeout"     "5.0"
    "buffer"      "0.1"
    "throttle"    "0.1"
    "heartbeat"   "1.0"
    "data"
    {{
        "provider"    "1"
        "map"         "1"
        "hero"        "1"
        "abilities"   "1"
    }}
}}
'''


# ──────────────────────────────────────────────────────────────────────────────
#  Таймеры
# ──────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TimerDef:
    key: str
    label: str
    first: int             # игровая секунда первого появления
    interval: int          # период, секунд
    alert_before: int = 0  # за сколько секунд звенеть колокольчиком (0 — не звенеть)
    short: str = ""        # подпись на компактной плашке


TIMERS: Tuple[TimerDef, ...] = (
    TimerDef("lotus", "Лотус", first=3 * 60, interval=3 * 60, alert_before=5),  # каждые 3 мин, с 3:00
    TimerDef("bounty", "Золото", first=0, interval=4 * 60),         # руны богатства: каждые 4 мин, с 0:00
    TimerDef("wisdom", "Опыт", first=7 * 60, interval=7 * 60),      # руны мудрости: каждые 7 мин, с 7:00
    TimerDef("power", "Усиление", first=6 * 60, interval=2 * 60, short="Усил."),  # каждые 2 мин, с 6:00
)


def seconds_until(clock: float, first: int, interval: int) -> float:
    """Сколько секунд осталось до ближайшего появления. 0 — объект появляется прямо сейчас."""
    if clock <= first:
        return float(first - clock)
    periods = math.ceil((clock - first) / interval)
    return first + periods * interval - clock


def format_mmss(seconds: float) -> str:
    """Обратный отсчёт в формате ММ:СС (округляем вверх: «00:00» — только в момент появления)."""
    total = max(0, int(math.ceil(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def format_clock(seconds: float) -> str:
    """Игровое время ММ:СС; до рога (предыгра) оно отрицательное: «-01:30»."""
    total = int(seconds)
    sign = "-" if total < 0 else ""
    total = abs(total)
    return f"{sign}{total // 60:02d}:{total % 60:02d}"


# ──────────────────────────────────────────────────────────────────────────────
#  Состояние матча
# ──────────────────────────────────────────────────────────────────────────────
OFFLINE = "offline"    # от Dota 2 ещё ни разу не приходили данные
STALE = "stale"        # данные были, но давно замолчали (игру закрыли / завис интернет-канал)
IDLE = "idle"          # игра на связи, но матч не идёт (меню, пик героев, итоги)
PREGAME = "pregame"    # подготовка до рога
LIVE = "live"          # матч идёт
PAUSED = "paused"      # пауза

_STATE_PREGAME = "DOTA_GAMERULES_STATE_PRE_GAME"
_STATE_LIVE = "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"


@dataclass(frozen=True)
class Snapshot:
    status: str
    clock: Optional[float]  # игровое время в секундах; None, если матча нет
    hero_id: int = 0        # герой игрока (появляется уже на стадии пика); 0 — неизвестен
    ability0: str = ""      # способность в первом слоте (у Кеза по ней видна стойка)


class GameState:
    """Потокобезопасное хранилище последнего пакета. Время между пакетами досчитывается локально."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._clock: Optional[float] = None
            self._received = 0.0
            self._game_state = ""
            self._paused = False
            self._last_packet: Optional[float] = None
            self._hero_id = 0
            self._ability0 = ""

    def update(self, payload: dict) -> None:
        now = time.monotonic()
        game_map = payload.get("map")
        hero = payload.get("hero")
        hero_id = hero.get("id") if isinstance(hero, dict) else None
        abilities = payload.get("abilities")
        first = abilities.get("ability0") if isinstance(abilities, dict) else None
        ability0 = str(first.get("name") or "") if isinstance(first, dict) else ""
        with self._lock:
            self._ability0 = ability0
            self._last_packet = now
            self._hero_id = hero_id if isinstance(hero_id, int) and not isinstance(hero_id, bool) \
                and hero_id > 0 else 0
            clock = game_map.get("clock_time") if isinstance(game_map, dict) else None
            if isinstance(clock, bool) or not isinstance(clock, (int, float)):
                # Пакет без блока map (меню, загрузка) — матча нет
                self._clock = None
                self._game_state = ""
                self._paused = False
                return
            self._clock = float(clock)
            self._received = now
            self._game_state = str(game_map.get("game_state", ""))
            self._paused = bool(game_map.get("paused", False))

    def snapshot(self) -> Snapshot:
        now = time.monotonic()
        with self._lock:
            if self._last_packet is None:
                return Snapshot(OFFLINE, None)
            if now - self._last_packet > STALE_AFTER:
                return Snapshot(STALE, None)
            hero, ab0 = self._hero_id, self._ability0
            if self._clock is None or self._game_state not in (_STATE_PREGAME, _STATE_LIVE):
                return Snapshot(IDLE, None, hero, ab0)
            if self._paused:
                return Snapshot(PAUSED, self._clock, hero, ab0)  # на паузе время стоит
            clock = self._clock + (now - self._received)
            return Snapshot(PREGAME if self._game_state == _STATE_PREGAME else LIVE, clock, hero, ab0)


# ──────────────────────────────────────────────────────────────────────────────
#  HTTP-сервер
# ──────────────────────────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    state: GameState = None  # подставляется в GSIServer.start()
    timeout = 5              # зависший клиент не должен держать поток вечно

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            self._reply(413)
            return
        body = self.rfile.read(length) if length > 0 else b""
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._reply(400)
                return
            if isinstance(payload, dict):
                self.state.update(payload)
        self._reply(200)

    def do_GET(self):  # удобно проверить из браузера: http://127.0.0.1:3000/
        self._reply(200, b"CCC Dota GSI: OK\n")

    def _reply(self, code: int, body: bytes = b""):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Стандартная реализация пишет в sys.stderr, которого в exe без консоли нет
        pass


class _Server(ThreadingHTTPServer):
    # На Windows SO_REUSEADDR позволяет «молча» сесть на чужой занятый порт, поэтому там он выключен;
    # на остальных системах он безопасен и нужен, чтобы быстрый перезапуск не упирался в TIME_WAIT
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True

    def handle_error(self, request, client_address):
        log.warning("Ошибка обработки GSI-запроса", exc_info=True)


class GSIServer:
    def __init__(self, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port
        self.state = GameState()
        self.error: Optional[str] = None  # текст для интерфейса, если сервер не поднялся
        self._httpd: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> bool:
        if self._httpd is not None:
            return True
        handler = type("GSIHandler", (_Handler,), {"state": self.state})
        httpd = None
        for attempt in range(3):  # порт мог не успеть освободиться после быстрого выкл/вкл
            try:
                httpd = _Server((self.host, self.port), handler)
                break
            except OSError as e:
                log.warning("GSI-сервер: попытка %s открыть %s:%s не удалась: %s",
                            attempt + 1, self.host, self.port, e)
                time.sleep(0.15)
        if httpd is None:
            self.error = f"Порт {self.port} недоступен"
            log.error("GSI-сервер не запустился на %s:%s", self.host, self.port)
            return False
        self.error = None
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.25},
                                        name="gsi-server", daemon=True)
        self._thread.start()
        log.info("GSI-сервер запущен на %s:%s", self.host, self.port)
        return True

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        self.error = None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.state.reset()
        log.info("GSI-сервер остановлен")


# ──────────────────────────────────────────────────────────────────────────────
#  Установка конфига в папку Dota 2
# ──────────────────────────────────────────────────────────────────────────────
def _steam_roots() -> List[str]:
    roots: List[str] = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, sub, value in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            ):
                try:
                    with winreg.OpenKey(hive, sub) as key:
                        roots.append(os.path.normpath(winreg.QueryValueEx(key, value)[0]))
                except OSError:
                    pass
        except ImportError:
            pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            roots.append(os.path.join(base, "Steam"))
    return roots


def _steam_libraries(root: str) -> List[str]:
    """Основная папка Steam + дополнительные библиотеки из libraryfolders.vdf."""
    libs = [root]
    try:
        with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), "r",
                  encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return libs
    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        libs.append(os.path.normpath(match.group(1).replace("\\\\", "\\")))
    return libs


def find_dota_cfg_dir() -> Optional[str]:
    seen = set()
    for root in _steam_roots():
        for lib in _steam_libraries(root):
            key = os.path.normcase(lib)
            if key in seen:
                continue
            seen.add(key)
            cfg = os.path.join(lib, "steamapps", "common", "dota 2 beta", "game", "dota", "cfg")
            if os.path.isdir(cfg):
                return cfg
    return None


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def install_gsi_config(fallback_dir: str) -> Tuple[bool, str]:
    """Кладёт конфиг в папку Dota 2. Возвращает (установлен_в_игру, путь_к_файлу).

    Если Dota 2 не нашлась или туда нельзя писать, файл сохраняется в fallback_dir, чтобы его
    можно было скопировать вручную. Пустой путь означает, что не удалось записать вообще ничего.
    """
    cfg_dir = find_dota_cfg_dir()
    if cfg_dir:
        target = os.path.join(cfg_dir, "gamestate_integration", GSI_CFG_NAME)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            _write_text(target, GSI_CFG_TEXT)
            log.info("GSI-конфиг установлен: %s", target)
            return True, target
        except OSError as e:
            log.error("Не удалось записать GSI-конфиг в игру: %s", e)

    target = os.path.join(fallback_dir, GSI_CFG_NAME)
    try:
        _write_text(target, GSI_CFG_TEXT)
        return False, target
    except OSError as e:
        log.error("Не удалось сохранить GSI-конфиг: %s", e)
        return False, ""


def refresh_installed_gsi_config() -> bool:
    """Если наш конфиг уже стоит в Dota 2, но от старой версии программы, — тихо обновляет его."""
    cfg_dir = find_dota_cfg_dir()
    if not cfg_dir:
        return False
    target = os.path.join(cfg_dir, "gamestate_integration", GSI_CFG_NAME)
    try:
        with open(target, "r", encoding="utf-8") as f:
            if f.read() == GSI_CFG_TEXT:
                return False
    except OSError:
        return False  # конфиг не ставили — без спроса не ставим
    try:
        _write_text(target, GSI_CFG_TEXT)
        log.info("GSI-конфиг обновлён: %s", target)
        return True
    except OSError as e:
        log.error("Не удалось обновить GSI-конфиг: %s", e)
        return False
