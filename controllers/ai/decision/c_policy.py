"""C 层简单版：choose 目标选择启发式（按 situation / 裸提示分派）。

定位（2026-08-13 设计裁决）：
- 引擎已把**合法性门**做完（options 里全是合法选择），本层只做"选哪个"——
  威胁最高目标 / 威胁密度最高地点 / 按人格的整备方式等最小启发式，
  替代 options[0] 的选择偏差；不写长周期规划（G5 锚定真策略本体等留后续）。
- 接入顺序（controller.choose）：m9_decide_choose（通用决策面）→
  c_decide_choose（本层）→ v2exp hook → ChooseMixin 旧层。
- 只处理"当前路径明确是 options[0]/随机"的接点；hook 已有良好逻辑的
  （oneslash_pick_weapon、hexagram_my/opp_choice、hoshino_*、G3 m9_g3 菜单）
  一律放行（返回 None）。
"""
from __future__ import annotations

from typing import Any, List, Optional

from engine.m9.text import m9_text

# ── 威胁最高目标选择的 situation 键（options = 玩家名列表）──
_TARGET_SITUATIONS = (
    "t3_stars_bounce_target",
    "t2_core_target",
    "oneslash_pick_target",
    "hexagram_thunder_target",
    "hexagram_steal_target",
    "hexagram_disarm_target",
    "g4_strike_pick_target",
)

# ── 保守跳过类（options 含"不指定"则选它）──
_SKIP_KEYWORDS = (
    m9_text("ai.c_policy.options.not_specified"),
    m9_text("ai.c_policy.options.skip"),
    m9_text("ai.c_policy.options.give_up"),
)


def _threat_scores(controller: Any) -> dict:
    return getattr(controller, "_threat_scores", None) or {}


def _pick_max_threat(options: List[str], controller: Any) -> str:
    threats = _threat_scores(controller)
    return max(options, key=lambda n: threats.get(n, 0.0))


def _pick_poem_for_top_threat(controller: Any, state: Any,
                              options: List[str]) -> str:
    """献诗：按最高威胁对手的天赋槽位选对应诗篇（POEM_TARGETS 逆映射）。"""
    try:
        from engine.m9.talents.poems import POEM_TARGETS
        slot_to_poem = {v: k for k, v in POEM_TARGETS.items()}
    except Exception:
        return options[0]
    threats = _threat_scores(controller)
    best_pid = None
    best_t = -1.0
    for pid in getattr(state, "player_order", []):
        p = state.get_player(pid)
        if p is None or not p.is_alive():
            continue
        t = threats.get(getattr(p, "name", ""), 0.0)
        if t > best_t:
            best_t = t
            best_pid = pid
    if best_pid is None:
        return options[0]
    p = state.get_player(best_pid)
    talent = getattr(p, "talent", None)
    try:
        from controllers.ai.decision.snapshot import _slot_id_for
        slot = _slot_id_for(talent)
    except Exception:
        slot = ""
    poem = slot_to_poem.get(slot)
    if poem is not None:
        for opt in options:
            if poem in str(opt):
                return opt
    return options[0]


def _shadow_action(player: Any, state: Any, options: List[str]) -> str:
    """G2 影身标准槽：同地点有敌人且持武器 → 攻击；否则不行动。"""
    if "attack" not in options:
        return "forfeit" if "forfeit" in options else options[0]
    weapons = getattr(player, "weapons", []) or []
    if not weapons:
        return "forfeit" if "forfeit" in options else options[0]
    my_loc = getattr(player, "location", None)
    for pid in getattr(state, "player_order", []):
        other = state.get_player(pid)
        if other is None or not other.is_alive():
            continue
        if pid == getattr(player, "owner_pid", None):
            continue
        if getattr(other, "location", None) == my_loc:
            return "attack"
    return "forfeit" if "forfeit" in options else options[0]


def _g1_declaration(player: Any, state: Any, options: List[str]) -> str:
    """G1 次级形态：只在真正拿到公演位且战斗窗口合适时完全燃烧。

    “卸甲宣言”免费，但与“完全燃烧”共用同一个内部菜单；不能依赖
    ``options[0]``，否则 AI 会在没有公演位时反复尝试公演，并在高失熵时
    错过唯一的卸甲出口。
    """
    unload = next(
        (opt for opt in options
         if m9_text("ai.c_policy.options.unarmor") in str(opt)), options[-1])
    full_burn = next(
        (opt for opt in options
         if m9_text("ai.c_policy.options.full_burn") in str(opt)), None)
    if full_burn is None or player is None or state is None:
        return unload

    talent = getattr(player, "talent", None)
    entropy = float(getattr(talent, "entropy", 0.0) or 0.0)
    m9 = getattr(state, "m9_system", None)
    # 满燃门与 t0_policy 同步为 entropy≤4；阈值 6、次级每轮 +2。
    if m9 is None or m9.get_sp(player.player_id) < 2:
        return unload

    round_num = int(getattr(state, "current_round", 0) or 0)
    holders = getattr(m9, "_public_holder_by_round", {}) or {}
    if holders.get(round_num) != player.player_id:
        return unload

    arc = getattr(state, "m9_arc", None)
    has_debut = bool(arc is not None and hasattr(arc, "has_debut")
                     and arc.has_debut(player.player_id))
    # 与 t0_policy 的满燃门同步：失熵阈值 6、次级每轮 +2，entropy≤4 仍能
    # 覆盖完整窗口；旧 ≤2 门把正常对局中的完全燃烧锁死。
    if entropy > 4 and has_debut:
        return unload

    # 完全燃烧并不要求近战；M9 全员起始弓使跨地点战斗窗口同样真实。
    # 把“同地点敌人”写成门会让公演位与次级形态很难在同一轮偶合。
    has_live_enemy = any(
        other is not None
        and other.is_alive()
        and pid != player.player_id
        for pid in getattr(state, "player_order", [])
        for other in (state.get_player(pid),)
    )
    has_weapon = any(weapon is not None for weapon in getattr(player, "weapons", []))
    return full_burn if has_live_enemy and has_weapon else unload


def c_decide_choose(controller: Any, prompt: str, options: List[str],
                    context: Optional[dict], state: Any) -> Optional[str]:
    """C 层 choose 启发式入口。返回 None = 放行旧层。"""
    if not options:
        return None
    situation = (context or {}).get("situation", "")
    prompt = str(prompt)
    personality = str(getattr(controller, "personality", "balanced")
                      or "balanced")
    improvise = m9_text("ai.c_policy.options.improvise")
    public = m9_text("ai.c_policy.options.public")
    poem = m9_text("ai.c_policy.options.poem")
    anchor = m9_text("ai.c_policy.options.anchor")
    ripple = m9_text("ai.c_policy.options.ripple")

    # 1. 目标选择：威胁最高（T3 弹射/T2 核心/T1 追猎/T7 复活/六爻三目标/G4 人形态）
    if situation in _TARGET_SITUATIONS:
        return _pick_max_threat(list(options), controller)

    # 1b. G4 人形态演出武器：优先伤害最高（磨刀小刀/高斯等）。
    if situation == "g4_strike_pick_weapon":
        player = getattr(controller, "_player", None)
        best_name = None
        best_dmg = -1.0
        for w in getattr(player, "weapons", []) or []:
            if w is None or getattr(w, "name", "?") not in options:
                continue
            dmg = float(getattr(w, "base_damage", 0) or 0)
            if getattr(w, "is_charged", False):
                dmg = float(getattr(w, "charged_damage", dmg) or dmg)
            if dmg > best_dmg:
                best_dmg = dmg
                best_name = getattr(w, "name", "?")
        return best_name if best_name is not None else options[0]

    # T7 是自由混战中的一次性保险；复活最高威胁对手是负收益。没有明确队伍
    # 事实时稳定挂自己，才是对自身胜率的占优选择。
    if situation == "resurrection_pick_target":
        player = getattr(controller, "_player", None)
        own_name = getattr(player, "name", None)
        if own_name in options:
            return own_name
        return options[0]

    # 2. G5 献诗：按最高威胁对手槽位选诗
    if prompt.startswith(m9_text("ai.c_policy.prompts.choose_poem")):
        return _pick_poem_for_top_threat(controller, state, list(options))

    # G5 德谬歌：有公演位且追忆足以献诗时优先把高价值预算
    # 用掉；无公演位时只用微澜，不发起必定失败的锚定。
    if (prompt.startswith(m9_text("ai.c_policy.prompts.choose_performance"))
            and getattr(getattr(getattr(controller, "_player", None),
                                "talent", None), "slot_id", "") == "G5"):
        player = getattr(controller, "_player", None)
        talent = getattr(player, "talent", None) if player is not None else None
        m9 = getattr(state, "m9_system", None) if state is not None else None
        round_num = int(getattr(state, "current_round", 0) or 0)
        holder = (getattr(m9, "_public_holder_by_round", {}) or {}).get(
            round_num) if m9 is not None else None
        if player is None or holder != getattr(player, "player_id", None):
            return ripple if ripple in options else options[0]
        try:
            from engine.balance import get as bget
            poem_cost = float(bget(
                "m9_talents_extended", "g5", "poem_cost", default=12))
            anchor_min_k = float(bget(
                "m9_talents_extended", "g5", "anchor_min_k", default=4))
        except Exception:
            poem_cost = 12.0
            anchor_min_k = 4.0
        sealed = float(getattr(talent, "sealed_reminiscence", 0) or 0)
        # 献诗必须给锚定留保底预算，否则 sealed 12~15 时献完诗只能空转，
        # 追忆 <anchor_min_k 的德谬歌会在下个 R0 被强制退场。
        if poem in options and sealed >= poem_cost + anchor_min_k:
            return poem
        if anchor in options:
            return anchor
        return ripple if ripple in options else options[0]

    # 3. G2 影身标准槽
    if m9_text("ai.c_policy.prompts.shadow_action") in prompt:
        return _shadow_action(getattr(controller, "_player", None),
                              state, list(options))

    # 3.5 G1 次级燃烧宣言：高失熵/无公演位/无交战窗口时主动卸甲。
    if prompt.startswith(m9_text("ai.c_policy.prompts.firefly_declaration")):
        return _g1_declaration(
            getattr(controller, "_player", None), state, list(options))

    # 完全燃烧受限追加只有 move/attack。仅在存在真实合法攻击组合时选
    # attack；否则移动。具体目标/武器由同一合法动作目录继续选择。
    if prompt.startswith(m9_text("ai.c_policy.prompts.restricted_followup")):
        player = getattr(controller, "_player", None)
        if player is not None and state is not None and "attack" in options:
            from engine.action_enumerator import build_action_options
            legal = build_action_options(player, state, ["attack"])
            if legal.get("attack"):
                return "attack"
        return "move" if "move" in options else options[0]

    # 3.6 G3 投影子菜单：有合法目标先打（远程锁定→螺旋剑、近战面对面→
    # 双刀攻势）；无目标时建立七重圆环/复制武器，避免默认螺旋剑空转。
    if prompt.startswith(m9_text("ai.c_policy.prompts.choose_projection")):
        player = getattr(controller, "_player", None)
        talent = getattr(player, "talent", None) if player is not None else None
        if talent is not None:
            try:
                if talent._legal_targets(player, ranged=True):
                    for opt in options:
                        if m9_text("ai.c_policy.options.spiral_sword") in str(opt):
                            return opt
                if talent._legal_targets(player, ranged=False):
                    for opt in options:
                        if m9_text("ai.c_policy.options.dual_blades") in str(opt) \
                                and m9_text("ai.c_policy.options.assault") in str(opt):
                            return opt
            except Exception:
                pass
            if not getattr(talent, "rho_aias", None):
                for opt in options:
                    if m9_text("ai.c_policy.options.rho_aias") in str(opt):
                        return opt
            if not getattr(talent, "copy_weapon", None):
                for opt in options:
                    if m9_text("ai.c_policy.options.copy_weapon") in str(opt):
                        return opt
        return options[0]

    # 4. G6 即演重演：已经取得唯一公演位时必须兑现公演；否则偏好低成本即演。
    # 引擎这里传的是内部稳定键（attack / t3_heavenly_star），不是中文展示名。
    if prompt == m9_text("ai.c_policy.prompts.g6_performance"):
        player = getattr(controller, "_player", None)
        m9 = getattr(state, "m9_system", None) if state is not None else None
        round_num = int(getattr(state, "current_round", 0) or 0)
        holder = (getattr(m9, "_public_holder_by_round", {}) or {}).get(
            round_num) if m9 is not None else None
        if (player is not None
                and holder == getattr(player, "player_id", None)):
            for opt in options:
                if public in str(opt):
                    return opt
        for opt in options:
            if improvise in opt:
                return opt
        return options[0]
    if m9_text("ai.c_policy.prompts.choose_replay_category") in prompt:
        for opt in options:
            if str(opt) == "attack" \
                    or m9_text("ai.c_policy.options.attack") in str(opt):
                return opt
        return options[0]
    if m9_text("ai.c_policy.prompts.choose_borrow_target") in prompt:
        return _pick_max_threat(list(options), controller)
    if m9_text("ai.c_policy.prompts.choose_borrow_core") in prompt:
        # 借用核心选择：强武器在手 → 一刀缭断（×2.5 倍率单点爆发）；
        # 否则同地点有对手 → 天星（地点 AOE+石化）；否则六爻（不依赖装备）。
        player = getattr(controller, "_player", None)
        best_dmg = 0.0
        if player is not None:
            for w in getattr(player, "weapons", []) or []:
                if w and getattr(w, "name", "") != "拳击":
                    best_dmg = max(best_dmg, float(getattr(
                        w, "base_damage",
                        getattr(w, "get_effective_damage",
                                lambda: 0)()) or 0))
        if best_dmg >= 6:
            for opt in options:
                if (str(opt) == "t1_one_slash"
                        or m9_text("ai.c_policy.options.one_slash") in str(opt)
                        or m9_text("ai.c_policy.options.ranger") in str(opt)):
                    return opt
        my_loc = getattr(player, "location", None) if player is not None else None
        has_nearby = False
        if my_loc is not None and state is not None:
            has_nearby = any(
                other is not None and other.is_alive()
                and getattr(other, "location", None) == my_loc
                and getattr(other, "player_id", None) != getattr(
                    player, "player_id", None)
                for pid in getattr(state, "player_order", [])
                for other in (state.get_player(pid),)
            )
        if has_nearby:
            for opt in options:
                if (str(opt) == "t3_heavenly_star"
                        or m9_text("ai.c_policy.options.heavenly_star") in str(opt)
                        or m9_text("ai.c_policy.options.stars") in str(opt)):
                    return opt
        if best_dmg > 0:
            for opt in options:
                if (str(opt) == "t1_one_slash"
                        or m9_text("ai.c_policy.options.one_slash") in str(opt)
                        or m9_text("ai.c_policy.options.ranger") in str(opt)):
                    return opt
        for opt in options:
            if str(opt) == "t4_hexagram" \
                    or m9_text("ai.c_policy.options.hexagram") in str(opt):
                return opt
        return options[0]

    # G0：有遗物时优先兑现永久资源；无遗物才使用十字炮火。
    if prompt.startswith(m9_text("ai.c_policy.prompts.g0_public")):
        player = getattr(controller, "_player", None)
        talent = getattr(player, "talent", None) if player is not None else None
        if getattr(talent, "relics", None):
            for opt in options:
                if m9_text("ai.c_policy.options.relic") in str(opt) \
                        or m9_text("ai.c_policy.options.support") in str(opt):
                    return opt
        for opt in options:
            if m9_text("ai.c_policy.options.cross") in str(opt):
                return opt
        return options[0]

    # 5. T6 联防整备：未登台且持公演位 → 公演换第一章；否则即演。
    if m9_text("ai.c_policy.prompts.choose_t6_performance") in prompt:
        player = getattr(controller, "_player", None)
        m9 = getattr(state, "m9_system", None) if state is not None else None
        round_num = int(getattr(state, "current_round", 0) or 0)
        holder = (getattr(m9, "_public_holder_by_round", {}) or {}).get(
            round_num) if m9 is not None else None
        arc = getattr(state, "m9_arc", None)
        has_debut = bool(
            arc is not None and hasattr(arc, "has_debut")
            and arc.has_debut(getattr(player, "player_id", "")))
        if (player is not None and holder == getattr(player, "player_id", None)
                and m9 is not None and m9.get_sp(player.player_id) >= 2
                and not has_debut):
            for opt in options:
                if public in str(opt):
                    return opt
        for opt in options:
            if improvise in str(opt):
                return opt
        return options[0]
    if m9_text("ai.c_policy.prompts.choose_t6_slot") in prompt:
        if personality in ("defensive", "builder"):
            for opt in options:
                if m9_text("ai.c_policy.options.equip_shield") in str(opt) \
                        or m9_text("ai.c_policy.options.equip_armor") in str(opt) \
                        or m9_text("ai.c_policy.options.equip_field") in str(opt):
                    return opt
        return options[0]

    # 6. 天机/卦象指定：保守跳过；没有跳过项时优先“正常出拳”，
    #    不要固定指定潜龙勿用白烧天机+2 SP。
    if situation == "hexagram_tianji" \
            or m9_text("ai.c_policy.prompts.tianji") in prompt \
            or m9_text("ai.c_policy.prompts.specify_hexagram") in prompt:
        for opt in options:
            if any(k in str(opt) for k in _SKIP_KEYWORDS):
                return opt
        for opt in options:
            if m9_text("ai.c_policy.options.normal_rps") in str(opt):
                return opt
        return options[0]

    return None


def anchor_script(player: Any, state: Any) -> List[tuple]:
    """G5 锚定脚本（真实预言启发）：可落空预言（攻击/拾取）+ move 槽垫至 K。

    委托 ``engine.m9.talents.g5.build_anchor_fallback_script`` 与引擎兜底保持同一
    语义（张力规则要求脚本必须含可能落空的预言）；构造不出可落空预言时返回
    空列表，调用方（T0 决策）据此放弃锚定。
    """
    try:
        from engine.m9.talents.g5 import build_anchor_fallback_script
        return build_anchor_fallback_script(player, state)
    except Exception:
        return []
