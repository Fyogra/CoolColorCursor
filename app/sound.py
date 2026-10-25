"""Звук: колокольчик, громкость, глобальные клавиши."""
from __future__ import annotations

import ctypes
import math
import os
import sys
import threading
import wave
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLabel
from array import array
from typing import List, Optional, Tuple
from app.config import ACCENT, DATA_DIR, DIM, FONT, IS_WINDOWS, TEXT, log
from app.gsi import TIMERS, TimerDef, seconds_until
from app.ui_common import ClickSlider, FramelessCard, SLIDER_STYLE, make_button, outline_button_style


class SoundDialog(FramelessCard):
    """Диалог «Настроить звук»: выбор WAV-файла, громкость, за сколько секунд звенеть, кнопка «Проверить»."""

    changed = pyqtSignal(str, int, int)  # (путь, громкость, секунды до лотуса) — испускается при любом применении

    def __init__(self, sound_file: str, volume: int, lead: int = 5, parent=None):
        super().__init__((420, 280), radius=16, border_width=2, parent=parent)
        self.body.setContentsMargins(20, 20, 20, 20)
        self.body.setSpacing(10)
        self._sound_file = sound_file
        self._volume = max(0, min(100, int(volume)))
        self._lead = max(0, min(MAX_LEAD, int(lead)))

        title = QLabel("НАСТРОЙКА ЗВУКА ЛОТУСА")
        title.setFont(QFont(FONT, 11, QFont.Bold))
        title.setStyleSheet(f"color: {ACCENT};")
        title.setAlignment(Qt.AlignCenter)
        self.body.addWidget(title)

        self.lbl_file = QLabel()
        self.lbl_file.setFont(QFont(FONT, 9))
        self.lbl_file.setStyleSheet(f"color: {TEXT};")
        self.lbl_file.setWordWrap(True)
        self.lbl_file.setAlignment(Qt.AlignCenter)
        self.body.addWidget(self.lbl_file)

        files = QHBoxLayout()
        btn_pick = make_button("🎵 Выбрать WAV…", height=30, font=QFont(FONT, 9, QFont.Bold),
                               style=outline_button_style(ACCENT, radius=14))
        btn_pick.clicked.connect(self._pick_file)
        btn_default = make_button("Встроенный", height=30, font=QFont(FONT, 9),
                                  style=outline_button_style(DIM, radius=14))
        btn_default.clicked.connect(lambda: self._set_file(""))
        files.addWidget(btn_pick, 1)
        files.addWidget(btn_default)
        self.body.addLayout(files)

        vol_row = QHBoxLayout()
        self.lbl_vol = QLabel()
        self.lbl_vol.setFont(QFont(FONT, 9, QFont.Bold))
        self.lbl_vol.setStyleSheet(f"color: {TEXT};")
        self.lbl_vol.setFixedWidth(90)
        self.slider = ClickSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(self._volume)
        self.slider.setStyleSheet(SLIDER_STYLE)
        self.slider.valueChanged.connect(self._on_volume)
        vol_row.addWidget(self.lbl_vol)
        vol_row.addWidget(self.slider, 1)
        self.body.addLayout(vol_row)

        lead_row = QHBoxLayout()
        self.lbl_lead = QLabel()
        self.lbl_lead.setFont(QFont(FONT, 9, QFont.Bold))
        self.lbl_lead.setStyleSheet(f"color: {TEXT};")
        self.lbl_lead.setFixedWidth(90)
        self.lbl_lead.setToolTip("За сколько секунд до появления лотуса звенеть (0 — в момент появления)")
        self.lead_slider = ClickSlider(Qt.Horizontal)
        self.lead_slider.setRange(0, MAX_LEAD)
        self.lead_slider.setValue(self._lead)
        self.lead_slider.setStyleSheet(SLIDER_STYLE)
        self.lead_slider.valueChanged.connect(self._on_lead)
        lead_row.addWidget(self.lbl_lead)
        lead_row.addWidget(self.lead_slider, 1)
        self.body.addLayout(lead_row)

        buttons = QHBoxLayout()
        btn_test = make_button("🔔 Проверить", height=30, font=QFont(FONT, 9, QFont.Bold),
                               style=outline_button_style(ACCENT, radius=14))
        btn_test.clicked.connect(self._test)
        btn_close = make_button("Готово", height=30, font=QFont(FONT, 9, QFont.Bold), style=(
            f"QPushButton {{ background: {ACCENT}; color: #000; border: none; border-radius: 14px; }}"
            " QPushButton:hover { background: #00cc99; }"))
        btn_close.clicked.connect(self.accept)
        buttons.addWidget(btn_test)
        buttons.addStretch()
        buttons.addWidget(btn_close)
        self.body.addLayout(buttons)

        self._refresh()

    def _refresh(self):
        name = os.path.basename(self._sound_file) if self._sound_file else "встроенный звон"
        self.lbl_file.setText(f"Текущий звук: {name}")
        self.lbl_vol.setText(f"Громкость: {self._volume}%")
        self.lbl_lead.setText(f"За {self._lead} с" if self._lead else "В момент")

    def _set_file(self, path: str):
        self._sound_file = path
        self._refresh()
        self.changed.emit(self._sound_file, self._volume, self._lead)

    def _pick_file(self):
        try:
            from PyQt5.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(self, "Выбери WAV-файл", "", "WAV-файлы (*.wav)")
        except Exception as e:  # noqa: BLE001
            log.error("Не удалось открыть диалог выбора файла: %s", e)
            return
        if path:
            self._set_file(path)

    def _on_volume(self, value: int):
        self._volume = int(value)
        self._refresh()
        self.changed.emit(self._sound_file, self._volume, self._lead)

    def _on_lead(self, value: int):
        self._lead = int(value)
        self._refresh()
        self.changed.emit(self._sound_file, self._volume, self._lead)

    def _test(self):
        play_bell(self._sound_file, self._volume)


# ──────────────────────────────────────────────────────────────────────────────
#  Звук: колокольчик перед лотусом
# ──────────────────────────────────────────────────────────────────────────────
MAX_LEAD = 30                                                    # звон лотуса: от 0 до 30 секунд до появления
BELL_FILE = os.path.join(DATA_DIR, "lotus_bell.wav")             # встроенный звон, синтезируется один раз
BELL_ACTIVE = os.path.join(DATA_DIR, "lotus_bell_active.wav")     # то, что реально играет: с учётом громкости
_BELL_LOCK = threading.Lock()


# ──────────────────────────────────────────────────────────────────────────────
#  Глобальные клавиши (нужны поверх игры, обычный Qt-shortcut сюда не достанет)
# ──────────────────────────────────────────────────────────────────────────────
VK_SHIFT, VK_CTRL, VK_ALT, VK_SPACE, VK_TAB, VK_CAPITAL = 0x10, 0x11, 0x12, 0x20, 0x09, 0x14
VK_NAMES = {VK_ALT: "Alt", VK_SHIFT: "Shift", VK_CTRL: "Ctrl", VK_SPACE: "Space", VK_TAB: "Tab",
            VK_CAPITAL: "CapsLock", 0: "—"}


def vk_name(vk: int) -> str:
    if vk in VK_NAMES:
        return VK_NAMES[vk]
    if 0x30 <= vk <= 0x5A:  # '0'..'9' и 'A'..'Z' совпадают с ASCII
        return chr(vk)
    if 0x70 <= vk <= 0x7B:
        return f"F{vk - 0x6F}"
    return f"0x{vk:02X}"


def is_key_down(vk: int) -> bool:
    """Нажата ли сейчас клавиша (VK-код Windows). Снаружи Windows всегда False."""
    if not IS_WINDOWS or not vk:
        return False
    try:
        # Старший бит — «нажата сейчас». & 0x8000 работает и когда getAsyncKeyState вернёт -1
        return bool(ctypes.windll.user32.GetAsyncKeyState(int(vk)) & 0x8000)
    except Exception:  # noqa: BLE001
        return False


# ──────────────────────────────────────────────────────────────────────────────
#  Звук: синтез встроенного колокольчика + громкость + свой WAV
# ──────────────────────────────────────────────────────────────────────────────
def make_bell_wav(path: str, freq: float = 880.0, duration: float = 1.5, rate: int = 44100) -> None:
    """Синтезирует звон колокольчика (несколько затухающих обертонов) и пишет 16-битный моно WAV."""
    # (кратность частоты, громкость, время затухания в секундах) — неровные кратности дают «колокольный» тембр
    partials = ((1.0, 1.00, 0.9), (2.0, 0.55, 0.6), (2.76, 0.40, 0.45), (5.40, 0.22, 0.3), (8.93, 0.10, 0.2))
    count = int(rate * duration)
    samples = [0.0] * count
    for ratio, amp, decay in partials:
        f = freq * ratio
        if f >= rate / 2:
            continue
        w = 2 * math.pi * f / rate
        env, step = amp, math.exp(-1.0 / (decay * rate))
        for i in range(count):
            samples[i] += env * math.sin(w * i)
            env *= step
    attack, release = int(rate * 0.004), int(rate * 0.25)
    for i in range(attack):  # мягкая атака без щелчка
        samples[i] *= i / attack
    for i in range(release):  # и мягкий хвост
        samples[count - 1 - i] *= i / release
    peak = max(abs(s) for s in samples) or 1.0
    scale = 0.7 * 32767 / peak
    frames = array("h", (int(s * scale) for s in samples))
    if sys.byteorder == "big":
        frames.byteswap()  # WAV хранит little-endian
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames.tobytes())
    os.replace(tmp, path)


def ensure_bell() -> Optional[str]:
    """Синтезирует встроенный звон, если его ещё нет на диске."""
    with _BELL_LOCK:
        if os.path.isfile(BELL_FILE):
            return BELL_FILE
        try:
            make_bell_wav(BELL_FILE)
            return BELL_FILE
        except OSError as e:
            log.error("Не удалось создать звук колокольчика: %s", e)
            return None


def _apply_volume_wav(src: str, dst: str, volume: int) -> bool:
    """Перечитывает WAV, умножает громкость, пишет новый WAV. volume: 0..100."""
    try:
        with wave.open(src, "rb") as wav:
            params = wav.getparams()
            frames = wav.readframes(params.nframes)
    except (OSError, wave.Error) as e:
        log.error("Не удалось прочитать WAV %s: %s", src, e)
        return False
    if params.sampwidth != 2:
        # Нестандартная глубина — проиграем как есть (без контроля громкости)
        try:
            with open(src, "rb") as fsrc, open(dst + ".tmp", "wb") as fdst:
                fdst.write(fsrc.read())
            os.replace(dst + ".tmp", dst)
            return True
        except OSError:
            return False
    vol = max(0.0, min(1.0, volume / 100.0))
    pcm = array("h", frames)
    if sys.byteorder == "big":
        pcm.byteswap()
    for i, s in enumerate(pcm):
        v = int(s * vol)
        pcm[i] = -32768 if v < -32768 else 32767 if v > 32767 else v
    if sys.byteorder == "big":
        pcm.byteswap()
    try:
        with wave.open(dst + ".tmp", "wb") as out:
            out.setparams(params)
            out.writeframes(pcm.tobytes())
        os.replace(dst + ".tmp", dst)
        return True
    except (OSError, wave.Error) as e:
        log.error("Не удалось записать WAV %s: %s", dst, e)
        return False


_ACTIVE_SIG: Tuple[str, int] = ("", -1)


def prepare_sound(source_path: str, volume: int) -> Optional[str]:
    """Готовит WAV с нужной громкостью. source_path="" — встроенный звон."""
    global _ACTIVE_SIG
    src = source_path if source_path else (ensure_bell() or "")
    if not src or not os.path.isfile(src):
        return None
    sig = (src, int(volume))
    if sig == _ACTIVE_SIG and os.path.isfile(BELL_ACTIVE):
        return BELL_ACTIVE
    if _apply_volume_wav(src, BELL_ACTIVE, volume):
        _ACTIVE_SIG = sig
        return BELL_ACTIVE
    return None


def play_bell(source_path: str = "", volume: int = 70) -> None:
    path = prepare_sound(source_path, volume)
    if IS_WINDOWS and path:
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return
        except Exception as e:  # noqa: BLE001 — звук не критичен
            log.error("Не удалось проиграть звук: %s", e)
    QApplication.beep()


class AlertTracker:
    """Решает, когда звенеть: один раз за каждое появление объекта, за alert_before секунд до него
    (или за столько, сколько задано в leads: {ключ таймера: секунды}, 0 — в момент появления)."""

    def __init__(self, timers=TIMERS, leads=None):
        self.timers = [t for t in timers if t.alert_before > 0]
        self.leads = dict(leads or {})
        self._fired = set()

    def check(self, clock: Optional[float]) -> List[TimerDef]:
        if clock is None:
            return []
        due = []
        for t in self.timers:
            left = seconds_until(clock, t.first, t.interval)
            if 0 <= left <= self.leads.get(t.key, t.alert_before):
                key = (t.key, int(round(clock + left)))  # игровая секунда появления
                if key not in self._fired:
                    self._fired.add(key)
                    due.append(t)
        self._fired = {k for k in self._fired if k[1] > clock - 120}
        return due
