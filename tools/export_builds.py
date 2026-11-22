"""Выгрузка сборок всех героев из Stratz в data/builds/*.json (запускается GitHub Actions раз в сутки).

    STRATZ_TOKEN=... python tools/export_builds.py [--only 1,73] [--out data]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.builds_data import (BuildsError, BuildsService, NoGamesError, STRATZ_MIN_GAMES,  # noqa: E402
                             apply_patch_items, new_item_games, stratz_token)

PAUSE = 3.0           # между героями: у Stratz лимит 150 запросов в минуту
RETRIES = 3
MAX_FAILED = 0.2      # доля героев без данных, после которой прогон считается проваленным
KEEP_DAYS = 10        # дольше старую позицию не держим, если новая выборка не совсем крошечная
EXPIRED_MIN = 50      # «не совсем крошечная»: столько игр или половина старой выборки


def keep_old_skills(data: dict, path: str) -> None:
    """OpenDota не ответила по позиции — оставляем прокачку из прошлой выгрузки (только из OpenDota)."""
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f).get("positions") or {}
    except (OSError, ValueError):
        return
    for pos, cur in data["positions"].items():
        prev = (old.get(pos) or {}).get("skills") or {}
        if not cur["skills"].get("order") and prev.get("source") == "opendota":
            cur["skills"] = prev


def load_old(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def takes_new(old: dict, new: dict, age: float) -> bool:
    """Записывать ли новую выборку позиции вместо прежней."""
    was, now = old.get("games", 0), new.get("games", 0)
    if not was:
        return True
    if not now:
        return False                  # ноль игр ничего не затирает
    if now >= STRATZ_MIN_GAMES or (was < STRATZ_MIN_GAMES and now >= was):
        return True
    return age > KEEP_DAYS * 86400 and now >= min(EXPIRED_MIN, was / 2)


def merge(old, new: dict, now: int):
    """(данные героя, ждёт ли основная позиция). Решаем по каждой позиции: возраст у каждой свой (updated)."""
    before = (old or {}).get("positions") or {}
    main = max(before, key=lambda p: before[p].get("games", 0), default=None)
    out, any_new, waits = {}, False, False
    for p, cur in new["positions"].items():
        prev = before.get(p)
        if prev is None:
            out[p], any_new = dict(cur, updated=now), True
            continue
        updated = prev.get("updated", old.get("generated", 0))
        if takes_new(prev, cur, now - updated):
            phases = {ph: row or (prev.get("phases") or {}).get(ph, []) for ph, row in cur["phases"].items()}
            out[p] = dict(cur, phases=phases, updated=now)
            any_new = any_new or bool(cur.get("games"))
        else:
            out[p] = dict(prev, updated=updated)
            if cur["skills"].get("order"):
                out[p]["skills"] = cur["skills"]          # прокачка из OpenDota от выборки Stratz не зависит
            waits = waits or p == main
    data = dict(new, positions=out, generated=now if any_new or not old else old.get("generated", 0))
    if old and "fresh" in old:
        data["fresh"] = old["fresh"]
    return data, waits


def save(path: str, data: dict) -> None:
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(path + ".tmp", path)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # консоль CI может быть не UTF-8
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="id героев через запятую (для проверки)")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    token = stratz_token()
    if not token:
        print("Нет STRATZ_TOKEN")
        return 2
    service = BuildsService()
    for attempt in range(RETRIES):
        try:
            consts = service.constants(force=True)
            break
        except BuildsError as e:
            if attempt == RETRIES - 1:
                print(f"Справочники OpenDota не загрузились: {e}")
                return 1
            time.sleep(30)
    ids = [int(x) for x in args.only.split(",") if x.strip()] or sorted(consts.heroes)
    folder = os.path.join(args.out, "builds")
    os.makedirs(folder, exist_ok=True)

    try:
        fresh = new_item_games()
    except (BuildsError, KeyError, TypeError, ValueError) as e:
        print(f"Новые предметы не обновились: {e}", flush=True)
        fresh = None
    failed, waiting = [], []
    for n, hero_id in enumerate(ids, 1):
        data, no_games = None, False
        path = os.path.join(folder, f"{hero_id}.json")
        old = load_old(path)
        for attempt in range(RETRIES):
            try:
                data = service._fetch_stratz(hero_id, consts, token)
                break
            except NoGamesError:
                no_games = True
                break
            except BuildsError as e:
                print(f"[{n}/{len(ids)}] герой {hero_id}: {e} (попытка {attempt + 1})", flush=True)
                time.sleep(65 if "много запросов" in str(e) else 10)
        if data is None and not (no_games and old):
            failed.append(hero_id)  # остаются прежние данные и дата, только без удалённых предметов
            if old:
                apply_patch_items(old, fresh)
                save(path, old)
            continue
        # В начале недели и после патча у Stratz бывает по несколько игр: ими нормальную сборку не затираем
        if no_games:
            data, waits = old, True
        else:
            keep_old_skills(data, path)
            data, waits = merge(old, data, int(time.time()))
        if waits:
            waiting.append(hero_id)
        apply_patch_items(data, fresh)
        save(path, data)
        print(f"[{n}/{len(ids)}] {consts.hero_name(hero_id)} ok", flush=True)
        time.sleep(PAUSE)

    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": int(time.time()), "heroes": len(ids) - len(failed), "failed": failed, "waiting": waiting}, f)
    print(f"Готово: {len(ids) - len(failed)} из {len(ids)}, без данных: {failed}, ждут статистику: {len(waiting)}")
    if len(waiting) > MAX_FAILED * len(ids):
        # после патча Stratz днями отдаёт null у всех и без ошибок: это не провал, старые сборки целы
        print(f"::warning::У Stratz мало игр: {len(waiting)} героев оставлены на прежних сборках")
    return 1 if len(failed) > MAX_FAILED * len(ids) else 0


if __name__ == "__main__":
    sys.exit(main())
