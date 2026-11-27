"""Кулдауны ульт союзников под их портретами в верхней панели Dota (пока зажат Alt).

Это кусок JS, который дописывается в пак магазина вместе со сборками (shop_mod.build). GSI здесь не помогает:
игроку он отдаёт только своего героя, блок allplayers приходит лишь зрителям.

Источники данных, по каждому герою первый, что сработал:
- способности героя через Entities/Abilities: точный откат. Работает в демо
  героя; в обычном матче способности союзников моду недоступны (имя юнита пустое);
- Game.GetPlayerUltimateStateOrTime(игрок) — им пользуется таблица счёта пользовательских игр: остаток отката
  в секундах. В обычном матче (лобби с ботами, 27.11) отдаёт −3 у всех — тогда идём дальше;
- родная таблица счёта (#scoreboard): в строке игрока «откат ульты, с» — точные секунды (таланты, Октарин,
  Аганим, всё). Заполняется, только пока таблица открыта: пока зажат Alt и чья-то ульта в откате, открываем
  её невидимой (DOTAHUDToggleScoreboard + opacity 0), отпустили Alt — закрываем. Проверено 27.11 в лобби;
- если секунд нет — родной индикатор под портретом (UltReady / UltOnCooldown): в момент перехода в «откат» запускаем таймер на
  откат из файлов игры (ULTS, уровень ульты по уровню героя 6/12/18), с Аганимом и Октарином из предметов
  союзника (Game.GetPlayerItems — их видно и в матче). Юнитов в матче моду не дают совсем (GetAllHeroEntities пуст). Таланты на откат не учтены: если таймер кончился раньше, до
  «готова» от игры показываем «…». Рефрешер и подобное: игра сразу показывает «готова», таймер гаснет.
Враги не показываются: только своя половина верхней панели.
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger(__name__)

# Внешний вид
SIZE = 34                  # диаметр круга, px интерфейса Dota
RING = 3                   # толщина кольца
RING_COLOR = "#3fc24a"     # прошедшая часть отката: зелёный, как кольцо свитка ТП под портретом
RING_BG = "#1b1b1f99"      # оставшаяся часть (полупрозрачная)
OPACITY = 0.7              # прозрачность всего круга: игра под ним видна
FONT = "Radiance"          # шрифт интерфейса Dota (лежит в игре: panorama/fonts/radiance-*.otf)
FONT_SIZE = 14
OFFSET_Y = 124             # от верха ячейки героя вниз, px: ниже родных Alt-кнопок (чаевые/подарок до 112, свиток ТП до ~120)
TICK_ALT = 0.1             # как часто обновлять, пока Alt зажат, с
TICK_IDLE = 0.25           # как часто следить за индикаторами ульт и Alt, с
OCTARINE = 0.75            # Октарин: откат способностей −25%
ULT_LEVELS = (6, 12, 18)   # на каких уровнях героя обычно берут 1/2/3 уровень ульты (для запасного таймера)

JS_TEMPLATE = r'''
// CCC: кулдауны ульт союзников под портретами, пока зажат Alt
( function ()
{
	var SIZE = __SIZE__, RING = __RING__, OFFSET_Y = __OFFSET_Y__;
	var ULTIMATE = 1;             // ABILITY_TYPES.ABILITY_TYPE_ULTIMATE
	var MAX_PLAYERS = 24;         // id игроков в Dota: 0…23 (зрители и тренеры тоже, их отсеивает команда)
	var ULTS = __ULTS__;          // короткое имя героя -> [ульта, [откат по уровням], [с Аганимом]?] из файлов игры
	var ULT_LEVELS = __ULT_LEVELS__;
	var OCTARINE = __OCTARINE__;  // во сколько раз Октарин укорачивает откат
	var shown = false;
	var timers = {};              // запасной путь: имя героя -> { state, start, full }
	var fulls = {};               // путь через таблицу счёта: слот -> полная длина текущего отката
	var ST = ( typeof PlayerUltimateStateOrTime_t != "undefined" ) ? PlayerUltimateStateOrTime_t : {};
	var NOT_LEVELED = ST.PLAYER_ULTIMATE_STATE_NOT_LEVELED !== undefined ? ST.PLAYER_ULTIMATE_STATE_NOT_LEVELED : -2;
	var HIDDEN = ST.PLAYER_ULTIMATE_STATE_HIDDEN !== undefined ? ST.PLAYER_ULTIMATE_STATE_HIDDEN : -3;

	function root()
	{
		var p = $.GetContextPanel();
		while ( p.GetParent() ) { p = p.GetParent(); }
		return p;
	}

	function now()
	{
		try { return Game.GetGameTime(); } catch ( e ) { return Date.now() / 1000; }
	}

	function ultOf( hero )
	{
		for ( var i = 0; i < Entities.GetAbilityCount( hero ); i++ )
		{
			var ab = Entities.GetAbility( hero, i );
			if ( ab >= 0 && Abilities.GetAbilityType( ab ) == ULTIMATE && !Abilities.IsHidden( ab ) ) { return ab; }
		}
		return -1;
	}

	// Союзники (и сам игрок): короткое имя героя -> [{ id, hero }] по порядку игроков (в демо у двоих бывает
	// один герой). Game.GetPlayerIDsOnTeam в матче отдаёт [null, …] — перебираем id сами; имя — из выбора игрока.
	function allies()
	{
		var out = {}, team = Players.GetTeam( Players.GetLocalPlayer() );
		for ( var id = 0; id < MAX_PLAYERS; id++ )
		{
			if ( !Players.IsValidPlayerID( id ) || Players.GetTeam( id ) != team ) { continue; }
			var name = String( Players.GetPlayerSelectedHero( id ) || "" ).replace( "npc_dota_hero_", "" );
			if ( !name ) { continue; }
			var hero = Players.GetPlayerHeroEntityIndex( id );
			( out[ name ] = out[ name ] || [] ).push( { id: id, hero: typeof hero == "number" ? hero : -1 } );
		}
		return out;
	}

	// Предметы союзника (их видно и в родной таблице счёта; в матче проверено): { имя: true }
	function itemsOf( id )
	{
		var out = {};
		try
		{
			var it = Game.GetPlayerItems( id );
			if ( !it || !it.inventory ) { return out; }
			for ( var s = it.inventory_slot_min; s < it.inventory_slot_max - ( it.backpack_size || 0 ); s++ )
			{
				var item = it.inventory[ s ];
				if ( item && item.item_name ) { out[ item.item_name ] = true; }
			}
		}
		catch ( e ) {}
		return out;
	}

	// Откат ульты союзника сейчас: по уровню героя, с Аганимом (если меняет откат) и Октарином (−25%)
	function fullCooldown( info, p )
	{
		var items = p ? itemsOf( p.id ) : {};
		var cds = info[ 1 ];
		if ( info[ 2 ] && items[ "item_ultimate_scepter" ] ) { cds = info[ 2 ]; }
		if ( !cds.length ) { return 0; }
		var cd = cds[ rankFor( p ? levelOf( p.id ) : 0, cds.length ) - 1 ];
		return items[ "item_octarine_core" ] ? cd * OCTARINE : cd;
	}

	function heroName( e )
	{
		try { return String( Entities.GetUnitName( e ) || "" ).replace( "npc_dota_hero_", "" ); } catch ( x ) { return ""; }
	}

	function levelOf( id )
	{
		try { var l = Players.GetLevel( id ); if ( l > 0 ) { return l; } } catch ( e ) {}
		return 0;
	}

	function rankFor( level, count )
	{
		var r = 1;
		for ( var i = 1; i < ULT_LEVELS.length; i++ ) { if ( level >= ULT_LEVELS[ i ] ) { r = i + 1; } }
		return Math.min( r, count );
	}

	function circle( parent, id, color )
	{
		var c = $.CreatePanel( "Panel", parent, id );
		c.hittest = false;
		c.style.width = "100%";
		c.style.height = "100%";
		c.style.borderRadius = "50%";
		c.style.backgroundColor = color;
		return c;
	}

	function widget( slot )
	{
		var w = slot.FindChildTraverse( "CCCUlt" );
		if ( w ) { return w; }
		w = $.CreatePanel( "Panel", slot, "CCCUlt" );
		w.style.width = SIZE + "px";
		w.style.height = SIZE + "px";
		w.style.horizontalAlign = "center";
		w.style.verticalAlign = "top";
		w.style.marginTop = OFFSET_Y + "px";
		w.style.zIndex = 50;
		// Как свиток ТП под портретом (#TopBarTPIcon): тень, появление с увеличением 0.8 -> 1 при Alt
		w.style.boxShadow = "0px 6px 16px -12px #00000080";
		w.style.borderRadius = "50%";
		w.style.transitionProperty = "opacity, pre-transform-scale2d";
		w.style.transitionDuration = "0.08s";
		w.style.transitionTimingFunction = "ease-in";
		w.style.opacity = "0";
		w.style.preTransformScale2d = "0.8";
		w.style.visibility = "collapse";
		circle( w, "CCCUltBg", "__RING_BG__" );
		w.ring = circle( w, "CCCUltRing", "__RING_COLOR__" );
		var icon = $.CreatePanel( "DOTAAbilityImage", w, "CCCUltIcon" );
		icon.hittest = false;
		icon.style.width = ( SIZE - 2 * RING ) + "px";
		icon.style.height = ( SIZE - 2 * RING ) + "px";
		icon.style.align = "center center";
		icon.style.borderRadius = "50%";
		w.icon = icon;
		w.shade = circle( icon, "CCCUltShade", "#000000a0" );
		var t = $.CreatePanel( "Label", w, "CCCUltTime" );
		t.hittest = false;
		t.style.align = "center center";
		t.style.fontFamily = "__FONT__";
		t.style.fontSize = "__FONT_SIZE__px";
		t.style.fontWeight = "bold";
		t.style.color = "#ffffff";
		t.style.textShadow = "0px 0px 3px 3.0 #000000";
		w.time = t;
		w.ab = -1;
		w.iconName = "";
		return w;
	}

	function show( w, on )
	{
		w.style.opacity = on ? "__OPACITY__" : "0";
		w.style.preTransformScale2d = on ? "1" : "0.8";
		w.hittest = false;   // клики проходят к портрету
	}

	// Кольцо и текст: done — какая часть отката прошла (0…1), left — секунд до готовности (0 — готова)
	function paint( w, learned, done, left, text )
	{
		if ( !learned )
		{
			w.ring.style.clip = "radial( 50% 50%, 0deg, 0deg )";
			w.shade.style.opacity = "1";
			w.time.text = "—";
			return;
		}
		w.ring.style.clip = "radial( 50% 50%, 0deg, " + ( Math.max( 0, Math.min( 1, done ) ) * 360 ).toFixed( 1 ) + "deg )";
		w.shade.style.opacity = left > 0 || text ? "1" : "0";
		w.time.text = text !== undefined ? text : ( left > 0 ? String( Math.ceil( left ) ) : "" );
	}

	// Точный путь: способность героя из игры
	function draw( w, ab )
	{
		if ( w.ab != ab )
		{
			w.ab = ab;
			w.iconName = Abilities.GetAbilityName( ab );
			w.icon.abilityname = w.iconName;
			w.icon.contextEntityIndex = ab;
		}
		var left = Abilities.GetCooldownTimeRemaining( ab );
		var full = Abilities.GetCooldownLength( ab );
		var done = left > 0 && full > 0 ? 1 - left / full : 1;
		paint( w, Abilities.GetLevel( ab ) >= 1, done, left > 0 ? left : 0 );
	}

	// Откат из таблицы счёта: v > 0 — секунд до готовности, иначе состояние
	function ultState( id )
	{
		try
		{
			var v = Game.GetPlayerUltimateStateOrTime( id );
			return typeof v == "number" ? v : null;
		}
		catch ( e ) { return null; }
	}

	function drawState( w, name, key, v )
	{
		var info = ULTS[ name ];
		if ( info && w.iconName != info[ 0 ] ) { w.icon.abilityname = info[ 0 ]; w.iconName = info[ 0 ]; w.ab = -1; }
		if ( v == NOT_LEVELED ) { if ( !info ) { return false; } paint( w, false ); return true; }
		if ( v <= 0 ) { fulls[ key ] = 0; paint( w, true, 1, 0 ); return true; }   // готова (или не хватает маны)
		var full = fulls[ key ] || 0;
		if ( !full || v > full + 0.5 ) { full = fulls[ key ] = v; }  // новый каст (после Рефрешера тоже)
		paint( w, true, 1 - v / full, v );
		return true;
	}

	// Пока Alt не зажат, тоже запоминаем начало отката, чтобы кольцо было полной длины
	function trackState( heroes )
	{
		for ( var name in heroes )
		{
			for ( var k = 0; k < heroes[ name ].length; k++ )
			{
				var v = ultState( heroes[ name ][ k ].id ), key = name + "#" + k;
				if ( v === null ) { continue; }
				if ( v <= 0 ) { fulls[ key ] = 0; }
				else if ( !fulls[ key ] || v > fulls[ key ] + 0.5 ) { fulls[ key ] = v; }
			}
		}
	}

	// Запасной путь: что показывает родной индикатор ульты под портретом
	function indicator( slot )
	{
		var ind = slot.FindChildTraverse( "TopBarUltIndicator" );
		if ( !ind ) { return ""; }
		if ( ind.BHasClass( "UltOnCooldown" ) ) { return "cd"; }
		if ( ind.BHasClass( "UltReady" ) || ind.BHasClass( "UltReadyNoMana" ) ) { return "ready"; }
		return ind.BHasClass( "UltLearned" ) ? "ready" : "none";
	}

	// Следим за индикаторами всегда (не только под Alt), чтобы поймать момент каста
	function track( box, heroes )
	{
		for ( var i = 0; i < box.GetChildCount(); i++ )
		{
			var slot = box.GetChild( i );
			var img = slot.FindChildTraverse( "HeroImage" );
			if ( !img || !img.heroname ) { continue; }
			var key = img.heroname + "#" + i;
			var state = indicator( slot );
			var t = timers[ key ] || ( timers[ key ] = { state: "", start: 0, full: 0 } );
			if ( state == "cd" && t.state != "cd" )
			{
				var info = ULTS[ img.heroname ];
				var p = ( heroes[ img.heroname ] || [] )[ 0 ];
				t.full = info ? fullCooldown( info, p ) : 0;
				t.start = t.state == "" ? 0 : now();          // откат уже шёл, когда мы появились: сколько осталось — неизвестно
			}
			t.state = state;
		}
	}

	function drawFallback( w, slot, name, key )
	{
		var info = ULTS[ name ];
		if ( info && w.iconName != info[ 0 ] ) { w.icon.abilityname = info[ 0 ]; w.iconName = info[ 0 ]; w.ab = -1; }
		var t = timers[ key ] || { state: indicator( slot ), start: 0, full: 0 };
		if ( t.state == "none" || t.state == "" ) { paint( w, false ); return; }
		if ( t.state != "cd" ) { paint( w, true, 1, 0 ); return; }
		if ( !t.start || !t.full ) { paint( w, true, 0, 0, "…" ); return; }
		var left = t.start + t.full - now();
		if ( left <= 0 ) { paint( w, true, 1, 0, "…" ); return; }    // по таблице уже готова, а игра ещё нет
		paint( w, true, 1 - left / t.full, left );
	}

	// Родная таблица счёта: в строке игрока есть «откат ульты, с» (UltCooldownSeconds, {d:ult_cooldown})
	// и значок ульты (UltIcon). Строки: имя героя -> [строка …] по порядку
	function scoreRows( team )
	{
		var out = {};
		var box = root().FindChildTraverse( team == 3 ? "DireTeamContainer" : "RadiantTeamContainer" );
		if ( !box ) { return out; }
		for ( var i = 0; i < box.GetChildCount(); i++ )
		{
			var row = box.GetChild( i ), img = row.FindChildTraverse( "HeroImage" );
			if ( img && img.heroname ) { ( out[ img.heroname ] = out[ img.heroname ] || [] ).push( row ); }
		}
		return out;
	}

	// Секунды до готовности ульты из строки таблицы счёта (точные: с талантами, Октарином, Аганимом), иначе 0
	function scoreSeconds( row )
	{
		if ( !row ) { return 0; }
		var lab = row.FindChildTraverse( "UltCooldownSeconds" );
		var s = lab ? parseFloat( lab.text ) : NaN;
		return s > 0 ? s : 0;
	}

	// Таблица счёта заполняет секунды, только пока открыта. Пока зажат Alt и чья-то ульта в откате, открываем
	// её невидимой (opacity 0) и закрываем, когда Alt отпущен. Открытую самим игроком не трогаем
	var peeking = false;
	function peek( on )
	{
		var sb = root().FindChildTraverse( "scoreboard" );
		if ( !sb || on == peeking ) { return; }
		if ( on )
		{
			if ( !sb.BHasClass( "ScoreboardClosed" ) ) { return; }
			sb.style.opacity = "0";
			sb.hittest = false;
			$.DispatchEvent( "DOTAHUDToggleScoreboard" );
			peeking = true;
		}
		else
		{
			$.DispatchEvent( "DOTAHUDToggleScoreboard" );
			peeking = false;
			$.Schedule( 0.4, function () { if ( !peeking ) { sb.style.opacity = null; sb.hittest = true; } } );
		}
	}

	function update( alt )
	{
		var team = Players.GetTeam( Players.GetLocalPlayer() );
		var box = root().FindChildTraverse( team == 3 ? "TopBarDirePlayersContainer" : "TopBarRadiantPlayersContainer" );
		if ( !box ) { return; }
		var heroes = allies();
		track( box, heroes );
		trackState( heroes );
		if ( !alt && !shown ) { return; }
		var anyCd = false;
		for ( var j = 0; j < box.GetChildCount(); j++ ) { if ( indicator( box.GetChild( j ) ) == "cd" ) { anyCd = true; } }
		try { peek( alt && anyCd ); } catch ( e ) { peeking = false; }
		var used = {}, rows = scoreRows( team );
		for ( var i = 0; i < box.GetChildCount(); i++ )
		{
			var slot = box.GetChild( i );
			var img = slot.FindChildTraverse( "HeroImage" );
			if ( !img ) { continue; }
			var w = widget( slot );
			var name = img.heroname;
			var list = heroes[ name ] || [];
			var n = used[ name ] = ( used[ name ] || 0 );
			var p = list[ n ];
			used[ name ]++;
			if ( !alt || !name || ( !p && !ULTS[ name ] ) )
			{
				show( w, false );
				continue;
			}
			// В обычном матче игра не даёт моду ни одного юнита (Entities.GetAllHeroEntities пуст) — только в демо
			var ab = -1;
			try { ab = p && p.hero >= 0 && heroName( p.hero ) == name ? ultOf( p.hero ) : -1; }
			catch ( e ) { ab = -1; }
			// HIDDEN (−3) таблица счёта в обычном матче отдаёт всегда (проверено в лобби) — тогда смотрим индикатор
			var v = ab < 0 && p ? ultState( p.id ) : null, ok = true;
			if ( v === HIDDEN ) { v = null; }
			var row = ( rows[ name ] || [] )[ n ], sec = scoreSeconds( row ), ind = indicator( slot );
			if ( ab >= 0 ) { draw( w, ab ); }
			else if ( v !== null ) { ok = drawState( w, name, name + "#" + n, v ); }
			else if ( ind == "cd" && sec > 0 ) { ok = drawState( w, name, name + "#" + n, sec ); }
			else if ( ULTS[ name ] ) { drawFallback( w, slot, name, name + "#" + i ); }
			else { ok = false; }
			w.style.visibility = ok ? "visible" : "collapse";      // пассивная ульта без отката — не показываем
			show( w, ok );
		}
	}

	function tick()
	{
		var alt = false;
		try
		{
			alt = GameUI.IsAltDown();
			update( alt );
			shown = alt;
		}
		catch ( e ) { $.Msg( "CCC ult: " + e ); }
		$.Schedule( alt ? __TICK_ALT__ : __TICK_IDLE__, tick );
	}
	$.Schedule( 1.0, tick );
} )();
'''


def _block(text: str, name: str):
    """Тело описания способности "name" { … } в KeyValues-файле героя или None.
    Блоков с таким именем бывает несколько (у фасетов тоже) — берём тот, где описано поведение способности."""
    first = None
    for m in re.finditer(rf'"{re.escape(name)}"\s*(?://[^\n]*)?\s*\{{', text):
        depth, i = 1, m.end()
        while depth and i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        body = text[m.end():i]
        if '"AbilityBehavior"' in body or '"AbilityType"' in body:
            return body
        first = body if first is None else first
    return first


def _values(body: str, key: str) -> list:
    """Числа по уровням: "key" "a b c" или "key" { "value" "a b c" … }."""
    m = re.search(rf'"{key}"\s+"([^"]+)"', body) or \
        re.search(rf'"{key}"\s*\{{[^}}]*?"value"\s+"([^"]+)"', body, re.S)
    out = []
    for v in (m.group(1).split() if m else []):
        try:
            out.append(round(float(v), 1))
        except ValueError:
            return []
    return out


def _cooldowns(body: str) -> list:
    """Откат ульты по уровням; у ульт с зарядами (Void Spirit) — время восстановления заряда."""
    out = _values(body, "AbilityCooldown")
    if not any(out):
        out = _values(body, "AbilityChargeRestoreTime") or out
    return out if any(out) else []


def _scepter(body: str, cds: list):
    """Откат с Аганимом: в блоке отката "special_bonus_scepter" "-20" (прибавка) или "=30 =24" (замена). Или None."""
    m = re.search(r'"(?:AbilityCooldown|AbilityChargeRestoreTime)"\s*\{([^}]*)\}', body)
    s = re.search(r'"special_bonus_scepter"\s+"([^"]+)"', m.group(1)) if m else None
    if not s:
        return None
    parts = s.group(1).split()
    try:
        vals = [float(v.lstrip("=+")) for v in parts]
    except ValueError:
        return None
    vals += [vals[-1]] * (len(cds) - len(vals))
    if parts[0].startswith("="):
        return [round(v, 1) for v in vals[:len(cds)]]
    return [round(max(0.0, c + v), 1) for c, v in zip(cds, vals)]


def ult_table() -> dict:
    """{короткое имя героя: [ульта, [откат по уровням], [с Аганимом]?]} из файлов игры. Пусто, если не вышло."""
    from app import ability_info, dota_mod as d
    try:
        base = os.path.join(d.game_dir(), "dota", "pak01_dir.vpk")
        tree = d._read_tree(base)[0]
        paths = [k for k in tree if k.startswith("scripts/npc/heroes/") and k.endswith(".txt")]
        files = d._read_files(base, paths)
        info = ability_info.collect()
    except (OSError, TypeError, ValueError, KeyError) as e:
        log.warning("Таблица откатов ульт не собралась: %s", e)
        return {}
    out = {}
    for path in paths:
        text = files[path].decode("utf-8", "replace")
        hid = re.search(r'"HeroID"\s+"(\d+)"', text)
        hero = re.search(r'"npc_dota_hero_(\w+)"\s*\{', text)
        if not hid or not hero or hid.group(1) not in info:
            continue
        ult = info[hid.group(1)]["a"][3]
        body = _block(text, ult)
        cds = _cooldowns(body) if body else []
        if cds:
            out[hero.group(1)] = [ult, cds] + ([_scepter(body, cds)] if _scepter(body, cds) else [])
    return out


def js(ults: dict = None) -> str:
    """JS для пака магазина с подставленными настройками и таблицей откатов ульт."""
    out = JS_TEMPLATE
    for key, value in (("__SIZE__", SIZE), ("__RING__", RING), ("__OFFSET_Y__", OFFSET_Y),
                       ("__RING_COLOR__", RING_COLOR), ("__RING_BG__", RING_BG), ("__FONT_SIZE__", FONT_SIZE),
                       ("__FONT__", FONT), ("__TICK_ALT__", TICK_ALT), ("__TICK_IDLE__", TICK_IDLE),
                       ("__ULT_LEVELS__", json.dumps(list(ULT_LEVELS))), ("__OCTARINE__", OCTARINE), ("__OPACITY__", OPACITY),
                       ("__ULTS__", json.dumps(ult_table() if ults is None else ults, separators=(",", ":")))):
        out = out.replace(key, str(value))
    return out.replace("\r\n", "\n").replace("\n", "\r\n")
