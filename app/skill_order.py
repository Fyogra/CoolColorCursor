"""Приведение порядка прокачки к правилам Dota 2: ультимейт только на 6/12/18, обычный скилл — не больше 4 уровней,
таланты — отдельные очки (на 10, 15, 20, 25), в порядке уровней скиллов не стоят. Статистика даёт лишь желаемый порядок, а легальную цепочку собираем по нему."""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

_INFO: Optional[Dict[str, dict]] = None
INVOKER = "74"                  # три сферы по 7 уровней, Invoke в порядок не включаем
STAT = "attribute_bonus"
# герои с особыми уровнями ульты: (уровни ульты, длина списка); пустые уровни остаются ""
SPECIAL = {"82": ((3, 10, 17, 24), 24)}      # Мипо: ульта в 4 ранга, только свои скиллы без добавок


def _info() -> Dict[str, dict]:
    global _INFO
    if _INFO is None:
        from app.ability_info import ASSET_PATH, DATA_PATH
        _INFO = {}
        for path in (DATA_PATH, ASSET_PATH):            # свежая копия после обновления игры, иначе из поставки
            try:
                with open(path, encoding="utf-8") as f:
                    _INFO = json.load(f)
                break
            except (OSError, ValueError):
                continue
    return _INFO


def reload() -> None:
    global _INFO
    _INFO = None


def _is_talent(key: str) -> bool:
    return key.startswith("special_bonus")


def _invoker(order: List[str]) -> List[str]:
    """Инвокер: только сферы по 7 уровней, без Invoke, очков в характеристики и добивки; таланты (отдельные очки) в конце."""
    quas, wex, exort = _info()[INVOKER]["a"][:3]
    spheres = (quas, wex, exort)
    wishes = [k for k in order if k in spheres]
    ranks = {n: 0 for n in spheres}
    result = []
    for _level in range(1, 25):
        pick = next((k for k in wishes if ranks[k] < 7), None)
        if pick is None:                             # желания кончились: докачиваем сферы до 7, дальше список заканчивается
            pick = max((n for n in spheres if ranks[n] < 7), key=lambda n: ranks[n], default=None)
            if pick is None:
                break
        else:
            wishes.remove(pick)
        ranks[pick] += 1
        result.append(pick)
    return result + [k for k in order if _is_talent(k)]


def _groups(info: dict, extras: bool = True):
    """Скиллы героя как группы: {имя в статистике: группа}, список групп по порядку слотов, группа ульты.
    Одна группа — один скилл с общими уровнями (Shadowraze1/2/3, топор Тролля в двух видах)."""
    group_of, order = {}, []
    names = list(info["a"][:3]) + ([n for n in info.get("s", []) if n not in info["a"]] if extras else []) + [info["a"][3]]
    for slot in names:
        for alias in slot.split(","):
            base = re.sub(r"\d+$", "", alias)
            gid = group_of.setdefault(base, base)
            group_of[alias] = gid
            if gid not in order:
                order.append(gid)
    ult = group_of[info["a"][3].split(",")[0]]
    return group_of, order, ult


def legalize(order: List[str], hero_id, length: int = 18) -> List[str]:
    info = _info().get(str(hero_id))
    if not info or not order:
        return ["" if k == STAT else k for k in order]  # очки в характеристики не показываем
    if str(hero_id) == INVOKER:
        return _invoker(order)
    special = str(hero_id) in SPECIAL
    group_of, groups, ult = _groups(info, extras=not special)
    ult_levels, length = SPECIAL.get(str(hero_id), ((6, 12, 18), length))
    wishes = [k for k in order if not _is_talent(k) and k in group_of]   # очки в характеристики и подспособности не показываем
    talents = [k for k in order if _is_talent(k)]
    if not wishes:                                  # по герою нет порядка прокачки — берём типовой
        main = [g for g in groups if g != ult]
        wishes = [main[0], main[1], main[2] if len(main) > 2 else main[0]] + [main[0]] * 3 + [main[1]] * 3 + [main[2] if len(main) > 2 else main[1]] * 3
        group_of = {g: g for g in groups}
    shown = {}
    for k in wishes:
        shown.setdefault(group_of[k], k)
    ranks = {g: 0 for g in groups}
    queue = [group_of[k] for k in wishes]
    fallback = sorted(groups, key=lambda g: queue.index(g) if g in queue else 99)

    def legal(g: str, level: int) -> bool:
        r = ranks[g] + 1
        return r <= (len(ult_levels) if g == ult else 4) and level >= (ult_levels[r - 1] if g == ult and r <= len(ult_levels) else 2 * r - 1)

    result = []
    for level in range(1, length + 1):
        if level in ult_levels and legal(ult, level):    # ультимейт на своих уровнях берут всегда
            pick = ult
            if ult in queue:
                queue.remove(ult)
        else:
            pick = next((g for g in queue if legal(g, level)), None)
            if pick is not None:
                queue.remove(pick)
            else:                                   # желаний не осталось: добираем любым доступным скиллом
                pick = next((g for g in fallback if g != ult and legal(g, level)), "")   # качать нечего — уровень пустой
        if pick in ranks:
            ranks[pick] += 1
        result.append(shown.get(pick, pick) if pick in ranks else pick)
    return result + talents                         # таланты — отдельные очки, на уровни скиллов не влияют


def normalize(data: dict) -> dict:
    """Применяет legalize ко всем позициям данных героя (изменяет и возвращает data)."""
    hero_id = data.get("hero_id")
    for pos in (data.get("positions") or {}).values():
        sk = pos.get("skills")
        if sk and sk.get("order"):
            sk["order"] = legalize(sk["order"], hero_id)
    return data
