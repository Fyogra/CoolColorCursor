"""Скиллы и таланты героев из файлов Dota (scripts/npc/heroes) для app/skill_order.py.
Копия в папке данных пересоздаётся после обновления игры; в app/assets лежит запасная из поставки."""
from __future__ import annotations

import json
import os
import re
from app import dota_mod as d
from app.config import DATA_DIR

ASSET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "abilities.json")
DATA_PATH = os.path.join(DATA_DIR, "abilities.json")

TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*')


def parse(text):
    """Минимальный разбор KeyValues (формат Valve) в словари."""
    stack, cur, key = [], {}, None
    root = cur
    for m in TOKEN.finditer(text):
        s, brace = m.group(1), m.group(2)
        if s is not None:
            if key is None:
                key = s
            else:
                cur[key] = s
                key = None
        elif brace == "{":
            new = {}
            cur[key] = new
            stack.append(cur)
            cur, key = new, None
        elif brace == "}":
            cur = stack.pop()
    return root


def slots_of(text):
    """Слоты способностей героя из первого блока Ability1..N: {номер: название}."""
    seen, out = set(), {}
    for n, a in re.findall(r'"Ability(\d+)"\s+"([^"]+)"', text):
        n = int(n)
        if n in seen:
            break
        seen.add(n)
        out[n] = a
    return out


def collect() -> dict:
    """По id героя: a — три скилла и ульта, t — четыре пары талантов, s — все свои способности по слотам."""
    base = os.path.join(d.game_dir(), "dota", "pak01_dir.vpk")
    tree = d._read_tree(base)[0]
    paths = [k for k in tree if k.startswith("scripts/npc/heroes/") and k.endswith(".txt")]
    files = d._read_files(base, paths)          # оглавление разбирается один раз
    out = {}
    for path in paths:
        text = files[path].decode("utf-8", "replace")
        hid = re.search(r'"HeroID"\s+"(\d+)"', text)
        if not hid:
            continue
        slots = slots_of(text)
        draft = None
        root = parse(text)
        for hero in root.values():
            for name, body in (hero.items() if isinstance(hero, dict) else []):
                if isinstance(body, dict) and name.startswith("npc_dota_hero_"):
                    dd = body.get("AbilityDraftAbilities")
                    if isinstance(dd, dict):
                        names = [dd.get(f"Ability{i}") for i in range(1, 5)]
                        draft = names if all(names) and not any(n.endswith('_ad') for n in names) else None
        main4 = draft or [slots.get(1), slots.get(2), slots.get(3), slots.get(6)]
        if not all(main4):
            continue
        talents = [v for n, v in sorted(slots.items()) if v.startswith("special_bonus")]
        tiers = [[talents[i], talents[i + 1]] for i in range(0, 8, 2)] if len(talents) == 8 else []
        own = [slots[n] for n in range(1, 7) if slots.get(n) and "hidden" not in slots[n] and not slots[n].startswith("special_bonus") and not re.search(r"switch|_break$|_stop$|_in$|_out$", slots[n])]
        out[hid.group(1)] = {"a": main4, "t": tiers, "s": own}
    return out


def generate(path: str = DATA_PATH) -> int:
    """Пересобирает файл; возвращает число героев."""
    out = collect()
    if not out:
        raise ValueError("в файлах игры не нашлось героев")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, path)
    return len(out)
