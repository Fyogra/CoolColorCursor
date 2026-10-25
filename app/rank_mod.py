"""Свой вид ранга в Dota 2: родные иконки медалей и звёзд переставляются местами, пак pak69_dir.vpk в game/dota_russian.

Картинка медали общая для всех игроков этого ранга: подмена видна только у тебя, но и у других игроков с тем же
рангом (и тем же числом звёзд) ты увидишь подменённую иконку. Файлы самой игры не меняются, выключение удаляет пак.
"""
from __future__ import annotations

import os
import shutil
import struct
from typing import Dict, Optional, Tuple
from app import dota_mod as d
from app.config import DATA_DIR, log

PAK_NAME = "pak69_dir.vpk"
OLD_PAK = "pak32_dir.vpk"                 # чужой пак с отрисовкой рангов: конфликтует с нашим, убираем в резерв
BACKUP_DIR = os.path.join(DATA_DIR, "backup")
SETS_DIR = os.path.join(DATA_DIR, "rank_sets")  # свои наборы иконок: папка на набор, внутри файлы как в игре
MY_SET = "Мои картинки"
ICON_DIR = "panorama/images/rank_tier_icons/"

# медали: код -> подпись; 8a/8b/8c — особые медали Титана из таблицы лидеров
MEDALS = (("0", "Без ранга"), ("1", "Рекрут"), ("2", "Страж"), ("3", "Рыцарь"), ("4", "Герой"), ("5", "Легенда"),
          ("6", "Властелин"), ("7", "Божество"), ("8", "Титан"), ("8a", "Титан, особая 1"),
          ("8b", "Титан, особая 2"), ("8c", "Титан, особая 3"))
REAL_MEDALS = MEDALS[:9]
# все иконки, которые можно заменить своей картинкой: (имя в игре без _psd, подпись)
SLOTS = tuple((f"rank{code}", name) for code, name in MEDALS) + tuple(
    (f"pip{n}", "★" * n) for n in range(1, 6))                  # настоящий ранг: особые медали Титана считаем обычным Титаном
MAX_STARS = 5


def has_stars(medal: str) -> bool:
    return medal not in ("0", "8", "8a", "8b", "8c")


def _base() -> Optional[str]:
    game = d.game_dir()
    return os.path.join(game, "dota", "pak01_dir.vpk") if game else None


_TREE = None                              # (путь, время файла, оглавление pak01): разбирать его долго, читаем один раз


def _native(inner: str) -> bytes:
    """Родной файл из pak01 по закэшированному оглавлению."""
    global _TREE
    base = _base()
    mtime = os.path.getmtime(base)
    if _TREE is None or _TREE[0] != base or _TREE[1] != mtime:
        _TREE = (base, mtime, d._read_tree(base))
    return d._read_from(base, _TREE[2], inner)


def pak_path() -> Optional[str]:
    game = d.game_dir()
    return os.path.join(game, d.LANG_DIR, PAK_NAME) if game else None


def is_installed() -> bool:
    path = pak_path()
    return bool(path and os.path.exists(path))


def _data_offset(data: bytes) -> Tuple[int, int, int]:
    """(начало картинки, ширина, высота) для текстуры BGRA8888 без mip; ValueError — формат не тот."""
    _fs, _hv, _ver, off, cnt = struct.unpack_from("<IHHII", data, 0)
    pos = 8 + off
    for _ in range(cnt):
        rel, size = struct.unpack_from("<II", data, pos + 4)
        if data[pos:pos + 4] == b"DATA":
            w, h, _depth, fmt, mips = struct.unpack_from("<HHHBB", data, pos + 4 + rel + 20)
            if fmt != 28 or mips != 1 or len(data) - w * h * 4 != pos + 4 + rel + size:
                raise ValueError("неожиданный формат текстуры")
            return pos + 4 + rel + size, w, h
        pos += 12
    raise ValueError("нет блока DATA")


def _blank(data: bytes) -> bytes:
    """Та же текстура, но полностью прозрачная (для «без звёзд»)."""
    start, _w, _h = _data_offset(data)
    return data[:start] + bytes(len(data) - start)


# ── наборы иконок ─────────────────────────────────────────────────────────────
def list_sets():
    try:
        return sorted(n for n in os.listdir(SETS_DIR) if os.path.isdir(os.path.join(SETS_DIR, n)))
    except OSError:
        return []


def _set_files(set_name: str) -> Dict[str, bytes]:
    """Все файлы набора: {путь внутри игры: данные}."""
    root = os.path.join(SETS_DIR, set_name) if set_name else ""
    out = {}
    if root and os.path.isdir(root):
        for folder, _dirs, names in os.walk(root):
            for n in names:
                full = os.path.join(folder, n)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                with open(full, "rb") as f:
                    out[ICON_DIR + rel] = f.read()
    return out


def _icon(name: str, set_name: str = "") -> bytes:
    """Иконка (name без _psd: 'rank5', 'mini/rank5', 'pip3') из набора, а если там нет — родная из игры."""
    rel = f"{name}_psd.vtex_c"
    path = os.path.join(SETS_DIR, set_name, *rel.split("/")) if set_name else ""
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return _native(ICON_DIR + rel)


def _save_to_set(set_name: str, rel: str, data: bytes) -> None:
    path = os.path.join(SETS_DIR, set_name, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def import_vpk(path: str) -> Tuple[bool, str]:
    """Набор из готового пака (например, pak32): забираем из него иконки рангов. Возвращает (успех, имя/ошибка)."""
    try:
        tree = d._read_tree(path)[0]
        names = [k for k in tree if k.startswith(ICON_DIR)]
        if not names:
            return False, "В этом паке нет иконок рангов."
        files = d._read_files(path, names)
    except (OSError, ValueError, KeyError, struct.error) as e:
        return False, f"Не получилось прочитать пак: {e}"
    set_name = os.path.splitext(os.path.basename(path))[0].replace("_dir", "")
    base_name, n = set_name, 2
    while os.path.exists(os.path.join(SETS_DIR, set_name)):
        set_name, n = f"{base_name} ({n})", n + 1
    for k, v in files.items():
        _save_to_set(set_name, k[len(ICON_DIR):], v)
    log.info("Набор рангов «%s» добавлен из %s: %d файлов", set_name, path, len(files))
    return True, set_name


def _encode(template: bytes, img) -> bytes:
    """Картинка в формате текстуры-образца: вписываем с сохранением пропорций по центру."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImage, QPainter
    start, w, h = _data_offset(template)
    canvas = QImage(w, h, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    scaled = img.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    p = QPainter(canvas)
    p.drawImage((w - scaled.width()) // 2, (h - scaled.height()) // 2, scaled)
    p.end()
    return template[:start] + canvas.constBits().asstring(w * h * 4)


def create_set(name: str) -> str:
    """Пустой набор (всё родное, пока не добавишь картинки); возвращает итоговое имя."""
    name = "".join(ch for ch in name.strip() if ch not in r'\/:*?"<>|') or MY_SET
    final, n = name, 2
    while os.path.exists(os.path.join(SETS_DIR, final)):
        final, n = f"{name} ({n})", n + 1
    os.makedirs(os.path.join(SETS_DIR, final), exist_ok=True)
    return final


def import_image(path: str, slot: str, set_name: str = MY_SET) -> Tuple[bool, str]:
    """Своя картинка для иконки slot ('rank5', 'rank8a', 'pip3'); у медалей заодно делается маленькая версия."""
    from PyQt5.QtGui import QImage
    img = QImage(path)
    if img.isNull():
        return False, "Не получилось открыть картинку."
    img = img.convertToFormat(QImage.Format_ARGB32)
    names = [slot]
    if slot.startswith("rank"):
        code = slot[4:]
        names.append(f"mini/rank{code}" if len(code) == 1 else f"rank{code}_mini")
    try:
        for name in names:
            data = _encode(_native(f"{ICON_DIR}{name}_psd.vtex_c"), img)
            _save_to_set(set_name, f"{name}_psd.vtex_c", data)
    except (OSError, KeyError, ValueError, struct.error) as e:
        return False, f"Не получилось сохранить картинку: {e}"
    return True, set_name


def reset_slot(set_name: str, slot: str) -> None:
    """Вернуть иконке slot в наборе родной вид."""
    code = slot[4:] if slot.startswith("rank") else ""
    names = [slot] + ([f"mini/rank{code}" if len(code) == 1 else f"rank{code}_mini"] if code else [])
    for name in names:
        try:
            os.remove(os.path.join(SETS_DIR, set_name, *f"{name}_psd.vtex_c".split("/")))
        except OSError:
            pass


def remove_set(set_name: str) -> None:
    shutil.rmtree(os.path.join(SETS_DIR, set_name), ignore_errors=True)


def image(name: str, set_name: str = ""):
    """QImage иконки для превью (name без _psd: 'rank5', 'pip3'); None — не получилось."""
    from PyQt5.QtGui import QImage
    try:
        data = _icon(name, set_name)
        start, w, h = _data_offset(data)
    except (OSError, KeyError, ValueError, TypeError, struct.error):
        return None
    return QImage(data[start:], w, h, w * 4, QImage.Format_ARGB32).copy()


def build_files(real: str, real_stars: int, show: str, show_stars: int, set_name: str = "") -> Dict[str, bytes]:
    """Файлы пака: весь выбранный набор иконок, а поверх — под именами настоящего ранга иконки выбранного."""
    pairs = []                                            # (куда, откуда или None — прозрачная)
    targets = [real] + (["8a", "8b", "8c"] if real == "8" else [])
    for t in targets:
        if t != show:
            pairs.append((f"rank{t}", f"rank{show}"))
    if real != show[0]:
        pairs.append((f"mini/rank{real}", f"mini/rank{show[0]}"))
    if real == "8":
        for t in ("8a", "8b", "8c"):
            if show.startswith("8") and t != show:
                pairs.append((f"rank{t}_mini", f"rank{show}_mini" if show != "8" else "rank8_mini"))
    if has_stars(real) and real_stars:
        want = show_stars if has_stars(show) else 0
        if want != real_stars:
            pairs.append((f"pip{real_stars}", f"pip{want}" if want else None))
    files = _set_files(set_name)
    for dst, src in pairs:
        files[f"{ICON_DIR}{dst}_psd.vtex_c"] = _icon(src, set_name) if src else _blank(_icon(dst, set_name))
    return files


def _stash_old_pak() -> None:
    """Чужой пак рангов (pak32) убираем в резерв, иначе игра может взять его иконки вместо наших."""
    game = d.game_dir()
    old = os.path.join(game, d.LANG_DIR, OLD_PAK)
    if not os.path.exists(old):
        return
    try:
        if not any(k.startswith(ICON_DIR) for k in d._read_tree(old)[0]):
            return
    except (OSError, ValueError, struct.error):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    shutil.move(old, os.path.join(BACKUP_DIR, OLD_PAK))
    log.info("Пак рангов %s убран в резерв: %s", OLD_PAK, BACKUP_DIR)


def install(real: str, real_stars: int, show: str, show_stars: int, set_name: str = "") -> Tuple[bool, str]:
    path = pak_path()
    if not path:
        return False, "Не нашёл папку Dota 2."
    try:
        files = build_files(real, real_stars, show, show_stars, set_name)
        if not files:
            ok, err = uninstall(restore=False)
            return (True, "Ранг и так такой — подменять нечего.") if ok else (False, err)
        _stash_old_pak()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        d._write_vpk(path, files)
    except PermissionError:
        return False, "Файл занят: закрой Dota 2 и попробуй снова."
    except (OSError, KeyError, ValueError, struct.error) as e:
        return False, f"Не получилось собрать пак: {e}"
    log.info("Ранг в Dota: %s/%d показывается как %s/%d, набор «%s» (%d файлов)",
             real, real_stars, show, show_stars, set_name or "родные", len(files))
    return True, ""


def uninstall(restore: bool = True) -> Tuple[bool, str]:
    """Удаляет наш пак; restore — вернуть из резерва убранный pak32."""
    path = pak_path()
    try:
        if path and os.path.exists(path):
            os.remove(path)
        saved = os.path.join(BACKUP_DIR, OLD_PAK)
        if restore and path and os.path.exists(saved):
            shutil.move(saved, os.path.join(os.path.dirname(path), OLD_PAK))
    except PermissionError:
        return False, "Файл занят: закрой Dota 2 и попробуй снова."
    except OSError as e:
        return False, f"Не получилось удалить пак: {e}"
    return True, ""
