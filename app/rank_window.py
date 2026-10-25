"""Окно «Ранг в Dota 2»: настоящий ранг, каким его показывать, превью и установка в игру."""
from __future__ import annotations

import os
from typing import Callable, Dict, Tuple
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QVBoxLayout
from app import rank_mod
from app.config import ACCENT, DIM, FONT, FONT_SYMBOL, TEXT
from app.ui_common import FramelessCard, caption, make_button, outline_button_style

Result = Tuple[bool, str]
COMBO_STYLE = (f"QComboBox {{ background: #1b1d22; color: {TEXT}; border: 1px solid #3a3f46; border-radius: 4px;"
               " padding: 3px 8px; } QComboBox QAbstractItemView { background: #1b1d22; color: #ddd;"
               f" selection-background-color: {ACCENT}; selection-color: #000; }}")


class RankPreview(QLabel):
    """Медаль со звёздами, как в игре: картинка медали и поверх — картинка звёзд."""

    SIZE = 120
    _cache: Dict[Tuple[str, str], QImage] = {}

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAlignment(Qt.AlignCenter)
        self.set_name = ""

    @classmethod
    def forget(cls):
        cls._cache.clear()

    def _img(self, name: str):
        key = (self.set_name, name)
        if key not in self._cache:
            self._cache[key] = rank_mod.image(name, self.set_name)
        return self._cache[key]

    def show_rank(self, medal: str, stars: int):
        pm = QPixmap(self.SIZE, self.SIZE)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        layers = [f"rank{medal}"] + ([f"pip{stars}"] if rank_mod.has_stars(medal) and stars else [])
        for name in layers:
            img = self._img(name)
            if img is not None:
                p.drawImage(pm.rect(), img)
        p.end()
        self.setPixmap(pm)


class RankSide(QVBoxLayout):
    """Колонка: подпись, превью, медаль и звёзды."""

    def __init__(self, title: str, medals, medal: str, stars: int, on_change: Callable[[], None]):
        super().__init__()
        self.setSpacing(6)
        head = caption(title, size=9, color=ACCENT)
        head.setAlignment(Qt.AlignCenter)
        self.addWidget(head)
        self.preview = RankPreview()
        self.addWidget(self.preview, 0, Qt.AlignHCenter)
        self.medal = QComboBox()
        self.medal.setStyleSheet(COMBO_STYLE)
        for code, name in medals:
            self.medal.addItem(name, code)
        idx = self.medal.findData(medal)
        self.medal.setCurrentIndex(idx if idx >= 0 else 0)
        self.stars = QComboBox()
        self.stars.setStyleSheet(COMBO_STYLE)
        for n in range(1, rank_mod.MAX_STARS + 1):
            self.stars.addItem("★" * n, n)
        self.stars.setCurrentIndex(max(0, min(rank_mod.MAX_STARS, stars) - 1))
        self.addWidget(self.medal)
        self.addWidget(self.stars)
        self._on_change = on_change
        self.medal.currentIndexChanged.connect(self._changed)
        self.stars.currentIndexChanged.connect(self._changed)
        self._changed()

    def value(self) -> Tuple[str, int]:
        return self.medal.currentData(), self.stars.currentData()

    def set_icons(self, set_name: str):
        self.preview.set_name = set_name
        self._changed()

    def _changed(self, *_):
        medal, stars = self.value()
        self.stars.setEnabled(rank_mod.has_stars(medal))
        self.preview.show_rank(medal, stars)
        self._on_change()


class RankDialog(FramelessCard):
    def __init__(self, real: Tuple[str, int], show: Tuple[str, int], set_name: str, installed: bool,
                 on_apply: Callable[[str, int, str, int, str], Result], on_revert: Callable[[], Result], parent=None):
        super().__init__((470, 530), radius=16, border_width=2, parent=parent, starry=True)
        self._on_apply, self._on_revert = on_apply, on_revert
        self.body.setContentsMargins(20, 18, 20, 18)
        self.body.setSpacing(10)

        title = QLabel("РАНГ В DOTA 2")
        title.setFont(QFont(FONT, 11, QFont.Bold))
        title.setStyleSheet(f"color: {ACCENT};")
        title.setAlignment(Qt.AlignCenter)
        self.body.addWidget(title)

        sides = QHBoxLayout()
        self.real = RankSide("МОЙ НАСТОЯЩИЙ", rank_mod.REAL_MEDALS, real[0], real[1], lambda: None)
        arrow = QLabel("➜")
        arrow.setFont(QFont(FONT_SYMBOL, 18))
        arrow.setStyleSheet(f"color: {DIM};")
        self.show_as = RankSide("ПОКАЗЫВАТЬ КАК", rank_mod.MEDALS, show[0], show[1], lambda: None)
        sides.addLayout(self.real, 1)
        sides.addWidget(arrow, 0, Qt.AlignVCenter)
        sides.addLayout(self.show_as, 1)
        self.body.addLayout(sides)

        sets = QHBoxLayout()
        sets.addWidget(caption("Набор иконок", size=9))
        self.sets = QComboBox()
        self.sets.setStyleSheet(COMBO_STYLE)
        self.sets.setToolTip("Свои картинки рангов: набор целиком заменяет родные иконки в игре")
        sets.addWidget(self.sets, 1)
        btn_pak = make_button("+ пак", size=(54, 26), font=QFont(FONT, 8, QFont.Bold), style=outline_button_style(ACCENT, radius=8))
        btn_pak.setToolTip("Добавить набор из готового пака рангов (.vpk)")
        btn_pak.clicked.connect(self._add_pak)
        btn_img = make_button("✎ редактор", size=(84, 26), font=QFont(FONT_SYMBOL, 8, QFont.Bold), style=outline_button_style(ACCENT, radius=8))
        btn_img.setToolTip("Свои картинки для любых медалей и звёзд выбранного набора (или новый набор)")
        btn_img.clicked.connect(self._edit_set)
        btn_del = make_button("✕", size=(26, 26), font=QFont(FONT_SYMBOL, 9, QFont.Bold), style=outline_button_style(DIM, radius=8))
        btn_del.setToolTip("Удалить выбранный набор")
        btn_del.clicked.connect(self._delete_set)
        sets.addWidget(btn_pak)
        sets.addWidget(btn_img)
        sets.addWidget(btn_del)
        self.body.addLayout(sets)
        self._fill_sets(set_name)
        self.sets.currentIndexChanged.connect(lambda *_: self.show_as.set_icons(self.sets.currentData() or ""))
        self.show_as.set_icons(set_name)

        hint = caption("Видишь только ты, но и у других игроков с таким же рангом и звёздами будет та же иконка.",
                       size=8, color=DIM)
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignCenter)
        self.body.addWidget(hint)

        self.lbl_status = caption("", size=9, color=DIM)
        self.lbl_status.setWordWrap(True)
        self.body.addWidget(self.lbl_status)
        self._set_status(installed)

        buttons = QHBoxLayout()
        btn_apply = make_button("✦ ПРИМЕНИТЬ В ИГРУ", height=34, font=QFont(FONT_SYMBOL, 9, QFont.Bold),
                                style=outline_button_style(ACCENT, radius=14))
        btn_apply.clicked.connect(self._apply)
        btn_revert = make_button("↺ ВЕРНУТЬ КАК БЫЛО", height=34, font=QFont(FONT_SYMBOL, 9, QFont.Bold),
                                 style=outline_button_style(DIM, radius=14))
        btn_revert.clicked.connect(self._revert)
        buttons.addWidget(btn_apply, 1)
        buttons.addWidget(btn_revert, 1)
        self.body.addLayout(buttons)

        btn_close = make_button("Закрыть", height=28, font=QFont(FONT, 9), style=outline_button_style(DIM, radius=12))
        btn_close.clicked.connect(self.accept)
        self.body.addWidget(btn_close)

    def _fill_sets(self, current: str):
        self.sets.blockSignals(True)
        self.sets.clear()
        self.sets.addItem("Родные Dota", "")
        for name in rank_mod.list_sets():
            self.sets.addItem(name, name)
        idx = self.sets.findData(current)
        self.sets.setCurrentIndex(idx if idx >= 0 else 0)
        self.sets.blockSignals(False)

    def _select_set(self, name: str):
        RankPreview.forget()
        self._fill_sets(name)
        self.show_as.set_icons(self.sets.currentData() or "")

    def _add_pak(self):
        start = rank_mod.pak_path()
        path, _ = QFileDialog.getOpenFileName(self, "Пак с рангами", os.path.dirname(start) if start else "", "Пак Dota (*.vpk)")
        if path:
            ok, res = rank_mod.import_vpk(path)
            self._set_status(rank_mod.is_installed(), f"Добавлен набор «{res}»." if ok else res)
            if ok:
                self._select_set(res)

    def _edit_set(self):
        name = self.sets.currentData()
        if not name:                                 # родные трогать нельзя — заводим новый набор
            text, ok = QInputDialog.getText(self, "Новый набор", "Название набора:", text=rank_mod.MY_SET)
            if not ok:
                return
            name = rank_mod.create_set(text)
        editor = SetEditor(name, self)
        editor.move(self.geometry().center() - editor.rect().center())
        editor.exec_()
        editor.deleteLater()
        self._select_set(name)
        self._set_status(rank_mod.is_installed(), f"Набор «{name}» сохранён. Нажми «Применить в игру».")

    def _delete_set(self):
        name = self.sets.currentData()
        if name:
            rank_mod.remove_set(name)
            self._select_set("")
            self._set_status(rank_mod.is_installed(), f"Набор «{name}» удалён.")

    def _set_status(self, installed: bool, note: str = ""):
        text = "Сейчас в игре: свой вид ранга установлен" if installed else "Сейчас в игре: родные иконки рангов"
        self.lbl_status.setText(text + (f"\n{note}" if note else ""))

    def _apply(self):
        (real, real_stars), (show, show_stars) = self.real.value(), self.show_as.value()
        ok, err = self._on_apply(real, real_stars, show, show_stars, self.sets.currentData() or "")
        self._set_status(ok and rank_mod.is_installed(), err or "Перезапусти Dota 2, чтобы увидеть.")

    def _revert(self):
        ok, err = self._on_revert()
        self._set_status(not ok, err if not ok else "Вернули родные иконки. Перезапусти Dota 2.")


class SlotTile(QLabel):
    """Ячейка редактора: картинка иконки (своя или родная) и подпись; клик — своя картинка, правый клик — родная."""

    SIZE = 64

    def __init__(self, editor: "SetEditor", slot: str, title: str):
        super().__init__()
        self.editor, self.slot = editor, slot
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{title}\nКлик — своя картинка, правый клик — вернуть родную")
        self.setStyleSheet("background: #1b1d22; border: 1px solid #3a3f46; border-radius: 6px;")
        self.refresh()

    def refresh(self):
        img = rank_mod.image(self.slot, self.editor.set_name)
        if img is None:
            self.setText("?")
            return
        self.setPixmap(QPixmap.fromImage(img).scaled(self.SIZE - 6, self.SIZE - 6, Qt.KeepAspectRatio,
                                                     Qt.SmoothTransformation))

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            rank_mod.reset_slot(self.editor.set_name, self.slot)
            self.refresh()
            return
        path, _ = QFileDialog.getOpenFileName(self, "Картинка", "", "Картинки (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            ok, err = rank_mod.import_image(path, self.slot, self.editor.set_name)
            self.editor.lbl_status.setText("" if ok else err)
            self.refresh()


class SetEditor(FramelessCard):
    """Редактор набора: все медали и звёзды, каждую можно заменить своей картинкой."""

    COLS = 6

    def __init__(self, set_name: str, parent=None):
        super().__init__((500, 430), radius=16, border_width=2, parent=parent, starry=True)
        self.set_name = set_name
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(8)
        title = QLabel(f"НАБОР «{set_name}»")
        title.setFont(QFont(FONT, 11, QFont.Bold))
        title.setStyleSheet(f"color: {ACCENT};")
        title.setAlignment(Qt.AlignCenter)
        self.body.addWidget(title)
        hint = caption("Клик — своя картинка (PNG, JPG…), правый клик — вернуть родную. "
                       "Картинка сама подгоняется под размер, маленькая версия медали делается тоже.", size=8, color=DIM)
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, (slot, name) in enumerate(rank_mod.SLOTS):
            cell = QVBoxLayout()
            cell.setSpacing(2)
            cell.addWidget(SlotTile(self, slot, name), 0, Qt.AlignHCenter)
            lbl = caption(name, size=7, color=DIM)
            lbl.setAlignment(Qt.AlignCenter)
            cell.addWidget(lbl)
            grid.addLayout(cell, i // self.COLS, i % self.COLS)
        self.body.addLayout(grid)
        self.lbl_status = caption("", size=9, color=DIM)
        self.body.addWidget(self.lbl_status)
        btn_close = make_button("Готово", height=30, font=QFont(FONT, 9, QFont.Bold), style=outline_button_style(ACCENT, radius=12))
        btn_close.clicked.connect(self.accept)
        self.body.addWidget(btn_close)
