"""Сборки героев: OpenDota и Stratz (без Qt)."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
from app.config import DATA_DIR, _app_dir, log


# ──────────────────────────────────────────────────────────────────────────────
#  Сборки героев: данные OpenDota (без Qt, работает в фоновом потоке)
# ──────────────────────────────────────────────────────────────────────────────
OPENDOTA_API = "https://api.opendota.com/api"
STEAM_CDN = "https://cdn.cloudflare.steamstatic.com"
HTTP_UA = "CCC-Overlay/2.0"
CACHE_DIR = os.path.join(DATA_DIR, "cache")
BUILDS_TTL = 24 * 3600        # сборки и справочники обновляются раз в сутки
BUILDS_DAYS = 90              # берём про-матчи за последние N дней
BUILDS_MATCH_LIMIT = 300      # и не больше N матчей на героя
TALENTS_TTL = 7 * 86400       # пары талантов героя меняются только с патчем

# Stratz: сборки по рангу. Токен лежит в api.txt рядом с программой (или в переменной STRATZ_TOKEN)
STRATZ_API = "https://api.stratz.com/graphql"
STRATZ_BRACKET = "DIVINE_IMMORTAL"                      # хай-рейтинговые лобби
# Если на позиции мало данных, недостающие фазы добираем из более широкой выборки рангов
STRATZ_BRACKETS_WIDER = ("LEGEND_ANCIENT, DIVINE_IMMORTAL", "CRUSADER_ARCHON, LEGEND_ANCIENT, DIVINE_IMMORTAL")
STRATZ_MIN_GAMES = 200
# Готовые сборки: GitHub Actions выгружает их из Stratz раз в сутки (tools/export_builds.py)
PUBLISHED_URL = "https://raw.githubusercontent.com/Fyogra/ccc-overlay/main/data/builds/{}.json"

POSITIONS = ("Carry", "Mid", "Offlane", "4", "5")  # вкладки 0..4 ↔ позиции 1..5
POSITION_NAMES = ("Керри", "Мидер", "Хардлейнер", "Четвёрка", "Пятёрка")
PHASES = (("start", "Стартовый закуп"), ("early", "Ранняя игра"),
          ("mid", "Середина игры"), ("late", "Поздняя игра"))
START_UNTIL = 10              # покупки до 0:10 — стартовый закуп
EARLY_UNTIL = 10 * 60         # до 10:00 — ранняя игра
MID_UNTIL = 25 * 60           # до 25:00 — середина, дальше — поздняя
ITEMS_PER_PHASE = 10
SKILL_PREFIX = 10             # самая частая последовательность первых N прокачек
SKILL_LEN = 18

# Расходники и прочее, что не показываем после старта
NOT_AN_ITEM_LATER = frozenset({
    "tango", "flask", "clarity", "enchanted_mango", "faerie_fire", "blood_grenade", "tpscroll",
    "ward_observer", "ward_sentry", "ward_dispenser", "smoke_of_deceit", "dust", "tome_of_knowledge",
    "cheese", "aegis", "refresher_shard", "famango", "great_famango", "greater_famango", "courier",
})


class BuildsError(Exception):
    """Понятная пользователю ошибка загрузки данных."""


def http_get(url: str, timeout: float = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": HTTP_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read(300).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        log.warning("HTTP %s для %s: %s", e.code, url.split("?")[0], body)
        if e.code == 429:
            raise BuildsError("OpenDota: слишком много запросов, попробуй через минуту") from e
        raise BuildsError(f"сервер ответил {e.code}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("Нет связи с %s: %s", url.split("?")[0], e)
        raise BuildsError("нет связи с сервером") from e


def http_get_json(url: str, timeout: float = 25):
    data = http_get(url, timeout)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise BuildsError("сервер вернул непонятный ответ") from e


def cache_load(name: str) -> Optional[Tuple[Any, float]]:
    """(данные, возраст в секундах) из дискового кэша или None."""
    try:
        with open(os.path.join(CACHE_DIR, name), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or "data" not in raw:
        return None
    fetched = raw.get("fetched")
    if isinstance(fetched, bool) or not isinstance(fetched, (int, float)):
        return None
    return raw["data"], time.time() - fetched


def cache_save(name: str, data) -> None:
    path = os.path.join(CACHE_DIR, name)
    tmp = path + ".tmp"
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fetched": time.time(), "data": data}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("Не удалось записать кэш %s: %s", name, e)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_list(value) -> list:
    """Postgres-массивы из explorer иногда приходят строкой JSON."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def cdn_url(path: str) -> str:
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return STEAM_CDN + (path if path.startswith("/") else "/" + path)


class DotaConstants:
    """Справочники: герои, предметы, способности. Хранятся урезанными, чтобы кэш был небольшим."""

    def __init__(self, data: dict):
        self.heroes: Dict[int, dict] = {}
        for k, v in (data.get("heroes") or {}).items():
            try:
                self.heroes[int(k)] = v
            except (TypeError, ValueError):
                pass
        self.items: Dict[str, dict] = dict(data.get("items") or {})
        self.abilities: Dict[str, dict] = dict(data.get("abilities") or {})
        self.ability_ids: Dict[int, str] = {}
        for k, v in (data.get("ability_ids") or {}).items():
            try:
                self.ability_ids[int(k)] = str(v)
            except (TypeError, ValueError):
                pass
        self.ability_by_name = {v: k for k, v in self.ability_ids.items()}
        self.items_by_id = {v["id"]: k for k, v in self.items.items() if _is_int(v.get("id"))}

    @staticmethod
    def reduce(raw_heroes, raw_items, raw_abilities, raw_ability_ids) -> dict:
        heroes = {}
        for h in (raw_heroes.values() if isinstance(raw_heroes, dict) else raw_heroes or []):
            if isinstance(h, dict) and _is_int(h.get("id")):
                heroes[str(h["id"])] = {"name": str(h.get("name") or ""),
                                        "localized_name": str(h.get("localized_name") or h.get("name") or h["id"]),
                                        "img": str(h.get("img") or "")}
        items = {}
        for key, it in (raw_items or {}).items() if isinstance(raw_items, dict) else ():
            if not isinstance(it, dict):
                continue
            cost = it.get("cost")
            comps = it.get("components")
            items[str(key)] = {"id": it.get("id") if _is_int(it.get("id")) else None,
                               "dname": str(it.get("dname") or key),
                               "img": str(it.get("img") or ""),
                               "cost": cost if _is_int(cost) else 0,
                               "qual": str(it.get("qual") or ""),
                               "components": [str(c) for c in comps] if isinstance(comps, list) else []}
        abilities = {}
        for key, ab in (raw_abilities or {}).items() if isinstance(raw_abilities, dict) else ():
            if isinstance(ab, dict):
                abilities[str(key)] = {"dname": str(ab.get("dname") or ""), "img": str(ab.get("img") or "")}
        ability_ids = {}
        for k, v in (raw_ability_ids or {}).items() if isinstance(raw_ability_ids, dict) else ():
            if isinstance(v, str):
                ability_ids[str(k)] = v
        if not heroes or not items:
            raise BuildsError("OpenDota вернул пустые справочники")
        return {"heroes": heroes, "items": items, "abilities": abilities, "ability_ids": ability_ids}

    # ── картинки и подписи ───────────────────────────────────────────────────
    def hero_name(self, hero_id: int) -> str:
        h = self.heroes.get(hero_id)
        return h["localized_name"] if h else f"Герой #{hero_id}"

    def hero_img(self, hero_id: int) -> str:
        h = self.heroes.get(hero_id) or {}
        if h.get("img"):
            return cdn_url(h["img"])
        short = str(h.get("name", "")).replace("npc_dota_hero_", "")
        return f"{STEAM_CDN}/apps/dota2/images/dota_react/heroes/{short}.png" if short else ""

    def item_name(self, key: str) -> str:
        return (self.items.get(key) or {}).get("dname") or key.replace("_", " ").title()

    def item_img(self, key: str) -> str:
        img = (self.items.get(key) or {}).get("img")
        return cdn_url(img) if img else f"{STEAM_CDN}/apps/dota2/images/dota_react/items/{key}.png"

    def ability_name(self, key: str) -> str:
        return (self.abilities.get(key) or {}).get("dname") or key.replace("_", " ")

    def ability_img(self, key: str) -> str:
        img = (self.abilities.get(key) or {}).get("img")
        return cdn_url(img) if img else f"{STEAM_CDN}/apps/dota2/images/dota_react/abilities/{key}.png"


def is_talent(key: str) -> bool:
    return key.startswith("special_bonus")


def skip_after_start(key: str, consts: DotaConstants) -> bool:
    """После стартового закупа не показываем расходники, рецепты и копеечные компоненты."""
    info = consts.items.get(key)
    if info is None or key.startswith("recipe_") or key in NOT_AN_ITEM_LATER:
        return True
    return info.get("qual") == "consumable" or info.get("cost", 0) < 200


def phase_of(t: float) -> str:
    if t <= START_UNTIL:
        return "start"
    if t <= EARLY_UNTIL:
        return "early"
    if t <= MID_UNTIL:
        return "mid"
    return "late"


def game_items(purchase_log, consts: DotaConstants) -> Optional[Dict[str, set]]:
    """Предметы одной игры по фазам. Компоненты, из которых потом собрали предмет, отбрасываем."""
    first: Dict[str, float] = {}
    for entry in _as_list(purchase_log):
        if isinstance(entry, str):
            try:
                entry = json.loads(entry)
            except ValueError:
                continue
        if not isinstance(entry, dict):
            continue
        key, t = entry.get("key"), entry.get("time")
        if not isinstance(key, str) or isinstance(t, bool) or not isinstance(t, (int, float)):
            continue
        if key.startswith("item_"):
            key = key[5:]
        if key not in first:
            first[key] = t
    if not first:
        return None

    result = {phase: set() for phase, _ in PHASES}
    for key, t in first.items():
        phase = phase_of(t)
        if phase != "start":
            if skip_after_start(key, consts):
                continue
            # Ступенька к более дорогому предмету. «>=»: последний компонент и готовый предмет
            # попадают в лог в одну и ту же секунду
            if any(k2 != key and t2 >= t and key in (consts.items.get(k2) or {}).get("components", ())
                   for k2, t2 in first.items()):
                continue
        result[phase].add(key)
    return result


def assign_positions(players) -> Dict[int, int]:
    """players: [(hero_id, lane_role, gpm)] одной команды → {hero_id: позиция 1..5}.

    Три самых богатых — коры: линия 1 → керри, 2 → мид, 3 → хард; двое оставшихся — саппорты:
    на лёгкой линии — пятёрка, на сложной — четвёрка. Нестыковки добиваем по золоту.
    """
    order = sorted(players, key=lambda p: -(p[2] or 0))[:5]
    result: Dict[int, int] = {}
    for group, free, lane_map in ((order[:3], [1, 2, 3], {1: 1, 2: 2, 3: 3}),
                                  (order[3:], [4, 5], {1: 5, 3: 4})):
        pending = []
        for hero, lane, _ in group:
            pos = lane_map.get(lane)
            if pos in free:
                result[hero] = pos
                free.remove(pos)
            else:
                pending.append(hero)
        for hero in pending:
            if free:
                result[hero] = free.pop(0)
    return result


def ability_sequence(upgrades, consts: DotaConstants) -> List[str]:
    seq = []
    for a in _as_list(upgrades):
        if _is_int(a):
            name = consts.ability_ids.get(a)
            if name:
                seq.append(name)
    return seq


TALENT_LEVELS = (10, 15, 20, 25)  # уровни, на которых прокачиваются таланты


def talent_pairs(seqs: List[List[str]]) -> List[dict]:
    """4 пары талантов (10/15/20/25). В каждой паре помечен более популярный выбор.

    Про-матчи дают нам «что реально выбрали», а Dota 2 на одном уровне даёт выбор из двух талантов,
    так что те два имени, которые встречаются на одном уровне, — и есть та самая пара в игре.
    """
    picks = {lvl: Counter() for lvl in TALENT_LEVELS}
    for seq in seqs:
        # Пропускаем уже прокачанные к талантам прокачки (уровни 1..SKILL_LEN в seq идут подряд)
        for i, name in enumerate(seq):
            level = i + 1
            if level in picks and is_talent(name):
                picks[level][name] += 1
    pairs = []
    for level in TALENT_LEVELS:
        c = picks[level]
        items = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
        # Талантов на уровне ровно два; если видим три+ — берём два самых популярных
        left_key, left_cnt = (items[0] if items else ("", 0))
        right_key, right_cnt = (items[1] if len(items) > 1 else ("", 0))
        total = left_cnt + right_cnt
        pct = round(100 * left_cnt / total) if total else 0
        pairs.append({"level": level, "left": left_key, "right": right_key,
                      "left_pct": pct, "right_pct": max(0, 100 - pct) if total else 0,
                      "picked": "left" if left_cnt >= right_cnt > 0 or (left_cnt and not right_cnt) else
                                ("right" if right_cnt else "")})
    return pairs


def skill_build(seqs: List[List[str]]) -> dict:
    seqs = [s for s in seqs if s]
    if not seqs:
        return {"order": [], "pct": 0, "games": 0}
    full = [s for s in seqs if len(s) >= SKILL_PREFIX]
    base = full or seqs
    if full:
        counts = Counter(tuple(s[:SKILL_PREFIX]) for s in full)
        prefix, cnt = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        order, pct = list(prefix), round(100 * cnt / len(full))
        group = [s for s in full if tuple(s[:SKILL_PREFIX]) == prefix]
    else:
        order, pct, group = [], 0, seqs
    for i in range(len(order), SKILL_LEN):
        column = [s[i] for s in group if len(s) > i] or [s[i] for s in base if len(s) > i]
        if not column:
            break
        order.append(max(Counter(column).items(), key=lambda kv: (kv[1], kv[0]))[0])
    return {"order": order, "pct": pct, "games": len(base)}


def _top(counter: Counter, total: int) -> List[dict]:
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:ITEMS_PER_PHASE]
    return [{"key": k, "pct": min(100, max(1, round(100 * v / total)))} for k, v in ranked] if total else []


def games_from_rows(rows: list, hero_id: int) -> List[dict]:
    """Строки explorer → [{"pos", "log", "upgrades"}] по одной на матч, где играл герой."""
    teams: Dict[Any, list] = {}
    for r in rows:
        if isinstance(r, dict) and r.get("match_id") is not None:
            teams.setdefault(r["match_id"], []).append(r)
    games = []
    for team in teams.values():
        mine = [r for r in team if r.get("hero_id") == hero_id]
        if not mine:
            continue
        players = [(r.get("hero_id"), r.get("lane_role"), r.get("gold_per_min")) for r in team]
        pos = assign_positions(players).get(hero_id)
        if pos:
            games.append({"pos": pos, "log": mine[0].get("purchase_log"),
                          "upgrades": mine[0].get("ability_upgrades_arr")})
    return games


def aggregate_builds(games: List[dict], consts: DotaConstants) -> Dict[str, dict]:
    out = {}
    for pos in range(1, 6):
        mine = [g for g in games if g["pos"] == pos]
        parsed = [game_items(g.get("log"), consts) for g in mine]
        parsed = [p for p in parsed if p is not None]
        phases = {}
        for phase, _ in PHASES:
            c = Counter()
            for p in parsed:
                c.update(p[phase])
            phases[phase] = _top(c, len(parsed))
        seqs = [ability_sequence(g.get("upgrades"), consts) for g in mine]
        out[str(pos)] = {"games": len(mine), "item_games": len(parsed), "phases": phases,
                         "skills": skill_build(seqs), "talents": talent_pairs(seqs), "generic_items": False}
    return out


def builds_from_popularity(raw, consts: DotaConstants) -> Dict[str, List[dict]]:
    """Запасной вариант: /heroes/{id}/itemPopularity — без разбивки по позициям."""
    if not isinstance(raw, dict):
        raise BuildsError("сервер вернул непонятный ответ")
    counts: Dict[str, Counter] = {}
    for phase, field in (("start", "start_game_items"), ("early", "early_game_items"),
                         ("mid", "mid_game_items"), ("late", "late_game_items")):
        c = Counter()
        block = raw.get(field)
        for item_id, value in (block.items() if isinstance(block, dict) else ()):
            if isinstance(value, dict):
                value = value.get("games", value.get("count"))
            try:
                key = consts.items_by_id.get(int(item_id))
                n = int(value)
            except (TypeError, ValueError):
                continue
            if key and n > 0 and (phase == "start" or not skip_after_start(key, consts)):
                c[key] += n
        counts[phase] = c
    # Сколько всего было игр, сервер не говорит; стартовые покупки есть почти в каждой — берём максимум
    start_values = list(counts["start"].values())
    all_values = [v for c in counts.values() for v in c.values()]
    denom = max(start_values) if start_values else max(all_values, default=0)
    return {phase: _top(c, denom) for phase, c in counts.items()}


def explorer_sql(hero_id: int, since: int, limit: int) -> str:
    hid = int(hero_id)
    return (
        "WITH hm AS (SELECT pm.match_id, (pm.player_slot < 128) AS radiant FROM player_matches pm "
        "JOIN matches m ON m.match_id = pm.match_id "
        f"WHERE pm.hero_id = {hid} AND m.start_time >= {int(since)} "
        f"ORDER BY pm.match_id DESC LIMIT {int(limit)}) "
        "SELECT p.match_id, p.hero_id, p.lane_role, p.gold_per_min, "
        f"CASE WHEN p.hero_id = {hid} THEN p.purchase_log END AS purchase_log, "
        f"CASE WHEN p.hero_id = {hid} THEN p.ability_upgrades_arr END AS ability_upgrades_arr "
        "FROM player_matches p JOIN hm ON hm.match_id = p.match_id AND (p.player_slot < 128) = hm.radiant"
    )


# ── Stratz: сборки хай-рейтинговых лобби (OpenDota разбирает только про-матчи) ──
def stratz_token() -> str:
    token = "".join(os.environ.get("STRATZ_TOKEN", "").split())
    if token:
        return token
    for folder in dict.fromkeys((DATA_DIR, _app_dir())):
        try:
            with open(os.path.join(folder, "api.txt"), "r", encoding="utf-8-sig") as f:
                token = "".join(f.read().split())
        except OSError:
            continue
        if token:
            return token
    return ""


def stratz_hero_query(hero_id: int, brackets: str, positions) -> str:
    parts = []
    for pos in positions:
        args = f"heroId: {int(hero_id)}, bracketBasicIds: [{brackets}], positionIds: [POSITION_{int(pos)}]"
        parts.append(f"p{pos}full: itemFullPurchase({args}, matchLimit: 1) {{ itemId time instance matchCount }} "
                     f"p{pos}start: itemStartingPurchase({args}) {{ itemId instance wasGiven matchCount }} "
                     f"p{pos}min: abilityMinLevel({args}) {{ abilityId level matchCount }} "
                     f"p{pos}max: abilityMaxLevel({args}) {{ abilityId level matchCount }} "
                     f"p{pos}tal: talent({args}) {{ abilityId matchCount }}")
    return "{ heroStats { " + " ".join(parts) + " } }"


def stratz_hero_stats(query: str, token: str, timeout: float = 90) -> dict:
    req = urllib.request.Request(
        STRATZ_API, data=json.dumps({"query": query}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "STRATZ_API"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        log.warning("Stratz: HTTP %s", e.code)
        if e.code in (401, 403):
            raise BuildsError("Stratz не принял токен из api.txt") from e
        if e.code == 429:
            raise BuildsError("Stratz: слишком много запросов, попробуй через минуту") from e
        raise BuildsError(f"Stratz ответил {e.code}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("Нет связи со Stratz: %s", e)
        raise BuildsError("нет связи со Stratz") from e
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise BuildsError("Stratz вернул непонятный ответ") from e
    stats = (body.get("data") or {}).get("heroStats") if isinstance(body, dict) else None
    if not isinstance(stats, dict):
        errors = body.get("errors") if isinstance(body, dict) else None
        msg = str(errors[0].get("message", "")) if isinstance(errors, list) and errors and isinstance(errors[0], dict) else ""
        raise BuildsError("Stratz: " + (msg[:120] or "нет данных"))
    return stats


def _stratz_rows(stats: dict, pos: int, name: str) -> List[dict]:
    rows = stats.get(f"p{pos}{name}")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _stratz_count(row: dict) -> int:
    n = row.get("matchCount")
    return n if _is_int(n) and n > 0 else 0


def _levels_by_ability(rows: List[dict]) -> Dict[int, Counter]:
    out: Dict[int, Counter] = {}
    for r in rows:
        aid, level = r.get("abilityId"), r.get("level")
        if _is_int(aid) and _is_int(level) and level > 0:
            out.setdefault(aid, Counter())[level] += _stratz_count(r)
    return out


def stratz_items(full: List[dict], start: List[dict], games: int, consts: DotaConstants) -> Dict[str, List[dict]]:
    counts = {phase: Counter() for phase, _ in PHASES}
    for r in start:
        key = consts.items_by_id.get(r.get("itemId"))
        if key and not r.get("wasGiven") and not r.get("instance"):
            counts["start"][key] += _stratz_count(r)
    for r in full:
        key, minute = consts.items_by_id.get(r.get("itemId")), r.get("time")
        if not key or r.get("instance") or not _is_int(minute) or skip_after_start(key, consts):
            continue
        phase = phase_of(minute * 60)
        counts["early" if phase == "start" else phase][key] += _stratz_count(r)
    return {phase: _top(c, games) for phase, c in counts.items()}


def stratz_talents(tiers: Dict[int, List[str]], rows: List[dict], consts: DotaConstants) -> List[dict]:
    counts: Counter = Counter()
    for r in rows:
        key = consts.ability_ids.get(r.get("abilityId"))
        if key:
            counts[key] += _stratz_count(r)
    pairs = []
    for i, level in enumerate(TALENT_LEVELS, start=1):
        # В справочнике первый талант уровня — правый на экране талантов, второй — левый
        right, left = (tiers.get(i, [])[:2] + ["", ""])[:2][0], (tiers.get(i, [])[:2] + ["", ""])[:2][1]
        lc, rc = counts[left] if left else 0, counts[right] if right else 0
        total = lc + rc
        pct = round(100 * lc / total) if total else 0
        pairs.append({"level": level, "left": left, "right": right,
                      "left_pct": pct, "right_pct": 100 - pct if total else 0,
                      "picked": "" if not total else ("left" if lc >= rc else "right"), "sided": True})
    return pairs


def stratz_skills(mins: List[dict], maxs: List[dict], talents: List[dict], consts: DotaConstants) -> dict:
    """Порядок прокачки на 18 уровней. Stratz отдаёт не цепочки, а «на каком уровне способность
    взяли впервые» и «на каком довели до максимума» — по ним собираем самый вероятный порядок."""
    by_min, by_max = _levels_by_ability(mins), _levels_by_ability(maxs)
    totals = {aid: sum(c.values()) for aid, c in by_min.items()}
    top = max(totals.values(), default=0)
    spells = []
    for aid, total in totals.items():
        key = consts.ability_ids.get(aid)
        if key and not is_talent(key) and "attribute" not in key and total * 2 >= top:
            spells.append((aid, key, by_min[aid].most_common(1)[0][0]))
    if not spells:
        return {"order": [], "pct": 0, "games": 0}

    slots: List[Optional[str]] = [None] * SKILL_LEN
    ult = max(spells, key=lambda s: s[2])  # ультимейт открывается позже всех
    for step in range(3):
        if ult[2] + 6 * step <= SKILL_LEN:
            slots[ult[2] + 6 * step - 1] = ult[1]
    for t in talents:  # выбранные таланты занимают свой уровень
        aid = consts.ability_by_name.get(t.get("left" if t.get("picked") == "left" else "right", ""))
        if aid in by_min and by_min[aid].most_common(1)[0][0] <= SKILL_LEN:
            slots[by_min[aid].most_common(1)[0][0] - 1] = t["left" if t["picked"] == "left" else "right"]
    need: Dict[int, int] = {}
    for aid, key, learn in spells:
        if (aid, key, learn) == ult:
            continue
        need[aid] = 3  # первое очко ставим сразу, ещё три — на прокачку до максимума
        if learn <= SKILL_LEN and slots[learn - 1] is None:
            slots[learn - 1] = key
        else:
            need[aid] += 1
    deadline = {aid: (by_max[aid].most_common(1)[0][0] if aid in by_max else SKILL_LEN) for aid in need}
    learned = {aid: learn for aid, _, learn in spells}
    names = {aid: key for aid, key, _ in spells}
    for i in range(SKILL_LEN):
        if slots[i] is not None:
            continue
        level = i + 1
        ready = [aid for aid in need if need[aid] > 0 and level > learned[aid]]
        if not ready:
            slots[i] = "attribute_bonus"
            continue
        # Берём способность, которой больше всего очков не хватает на оставшиеся до «максимума» уровни
        best = max(ready, key=lambda a: (need[a] / max(deadline[a] - level + 1, 1), -deadline[a]))
        slots[i] = names[best]
        need[best] -= 1
    return {"order": slots, "pct": 0, "games": top, "approx": True}


def stratz_position(stats: dict, pos: int, consts: DotaConstants, tiers: Dict[int, List[str]]) -> dict:
    full, start = _stratz_rows(stats, pos, "full"), _stratz_rows(stats, pos, "start")
    games = max((_stratz_count(r) for r in start if not r.get("wasGiven") and not r.get("instance")), default=0)
    talents = stratz_talents(tiers, _stratz_rows(stats, pos, "tal"), consts)
    return {"games": games, "item_games": games, "phases": stratz_items(full, start, games, consts),
            "skills": stratz_skills(_stratz_rows(stats, pos, "min"), _stratz_rows(stats, pos, "max"),
                                    talents, consts),
            "talents": talents, "generic_items": False}


class BuildsService:
    """Загрузка и кэширование сборок. Методы блокирующие — вызывать из фонового потока."""

    def __init__(self):
        self._lock = threading.Lock()
        self._consts: Optional[DotaConstants] = None

    def constants(self, force: bool = False) -> DotaConstants:
        with self._lock:
            if self._consts is not None and not force:
                return self._consts
            cached = cache_load("constants.json")
            if cached and not force and 0 <= cached[1] < BUILDS_TTL:
                self._consts = DotaConstants(cached[0])
                return self._consts
            try:
                data = DotaConstants.reduce(
                    http_get_json(f"{OPENDOTA_API}/constants/heroes"),
                    http_get_json(f"{OPENDOTA_API}/constants/items"),
                    http_get_json(f"{OPENDOTA_API}/constants/abilities", timeout=60),
                    http_get_json(f"{OPENDOTA_API}/constants/ability_ids", timeout=60))
                cache_save("constants.json", data)
            except BuildsError:
                if not cached:
                    raise
                log.warning("Справочники не обновились — работаю со старым кэшем")
                data = cached[0]
            self._consts = DotaConstants(data)
            return self._consts

    def builds(self, hero_id: int, force: bool = False) -> dict:
        consts = self.constants()
        name = f"builds4_{int(hero_id)}.json"
        cached = cache_load(name)
        if cached and not force and 0 <= cached[1] < BUILDS_TTL:
            return dict(cached[0], age=cached[1])
        try:
            data = self._fetch(int(hero_id), consts)
        except BuildsError as e:
            if cached:
                return dict(cached[0], age=cached[1], stale=True, error=str(e))
            raise
        cache_save(name, data)
        return dict(data, age=0.0)

    def talent_tiers(self, hero_id: int, consts: DotaConstants) -> Dict[int, List[str]]:
        """Пары талантов героя: {1: [имя, имя], ..., 4: [...]} (1..4 ↔ уровни 10/15/20/25)."""
        cached = cache_load("hero_talents.json")
        if cached and 0 <= cached[1] < TALENTS_TTL:
            data = cached[0]
        else:
            try:
                raw = http_get_json(f"{OPENDOTA_API}/constants/hero_abilities", timeout=60)
                data = {k: [[str(t.get("name")), t.get("level")] for t in v.get("talents", [])
                            if isinstance(t, dict) and _is_int(t.get("level"))]
                        for k, v in raw.items() if isinstance(v, dict) and isinstance(v.get("talents"), list)}
                cache_save("hero_talents.json", data)
            except (BuildsError, AttributeError):
                if not cached:
                    raise BuildsError("нет справочника талантов")
                data = cached[0]
        tiers: Dict[int, List[str]] = {}
        for name, level in data.get((consts.heroes.get(hero_id) or {}).get("name"), []):
            tiers.setdefault(level, []).append(name)
        return tiers

    def _fetch_stratz(self, hero_id: int, consts: DotaConstants, token: str) -> dict:
        try:
            tiers = self.talent_tiers(hero_id, consts)
        except BuildsError as e:
            log.warning("Таланты героя %s не загрузились: %s", hero_id, e)
            tiers = {}
        stats = stratz_hero_stats(stratz_hero_query(hero_id, STRATZ_BRACKET, range(1, 6)), token)
        built = {pos: stratz_position(stats, pos, consts, tiers) for pos in range(1, 6)}
        later = ("early", "mid", "late")
        wide = False
        for brackets in STRATZ_BRACKETS_WIDER:
            thin = [p for p, b in built.items() if b["games"] < STRATZ_MIN_GAMES or not all(b["phases"][ph] for ph in later)]
            if not thin:
                break
            try:
                more = stratz_hero_stats(stratz_hero_query(hero_id, brackets, thin), token)
            except BuildsError as e:
                log.warning("Расширенная выборка Stratz не загрузилась: %s", e)
                break
            for p in thin:
                b, cur = stratz_position(more, p, consts, tiers), built[p]
                for ph in later:  # каждую фазу берём из самой узкой выборки, где она есть
                    if not cur["phases"][ph] and b["phases"][ph]:
                        cur["phases"][ph], wide = b["phases"][ph], True
                if cur["games"] < b["games"] and not any(cur["phases"][ph] for ph in later):
                    built[p], wide = b, True
        if not any(b["item_games"] for b in built.values()):
            raise BuildsError("Stratz: по герою нет данных")
        return {"hero_id": hero_id, "source": "stratz", "wide": wide,
                "positions": {str(p): b for p, b in built.items()}}

    def _fetch_published(self, hero_id: int) -> dict:
        data = http_get_json(PUBLISHED_URL.format(int(hero_id)), timeout=20)
        if not isinstance(data, dict) or not isinstance(data.get("positions"), dict):
            raise BuildsError("готовые сборки: неверный формат")
        return data

    def _fetch(self, hero_id: int, consts: DotaConstants) -> dict:
        try:
            return self._fetch_published(hero_id)
        except BuildsError as e:
            log.info("Готовых сборок героя %s нет (%s) — пробую Stratz напрямую", hero_id, e)
        token, stratz_error = stratz_token(), None
        if token:
            try:
                return self._fetch_stratz(hero_id, consts, token)
            except BuildsError as e:
                stratz_error = str(e)
                log.warning("Stratz недоступен для героя %s: %s", hero_id, e)
        data = self._fetch_opendota(hero_id, consts)
        if stratz_error:
            data["stratz_error"] = stratz_error
        return data

    def _fetch_opendota(self, hero_id: int, consts: DotaConstants) -> dict:
        games, explorer_error = [], None
        try:
            sql = explorer_sql(hero_id, int(time.time()) - BUILDS_DAYS * 86400, BUILDS_MATCH_LIMIT)
            resp = http_get_json(f"{OPENDOTA_API}/explorer?" + urllib.parse.urlencode({"sql": sql}), timeout=60)
            if not isinstance(resp, dict) or resp.get("err"):
                raise BuildsError(f"explorer: {resp.get('err') if isinstance(resp, dict) else 'нет данных'}")
            rows = resp.get("rows")
            if not isinstance(rows, list):
                raise BuildsError("explorer: нет строк в ответе")
            games = games_from_rows(rows, hero_id)
        except BuildsError as e:
            explorer_error = str(e)
            log.warning("Explorer недоступен для героя %s: %s", hero_id, e)

        positions = aggregate_builds(games, consts)
        source = "explorer"
        if not any(p["item_games"] for p in positions.values()):
            try:
                generic = builds_from_popularity(
                    http_get_json(f"{OPENDOTA_API}/heroes/{hero_id}/itemPopularity"), consts)
                for p in positions.values():
                    p["phases"], p["generic_items"] = generic, True
                source = "mixed" if games else "popularity"
            except BuildsError as e:
                if not games:
                    raise BuildsError(explorer_error or str(e)) from e
        return {"hero_id": hero_id, "source": source, "positions": positions}


def fetch_image_bytes(url: str) -> bytes:
    path = os.path.join(CACHE_DIR, "img", hashlib.sha1(url.encode("utf-8")).hexdigest() + ".png")
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        pass
    data = http_get(url, timeout=15)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    except OSError:
        pass
    return data


def format_age(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 90:
        return "только что"
    if seconds < 3600:
        return f"{int(seconds // 60)} мин назад"
    if seconds < 2 * 86400:
        return f"{int(seconds // 3600)} ч назад"
    return f"{int(seconds // 86400)} дн. назад"
