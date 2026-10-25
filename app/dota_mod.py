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
from app.config import DATA_DIR
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
# Номер по умолчанию может быть занят чужим модом (мод-менеджеры тоже кладут pakNN в dota_russian) — тогда берём
# следующий свободный. Выбранные имена запоминаются в paks.json, а внутри пака лежит метка ccc/<вид>.txt.
DEFAULT_PAKS = {"marker": 67, "shop": 68, "rank": 69}
PAKS_FILE = os.path.join(DATA_DIR, "paks.json")
# старые версии писали паки без метки: узнаём их по содержимому (только на номере по умолчанию)
_LEGACY = {"marker": ("particles/ui_mouseactions/", "materials/"), "shop": ("panorama/scripts/hud/dota_hud_shop",),
           "rank": ("panorama/images/rank_tier_icons/",)}


def _tag(kind: str) -> str:
    return f"ccc/{kind}.txt"


def _registry() -> Dict[str, str]:
    try:
        with open(PAKS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _remember(kind: str, name: str) -> None:
    reg = _registry()
    reg[kind] = name
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PAKS_FILE, "w", encoding="utf-8") as f:
            json.dump(reg, f)
    except OSError:
        pass


def _is_ours(path: str, kind: str, legacy: bool = False) -> bool:
    try:
        names = _read_tree(path)[0]
    except (OSError, ValueError, struct.error):
        return False
    if _tag(kind) in names:
        return True
    return legacy and bool(names) and all(n.startswith(_LEGACY[kind]) for n in names)


def our_pak(kind: str) -> Optional[str]:
    """Наш уже лежащий пак этого вида или None."""
    game = game_dir()
    name = _registry().get(kind) or f"pak{DEFAULT_PAKS[kind]}_dir.vpk"
    path = os.path.join(game, LANG_DIR, name) if game else ""
    legacy = kind not in _registry()
    return path if path and os.path.isfile(path) and _is_ours(path, kind, legacy) else None


def pak_for_write(kind: str) -> Optional[str]:
    """Куда писать пак: наш прежний файл или первый номер, не занятый чужими модами."""
    game = game_dir()
    if not game:
        return None
    current = our_pak(kind)
    if current:
        _remember(kind, os.path.basename(current))
        return current
    folder = os.path.join(game, LANG_DIR)
    taken = {v for k, v in _registry().items() if k != kind}
    for n in range(DEFAULT_PAKS[kind], 100):
        name = f"pak{n}_dir.vpk"
        if name in taken or os.path.exists(os.path.join(folder, name)) or \
                os.path.exists(os.path.join(folder, f"pak{n}_000.vpk")):
            continue
        _remember(kind, name)
        return os.path.join(folder, name)
    return None


def write_pak(kind: str, files: Dict[str, bytes]) -> str:
    """Записывает наш пак (с меткой) и возвращает путь; OSError/PermissionError — наружу."""
    path = pak_for_write(kind)
    if not path:
        raise OSError("нет свободного номера пака в dota_russian")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_vpk(path, {**files, _tag(kind): b"CoolColorCursor\n"})
    return path


def remove_pak(kind: str) -> None:
    """Удаляет только наш пак; чужой файл с тем же номером не трогаем."""
    path = our_pak(kind)
    if path:
        os.remove(path)


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
    os.replace(tmp, path)


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
