"""Общее для модов Dota 2: папка игры, чтение и запись VPK, LZ4.

Наши паки кладутся в game/dota_russian: Dota монтирует эту папку раньше основной, если в параметрах запуска Steam
стоит -language russian. Файлы самой игры не меняются — выключение мода просто удаляет его пак.
"""
from __future__ import annotations

import binascii
import json
import os
import re
import struct
from typing import Dict, Optional, Tuple
from app.config import APP_NAME, DATA_DIR
from app.gsi import _steam_roots, find_dota_cfg_dir

LANG_DIR = "dota_russian"
LAUNCH_OPTION = "-language russian"
VPK_SIGNATURE = 0x55AA1234
IN_DIR_FILE = 0x7FFF


# ── LZ4 ─────────────────────────────────────────────────────────────────────
def _lz4_decompress(src: bytes, size: int) -> bytes:
    out, i = bytearray(), 0
    while i < len(src):
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= len(src):
            break
        offset = src[i] | src[i + 1] << 8
        i += 2
        match = token & 15
        if match == 15:
            while True:
                b = src[i]
                i += 1
                match += b
                if b != 255:
                    break
        for _ in range(match + 4):
            out.append(out[-offset])
    if len(out) != size:
        raise ValueError("LZ4: неверный размер данных")
    return bytes(out)


def _lz4_literals(data: bytes) -> bytes:
    """LZ4-блок из одних литералов: без сжатия, но формат корректный и игра его читает."""
    n = len(data)
    head = bytearray([min(n, 15) << 4])
    if n >= 15:
        rest = n - 15
        while rest >= 255:
            head.append(255)
            rest -= 255
        head.append(rest)
    return bytes(head) + data


def game_dir() -> Optional[str]:
    cfg = find_dota_cfg_dir()  # …/game/dota/cfg
    return os.path.dirname(os.path.dirname(cfg)) if cfg else None


# ── номера наших паков ─────────────────────────────────────────────────────────
# Номер может быть занят чужим модом (мод-менеджеры тоже кладут pakNN в dota_russian) — тогда берём следующий
# свободный. Какой пак наш, запоминаем в paks.json: номер, размер и CRC файла — чужой пак под тем же
# номером не совпадёт. В самих паках ничего лишнего нет: правила сайтов модов (d2pfxwiki) запрещают
# не скомпилированные файлы, поэтому прежняя метка ccc/<вид>.txt при запуске убирается (relocate).
# Реестр — всегда в %APPDATA%\CCC, а не рядом с exe: перенесли программу в другую папку — паки всё равно наши
REGISTRY = os.path.join(os.environ.get("APPDATA") or DATA_DIR, APP_NAME, "paks.json")
FIRST_PAK = 68
RESERVED = {67}                                  # pak67 занимает Minify
DEFAULT_PAKS = {"marker": 68, "shop": 69, "rank": 70}
# старые версии писали паки без метки под номерами 67/68/69: узнаём их по номеру и содержимому
# (признак узкий: на pak67 лежит Minify, его пак из частиц нельзя принять за свой и «перенести»)
_LEGACY = {"marker": (67, ("particles/ui_mouseactions/", "materials/particle/arrow_down.vtex_c")),
           "shop": (68, ("panorama/scripts/hud/dota_hud_shop",)),
           "rank": (69, ("panorama/images/rank_tier_icons/",))}


SHOP_FILE = "panorama/scripts/hud/dota_hud_shop.vjs_c"
SHOP_SIGN = b'"CCCBuild"'                        # id нашей панели в JS магазина (shop_mod)


def _tag(kind: str) -> str:
    """Метка старых версий (2.0.0–2.0.2) — только чтобы узнать их паки и пересобрать без неё."""
    return f"ccc/{kind}.txt"


def _load_registry() -> Dict[str, dict]:
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_registry(reg: Dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, REGISTRY)


def _fingerprint(path: str) -> Tuple[int, int]:
    with open(path, "rb") as f:
        data = f.read()
    return len(data), binascii.crc32(data) & 0xFFFFFFFF


def _register(kind: str, path: Optional[str]) -> None:
    reg = _load_registry()
    if path:
        size, crc = _fingerprint(path)
        reg[kind] = {"file": os.path.basename(path), "size": size, "crc": crc}
    else:
        reg.pop(kind, None)
    _save_registry(reg)


def _registered(folder: str) -> Dict[str, str]:
    """{вид: путь} паков из paks.json, которые лежат на месте и не изменились (размер и CRC те же)."""
    found = {}
    for kind, e in _load_registry().items():
        try:
            path = os.path.join(folder, e["file"])
            if kind in DEFAULT_PAKS and os.path.getsize(path) == e["size"] and _fingerprint(path)[1] == e["crc"]:
                found[kind] = path
        except (OSError, KeyError, TypeError):
            continue
    return found


def _names(path: str) -> set:
    """Имена файлов в VPK: читаем только оглавление, без данных."""
    with open(path, "rb") as f:
        head = f.read(12)
        sig, version, tree_size = struct.unpack("<III", head)
        if sig != VPK_SIGNATURE or version not in (1, 2):
            raise ValueError("не VPK")
        data = head + f.read((0 if version == 1 else 16) + tree_size)
    names, i = set(), 12 if version == 1 else 28
    while True:
        ext, i = _cstr(data, i)
        if not ext:
            return names
        while True:
            folder, i = _cstr(data, i)
            if not folder:
                break
            while True:
                name, i = _cstr(data, i)
                if not name:
                    break
                preload = struct.unpack_from("<H", data, i + 4)[0]
                i += 18 + preload
                names.add(f"{name}.{ext}" if folder == " " else f"{folder}/{name}.{ext}")


def _lang_folder() -> Optional[str]:
    game = game_dir()
    return os.path.join(game, LANG_DIR) if game else None


def _scan(with_tags: bool = False) -> Dict[str, str]:
    """{вид: путь} наших паков в dota_russian: по paks.json, а паки старых версий — по метке или номеру."""
    folder = _lang_folder()
    found: Dict[str, str] = _registered(folder) if folder else {}
    if len(found) == len(DEFAULT_PAKS) and not with_tags:
        return found
    legacy: Dict[str, str] = {}
    try:
        files = sorted(os.listdir(folder)) if folder else []
    except OSError:
        files = []
    for fname in files:
        m = re.fullmatch(r"pak(\d+)_dir\.vpk", fname)
        path = os.path.join(folder, fname)
        if not m or os.path.exists(path.replace("_dir.vpk", "_000.vpk")):
            continue                                               # многотомные паки — файлы самой игры
        try:
            names = _names(path)
        except (OSError, ValueError, struct.error):
            continue
        if path in found.values():
            continue
        if names == {SHOP_FILE} and "shop" not in found:   # paks.json потерян — магазин узнаём по нашему JS
            try:
                if SHOP_SIGN in _read_file(path, SHOP_FILE):
                    found["shop"] = path
                    continue
            except (OSError, ValueError, KeyError, struct.error):
                pass
        for kind in DEFAULT_PAKS:
            if _tag(kind) in names:
                found.setdefault(kind, path)
            elif _LEGACY[kind][0] == int(m.group(1)) and names and                     all(n.startswith(_LEGACY[kind][1]) for n in names):
                legacy[kind] = path
    for kind, path in legacy.items():
        found.setdefault(kind, path)
    return found


def our_pak(kind: str) -> Optional[str]:
    """Наш уже лежащий пак этого вида или None."""
    return _scan().get(kind)


def _num(path: str) -> int:
    return int(re.search(r"pak(\d+)_dir", os.path.basename(path)).group(1))


def _free_slot(kind: str) -> Optional[str]:
    folder = _lang_folder()
    if not folder:
        return None
    for n in list(range(DEFAULT_PAKS[kind], 100)) + list(range(FIRST_PAK, DEFAULT_PAKS[kind])):
        name = f"pak{n}_dir.vpk"
        if n in RESERVED or os.path.exists(os.path.join(folder, name)) or                 os.path.exists(os.path.join(folder, f"pak{n}_000.vpk")):
            continue
        return os.path.join(folder, name)
    return None


def pak_for_write(kind: str) -> Optional[str]:
    """Куда писать пак: наш прежний файл (если номер не зарезервирован) или первый свободный номер."""
    current = our_pak(kind)
    if current and _num(current) not in RESERVED:
        return current
    return _free_slot(kind)


def relocate() -> None:
    """Паки старых версий приводим в порядок: с зарезервированных номеров (маркер в pak67) переносим на свободные,
    метку ccc/<вид>.txt выкидываем, всё записываем в paks.json."""
    for kind, path in _scan(with_tags=True).items():
        tree = _read_tree(path)
        if _num(path) not in RESERVED and _tag(kind) not in tree[0]:
            if kind not in _load_registry():
                _register(kind, path)                # старый пак без метки (по номеру) — просто запоминаем
            continue
        new = _free_slot(kind) if _num(path) in RESERVED else path
        if not new:
            continue
        files = {inner: _read_from(path, tree, inner) for inner in tree[0] if inner != _tag(kind)}
        _write_vpk(new, files)
        if new != path:
            os.remove(path)
        _register(kind, new)


def write_pak(kind: str, files: Dict[str, bytes]) -> str:
    """Записывает наш пак, запоминает его в paks.json и возвращает путь; OSError/PermissionError — наружу."""
    path = pak_for_write(kind)
    if not path:
        raise OSError("нет свободного номера пака в dota_russian")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_vpk(path, files)
    _register(kind, path)
    return path


def remove_pak(kind: str) -> None:
    """Удаляет только наш пак; чужой файл с тем же номером не трогаем."""
    path = our_pak(kind)
    if path:
        os.remove(path)
    _register(kind, None)


def is_installed() -> bool:
    return bool(our_pak("marker"))


# ── VPK ─────────────────────────────────────────────────────────────────────
def _cstr(data: bytes, i: int) -> Tuple[str, int]:
    j = data.index(b"\0", i)
    return data[i:j].decode("utf-8", "replace"), j + 1


def _read_tree(path: str) -> Tuple[Dict[str, Tuple[int, int, int, bytes]], int]:
    """Оглавление VPK: {путь: (архив, смещение, длина, preload)} и начало данных внутри _dir-файла."""
    with open(path, "rb") as f:
        data = f.read()
    sig, version, tree_size = struct.unpack_from("<III", data, 0)
    if sig != VPK_SIGNATURE or version not in (1, 2):
        raise ValueError(f"{os.path.basename(path)}: не VPK")
    header = 12 if version == 1 else 28
    files, i = {}, header
    while True:
        ext, i = _cstr(data, i)
        if not ext:
            break
        while True:
            folder, i = _cstr(data, i)
            if not folder:
                break
            while True:
                name, i = _cstr(data, i)
                if not name:
                    break
                _crc, preload, archive, offset, length, _end = struct.unpack_from("<IHHIIH", data, i)
                i += 18
                full = f"{name}.{ext}" if folder == " " else f"{folder}/{name}.{ext}"
                files[full] = (archive, offset, length, data[i:i + preload])
                i += preload
    return files, header + tree_size


def _read_files(dir_path: str, inner_paths) -> Dict[str, bytes]:
    """Несколько файлов из VPK за один разбор оглавления (оно большое — разбор занимает около секунды)."""
    tree = _read_tree(dir_path)
    return {inner: _read_from(dir_path, tree, inner) for inner in inner_paths}


def _read_file(dir_path: str, inner: str) -> bytes:
    return _read_from(dir_path, _read_tree(dir_path), inner)


def _read_from(dir_path: str, tree, inner: str) -> bytes:
    files, data_start = tree
    archive, offset, length, preload = files[inner]
    if not length:
        return preload
    if archive == IN_DIR_FILE:
        src, offset = dir_path, data_start + offset
    else:
        src = dir_path.replace("_dir.vpk", f"_{archive:03d}.vpk")
    with open(src, "rb") as f:
        f.seek(offset)
        return preload + f.read(length)


def _write_vpk(path: str, contents: Dict[str, bytes]) -> None:
    """Простой VPK v1: всё в одном _dir-файле, без preload."""
    tree, blob = bytearray(), bytearray()
    by_ext: Dict[str, Dict[str, list]] = {}
    for full, body in contents.items():
        folder, name = full.rsplit("/", 1)
        stem, ext = name.rsplit(".", 1)
        by_ext.setdefault(ext, {}).setdefault(folder, []).append((stem, body))
    for ext, folders in by_ext.items():
        tree += ext.encode() + b"\0"
        for folder, names in folders.items():
            tree += folder.encode() + b"\0"
            for stem, body in names:
                tree += stem.encode() + b"\0"
                tree += struct.pack("<IHHIIH", binascii.crc32(body) & 0xFFFFFFFF, 0, IN_DIR_FILE,
                                    len(blob), len(body), 0xFFFF)
                blob += body
            tree += b"\0"
        tree += b"\0"
    tree += b"\0"
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(struct.pack("<III", VPK_SIGNATURE, 1, len(tree)) + tree + blob)
    try:
        os.replace(tmp, path)
    except OSError:                                  # Dota держит пак открытым — не оставляем мусор в папке игры
        os.remove(tmp)
        raise


def launch_option_set() -> Optional[bool]:
    """Стоит ли -language russian в параметрах запуска Dota (по localconfig.vdf Steam); None — не узнать."""
    found = None
    for root in dict.fromkeys(_steam_roots()):
        users = os.path.join(root, "userdata")
        if not os.path.isdir(users):
            continue
        for user in os.listdir(users):
            try:
                with open(os.path.join(users, user, "config", "localconfig.vdf"), encoding="utf-8",
                          errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            for m in re.finditer(r'"570"\s*\{', text):
                depth, i = 1, m.end()
                while depth and i < len(text):
                    depth += {"{": 1, "}": -1}.get(text[i], 0)
                    i += 1
                opts = re.search(r'"LaunchOptions"\s*"([^"]*)"', text[m.end():i])
                if opts is not None:
                    if re.search(r"-language\s+russian\b", opts.group(1)):
                        return True
                    found = False
    return found
