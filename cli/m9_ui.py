"""M9 CLI rendering layer (2026-09 human display revamp).

Goals:
- single turn view: banner + phase/HP/location/SP/arc/PP + grouped numbered menu;
- `special` opens a browsable list of currently available special actions;
- only one round-boundary separator, no wall of `═══` decorations;
- all user-visible text comes from `data/prompts.json` namespace `m9.ui.*`
  via `engine/m9/text.m9_text`; no Chinese fallback in code;
- `compact_mode / show_icons / use_colors` are honored in this layer.

Display only: never participates in mechanics; the AI path never reaches here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from engine.m9.text import m9_text
from engine.prompt_manager import prompt_manager


# ── 配置 ───────────────────────────────────────────────────

def _config() -> Dict[str, Any]:
    return getattr(prompt_manager, "config", {}) or {}


def _ui_config() -> Dict[str, Any]:
    cfg = _config()
    ui = cfg.get("ui", {}) if isinstance(cfg.get("ui"), dict) else {}
    flat = {
        "compact_mode": bool(cfg.get("compact_mode", False)),
        "show_icons": bool(cfg.get("show_icons", True)),
        "max_line_length": int(cfg.get("max_line_length", 80)),
    }
    for key in flat:
        if key in ui:
            flat[key] = bool(ui[key]) if isinstance(ui[key], bool) else ui[key]
    return flat


def compact() -> bool:
    return bool(_ui_config()["compact_mode"])


def show_icons() -> bool:
    return bool(_ui_config()["show_icons"])


def use_colors() -> bool:
    return bool(_config().get("use_colors", True))


def _line(label: str, value: Any = "") -> str:
    if value in (None, ""):
        return f"  {label}"
    return f"  {label}: {value}"


# ── 只读资源 ───────────────────────────────────────────────

def _resources(player: Any, state: Any) -> Tuple[int, int, int]:
    sp = 0
    chapters = 0
    pp = 0
    m9 = getattr(state, "m9_system", None)
    if m9 is not None:
        sp = int(m9.get_sp(getattr(player, "player_id", "")))
    arc = getattr(state, "m9_arc", None)
    if arc is not None:
        try:
            chapters = len(arc.chapters_of(getattr(player, "player_id", ""))
                           or set())
        except Exception:
            chapters = 0
    pp_ledger = getattr(state, "m9_pp", None)
    if pp_ledger is not None:
        try:
            pp = int(pp_ledger.balance(getattr(player, "player_id", "")))
        except Exception:
            pp = 0
    return sp, chapters, pp


def _location_name(state: Any, player: Any) -> str:
    loc = getattr(player, "location", None)
    if not loc:
        return m9_text("ui.location_unknown")
    try:
        from actions.move import get_location_display_name
        return str(get_location_display_name(state, loc))
    except Exception:
        return str(loc)


# ── 回合视图 ───────────────────────────────────────────────

def show_turn_view(
    player: Any,
    state: Any,
    action_names: List[str],
    action_display: List[Dict[str, str]],
    action_options: Dict[str, List[str]],
) -> Optional[List[str]]:
    """打印横幅+分组菜单，返回编号菜单（供 HumanController 数字选择）。"""
    sp, chapters, pp = _resources(player, state)
    round_num = int(getattr(state, "current_round", 0) or 0)
    phase = str(getattr(state, "current_phase", "action") or "action")
    phase_label = m9_text("ui.phase_label", phase=phase)

    print()
    width = 58 if not compact() else 46
    banner = m9_text("ui.turn_banner", round=round_num, phase=phase_label,
                     name=getattr(player, "name", ""))
    banner += " " + "─" * max(0, width - 22 - len(str(round_num)))
    print(banner)
    print(m9_text("ui.turn_status",
                  hp=f"{float(getattr(player, 'hp', 0) or 0):g}",
                  max_hp=f"{float(getattr(player, 'max_hp', 20) or 20):g}",
                  location=_location_name(state, player),
                  sp=sp, chapters=chapters, pp=pp))

    menu: List[str] = []
    groups: List[Tuple[str, List[Tuple[str, str]]]] = []

    def add_group(title: str, commands: List[str]) -> None:
        items: List[Tuple[str, str]] = []
        for cmd in commands:
            if cmd in menu:
                continue
            menu.append(cmd)
            items.append((cmd, ""))
        if items:
            groups.append((title, items))

    for atype in ("move", "interact", "find", "lock", "attack"):
        cmds = list(action_options.get(atype, []) or [])
        if cmds:
            add_group(m9_text(f"ui.group_{atype}"), cmds)

    specials: List[Tuple[str, str]] = []
    try:
        from actions.special_op import get_available_specials
        for spec in get_available_specials(player, state):
            specials.append((str(spec["name"]),
                             str(spec.get("description", ""))))
    except Exception:
        pass
    if specials:
        menu.extend(name for name, _ in specials)
        groups.append((m9_text("ui.group_special"), specials))

    for action in ("wake", "forfeit"):
        if action in action_names and action not in menu:
            menu.append(action)
            groups.append((m9_text(f"ui.group_{action}"), [(action, "")]))

    for index, (title, items) in enumerate(groups):
        print(f"  [{title}]")
        max_show = 8 if not compact() else 5
        for offset, (cmd, desc) in enumerate(items[:max_show]):
            number = menu.index(cmd) + 1
            icon = "" if not show_icons() else "·"
            text = f"  {number:>2}{icon} {cmd}"
            if desc:
                text += f"  — {desc}"
            print(text)
        if len(items) > max_show:
            print(m9_text("ui.group_more", count=len(items)))

    view = [m9_text("ui.group_view"), "status", "allstatus", "police", "help"]
    print(m9_text("ui.view_line", title=view[0], views=" / ".join(view[1:])))
    return menu


def show_talent_available(t0_option: Dict[str, Any]) -> None:
    name = str(t0_option.get("name", ""))
    desc = str(t0_option.get("description", ""))
    icon = m9_text("ui.icon_talent") if show_icons() else ""
    print(m9_text("ui.talent_available", icon=icon, name=name, description=desc))


def choose_special(player: Any, state: Any) -> Optional[str]:
    """输入 `special` 时列出当前可用特殊操作并选择；取消返回 None。"""
    try:
        from actions.special_op import get_available_specials
        specs = get_available_specials(player, state)
    except Exception:
        specs = []
    if not specs:
        print(m9_text("ui.no_special"))
        return None
    print(m9_text("ui.special_header"))
    names = []
    for i, spec in enumerate(specs, 1):
        name = str(spec["name"])
        desc = str(spec.get("description", ""))
        names.append(name)
        suffix = f" — {desc}" if desc else ""
        print(m9_text("ui.special_item", index=i, name=name, suffix=suffix))
    try:
        from cli.display import prompt_choice
        return prompt_choice(
            m9_text("ui.special_prompt"),
            names + [m9_text("ui.special_cancel")])
    except Exception:
        return None


# ── 状态视图 ───────────────────────────────────────────────

def show_player_status(player: Any, state: Any) -> None:
    sp, chapters, pp = _resources(player, state)
    print(m9_text("ui.status_name_line",
                  name=getattr(player, "name", ""),
                  player_id=getattr(player, "player_id", "")))
    talent_name = getattr(player, "talent_name", "")
    talent = getattr(player, "talent", None)
    talent_status = ""
    if talent is not None:
        try:
            talent_status = str(talent.describe_status() or "")
        except Exception:
            talent_status = ""
    if talent_status:
        print(m9_text("ui.status_talent", talent=talent_name or m9_text("ui.none"),
                      status=talent_status))
    else:
        print(m9_text("ui.status_talent_plain",
                      talent=talent_name or m9_text("ui.none")))
    print(m9_text("ui.status_hp_line",
                  hp=f"{float(getattr(player, 'hp', 0) or 0):g}",
                  max_hp=f"{float(getattr(player, 'max_hp', 20) or 20):g}",
                  location=_location_name(state, player),
                  sp=sp, chapters=chapters, pp=pp))
    weapons = ", ".join(str(w) for w in getattr(player, "weapons", []) or []) \
        or m9_text("ui.none")
    print(m9_text("ui.status_weapons", items=weapons))
    armor = getattr(getattr(player, "armor", None), "describe", None)
    if callable(armor):
        try:
            print(m9_text("ui.status_armor", description=armor()))
        except Exception:
            pass
    items = ", ".join(str(i) for i in getattr(player, "items", []) or []) \
        or m9_text("ui.none")
    print(m9_text("ui.status_items", items=items))


def show_all_players_status(state: Any) -> None:
    print(m9_text("ui.all_status_header"))
    for pid in getattr(state, "player_order", []):
        player = state.get_player(pid)
        if player is None:
            continue
        alive = m9_text("ui.alive") if player.is_alive() else m9_text("ui.dead")
        show_player_status(player, state)
        print(m9_text("ui.alive_line", status=alive))
        print()


def show_police_status(state: Any) -> None:
    station = getattr(state, "m9_police", None)
    if station is None:
        print(m9_text("ui.police_not_mounted"))
        return
    if station.is_disabled():
        print(m9_text("ui.police_disabled"))
        return
    captain = station.captain_id or m9_text("ui.none")
    wanted = station.open_wanted()
    wanted_name = ""
    if wanted is not None:
        target = state.get_player(wanted.suspect_id)
        wanted_name = m9_text("ui.wanted_suffix",
                              name=getattr(target, "name", wanted.suspect_id))
    print(m9_text("ui.police_summary", captain=captain,
                  authority=station.authority,
                  wanted=wanted.suspect_id if wanted else m9_text("ui.none"),
                  wanted_suffix=wanted_name))
    for unit in station.units():
        if not unit.is_alive():
            continue
        status = (m9_text("ui.unit_controlled") if unit.is_disabled()
                  else m9_text("ui.unit_ready"))
        armor_name = getattr(getattr(unit, "armor", None), "name", None) \
            or m9_text("ui.none")
        cover = station._cover.get(unit.unit_id, 0)
        print(m9_text("ui.police_unit_line", unit=unit.unit_id, status=status,
                      hp=unit.hp, max_hp=unit.max_hp,
                      location=unit.location or m9_text("ui.location_unknown"),
                      weapon=unit.weapon_name, armor=armor_name, cover=cover))


def show_help(state: Any = None) -> None:
    if state is None:
        state_holder = getattr(prompt_manager, "_m9_last_state", None)
    else:
        state_holder = state
    if state_holder is not None:
        from engine.m9.gate import m9_enabled
        if m9_enabled(state_holder):
            print(m9_text("ui.help_m9"))
            return
    print(m9_text("ui.help_legacy"))
