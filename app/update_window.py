"""Окна обновления: «Доступна новая версия» и «Что нового» после обновления."""
from __future__ import annotations

import html
import threading
import webbrowser
from typing import List

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QTextBrowser

from app import updater
from app.config import ACCENT, BORDER, FONT, MUTED, TEXT, log
from app.ui_common import FramelessCard, make_button
from app.version import VERSION

PRIMARY = (f"QPushButton {{ background: {ACCENT}; color: #000; border: none; border-radius: 8px; }}"
           " QPushButton:hover { background: #00cc99; } QPushButton:disabled { background: #335; color: #889; }")
SECONDARY = (f"QPushButton {{ background: transparent; color: {TEXT}; border: 1px solid {MUTED}; border-radius: 8px; }}"
             " QPushButton:hover { background: #222233; } QPushButton:disabled { color: #555; border-color: #444; }")


def notes_html(releases: List[updater.Release]) -> str:
    parts = []
    for r in releases:
        lines = []
        for line in (r.notes or "Без описания.").split("\n"):
            text = html.escape(line.strip())
            if not text:
                lines.append("<br>")
            elif text.startswith(("- ", "— ", "• ")):
                lines.append(f"<div style='margin-left:10px'>• {text[2:]}</div>")
            else:
                lines.append(f"<div>{text}</div>")
        parts.append(f"<div style='color:{ACCENT}; font-weight:bold; margin-top:6px'>{html.escape(r.tag)}</div>"
                     + "".join(lines))
    return f"<div style='color:{TEXT}'>{''.join(parts)}</div>"


class _Card(FramelessCard):
    def __init__(self, title: str, subtitle: str, releases: List[updater.Release], parent=None):
        super().__init__((480, 420), radius=16, border_width=2, parent=parent)
        self.body.setContentsMargins(20, 18, 20, 18)
        self.body.setSpacing(10)

        lbl_title = QLabel(title)
        lbl_title.setFont(QFont(FONT, 11, QFont.Bold))
        lbl_title.setStyleSheet(f"color: {ACCENT};")
        lbl_title.setAlignment(Qt.AlignCenter)

        self.lbl_sub = QLabel(subtitle)
        self.lbl_sub.setFont(QFont(FONT, 9))
        self.lbl_sub.setStyleSheet(f"color: {TEXT};")
        self.lbl_sub.setAlignment(Qt.AlignCenter)
        self.lbl_sub.setWordWrap(True)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setFont(QFont(FONT, 9))
        notes.setStyleSheet(f"QTextBrowser {{ background: #0b0b12; border: 1px solid {BORDER}; border-radius: 8px;"
                            " padding: 6px; }")
        notes.setHtml(notes_html(releases))

        self.body.addWidget(lbl_title)
        self.body.addWidget(self.lbl_sub)
        self.body.addWidget(notes, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(10)
        self.body.addLayout(self.buttons)


class NewsDialog(_Card):
    """После обновления: что поменялось."""

    def __init__(self, releases: List[updater.Release], parent=None):
        super().__init__("ЧТО НОВОГО", f"Программа обновлена до версии v.{VERSION}", releases, parent)
        btn = make_button("ПОНЯТНО", height=32, font=QFont(FONT, 9, QFont.Bold), style=PRIMARY)
        btn.clicked.connect(self.accept)
        self.buttons.addWidget(btn)


class UpdateDialog(_Card):
    """Найдена новая версия: обновиться сейчас или позже. Скачивание идёт в фоне, окно показывает проценты."""

    progressed = pyqtSignal(int, int)
    finished = pyqtSignal(str)          # пустая строка — успех, иначе текст ошибки

    def __init__(self, release: updater.Release, parent=None):
        super().__init__("ДОСТУПНО ОБНОВЛЕНИЕ", f"Вышла версия {release.tag}, у вас v.{VERSION}. Что изменилось:",
                         release.newer or [release], parent)
        self.release = release
        self.installed = False
        self.can_install = updater.can_self_update()
        self.btn_later = make_button("ПОЗЖЕ", height=32, font=QFont(FONT, 9), style=SECONDARY)
        self.btn_go = make_button("ОБНОВИТЬ" if self.can_install else "ОТКРЫТЬ СТРАНИЦУ ЗАГРУЗКИ",
                                  height=32, font=QFont(FONT, 9, QFont.Bold), style=PRIMARY)
        self.btn_later.clicked.connect(self.reject)
        self.btn_go.clicked.connect(self._go)
        self.buttons.addWidget(self.btn_later)
        self.buttons.addWidget(self.btn_go)
        self.progressed.connect(self._on_progress)
        self.finished.connect(self._on_finished)

    def _go(self):
        if not self.can_install:
            webbrowser.open(self.release.page)
            self.reject()
            return
        self.btn_go.setEnabled(False)
        self.btn_later.setEnabled(False)
        self.lbl_sub.setText("Скачивание…")
        threading.Thread(target=self._work, name="update", daemon=True).start()

    def _work(self):
        try:
            updater.install(self.release, lambda done, total: self.progressed.emit(done, total))
            self.finished.emit("")
        except Exception as e:  # noqa: BLE001
            log.warning("Обновление не удалось: %s", e, exc_info=True)
            self.finished.emit(str(e) or e.__class__.__name__)

    def _on_progress(self, done: int, total: int):
        mb = done / 1048576
        self.lbl_sub.setText(f"Скачивание… {100 * done // total}% ({mb:.1f} МБ)" if total else f"Скачивание… {mb:.1f} МБ")

    def _on_finished(self, error: str):
        if not error:
            self.installed = True
            self.lbl_sub.setText("Готово, перезапуск…")
            self.accept()
            return
        self.lbl_sub.setText(f"Не получилось обновить: {error}")
        self.btn_later.setEnabled(True)
        self.btn_later.setText("ЗАКРЫТЬ")
        self.btn_go.setEnabled(True)
        self.btn_go.setText("СКАЧАТЬ ВРУЧНУЮ")
        self.can_install = False

    def reject(self):
        if self.btn_later.isEnabled():   # пока качается, Esc окно не закрывает
            super().reject()
