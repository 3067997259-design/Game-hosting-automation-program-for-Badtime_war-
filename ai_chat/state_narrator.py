"""
State Narrator —— 游戏状态自然语言翻译器
═════════════════════════════════════════
将 GameState + Player 翻译为简洁的中文自然语言段落，
供 LLM 作为系统提示的一部分。

设计要点：
- 不调用 build_obs()，直接读取对象属性，避免 numpy 依赖与归一化复杂度。
- 维度划分参考 rl/obs_builder.py（自身、对手、天赋、警察、病毒、高层特征）。
- 空段（无天赋/无警察活动等）整段跳过，控制 token 预算。
"""

from typing import Any, List, Optional


# ─────────────────────────────────────────────────────────
#  常量与辅助
# ─────────────────────────────────────────────────────────

_LOCATION_DISPLAY = {
    "商店": "商店",
    "魔法所": "魔法所",
    "医院": "医院",
    "军事基地": "军事基地",
    "警察局": "警察局",
}

_PHASE_DISPLAY = {
    "r0_start": "轮次开始",
    "r1_d4": "D4 判定",
    "r2_priority": "优先级裁定",
    "r3_actions": "行动阶段",
    "r4_end": "轮次结束",
    "not_started": "未开始",
}

_REPORT_PHASE_DISPLAY = {
    "idle": "未举报",
    "reported": "已举报",
    "assembled": "已集结",
    "dispatched": "已派出",
}

_TALENT_DISPLAY_NAME = {
    "OneSlash": "一刀缭断",
    "ScissorRush": "剪刀手一突",
    "Star": "天星",
    "Hexagram": "六爻",
    "Combo": "combo",
    "GoodCitizen": "朝阳好市民",
    "Resurrection": "死者苏生",
    "G1MythFire": "火萤IV型-完全燃烧",
    "Hologram": "请一直，注视着我",
    "Mythland": "神话之外",
    "Savior": "愿负世，照拂黎明",
    "Ripple": "往世的涟漪",
    "CutawayJoke": "要有笑声！",
    "Hoshino": "大叔我啊，剪短发了",
}


def _fmt_location(loc: Optional[str], my_id: Optional[str] = None) -> str:
    """格式化地点显示：home_pX 显示为'自己家'/'某某家'"""
    if loc is None:
        return "未知"
    if loc.startswith("home_"):
        if my_id and loc == f"home_{my_id}":
            return "自己家"
        return f"{loc[5:]}的家"
    return loc


def _fmt_status_flags(p: Any) -> str:
    """玩家状态标记列表"""
    flags = []
    if not getattr(p, "is_awake", True):
        flags.append("未起床")
    if getattr(p, "is_stunned", False):
        flags.append("眩晕")
    if getattr(p, "is_shocked", False):
        flags.append("感电")
    if getattr(p, "is_petrified", False):
        flags.append("石化")
    if getattr(p, "is_invisible", False):
        flags.append("隐身")
    return "/".join(flags) if flags else "无异常状态"


def _fmt_weapons(weapons: List[Any]) -> str:
    """武器列表（详细：名称/射程/属性/伤害/蓄力状态）"""
    if not weapons:
        return "无"
    parts = []
    for w in weapons:
        if not w or getattr(w, "_hexagram_disabled", False):
            continue
        wr = getattr(w, "weapon_range", None)
        rng = wr.value if wr is not None else "?"
        attr = w.attribute.value if hasattr(w, "attribute") and w.attribute else "?"
        dmg = w.get_effective_damage() if hasattr(w, "get_effective_damage") else getattr(w, "base_damage", 0)
        seg = f"{w.name}({rng},{attr},伤害{dmg}"
        if getattr(w, "requires_charge", False):
            if getattr(w, "is_charged", False):
                seg += ",已蓄力"
            else:
                seg += ",需蓄力" if getattr(w, "charge_mandatory", True) else ",可蓄力"
        seg += ")"
        parts.append(seg)
    return ", ".join(parts) if parts else "无"


def _fmt_armor(player: Any) -> str:
    """护甲：外层 / 内层"""
    if not hasattr(player, "armor") or not player.armor:
        return "无"
    try:
        from models.equipment import ArmorLayer
    except Exception:
        return "无"

    out_pieces = player.armor.get_active(ArmorLayer.OUTER)
    in_pieces = player.armor.get_active(ArmorLayer.INNER)

    def fmt_piece(piece):
        attr = piece.attribute.value if hasattr(piece, "attribute") and piece.attribute else "?"
        return f"{piece.name}({attr})"

    parts = []
    if out_pieces:
        parts.append("外层[" + ", ".join(fmt_piece(p) for p in out_pieces) + "]")
    if in_pieces:
        parts.append("内层[" + ", ".join(fmt_piece(p) for p in in_pieces) + "]")
    return " ".join(parts) if parts else "无"


def _fmt_items(items: List[Any]) -> str:
    if not items:
        return "无"
    return ", ".join(getattr(i, "name", str(i)) for i in items)


# ─────────────────────────────────────────────────────────
#  自身段落
# ─────────────────────────────────────────────────────────

def _narrate_self(player: Any) -> str:
    lines = ["【你的状态】"]
    lines.append(
        f"HP: {player.hp}/{player.max_hp} | "
        f"位置: {_fmt_location(player.location, player.player_id)} | "
        f"{'已起床' if getattr(player, 'is_awake', False) else '未起床'} | "
        f"{_fmt_status_flags(player)}"
    )
    lines.append(f"武器: {_fmt_weapons(getattr(player, 'weapons', []) or [])}")
    armor_str = _fmt_armor(player)
    if armor_str and armor_str != "无":
        lines.append(f"护甲: {armor_str}")
    items_str = _fmt_items(getattr(player, "items", []) or [])
    if items_str != "无":
        lines.append(f"物品: {items_str}")

    extra = []
    extra.append(f"购买凭证: {getattr(player, 'vouchers', 0)}")
    if getattr(player, "has_military_pass", False):
        extra.append("通行证: 有")
    if getattr(player, "has_detection", False):
        extra.append("探测能力: 有")
    if getattr(player, "is_captain", False):
        extra.append("身份: 警察队长")
    elif getattr(player, "is_police", False):
        extra.append("身份: 警察")
    if getattr(player, "is_criminal", False):
        extra.append("⚠️ 犯罪记录在身")
    if getattr(player, "kill_count", 0) > 0:
        extra.append(f"已击杀: {player.kill_count}")
    lines.append(" | ".join(extra))

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
#  对手段落
# ─────────────────────────────────────────────────────────

def _talent_public_summary(opp: Any) -> str:
    """对手天赋的公开信息摘要"""
    talent = getattr(opp, "talent", None)
    if talent is None:
        return ""
    cls = talent.__class__.__name__
    name = _TALENT_DISPLAY_NAME.get(cls, getattr(talent, "name", cls))
    parts = [name]

    if cls == "OneSlash":
        uses = getattr(talent, "uses_remaining", 0)
        parts.append(f"剩余次数{uses}")
    elif cls == "Star":
        parts.append(f"剩余次数{getattr(talent, 'uses_remaining', 0)}")
    elif cls == "Hexagram":
        parts.append(f"充能{getattr(talent, 'charges', 0)}/2")
        if getattr(talent, "immunity_active", False):
            parts.append("金身生效")
    elif cls == "Combo":
        parts.append(f"连击{getattr(talent, 'consecutive_actions', 0)}/3")
    elif cls == "ScissorRush":
        parts.append(f"反击次数{getattr(talent, 'response_uses_remaining', 0)}")
    elif cls == "Resurrection":
        if getattr(talent, "used", False):
            parts.append("已使用")
        elif getattr(talent, "mounted_on", None) is not None:
            parts.append("已挂载")
        elif getattr(talent, "learned", False):
            parts.append("已学会未挂载")
        else:
            parts.append(f"学习中({getattr(talent, 'learn_progress', 0)}/2)")
    elif cls == "GoodCitizen":
        pass
    elif cls == "CutawayJoke":
        parts.append(f"笑点{getattr(talent, 'laugh_points', 0)}")
        if getattr(talent, "cutaway_charges", 0) > 0:
            parts.append(f"插入式笑话×{talent.cutaway_charges}")
    elif cls == "G1MythFire":
        if getattr(talent, "debuff_started", False):
            parts.append("debuff已激活")
        if getattr(talent, "has_supernova", False):
            parts.append("超新星就绪")
    elif cls == "Hologram":
        if getattr(talent, "active", False):
            parts.append(f"影像展开中(剩{getattr(talent, 'remaining_rounds', 0)}轮)")
        elif getattr(talent, "used", False):
            parts.append("已使用")
    elif cls == "Mythland":
        if getattr(talent, "active", False):
            parts.append("结界展开中")
        elif getattr(talent, "used", False):
            parts.append("已使用")
    elif cls == "Savior":
        if getattr(talent, "is_savior", False):
            parts.append(
                f"救世主状态(剩{getattr(talent, 'savior_duration', 0)}轮)"
            )
        elif getattr(talent, "spent", False):
            parts.append("已消耗")
        else:
            parts.append(f"火种{getattr(talent, 'divinity', 0)}/12")
    elif cls == "Ripple":
        rem = getattr(talent, "reminiscence", 0)
        thr = getattr(talent, "activation_threshold", 24)
        if getattr(talent, "anchor_active", False):
            parts.append(
                f"锚定中(剩{getattr(talent, 'anchor_rounds_left', 0)}轮)"
            )
        else:
            parts.append(f"追忆{rem}/{thr}")
    elif cls == "Hoshino":
        form = getattr(talent, "form", None)
        if form:
            parts.append(f"形态{form}")
        if getattr(talent, "is_terror", False):
            parts.append("Terror")

    return " | ".join(parts)


def _opp_short_line(opp: Any, my_id: Optional[str]) -> str:
    """单个对手的一行情报"""
    if not opp.is_alive():
        return f"{opp.name}: 已死亡"

    parts = [f"{opp.name}", f"HP {opp.hp}"]
    parts.append(f"位置 {_fmt_location(opp.location, my_id)}")

    if not getattr(opp, "is_awake", True):
        parts.append("未起床")
    flags = []
    for k, label in [("is_stunned", "眩晕"), ("is_shocked", "感电"),
                     ("is_petrified", "石化"), ("is_invisible", "隐身")]:
        if getattr(opp, k, False):
            flags.append(label)
    if flags:
        parts.append("/".join(flags))

    weapons = [w for w in (getattr(opp, "weapons", []) or [])
               if w and getattr(w, "name", "") and w.name != "拳击"]
    if weapons:
        parts.append("武器[" + ",".join(w.name for w in weapons) + "]")

    if hasattr(opp, "armor") and opp.armor:
        try:
            from models.equipment import ArmorLayer
            outer = opp.armor.get_active(ArmorLayer.OUTER)
            inner = opp.armor.get_active(ArmorLayer.INNER)
            if outer:
                parts.append(
                    "外甲[" + ",".join(
                        f"{p.name}({p.attribute.value})" for p in outer
                    ) + "]"
                )
            if inner:
                parts.append(
                    "内甲[" + ",".join(
                        f"{p.name}({p.attribute.value})" for p in inner
                    ) + "]"
                )
        except Exception:
            pass

    if getattr(opp, "kill_count", 0) > 0:
        parts.append(f"击杀{opp.kill_count}")
    if getattr(opp, "is_captain", False):
        parts.append("队长")
    elif getattr(opp, "is_police", False):
        parts.append("警察")
    if getattr(opp, "is_criminal", False):
        parts.append("犯罪")

    talent_str = _talent_public_summary(opp)
    if talent_str:
        parts.append(f"天赋[{talent_str}]")

    return " | ".join(parts)


def _narrate_opponents(player: Any, game_state: Any) -> str:
    lines = ["【对手情报】"]
    has_any = False
    for pid in getattr(game_state, "player_order", []):
        if pid == player.player_id:
            continue
        opp = game_state.get_player(pid)
        if opp is None:
            continue
        has_any = True
        lines.append(_opp_short_line(opp, player.player_id))

    if not has_any:
        return ""
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
#  自身天赋
# ─────────────────────────────────────────────────────────

def _self_talent_detail(talent: Any) -> str:
    """自身天赋的详细中文状态（自由输出 describe_status）"""
    cls = talent.__class__.__name__
    name = _TALENT_DISPLAY_NAME.get(cls, getattr(talent, "name", cls))
    tier = getattr(talent, "tier", "")
    head = f"{tier}天赋-{name}"

    try:
        status = talent.describe_status() if hasattr(talent, "describe_status") else ""
    except Exception:
        status = ""

    if status:
        return f"{head}: {status}"
    return head


def _narrate_self_talent(player: Any) -> str:
    talent = getattr(player, "talent", None)
    if talent is None:
        return ""
    return "【你的天赋】\n" + _self_talent_detail(talent)


# ─────────────────────────────────────────────────────────
#  全局段落（轮次、警察、病毒）
# ─────────────────────────────────────────────────────────

def _narrate_global(player: Any, game_state: Any) -> str:
    lines = ["【全局状态】"]
    rnd = getattr(game_state, "current_round", 0)
    phase = _PHASE_DISPLAY.get(
        getattr(game_state, "current_phase", ""),
        getattr(game_state, "current_phase", "?"),
    )
    lines.append(f"轮次: 第{rnd}轮 | 阶段: {phase}")

    police = getattr(game_state, "police", None)
    if police is not None:
        police_parts = []
        if police.has_captain():
            cap = game_state.get_player(police.captain_id)
            cap_name = cap.name if cap else "未知"
            police_parts.append(f"有队长({cap_name})")
        else:
            police_parts.append("无队长")

        units = getattr(police, "alive_units", lambda: [])()
        if units:
            active_n = sum(1 for u in units
                           if getattr(u, "is_disabled", lambda: False)() is False)
            police_parts.append(f"警察单位 {active_n}/{len(units)}")

        rp = getattr(police, "report_phase", "idle")
        if rp != "idle":
            rp_name = _REPORT_PHASE_DISPLAY.get(rp, rp)
            tgt_id = getattr(police, "reported_target_id", None)
            tgt = game_state.get_player(tgt_id) if tgt_id else None
            tgt_name = tgt.name if tgt else "?"
            police_parts.append(f"举报阶段: {rp_name} | 目标: {tgt_name}")

        auth = getattr(police, "authority", 0)
        if auth:
            police_parts.append(f"威信: {auth}")

        my_crimes = police.crime_records.get(player.player_id, set())
        if my_crimes:
            police_parts.append(f"自身犯罪: {len(my_crimes)}")

        if police_parts:
            lines.append("警察: " + " | ".join(police_parts))

    virus = getattr(game_state, "virus", None)
    if virus is not None:
        if getattr(virus, "is_active", False):
            cd = getattr(virus, "countdown", 0)
            lines.append(f"病毒: 已激活 (倒计时{cd})")
        elif getattr(virus, "countdown", 0) > 0:
            lines.append(f"病毒: 倒计时中({virus.countdown})")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
#  战略评估（高层特征）
# ─────────────────────────────────────────────────────────

def _compute_my_power(player: Any) -> float:
    try:
        from models.equipment import ArmorLayer
    except Exception:
        return 0.0
    real_w = [w for w in (getattr(player, "weapons", []) or [])
              if w and getattr(w, "name", "") != "拳击"]
    outer = len(player.armor.get_active(ArmorLayer.OUTER)) \
        if hasattr(player, "armor") and player.armor else 0
    inner = len(player.armor.get_active(ArmorLayer.INNER)) \
        if hasattr(player, "armor") and player.armor else 0
    return player.hp * 10 + len(real_w) * 15 + outer * 20 + inner * 15


def _narrate_strategy(player: Any, game_state: Any) -> str:
    try:
        from models.equipment import ArmorLayer, WeaponRange
    except Exception:
        return ""

    real_w = [w for w in (getattr(player, "weapons", []) or [])
              if w and getattr(w, "name", "") != "拳击"]
    outer_count = len(player.armor.get_active(ArmorLayer.OUTER)) \
        if hasattr(player, "armor") and player.armor else 0
    inner_count = len(player.armor.get_active(ArmorLayer.INNER)) \
        if hasattr(player, "armor") and player.armor else 0

    dev_score = 0
    if real_w:
        dev_score += 25
    if outer_count > 0:
        dev_score += 25
    if inner_count > 0:
        dev_score += 25
    if getattr(player, "has_detection", False):
        dev_score += 25

    has_ranged = any(
        getattr(w, "weapon_range", None) == WeaponRange.RANGED
        for w in (getattr(player, "weapons", []) or []) if w
    )

    markers = getattr(game_state, "markers", None)
    i_locked_anyone = False
    i_engaged_anyone = False
    locked_by = set()
    engaged_with = set()
    if markers is not None:
        locked_by = set(markers.get_related(player.player_id, "LOCKED_BY"))
        engaged_with = set(markers.get_related(player.player_id, "ENGAGED_WITH"))
        for pid in getattr(game_state, "player_order", []):
            if pid == player.player_id:
                continue
            opp = game_state.get_player(pid)
            if opp is None or not opp.is_alive():
                continue
            if markers.has_relation(opp.player_id, "LOCKED_BY", player.player_id):
                i_locked_anyone = True
            if markers.has_relation(player.player_id, "ENGAGED_WITH", opp.player_id):
                i_engaged_anyone = True

    if i_locked_anyone and has_ranged:
        chain_str = "已锁定且有远程"
    elif i_locked_anyone:
        chain_str = "已锁定目标"
    elif i_engaged_anyone:
        chain_str = "已面对面"
    else:
        chain_str = "未发现目标"

    my_power = _compute_my_power(player)
    max_opp_power = 0.0
    armor_total_opps = 0
    alive_opps = 0
    enemies_here = 0
    my_loc = player.location
    for pid in getattr(game_state, "player_order", []):
        if pid == player.player_id:
            continue
        opp = game_state.get_player(pid)
        if opp is None or not opp.is_alive():
            continue
        alive_opps += 1
        if opp.location == my_loc and my_loc is not None:
            enemies_here += 1
        opp_pow = _compute_my_power(opp)
        max_opp_power = max(max_opp_power, opp_pow)
        if hasattr(opp, "armor") and opp.armor:
            armor_total_opps += (
                len(opp.armor.get_active(ArmorLayer.OUTER))
                + len(opp.armor.get_active(ArmorLayer.INNER))
            )

    avg_opp_armor = (armor_total_opps / alive_opps) if alive_opps else 0
    armor_diff = (outer_count + inner_count) - avg_opp_armor

    threat_ratio = (max_opp_power / my_power) if my_power > 0 else 0
    threat_str = f"{threat_ratio:.1f}倍" if threat_ratio else "未知"

    is_targeted = bool(locked_by or engaged_with)

    has_mask = "防毒面具" in {item.name for item in (getattr(player, "items", []) or [])}
    virus = getattr(game_state, "virus", None)
    virus_active = bool(virus and getattr(virus, "is_active", False))
    if virus_active and not has_mask:
        virus_str = "高（无面具）"
    elif virus_active:
        virus_str = "低（已有面具）"
    else:
        virus_str = "无"

    lines = ["【战略评估】"]
    lines.append(
        f"发育完成度: {dev_score}% | 击杀链: {chain_str} | "
        f"对手最强者是你的{threat_str}"
    )
    lines.append(
        f"护甲优势: {armor_diff:+.1f}（高于对手平均） | "
        f"同地点敌人: {enemies_here} | 被瞄准: {'是' if is_targeted else '否'}"
    )
    if virus_str != "无":
        lines.append(f"病毒威胁: {virus_str}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
#  可用行动白名单
# ─────────────────────────────────────────────────────────

def _narrate_actions(player: Any, game_state: Any) -> str:
    try:
        from actions.action_registry import get_available_actions
    except Exception:
        return ""

    try:
        actions = get_available_actions(player, game_state) or []
    except Exception:
        return ""

    if not actions:
        return ""

    lines = ["【当前可用行动】"]
    parts = []
    for act in actions:
        name = act.get("name", "")
        desc = act.get("description", "")
        if desc:
            parts.append(f"{name}({desc})")
        else:
            parts.append(name)
    lines.append(", ".join(parts))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
#  主入口
# ─────────────────────────────────────────────────────────

def narrate_state(player: Any, game_state: Any, controller: Any = None) -> str:
    """将游戏状态翻译为简洁的中文段落文本。

    参数：
        player: 当前玩家对象
        game_state: GameState 实例
        controller: BasicAIController 实例（可选，目前不直接使用，
                    保留接口以便后续扩展）
    返回：
        多段拼接的中文字符串。空段会被自动跳过。
    """
    if player is None or game_state is None:
        return ""

    sections = []

    try:
        sections.append(_narrate_self(player))
    except Exception:
        pass

    try:
        opp = _narrate_opponents(player, game_state)
        if opp:
            sections.append(opp)
    except Exception:
        pass

    try:
        tlt = _narrate_self_talent(player)
        if tlt:
            sections.append(tlt)
    except Exception:
        pass

    try:
        glo = _narrate_global(player, game_state)
        if glo:
            sections.append(glo)
    except Exception:
        pass

    try:
        strat = _narrate_strategy(player, game_state)
        if strat:
            sections.append(strat)
    except Exception:
        pass

    try:
        acts = _narrate_actions(player, game_state)
        if acts:
            sections.append(acts)
    except Exception:
        pass

    return "\n\n".join(s for s in sections if s)
