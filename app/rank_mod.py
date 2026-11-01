"""Свой вид ранга в Dota 2: родные иконки медалей и звёзд переставляются местами, свой пак в game/dota_russian.

Картинка медали общая для всех игроков этого ранга: подмена видна только у тебя, но и у других игроков с тем же
рангом (и тем же числом звёзд) ты увидишь подменённую иконку. Файлы самой игры не меняются, выключение удаляет пак.
"""
from __future__ import annotations

import json
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

# анимация медали: кадры лежат в наборе как anim/rank<код>/<номер>_psd.vtex_c (текстуры крупнее родной игра не рисует,
# поэтому кадр — отдельный файл); переключает их JS, дописанный в скрипты, которые выполняются в главном меню
ANIM_DIR = "anim"
ANIM_MEDALS = tuple(code for code, _name in MEDALS)
ANIM_SCRIPTS = ("panorama/scripts/hero_model_overlay.vjs_c", "panorama/scripts/popups/popup_settings_reborn.vjs_c")
MAX_FRAMES = 24
MIN_DELAY = 40                                                   # мс: быстрее кадры всё равно не успеют смениться
ANIM_JS = r"""
;(function () {
    var ANIM = __ANIM__, items = [], ticks = 0, n;
    for (n in ANIM) {
        ANIM[n].t = 0;
        for (var i = 0; i < ANIM[n].d.length; i++) ANIM[n].t += ANIM[n].d[i];
    }
    var root = $.GetContextPanel();
    while (root.GetParent()) root = root.GetParent();
    function rankOf(p) {
        for (var q = p; q; q = q.GetParent()) {
            if (q.BHasClass("LeaderboardTop1") || q.BHasClass("LeaderboardTop10")) return "8c";
            if (q.BHasClass("LeaderboardTop100")) return "8b";
            if (q.BHasClass("LeaderboardTop5000")) return "8a";
            for (var r = 0; r < 9; r++)
                if (q.BHasClass("RankTier" + r)) return String(r);
        }
        return "";
    }
    function url(a, k) {
        return 'url("s2r://panorama/images/rank_tier_icons/anim/rank' + a.m + "/" + k + '_psd.vtex")';
    }
    function preload(a) {
        // игра выгружает картинку, которую никто не показывает: без этого кадры грузились бы заново и медаль мигала
        a.ready = ticks + 60;
        for (var k = 0; k < a.d.length; k++) {
            var h = $.CreatePanel("Panel", root, "");
            h.hittest = false;
            h.style.width = "1px";
            h.style.height = "1px";
            h.style.opacity = "0.01";
            h.style.backgroundImage = url(a, k);
        }
    }
    function refresh() {
        items = [];
        var found = root.FindChildrenWithClassTraverse("RankTierImage");
        for (var i = 0; i < found.length; i++) {
            var p = found[i];
            if (p.BHasClass("Minimal")) continue;
            var r = rankOf(p);
            if (ANIM[r] && p.FindChildrenWithClassTraverse("RankTierImage").length) {
                // медаль из двух вложенных панелей (окно рангов): непрозрачная гифка торчала бы из-под внутренней
                p.style.backgroundImage = "none";
                p.cccAnim = true;
                continue;
            }
            if (ANIM[r]) {
                if (ANIM[r].ready === undefined) preload(ANIM[r]);
                items.push({ p: p, n: r, k: -1 });
            }
            else if (p.cccAnim) { p.style.backgroundImage = null; p.cccAnim = false; }
        }
    }
    function frameOf(a, now) {
        var t = now % a.t;
        for (var i = 0; i < a.d.length; i++) {
            t -= a.d[i];
            if (t < 0) return i;
        }
        return 0;
    }
    function tick() {
        try {
            if (ticks++ % 20 === 0) refresh();
            var now = Date.now();
            for (var i = 0; i < items.length; i++) {
                var it = items[i], a = ANIM[it.n];
                if (!it.p.IsValid() || ticks < a.ready) continue;
                var k = frameOf(a, now);
                if (k === it.k) continue;
                it.k = k;
                it.p.cccAnim = true;
                it.p.style.backgroundImage = url(a, k);
            }
        } catch (e) { $.Msg("CCC rank anim: " + e); }
        $.Schedule(0.05, tick);
    }
    $.Schedule(1.0, tick);
})();
"""


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
    for name, _head, start, size in d.resource_blocks(data):
        if name == b"DATA":
            w, h, _depth, fmt, mips = struct.unpack_from("<HHHBB", data, start + 20)
            if fmt != 28 or mips != 1 or len(data) - w * h * 4 != start + size:
                raise ValueError("неожиданный формат текстуры")
            return start + size, w, h
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
                if n.endswith(".json"):                    # задержки кадров анимации в пак не нужны
                    continue
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


def _alpha_box(alpha: bytes, w: int, h: int):
    """(x0, y0, x1, y1) того, что нарисовано, по байтам альфы; None — картинка пустая."""
    rows = [y for y in range(h) if max(alpha[y * w:(y + 1) * w]) > 20]
    if not rows:
        return None
    cols = [x for x in range(w) if max(alpha[x::w]) > 20]
    return min(cols), rows[0], max(cols) + 1, rows[-1] + 1


def _boxes(template: bytes, parts: int = 1):
    """Где в родной текстуре нарисовано: рамки непрозрачного. parts > 1 — столько звёзд в ряд (pipN):
    точки раскладываются на parts групп по горизонтали (звёзды могут касаться, поэтому не по связности)."""
    w, h, alpha = _alpha(template)
    box = _alpha_box(alpha, w, h)
    if box is None:
        return [(0, 0, w, h)]
    if parts == 1:
        return [box]
    pts = [(i % w, i // w) for i, a in enumerate(alpha) if a > 20]
    lo, hi = min(x for x, _ in pts), max(x for x, _ in pts)
    centers = [lo + (hi - lo) * (k + 0.5) / parts for k in range(parts)]
    for _ in range(10):                                    # k-средних по x: центры звёзд
        groups = [[] for _ in centers]
        for x, y in pts:
            groups[min(range(parts), key=lambda k: abs(x - centers[k]))].append((x, y))
        centers = [sum(x for x, _ in g) / len(g) if g else c for g, c in zip(groups, centers)]
    return [(min(x for x, _ in g), min(y for _, y in g), max(x for x, _ in g) + 1, max(y for _, y in g) + 1)
            for g in groups if g]


def _trim_box(img):
    """(x, y, ширина, высота) картинки без прозрачных полей (ищем по уменьшенной копии — у больших картинок
    миллионы точек); None — обрезать нечего."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImage
    if not img.hasAlphaChannel():
        return None
    small = img.scaled(256, 256, Qt.KeepAspectRatio, Qt.FastTransformation).convertToFormat(QImage.Format_ARGB32)
    sw, sh = small.width(), small.height()
    box = _alpha_box(small.constBits().asstring(sw * sh * 4)[3::4], sw, sh)
    if box is None:
        return None
    kx, ky = img.width() / sw, img.height() / sh
    x0, y0 = int(box[0] * kx), int(box[1] * ky)
    return x0, y0, int(box[2] * kx) - x0, int(box[3] * ky) - y0


def _trim(img):
    box = _trim_box(img)
    return img.copy(*box) if box else img


def _encode(template: bytes, img, parts: int = 1, trim: bool = True, boxes=None) -> bytes:
    """Картинка в формате текстуры-образца: вписываем туда, где нарисована родная иконка, с сохранением
    пропорций. Для звёзд (parts = 1..5) одна картинка звезды ставится на место каждой родной звезды."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImage, QPainter
    start, w, h = _data_offset(template)
    canvas = QImage(w, h, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    if trim:
        img = _trim(img)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    for x0, y0, x1, y1 in boxes or _boxes(template, parts):
        scaled = img.scaled(x1 - x0, y1 - y0, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawImage(x0 + (x1 - x0 - scaled.width()) // 2, y0 + (y1 - y0 - scaled.height()) // 2, scaled)
    p.end()
    return template[:start] + canvas.constBits().asstring(w * h * 4)


def _read_frames(path: str):
    """Кадры анимированной картинки: [(QImage, задержка в мс)] не больше MAX_FRAMES, с общей длительностью как в
    файле и одной обрезкой на все кадры (иначе картинка дрожала бы). Пусто — картинка не анимированная."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImage, QImageReader, QPainter
    reader = QImageReader(path)
    if not reader.supportsAnimation():
        return []
    frames = []
    while True:
        img = reader.read()
        if img.isNull():
            break
        delay = reader.nextImageDelay()
        frames.append((img.convertToFormat(QImage.Format_ARGB32), delay if delay > 0 else 100))
    if len(frames) < 2:
        return []
    if len(frames) > MAX_FRAMES:
        marks = [i * len(frames) // MAX_FRAMES for i in range(MAX_FRAMES + 1)]
        frames = [(frames[a][0], sum(x[1] for x in frames[a:b])) for a, b in zip(marks, marks[1:])]
    all_frames = QImage(frames[0][0].size(), QImage.Format_ARGB32)
    all_frames.fill(Qt.transparent)
    p = QPainter(all_frames)
    for img, _delay in frames:
        p.drawImage(0, 0, img)
    p.end()
    box = _trim_box(all_frames)
    return [(img.copy(*box) if box else img, max(delay, MIN_DELAY)) for img, delay in frames]


def _anim_delays(code: str, set_name: str):
    """Задержки кадров анимации медали в наборе; None — медаль не анимированная."""
    if not set_name:
        return None
    try:
        with open(os.path.join(SETS_DIR, set_name, ANIM_DIR, f"rank{code}", "delays.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _anim_script(real: str, show: str, set_name: str) -> Optional[str]:
    """JS анимации: настоящий ранг показывает кадры выбранной медали, остальные — свои. Как и в build_files,
    настоящий Титан подменяется вместе с особыми медалями."""
    swapped = [real] + (["8a", "8b", "8c"] if real == "8" else [])
    table = {}
    for code in ANIM_MEDALS:
        src = show if code in swapped else code
        delays = _anim_delays(src, set_name)
        if delays:
            table[code] = {"m": src, "d": delays}
    return ANIM_JS.replace("__ANIM__", json.dumps(table, separators=(",", ":"))) if table else None


def _medal_files(slot: str) -> list:
    """Иконки медали: сама и её маленькая версия."""
    code = slot[4:]
    return [slot, f"mini/rank{code}" if len(code) == 1 else f"rank{code}_mini"]


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
    родных звёзд сразу во всех пяти вариантах (★…★★★★★). GIF на медали становится анимацией, а первый кадр —
    её обычной картинкой (там, где анимации нет); на звёздах берётся только первый кадр."""
    from PyQt5.QtGui import QImage
    img = QImage(path)
    if img.isNull():
        return False, "Не получилось открыть картинку."
    img = img.convertToFormat(QImage.Format_ARGB32)
    code = slot[4:] if slot.startswith("rank") else ""
    frames = _read_frames(path) if code in ANIM_MEDALS else []
    if frames:
        img = frames[0][0]
    if slot.startswith("pip"):
        names = [(f"pip{n}", n) for n in range(1, MAX_STARS + 1)]
    else:
        names = [(name, 1) for name in _medal_files(slot)]
    try:
        for name, parts in names:
            data = _encode(_native(f"{ICON_DIR}{name}_psd.vtex_c"), img, parts, trim=not frames)   # кадры уже обрезаны
            _save_to_set(set_name, f"{name}_psd.vtex_c", data)
        if code:
            shutil.rmtree(os.path.join(SETS_DIR, set_name, ANIM_DIR, slot), ignore_errors=True)
        if frames:
            template = _native(f"{ICON_DIR}{slot}_psd.vtex_c")
            boxes = _boxes(template)
            for k, (frame, _delay) in enumerate(frames):
                _save_to_set(set_name, f"{ANIM_DIR}/{slot}/{k}_psd.vtex_c", _encode(template, frame, trim=False, boxes=boxes))
            with open(os.path.join(SETS_DIR, set_name, ANIM_DIR, slot, "delays.json"), "w", encoding="utf-8") as f:
                json.dump([delay for _frame, delay in frames], f)
    except (OSError, KeyError, ValueError, struct.error) as e:
        return False, f"Не получилось сохранить картинку: {e}"
    return True, set_name


def reset_slot(set_name: str, slot: str) -> None:
    """Вернуть иконке slot в наборе родной вид."""
    code = slot[4:] if slot.startswith("rank") else ""
    names = _medal_files(slot) if code else [slot]
    if code:
        shutil.rmtree(os.path.join(SETS_DIR, set_name, ANIM_DIR, slot), ignore_errors=True)
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
    js = _anim_script(real, show, set_name)
    if js:
        try:
            for script in ANIM_SCRIPTS:
                files[script] = d.append_to_data(_native(script), js.encode("utf-8"))
        except (KeyError, ValueError, struct.error) as e:      # после патча Dota: ранги важнее анимации
            for script in ANIM_SCRIPTS:
                files.pop(script, None)
            log.warning("Анимация медалей выключена: %s", e)
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
