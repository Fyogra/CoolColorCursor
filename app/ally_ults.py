"""Кулдауны ульт союзников под их портретами в верхней панели Dota (пока зажат Alt).

Это кусок JS, который дописывается в пак магазина вместе со сборками (shop_mod.build). Данные берёт сама
игра: герой союзника, уровень ульты и сколько осталось отката (с Октарином, талантами и т. п.). GSI здесь не
помогает: игроку он отдаёт только своего героя, блок allplayers приходит лишь зрителям.

Клик по кругу (с зажатым Alt) — родной пинг способности в командный чат, как Alt+клик по способности в игре.
Враги не показываются: только своя половина верхней панели.
"""
from __future__ import annotations

# Внешний вид
SIZE = 34                  # диаметр круга, px интерфейса Dota
RING = 3                   # толщина кольца
RING_COLOR = "#e9b13c"     # прошедшая часть отката: золотой, как уровни прокачки в дереве навыков
RING_BG = "#1b1b1f"        # оставшаяся часть
FONT = "Radiance"          # шрифт интерфейса Dota (лежит в игре: panorama/fonts/radiance-*.otf)
FONT_SIZE = 14
OFFSET_Y = 124             # от верха ячейки героя вниз, px: ниже родных Alt-кнопок (чаевые/подарок до 112, свиток ТП до ~120)
TICK_ALT = 0.1             # как часто обновлять, пока Alt зажат, с
TICK_IDLE = 0.25           # как часто проверять Alt, с

JS_TEMPLATE = r'''
// CCC: кулдауны ульт союзников под портретами, пока зажат Alt
( function ()
{
	var SIZE = __SIZE__, RING = __RING__, OFFSET_Y = __OFFSET_Y__;
	var ULTIMATE = 1;             // ABILITY_TYPES.ABILITY_TYPE_ULTIMATE
	var shown = false, warned = {};

	function root()
	{
		var p = $.GetContextPanel();
		while ( p.GetParent() ) { p = p.GetParent(); }
		return p;
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

	// союзники (и сам игрок): короткое имя героя -> индексы героев по порядку игроков
	// (список: в демо и редких режимах у двоих бывает один герой)
	function allies()
	{
		var out = {}, team = Players.GetTeam( Players.GetLocalPlayer() );
		var ids = Game.GetPlayerIDsOnTeam( team );
		for ( var i = 0; i < ids.length; i++ )
		{
			var hero = Players.GetPlayerHeroEntityIndex( ids[ i ] );
			if ( hero < 0 ) { continue; }
			var name = Entities.GetUnitName( hero ).replace( "npc_dota_hero_", "" );
			( out[ name ] = out[ name ] || [] ).push( hero );
		}
		return out;
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
		// Alt+клик: родной пинг способности в командный чат («ульта готова / откат N с / не изучена»)
		w.SetPanelEvent( "onactivate", function ()
		{
			if ( w.ab >= 0 ) { Abilities.PingAbility( w.ab ); }
		} );
		return w;
	}

	function draw( w, ab )
	{
		if ( w.ab != ab ) { w.ab = ab; w.icon.contextEntityIndex = ab; }
		var level = Abilities.GetLevel( ab );
		var left = Abilities.GetCooldownTimeRemaining( ab );
		var full = Abilities.GetCooldownLength( ab );
		if ( level < 1 )
		{
			w.ring.style.clip = "radial( 50% 50%, 0deg, 0deg )";
			w.shade.style.opacity = "1";
			w.time.text = "—";
			return;
		}
		var done = left > 0 && full > 0 ? Math.max( 0, Math.min( 1, 1 - left / full ) ) : 1;
		w.ring.style.clip = "radial( 50% 50%, 0deg, " + ( done * 360 ).toFixed( 1 ) + "deg )";
		w.shade.style.opacity = left > 0 ? "1" : "0";
		w.time.text = left > 0 ? String( Math.ceil( left ) ) : "";
	}

	function update( alt )
	{
		var team = Players.GetTeam( Players.GetLocalPlayer() );
		var box = root().FindChildTraverse( team == 3 ? "TopBarDirePlayersContainer" : "TopBarRadiantPlayersContainer" );
		if ( !box ) { return; }
		var heroes = alt ? allies() : {};
		for ( var i = 0; i < box.GetChildCount(); i++ )
		{
			var slot = box.GetChild( i );
			var img = slot.FindChildTraverse( "HeroImage" );
			if ( !img ) { continue; }
			var w = widget( slot );
			var hero = ( heroes[ img.heroname ] || [] ).shift();
			var ab = hero !== undefined ? ultOf( hero ) : -1;
			if ( !alt || ab < 0 )
			{
				if ( alt && img.heroname && hero !== undefined && !warned[ img.heroname ] )
				{
					warned[ img.heroname ] = true;
					$.Msg( "CCC ult: нет ульты у " + img.heroname );
				}
				w.style.visibility = "collapse";
				continue;
			}
			draw( w, ab );
			w.style.visibility = "visible";
		}
	}

	function tick()
	{
		var alt = false;
		try
		{
			alt = GameUI.IsAltDown();
			if ( alt || shown ) { update( alt ); }
			shown = alt;
		}
		catch ( e ) { $.Msg( "CCC ult: " + e ); }
		$.Schedule( alt ? __TICK_ALT__ : __TICK_IDLE__, tick );
	}
	$.Schedule( 1.0, tick );
} )();
'''


def js() -> str:
    """JS для пака магазина с подставленными настройками."""
    out = JS_TEMPLATE
    for key, value in (("__SIZE__", SIZE), ("__RING__", RING), ("__OFFSET_Y__", OFFSET_Y),
                       ("__RING_COLOR__", RING_COLOR), ("__RING_BG__", RING_BG), ("__FONT_SIZE__", FONT_SIZE),
                       ("__FONT__", FONT), ("__TICK_ALT__", TICK_ALT), ("__TICK_IDLE__", TICK_IDLE)):
        out = out.replace(key, str(value))
    return out.replace("\r\n", "\n").replace("\n", "\r\n")
