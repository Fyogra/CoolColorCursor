"""Сборки внутри магазина Dota 2: JS с данными дописывается в dota_hud_shop.vjs_c, свой пак в game/dota_russian.
Данные берутся из опубликованных сборок (PUBLISHED_URL); пак обновляется раз в сутки и после обновления игры,
если он уже установлен. Файлы самой игры не меняются."""
from __future__ import annotations

import json
import os
import struct
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple
from app import dota_mod as d
from app.builds_data import CACHE_DIR, PUBLISHED_URL, BuildsService
from app.config import DATA_DIR, log
from app import ability_info, skill_order
from app.skill_order import normalize, _info as so_info

JS = "panorama/scripts/hud/dota_hud_shop.vjs_c"
STATE_FILE = os.path.join(DATA_DIR, "shop_state.json")
REFRESH_SECONDS = 20 * 3600
PHASES = ("start", "early", "mid", "late")
LIMITS = {"start": 15, "early": 15, "mid": 15, "late": 15}
RARE_PCT = 6          # предметы реже этого % отбрасываем (но не меньше двух в стадии)
RARE_PCT_THIN = 3     # ...а на непопулярной позиции героя (игр меньше THIN_SHARE от главной) — реже этого
THIN_SHARE = 0.25
MAX_MISSING = 0.1     # доля героев без скачанных сборок, при которой пак не пересобираем


def fetch(hero_id):
    try:
        with urllib.request.urlopen(PUBLISHED_URL.format(hero_id), timeout=20) as r:
            return hero_id, json.load(r)
    except Exception:
        return hero_id, None


def collect():
    BuildsService().constants()   # справочники OpenDota: без оверлейных сборок кэша ещё нет — скачает
    consts = json.load(open(os.path.join(CACHE_DIR, "constants.json"), encoding="utf-8"))["data"]
    heroes = {int(k): v for k, v in consts["heroes"].items()}
    items = consts["items"]
    with ThreadPoolExecutor(16) as ex:
        results = dict(ex.map(fetch, list(heroes)))
    missing = [h for h in heroes if not results.get(h)]
    if len(missing) > MAX_MISSING * len(heroes):     # сеть подвела — лучше оставить прежний пак, чем урезанный
        raise OSError(f"не загрузились сборки {len(missing)} героев из {len(heroes)} (нет сети?)")
    table, index, data, names = [], {}, {}, {}
    abil, abil_idx, tal, tal_idx = [], {}, [], {}

    def ab(key):
        if key not in abil_idx:
            abil_idx[key] = len(abil)
            abil.append(key)
        return abil_idx[key]

    def tl(key):
        if key not in tal_idx:
            tal_idx[key] = len(tal)
            tal.append(key)
        return tal_idx[key]
    for hid, v in heroes.items():
        names[v["name"]] = hid
        b = results.get(hid)
        if not b:
            continue
        b = normalize(b)
        positions = {}
        top_games = max([pd.get("games", 0) for pd in (b.get("positions") or {}).values()] or [0])
        for pos, pd in (b.get("positions") or {}).items():
            rare = RARE_PCT_THIN if pd.get("games", 0) < THIN_SHARE * top_games else RARE_PCT
            phases = []
            for ph in PHASES:
                row = []
                for it in (pd.get("phases") or {}).get(ph, [])[:LIMITS[ph]]:
                    key = it["key"]
                    if key not in items or (it.get("pct", 0) < rare and len(row) >= 2):
                        continue          # редкие предметы отбрасываем, но позиция без данных не остаётся
                    if key not in index:
                        index[key] = len(table)
                        table.append(key)
                    row.append([index[key], it.get("pct", 0)])   # [номер предмета, % покупок]
                phases.append(row)
            skills = []
            order_ = ((pd.get("skills") or {}).get("order") or [])[:24]
            for lvl, key in enumerate(order_, start=1):
                if key and not key.startswith("special_bonus"):
                    skills.append([lvl, ab(key)])
            talents = []
            for t in (pd.get("talents") or []):
                if t.get("left") and t.get("right"):
                    talents.append([t.get("level", 0), tl(t["left"]), tl(t["right"]), t.get("left_pct", 0),
                                    t.get("right_pct", 0), 0 if t.get("picked") == "left" else 1])
            if not talents:               # в статистике таланты не пришли — берём из файлов игры без процентов
                for k, (l, r) in enumerate(((so_info().get(str(hid)) or {}).get("t") or [])):
                    talents.append([10 + 5 * k, tl(l), tl(r), 0, 0, 0])
            if any(phases):
                positions[pos] = (pd.get("games", 0), phases, skills, talents)
        if positions:
            order = sorted(positions, key=lambda p: -positions[p][0])
            data[hid] = {"o": [int(p) for p in order], "p": {p: positions[p][1] for p in order},
                         "s": {p: positions[p][2] for p in order}, "t": {p: positions[p][3] for p in order}}
    log.info("Магазин: героев с данными %d, предметов %d, скиллов %d, талантов %d", len(data), len(table), len(abil), len(tal))
    return data, table, names, abil, tal


JS_TEMPLATE = r'''

// ── CCC: сборки на героя справа от магазина ──
(function ()
{
	var DATA = __DATA__;
	var ITEMS = __ITEMS__;
	var HEROES = __HEROES__;
	var ABIL = __ABIL__;
	var TAL = __TAL__;
	var PHASE_NAMES = [ "НАЧАЛЬНЫЕ ПРЕДМЕТЫ", "РАННЯЯ ИГРА", "ОСНОВНЫЕ ПРЕДМЕТЫ", "ПОЗДНЯЯ ИГРА" ];
	var panel = null, curName = "", curPos = 0, curHero = 0;
	var GUIDE_W = 210;        // ширина родной колонки гайда (dota_hud_shop.css, #GuideFlyout)
	// Кез: у второй стойки в тех же слотах другие способности (уровни общие) — иконки меняем вслед за стойкой
	var STANCE2 = { kez_echo_slash: "kez_falcon_rush", kez_grappling_claw: "kez_talon_toss",
		kez_kazurai_katana: "kez_shodo_sai", kez_raptor_dance: "kez_ravens_veil" };
	var stanceImgs = [], stance2 = false;
	// Иконки прокачки берём у способностей своего героя в матче — так видны иконки арканы и шмоток
	var iconImgs = [];

	function bindIcons()
	{
		var hero = Players.GetPlayerHeroEntityIndex( Players.GetLocalPlayer() );
		if ( hero < 0 ) { return; }
		for ( var i = 0; i < iconImgs.length; i++ )
		{
			var img = iconImgs[ i ][ 0 ], name = iconImgs[ i ][ 1 ];
			var ab = Entities.GetAbilityByName( hero, name );
			if ( !img.IsValid() || ab < 0 ) { continue; }
			img.contextEntityIndex = ab;    // так подхватываются перекраски от шмоток (проверено на ПЛе)
			// у арканы и шмоток своя текстура (spellicons/tidehunter/arcana/...): рисуем её поверх родной иконки
			var tex = Abilities.GetAbilityTextureName( ab ) || "";
			$.Msg( "CCC icon: " + name + " -> " + tex );
			if ( !tex || tex == name ) { continue; }
			var own = $.CreatePanel( "Image", img.GetParent(), "" );
			own.SetImage( "file://{images}/spellicons/" + tex + ".png" );
			own.SetScaling( "stretch-to-fit-preserve-aspect" );
			own.style.width = "26px";
			own.style.height = "26px";
			img.GetParent().MoveChildBefore( own, img );
			img.style.visibility = "collapse";
		}
		iconImgs = [];              // не нашлась способность или своей текстуры нет — остаётся стандартная иконка
	}

	function kezStance2()
	{
		var hero = Players.GetPlayerHeroEntityIndex( Players.GetLocalPlayer() );
		if ( hero < 0 ) { return false; }
		if ( Abilities.GetAbilityName( Entities.GetAbility( hero, 0 ) ) == "kez_falcon_rush" ) { return true; }
		var first = Entities.GetAbilityByName( hero, "kez_echo_slash" );
		return first >= 0 && Abilities.IsHidden( first );
	}

	function updateStance()
	{
		var now = kezStance2();
		if ( now == stance2 ) { return; }
		stance2 = now;
		for ( var i = 0; i < stanceImgs.length; i++ )
		{
			var e = stanceImgs[ i ];
			if ( e[ 0 ].IsValid() ) { e[ 0 ].abilityname = now ? STANCE2[ e[ 1 ] ] : e[ 1 ]; }
		}
	}

	function label( parent, text, size, color )
	{
		var l = $.CreatePanel( "Label", parent, "" );
		l.text = text;
		l.style.fontSize = size + "px";
		l.style.color = color;
		return l;
	}

	function itemPanel( parent, key )
	{
		var it;
		try { it = $.CreatePanel( "DOTAShopItem", parent, "", { itemname: "item_" + key } ); }
		catch ( e ) { it = null; }
		if ( !it ) { it = $.CreatePanel( "DOTAItemImage", parent, "", { itemname: "item_" + key } ); }
		it.style.width = "42px";
		it.style.height = "31px";
		return it;
	}

	function pctColor( pct )          // 100% — фиолетовый, от 60 — зелёный, от 25 — жёлтый, ниже — красный
	{
		if ( pct >= 100 ) { return "#c77dff"; }
		if ( pct >= 60 ) { return "#5fd35f"; }
		if ( pct >= 25 ) { return "#f2d24b"; }
		return "#ff5a5a";
	}

	function itemCell( parent, key, pct )
	{
		var cell = $.CreatePanel( "Panel", parent, "" );
		cell.style.flowChildren = "down";
		cell.style.marginRight = "6px";
		cell.style.marginBottom = "3px";
		itemPanel( cell, key );
		var p = label( cell, pct + "%", 11, pctColor( pct ) );
		p.hittest = false;
		p.style.horizontalAlign = "center";
		p.style.fontWeight = "bold";
		return cell;
	}

	function ctx() { return $.GetContextPanel(); }

	function hideNative()
	{
		var names = [ "ItemBuild", "ItemPlusGuide" ];
		for ( var i = 0; i < names.length; i++ )
		{
			var n = ctx().FindChildTraverse( names[ i ] );
			if ( n ) { n.style.visibility = "collapse"; n.visible = false; }
		}
		var fly = ctx().FindChildTraverse( "GuideFlyout" );
		if ( fly ) { fly.style.width = GUIDE_W + "px"; }
	}

	function ensurePanel()
	{
		if ( panel && panel.IsValid() ) { return; }
		var host = ctx().FindChildTraverse( "ItemBuildContainer" );
		var inside = !!host;
		panel = $.CreatePanel( "Panel", host || ctx(), "CCCBuild", { scrollbars: "y" } );
		panel.style.flowChildren = "down";
		panel.style.overflow = "squish scroll";
		panel.style.backgroundImage = "url( 's2r://panorama/images/hud/reborn/bg_shop_guide_psd.vtex' )";
		panel.style.backgroundSize = "cover";
		panel.style.backgroundPosition = "bottom";
		if ( inside )
		{
			panel.style.width = "100%";
			panel.style.height = "100%";
			panel.style.padding = "12px 0px 0px 12px";
		}
		else
		{
			panel.style.width = GUIDE_W + "px";
			panel.style.backgroundColor = "#000000ee";
			panel.style.horizontalAlign = "right";
			panel.style.marginRight = "-" + ( GUIDE_W + 10 ) + "px";
			panel.style.marginTop = "60px";
			panel.style.padding = "8px";
			panel.style.zIndex = "9999";
		}
	}

	// Ничего не удаляем (игра падала при удалении панелей магазина): блоки героя и позиций создаются один раз,
	// вкладки только прячут и показывают нужный.
	var blocks = {};          // hero -> { root, pos: { pos: block }, tabs: { pos: tab } }

	function showPos( b, pos )
	{
		for ( var p in b.pos )
		{
			b.pos[ p ].style.visibility = ( p == pos ) ? "visible" : "collapse";
			b.tabs[ p ].style.backgroundColor = ( p == pos ) ? "#ffffff" : "#44484fcc";
			b.tabLabels[ p ].style.color = ( p == pos ) ? "#000000" : "#ffffff";
		}
	}

	function header( parent, text )
	{
		var h = label( parent, text, 14, "#ffffff" );
		h.style.marginTop = "4px";
		h.style.marginBottom = "2px";
		h.style.letterSpacing = "2px";
		return h;
	}

	function skillsAndTalents( col, d, pos )
	{
		var sk = ( d.s && d.s[ pos ] ) || [];
		if ( sk.length )
		{
			header( col, "ПРОКАЧКА" );
			var srow = $.CreatePanel( "Panel", col, "" );
			srow.style.flowChildren = "right-wrap";
			srow.style.width = "100%";
			for ( var i = 0; i < sk.length; i++ )
			{
				var cell = $.CreatePanel( "Panel", srow, "" );
				cell.style.flowChildren = "down";
				cell.style.marginRight = "2px";
				cell.style.marginBottom = "2px";
				var name = ABIL[ sk[ i ][ 1 ] ];
				if ( name == "attribute_bonus" )
				{
					var box = $.CreatePanel( "Panel", cell, "" );
					box.style.width = "26px";
					box.style.height = "26px";
					box.style.backgroundColor = "#6b5a2acc";
					var plus = label( box, "+", 22, "#ffd27a" );
					plus.style.horizontalAlign = "center";
					plus.style.verticalAlign = "center";
				}
				else
				{
					var img = $.CreatePanel( "DOTAAbilityImage", cell, "", { abilityname: name } );
					img.style.width = "26px";
					img.style.height = "26px";
					if ( STANCE2[ name ] ) { stanceImgs.push( [ img, name ] ); }
					else { iconImgs.push( [ img, name ] ); }
				}
				var lv = label( cell, "" + sk[ i ][ 0 ], 11, "#cccccc" );
				lv.style.horizontalAlign = "center";
			}
		}
		var tl = ( d.t && d.t[ pos ] ) || [];
		if ( tl.length )
		{
			// дерево как в игре: круглая подложка, серое дерево и оранжевые листья выбранных талантов
			// (картинки Valve; лист каждого яруса нарисован справа, левый — зеркальное отражение)
			header( col, "ТАЛАНТЫ" );
			var tree = $.CreatePanel( "Panel", col, "" );
			tree.style.width = "120px";
			tree.style.height = "120px";
			tree.style.horizontalAlign = "center";
			var layers = [ "statbranch_button_bg", "statbranch_button_bg_all_off" ];
			var tiers = [ 10, 15, 20, 25 ];
			for ( var j = 0; j < tl.length && j < 4; j++ )
			{
				var lvl = tiers.indexOf( tl[ j ][ 0 ] ) >= 0 ? tl[ j ][ 0 ] : tiers[ j ];
				layers.push( [ "statbranch_button_pip" + lvl, tl[ j ][ 5 ] == 0 ] );
			}
			for ( var k = 0; k < layers.length; k++ )
			{
				var src = typeof layers[ k ] == "string" ? layers[ k ] : layers[ k ][ 0 ];
				var im = $.CreatePanel( "Image", tree, "" );
				im.SetImage( "file://{images}/hud/reborn/" + src + ".psd" );
				im.SetScaling( "stretch-to-fit-preserve-aspect" );
				im.style.width = "120px";
				im.style.height = "120px";
				if ( typeof layers[ k ] != "string" && layers[ k ][ 1 ] ) { im.style.transform = "scale3d( -1, 1, 1 )"; }
			}
		}
	}

	function makeBlock( hero )
	{
		var d = DATA[ hero ];
		var root = $.CreatePanel( "Panel", panel, "" );
		root.style.flowChildren = "down";
		root.style.width = "100%";
		var b = { root: root, pos: {}, tabs: {}, tabLabels: {} };
		var title = label( root, "СБОРКА ПО СТАТИСТИКЕ", 14, "#d3d3d3" );
		title.style.fontWeight = "semi-bold";
		if ( !d ) { label( root, "Нет данных для героя", 14, "#cccccc" ); return b; }
		var tabs = $.CreatePanel( "Panel", root, "" );
		tabs.style.flowChildren = "right";
		tabs.style.margin = "22px 0px 4px 0px";   // ниже стрелки «»» родного гайда в правом верхнем углу
		tabs.style.width = "192px";
		for ( var i = 0; i < d.o.length; i++ )
		{
			( function ( pos )
			{
				var t = $.CreatePanel( "Panel", tabs, "" );
				t.style.width = "fill-parent-flow(1)";
				t.style.marginRight = "4px";
				t.style.borderRadius = "4px";
				b.tabs[ pos ] = t;
				b.tabLabels[ pos ] = label( t, "" + pos, 14, "#ffffff" );
				b.tabLabels[ pos ].style.horizontalAlign = "center";
				t.SetPanelEvent( "onactivate", function () { curPos = pos; showPos( b, pos ); } );
			} )( d.o[ i ] );
		}
		for ( var i = 0; i < d.o.length; i++ )
		{
			var pos = d.o[ i ];
			var phases = d.p[ pos ];
			var col = $.CreatePanel( "Panel", root, "" );
			col.style.flowChildren = "down";
			col.style.width = "100%";
			b.pos[ pos ] = col;
			for ( var ph = 0; ph < 4; ph++ )
			{
				if ( !phases[ ph ].length ) { continue; }
				var pl = label( col, PHASE_NAMES[ ph ], 14, "#ffffff" );
				pl.style.marginTop = "4px";
				pl.style.marginBottom = "2px";
				pl.style.letterSpacing = "2px";
				var row = $.CreatePanel( "Panel", col, "" );
				row.style.flowChildren = "right-wrap";
				row.style.width = "100%";
				for ( var k = 0; k < phases[ ph ].length; k++ )
				{
					var e = phases[ ph ][ k ];          // [номер предмета, % покупок]
					itemCell( row, ITEMS[ e[ 0 ] ], e[ 1 ] );
				}
			}
			skillsAndTalents( col, d, pos );
		}
		return b;
	}

	function render()
	{
		ensurePanel();
		for ( var h in blocks ) { blocks[ h ].root.style.visibility = "collapse"; blocks[ h ].root.style.height = "0px"; }
		if ( !blocks[ curHero ] ) { blocks[ curHero ] = makeBlock( curHero ); }
		var b = blocks[ curHero ];
		b.root.style.visibility = "visible";
		b.root.style.height = "fit-children";
		showPos( b, curPos );
	}

	function tick()
	{
		try
		{
			hideNative();
			var name = Players.GetPlayerSelectedHero( Players.GetLocalPlayer() ) || "";
			if ( name != curName )
			{
				curName = name;
				curHero = HEROES[ name ] || 0;
				var d = DATA[ curHero ];
				curPos = d ? d.o[ 0 ] : 0;
				render();
			}
			if ( iconImgs.length ) { bindIcons(); }
			if ( stanceImgs.length && curName == "npc_dota_hero_kez" ) { updateStance(); }
		}
		catch ( e ) { $.Msg( "CCC: " + e ); }
		$.Schedule( curName == "npc_dota_hero_kez" ? 0.25 : 1.0, tick );
	}
	$.Schedule( 0.5, tick );
} )();
'''.replace("\n", "\r\n")


def is_installed() -> bool:
    return bool(d.our_pak("shop"))


def _game_signature() -> str:
    """Размер и время файла игры: меняется после обновления Dota."""
    game = d.game_dir()
    try:
        st = os.stat(os.path.join(game, "dota", "pak01_dir.vpk"))
        return f"{st.st_size}:{int(st.st_mtime)}"
    except (OSError, TypeError):
        return ""


def _read_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def needs_refresh() -> bool:
    """Пак уже стоит, но данные старше суток или игра обновилась."""
    if not is_installed():
        return False
    state = _read_state()
    return time.time() - state.get("built_at", 0) > REFRESH_SECONDS or state.get("game") != _game_signature()


def uninstall() -> Tuple[bool, str]:
    try:
        d.remove_pak("shop")
    except PermissionError:
        return False, "Файл занят: закрой Dota 2 и попробуй снова."
    except OSError as e:
        return False, f"Не получилось удалить пак: {e}"
    return True, ""


def build() -> Tuple[bool, str]:
    """Собирает данные и записывает пак. При открытой Dota файл занят — вернёт (False, причина)."""
    if not d.game_dir():
        return False, "Не нашёл папку Dota 2."
    if _read_state().get("game") != _game_signature():   # игра обновилась: новые герои и скиллы
        try:
            ability_info.generate()
            skill_order.reload()
        except (OSError, KeyError, ValueError) as e:
            log.warning("Не удалось обновить список скиллов героев: %s", e)
    try:
        data, table, names, abil, tal = collect()
        js = (JS_TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
              .replace("__ITEMS__", json.dumps(table, separators=(",", ":")))
              .replace("__HEROES__", json.dumps(names, separators=(",", ":")))
              .replace("__ABIL__", json.dumps(abil, separators=(",", ":")))
              .replace("__TAL__", json.dumps(tal, separators=(",", ":"))))
        if not data:
            return False, "Нет данных сборок (нет сети?)."
        blob = d._read_file(os.path.join(d.game_dir(), "dota", "pak01_dir.vpk"), JS)
        _fs, _hv, _ver, off, cnt = struct.unpack_from("<IHHII", blob, 0)
        pos, last = 8 + off, None
        for _ in range(cnt):
            rel, size = struct.unpack_from("<II", blob, pos + 4)
            last = (blob[pos:pos + 4], pos + 4 + rel, size, pos)
            pos += 12
        name, start, size, head_pos = last
        if name != b"DATA" or start + size != len(blob):
            return False, "Скрипт магазина в игре изменился, нужна правка сборщика."
        new_blk = blob[start:start + size] + js.encode("utf-8")
        out = bytearray(blob[:start]) + new_blk
        struct.pack_into("<I", out, 0, len(out))
        struct.pack_into("<I", out, head_pos + 8, len(new_blk))
        path = d.write_pak("shop", {JS: bytes(out)})
    except PermissionError:
        return False, "Файл занят: закрой Dota 2 и попробуй снова."
    except (OSError, KeyError, ValueError, struct.error) as e:
        return False, f"Не получилось собрать пак: {e}"
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"built_at": time.time(), "game": _game_signature()}, f)
    except OSError:
        pass
    log.info("Пак магазина записан: %s (%d байт)", path, len(out))
    return True, ""
