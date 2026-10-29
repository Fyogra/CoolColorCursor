"""Свой вид ранга в Dota 2: родные иконки медалей и звёзд переставляются местами, свой пак в game/dota_russian.

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

BACKUP_DIR = os.path.join(DATA_DIR, "backup", "ranks")   # сторонние паки рангов на время нашей подмены
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


def is_installed() -> bool:
    return bool(d.our_pak("rank"))


def lang_dir() -> Optional[str]:
    game = d.game_dir()
    return os.path.join(game, d.LANG_DIR) if game else None


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
    """Набор из готового пака рангов (.vpk): забираем из него иконки рангов. Возвращает (успех, имя/ошибка)."""
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


def _alpha(data: bytes):
    """(ширина, высота, байты альфы) текстуры-образца."""
    start, w, h = _data_offset(data)
    return w, h, data[start + 3::4]


def _boxes(template: bytes, parts: int = 1):
    """Где в родной текстуре нарисовано: рамки непрозрачного. parts > 1 — столько звёзд в ряд (pipN):
    точки раскладываются на parts групп по горизонтали (звёзды могут касаться, поэтому не по связности)."""
    w, h, alpha = _alpha(template)
    pts = [(i % w, i // w) for i, a in enumerate(alpha) if a > 20]
    if not pts:
        return [(0, 0, w, h)]
    if parts == 1:
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        return [(min(xs), min(ys), max(xs) + 1, max(ys) + 1)]
    lo, hi = min(x for x, _ in pts), max(x for x, _ in pts)
    centers = [lo + (hi - lo) * (k + 0.5) / parts for k in range(parts)]
    for _ in range(10):                                    # k-средних по x: центры звёзд
        groups = [[] for _ in centers]
        for x, y in pts:
            groups[min(range(parts), key=lambda k: abs(x - centers[k]))].append((x, y))
        centers = [sum(x for x, _ in g) / len(g) if g else c for g, c in zip(groups, centers)]
    return [(min(x for x, _ in g), min(y for _, y in g), max(x for x, _ in g) + 1, max(y for _, y in g) + 1)
            for g in groups if g]


def _trim(img):
    """Картинка без прозрачных полей (ищем по уменьшенной копии — у больших картинок миллионы точек)."""
    from PyQt5.QtCore import Qt
    if not img.hasAlphaChannel():
        return img
    small = img.scaled(256, 256, Qt.KeepAspectRatio, Qt.FastTransformation)
    sw, sh = small.width(), small.height()
    xs, ys = [], []
    for y in range(sh):
        for x in range(sw):
            if (small.pixel(x, y) >> 24) > 20:
                xs.append(x)
                ys.append(y)
    if not xs:
        return img
    kx, ky = img.width() / sw, img.height() / sh
    x0, y0 = int(min(xs) * kx), int(min(ys) * ky)
    return img.copy(x0, y0, int((max(xs) + 1) * kx) - x0, int((max(ys) + 1) * ky) - y0)


def _encode(template: bytes, img, parts: int = 1) -> bytes:
    """Картинка в формате текстуры-образца: вписываем туда, где нарисована родная иконка, с сохранением
    пропорций. Для звёзд (parts = 1..5) одна картинка звезды ставится на место каждой родной звезды."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImage, QPainter
    start, w, h = _data_offset(template)
    canvas = QImage(w, h, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    img = _trim(img)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    for x0, y0, x1, y1 in _boxes(template, parts):
        scaled = img.scaled(x1 - x0, y1 - y0, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawImage(x0 + (x1 - x0 - scaled.width()) // 2, y0 + (y1 - y0 - scaled.height()) // 2, scaled)
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
    """Своя картинка для иконки slot ('rank5', 'rank8a', 'pip3'). Размер и место подгоняются под родную иконку;
    у медалей заодно делается маленькая версия. Для звёзд хватает одной звезды: она расставляется по местам
    родных звёзд сразу во всех пяти вариантах (★…★★★★★)."""
    from PyQt5.QtGui import QImage
    img = QImage(path)
    if img.isNull():
        return False, "Не получилось открыть картинку."
    img = img.convertToFormat(QImage.Format_ARGB32)
    if slot.startswith("pip"):
        names = [(f"pip{n}", n) for n in range(1, MAX_STARS + 1)]
    else:
        code = slot[4:]
        names = [(slot, 1), (f"mini/rank{code}" if len(code) == 1 else f"rank{code}_mini", 1)]
    try:
        for name, parts in names:
            data = _encode(_native(f"{ICON_DIR}{name}_psd.vtex_c"), img, parts)
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


def _stash_other_paks() -> None:
    """Сторонние паки с иконками рангов в dota_russian убираем в резерв, иначе игра может взять их иконки вместо наших."""
    folder = os.path.join(d.game_dir(), d.LANG_DIR)
    ours = {d.our_pak(k) for k in d.DEFAULT_PAKS}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not name.endswith("_dir.vpk") or path in ours:
            continue
        if os.path.exists(path.replace("_dir.vpk", "_000.vpk")):
            continue                                       # многотомный пак — это файлы самой игры
        try:
            files = d._read_tree(path)[0]
        except (OSError, ValueError, struct.error):
            continue
        if not files or sum(k.startswith(ICON_DIR) for k in files) * 2 < len(files):
            continue                                       # трогаем только паки, которые почти целиком — ранги
        os.makedirs(BACKUP_DIR, exist_ok=True)
        shutil.move(path, os.path.join(BACKUP_DIR, name))
        log.info("Сторонний пак рангов %s убран в резерв: %s", name, BACKUP_DIR)


def install(real: str, real_stars: int, show: str, show_stars: int, set_name: str = "") -> Tuple[bool, str]:
    if not d.game_dir():
        return False, "Не нашёл папку Dota 2."
    try:
        files = build_files(real, real_stars, show, show_stars, set_name)
        if not files:
            ok, err = uninstall(restore=False)
            return (True, "Ранг и так такой — подменять нечего.") if ok else (False, err)
        _stash_other_paks()
        d.write_pak("rank", files)
    except PermissionError:
        return False, "Файл занят: закрой Dota 2 и попробуй снова."
    except (OSError, KeyError, ValueError, struct.error) as e:
        return False, f"Не получилось собрать пак: {e}"
    log.info("Ранг в Dota: %s/%d показывается как %s/%d, набор «%s» (%d файлов)",
             real, real_stars, show, show_stars, set_name or "родные", len(files))
    return True, ""


def uninstall(restore: bool = True) -> Tuple[bool, str]:
    """Удаляет наш пак; restore — вернуть из резерва убранные сторонние паки рангов."""
    try:
        d.remove_pak("rank")
        game = d.game_dir()
        if restore and game and os.path.isdir(BACKUP_DIR):
            for name in os.listdir(BACKUP_DIR):
                shutil.move(os.path.join(BACKUP_DIR, name), os.path.join(game, d.LANG_DIR, name))
    except PermissionError:
        return False, "Файл занят: закрой Dota 2 и попробуй снова."
    except OSError as e:
        return False, f"Не получилось удалить пак: {e}"
    return True, ""
