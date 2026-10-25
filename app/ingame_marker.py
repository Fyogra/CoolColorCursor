"""Свой маркер движения внутри Dota 2: шесть частиц clicked_occlusion_* с нужными цветами и шевроны вместо стрелок.

Пак (номер с 68, см. dota_mod) кладётся в game/dota_russian (работает при параметре запуска -language russian). Цвета лежат в
самих частицах как целые числа (r, g, b): заготовки в app/assets/marker — родные частицы Valve, пересобранные
общим сборщиком модов (d2pfx_compiler) с полями цвета, как в любом паке маркера; мы подставляем в них свои цвета.
Файлы самой игры не меняются, выключение удаляет наш пак.
"""
from __future__ import annotations

import os
import struct
from typing import Dict, Optional, Tuple
from app import dota_mod as dm
from app.config import log

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "marker")
TEMPLATE_RGB = (99, 60, 175)           # цвет, которым покрашены заготовки
PARTICLE_DIR = "particles/ui_mouseactions/"
ARROW_TEXTURE = "materials/particle/arrow_down.vtex_c"

# какая частица к какой группе цвета относится: лепестки (стрелки и хвост) и всё остальное (центр, свечение, кольца)
PETAL_PARTICLES = ("clicked_occlusion_basemove_arrows", "clicked_occlusion_arrow_trail")
REST_PARTICLES = ("clicked_occlusion_basemove", "clicked_occlusion_basemove_shadow",
                  "clicked_occlusion_rings", "clicked_occlusion_rings_c")

RGB = Tuple[int, int, int]


def recolor_template(data: bytes, rgb: RGB) -> Optional[bytes]:
    """Заготовка .vpcf_c (KV3 v5, два LZ4-блока) с цветом rgb вместо TEMPLATE_RGB; None — формат не тот."""
    try:
        _fs, _hv, _ver, block_off, count = struct.unpack_from("<IHHII", data, 0)
        pos, last = 8 + block_off, None
        for _ in range(count):
            rel, size = struct.unpack_from("<II", data, pos + 4)
            last = (data[pos:pos + 4], pos + 4 + rel, size, pos)
            pos += 12
        name, start, size, head_pos = last
        blk = data[start:start + size]
        if name != b"DATA" or start + size != len(data) or blk[:4] != bytes.fromhex("0533564b"):
            return None
        if struct.unpack_from("<I", blk, 20)[0] != 1:                       # LZ4
            return None
        u1, c1, u2, c2 = struct.unpack_from("<IIII", blk, 72)
        body = 120
        if body + c1 + c2 != len(blk):
            return None
        raw1 = bytearray(dm._lz4_decompress(blk[body:body + c1], u1))
        old, new = struct.pack("<iii", *TEMPLATE_RGB), struct.pack("<iii", *rgb)
        hits, i = 0, raw1.find(old)
        while i >= 0:
            raw1[i:i + 12] = new
            hits += 1
            i = raw1.find(old, i + 12)
        if not hits:
            return None
        packed = dm._lz4_literals(bytes(raw1))
        new_blk = bytearray(blk[:body]) + packed + blk[body + c1:]
        struct.pack_into("<I", new_blk, 52, len(packed) + c2)               # общий сжатый размер
        struct.pack_into("<I", new_blk, 76, len(packed))                    # размер первого блока
        out = bytearray(data[:start]) + new_blk
        struct.pack_into("<I", out, 0, len(out))
        struct.pack_into("<I", out, head_pos + 8, len(new_blk))
        return bytes(out)
    except (struct.error, ValueError, IndexError):
        return None


def chevron_texture(original: bytes) -> Optional[bytes]:
    """Текстура стрелки arrow_down (DXT5 128x128, 6 mip) с белым шевроном и мягким свечением. None — формат не тот."""
    from PyQt5.QtCore import QPointF, Qt
    from PyQt5.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF
    try:
        _fs, _hv, _ver, off, cnt = struct.unpack_from("<IHHII", original, 0)
        pos, data_off, data_size = 8 + off, None, None
        for _ in range(cnt):
            rel, size = struct.unpack_from("<II", original, pos + 4)
            if original[pos:pos + 4] == b"DATA":
                data_off, data_size = pos + 4 + rel, size
            pos += 12
        w, h, _depth, fmt, mips = struct.unpack_from("<HHHBB", original, data_off + 20)
        if (w, h, fmt, mips) != (128, 128, 2, 6) or data_off + data_size + 21840 != len(original):
            return None
    except (struct.error, TypeError):
        return None

    img = QImage(128, 128, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    poly = QPolygonF([QPointF(64, 112), QPointF(115, 46), QPointF(64, 78), QPointF(13, 46)])
    path = QPainterPath()
    path.addPolygon(poly)
    path.closeSubpath()
    for i in range(5, 0, -1):                       # мягкое свечение
        pen = QPen(QColor(255, 255, 255, 38), 12.0 * i / 5)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(255, 255, 255, 255)))
    p.drawPolygon(poly)
    p.end()

    def dxt5(level: QImage) -> bytes:
        out = bytearray()
        for by in range(0, level.height(), 4):
            for bx in range(0, level.width(), 4):
                al = [(level.pixel(bx + x, by + y) >> 24) & 255 for y in range(4) for x in range(4)]
                a0, a1 = max(al), min(al)
                if a0 == a1:
                    pal, idx = [a0] * 8, [0] * 16
                else:
                    pal = [a0, a1] + [((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)]
                    idx = [min(range(8), key=lambda j: abs(pal[j] - a)) for a in al]
                bits = 0
                for k, v in enumerate(idx):
                    bits |= v << (3 * k)
                out += bytes([a0, a1]) + bits.to_bytes(6, "little") + struct.pack("<HHI", 0xFFFF, 0xFFFF, 0)
        return bytes(out)

    levels = [img.scaled(128 >> i, 128 >> i, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) for i in range(6)]
    image = b"".join(dxt5(levels[i]) for i in range(5, -1, -1))      # мелкие mip идут первыми
    return original[:data_off + data_size] + image


def build_files(petals: RGB, rest: RGB, chevron: bool = True) -> Tuple[Dict[str, bytes], int]:
    """Файлы пака: частицы с цветами и (по желанию) шеврон. Возвращает (файлы, сколько частиц получилось)."""
    files: Dict[str, bytes] = {}
    for names, rgb in ((PETAL_PARTICLES, petals), (REST_PARTICLES, rest)):
        for name in names:
            with open(os.path.join(TEMPLATE_DIR, name + ".vpcf_c"), "rb") as f:
                new = recolor_template(f.read(), rgb)
            if new is not None:
                files[PARTICLE_DIR + name + ".vpcf_c"] = new
    count = len(files)
    if chevron:
        game = dm.game_dir()
        tex = chevron_texture(dm._read_file(os.path.join(game, "dota", "pak01_dir.vpk"), ARROW_TEXTURE)) if game else None
        if tex is not None:
            files[ARROW_TEXTURE] = tex
    return files, count


def install(petals: RGB, rest: RGB, chevron: bool = True) -> Tuple[bool, str]:
    game = dm.game_dir()
    if not game:
        return False, "Не нашёл папку Dota 2."
    try:
        files, count = build_files(petals, rest, chevron)
        if count < len(PETAL_PARTICLES) + len(REST_PARTICLES):
            return False, "Не удалось подготовить файлы маркера: формат изменился."
        dm.write_pak("marker", files)
    except PermissionError:
        return False, "Файл занят: закрой Dota 2 и попробуй снова."
    except (OSError, KeyError, ValueError) as e:
        return False, f"Не получилось записать пак: {e}"
    log.info("Свой маркер в Dota установлен: лепестки %s, остальное %s, шеврон %s", petals, rest, chevron)
    return True, ""


def uninstall() -> Tuple[bool, str]:
    try:
        dm.remove_pak("marker")
    except PermissionError:
            return False, "Файл занят: закрой Dota 2 и попробуй снова."
    except OSError as e:
        return False, f"Не получилось удалить пак: {e}"
    return True, ""
