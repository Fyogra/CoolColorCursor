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
from app.builds_data import BuildsError, BuildsService, stratz_token  # noqa: E402

PAUSE = 3.0           # между героями: у Stratz лимит 150 запросов в минуту
RETRIES = 3
MAX_FAILED = 0.2      # доля героев без данных, после которой прогон считается проваленным


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="id героев через запятую (для проверки)")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    token = stratz_token()
    if not token:
        print("Нет STRATZ_TOKEN")
        return 2
    service = BuildsService()
    consts = service.constants(force=True)
    ids = [int(x) for x in args.only.split(",") if x.strip()] or sorted(consts.heroes)
    folder = os.path.join(args.out, "builds")
    os.makedirs(folder, exist_ok=True)

    failed = []
    for n, hero_id in enumerate(ids, 1):
        data = None
        for attempt in range(RETRIES):
            try:
                data = service._fetch_stratz(hero_id, consts, token)
                break
            except BuildsError as e:
                print(f"[{n}/{len(ids)}] герой {hero_id}: {e} (попытка {attempt + 1})", flush=True)
                time.sleep(65 if "много запросов" in str(e) else 10)
        if data is None:
            failed.append(hero_id)  # старый файл остаётся как был
            continue
        data["generated"] = int(time.time())
        path = os.path.join(folder, f"{hero_id}.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(path + ".tmp", path)
        print(f"[{n}/{len(ids)}] {consts.hero_name(hero_id)} ok", flush=True)
        time.sleep(PAUSE)

    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": int(time.time()), "heroes": len(ids) - len(failed), "failed": failed}, f)
    print(f"Готово: {len(ids) - len(failed)} из {len(ids)}, без данных: {failed}")
    return 1 if len(failed) > MAX_FAILED * len(ids) else 0


if __name__ == "__main__":
    sys.exit(main())
