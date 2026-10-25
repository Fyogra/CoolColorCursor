"""Общее для модов Dota 2: папка игры, чтение и запись VPK, LZ4.

Наши паки кладутся в game/dota_russian: Dota монтирует эту папку раньше основной, если в параметрах запуска Steam
стоит -language russian. Файлы самой игры не меняются — выключение мода просто удаляет его пак.
"""
from __future__ import annotations

import binascii
import os
import re
import struct
from typing import Dict, Optional, Tuple
from app.gsi import _steam_roots, find_dota_cfg_dir

PAK_NAME = "pak67_dir.vpk"          # пак маркера клика (app/ingame_marker.py)
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


def pak_path() -> Optional[str]:
    game = game_dir()
    return os.path.join(game, LANG_DIR, PAK_NAME) if game else None


def is_installed() -> bool:
    path = pak_path()
    return bool(path) and os.path.isfile(path)


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
