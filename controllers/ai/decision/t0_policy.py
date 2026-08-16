"""M9 通用决策面：T0 发动 / R0 公演报名 / 石化挣脱 / 焚诏拉条（表驱动）。

分层（2026-08-13 设计裁决）：
- 引擎已把**合法性前置门**过滤完（get_t0_option 非空即合法、R0 报名只在
  SP≥2 且资格满足时询问、石化挣脱只有 1 SP/次 预算检查）——本层只做
  **"是否值得"** 的预算/守卫/人格决策，不写每槽专属策略本体（C 层）。
- 全部按 **slot_id** 分派（`_slot_id_for`），不依赖显示名；v2exp profile
  不经过本层（保持旧显示名匹配行为）。
- 每个判定都是纯函数式表驱动，单元测试直接断言；controller.choose 只做
  转发（`m9_decide_choose`）。

覆盖的引擎 choose 表面（信源：engine/action_turn.py / round_manager.py / g4.py）：
1. talent_t0：options=["发动天赋","不发动，正常行动"]（situation="talent_t0"）
2. R0 公演报名：options=["保留","报名公演"]（phase="M9_PUBLIC_REGISTRATION"，
   无 situation 键，但 context 带 game_state/player）
3. M9 石化：options=["保持石化（跳过本槽，不获SP）","尝试挣脱（1 SP/次，50%）"]
   + 裸提示 "是否再尝试一次？" ["继续尝试","放弃（本槽收尾）"]
4. G4 焚诏拉条：裸提示 "焚诏拉条：…选择攻击或拒战？" ["攻击","拒战"]
"""
from __future__ import annotations

from typing import Any, List, Optional

from controllers.ai.decision.snapshot import _slot_id_for
from engine.m9.text import m9_text

# ── 人格分组 ──
_SPENDERS = ("aggressive", "assassin")          # 花光最后 1 SP 也发动
_HOLDERS = ("defensive", "builder")             # SP=1 时保留
_NEUTRAL = ("balanced", "political")

# ── 公演位价值分档（R0 报名；引擎已保证 SP≥2）──
_PUBLIC_REQUIRED = {"T3", "G3"}     # 唯一演出入口是公演（T3 无即演 / G3 展开须公演位）
_PUBLIC_HEAVY = {"G4", "G5", "G6", "G7"}  # 公演价值显著

# ── T0 守卫表：slot_id → 额外守卫条件（引擎门之外的"值不值"判断）──
_T0_HP_COST_SLOTS = {"G0"}          # 即演/公演均付 20% 当前 HP 成本

_PETRIFY_HOLD_PERSONALITIES = ("defensive", "builder")


def _g5_anchor_min_k() -> float:
    from engine.balance import get as bget
    return float(bget(
        "m9_talents_extended", "g5", "anchor_min_k", default=3))


def _sp_of(state: Any, player: Any) -> int:
    m9 = getattr(state, "m9_system", None)
    if m9 is None:
        return 0
    return int(m9.get_sp(getattr(player, "player_id", "")))


def _has_debut(state: Any, player: Any) -> bool:
    """三章制完结条：第一章·登台是否已点亮（arc RFC v0.1）。"""
    arc = getattr(state, "m9_arc", None)
    if arc is None:
        return False
    try:
        return bool(arc.has_debut(getattr(player, "player_id", "")))
    except Exception:
        return False


def _same_location_threat(state: Any, player: Any) -> bool:
    """同地点是否有存活对手（石化挣脱的价值判断用）。"""
    my_loc = getattr(player, "location", None)
    if not my_loc:
        return False
    for pid in getattr(state, "player_order", []):
        if pid == getattr(player, "player_id", None):
            continue
        other = state.get_player(pid)
        if other is None or not other.is_alive():
            continue
        if getattr(other, "location", None) == my_loc:
            return True
    return False


def _g3_has_capture_target(state: Any, player: Any) -> bool:
    """G3 公演报名价值门：当前地点至少有一个会被真实捕捉的存活玩家。"""
    if state is None or player is None:
        return False
    my_id = getattr(player, "player_id", None)
    my_loc = getattr(player, "location", None)
    if not my_loc:
        return False
    for pid in getattr(state, "player_order", []):
        if pid == my_id:
            continue
        other = state.get_player(pid)
        if (other is not None and other.is_alive()
                and getattr(other, "location", None) == my_loc):
            return True
    return False


def _g3_collapse_base(talent: Any) -> int:
    """G3 幻想崩坏的结构伤害（不含命中/护甲修正）。

    与 engine/m9/talents/g3.py `_collapse` 同一公式：基础值 + 式样数
    （受 cap 截断）+ 剑阵·崩坏准备加成。只用只读属性，异常一律 0。
    """
    try:
        styles = len(getattr(talent, "ideal_styles", set()) or set())
        base = int(getattr(talent, "collapse_base_damage", 5))
        base += int(getattr(talent, "collapse_per_style", 2)) * min(
            styles, int(getattr(talent, "collapse_style_cap", 5)))
        array = getattr(talent, "sword_array", None) or {}
        if array.get("function") == "collapse_prep":
            base += int(getattr(talent, "sword_array_collapse_bonus", 2))
        return base
    except Exception:
        return 0


def _g3_collapse_lethal(talent: Any, state: Any) -> bool:
    """G3 幻想崩坏可击杀性：结构伤害 ≥ 主目标有效承伤。

    保守口径：外甲按 2 点承伤/件、内甲按 1 点承伤/件估算。只用于
    “值不值得清空魔力 + 解除结界”的时机门，不修改引擎结算。
    """
    try:
        target_pid = getattr(talent, "main_target", None)
        if not target_pid:
            return False
        target = state.get_player(target_pid)
        if target is None or not target.is_alive():
            return False
        from controllers.ai.game_query import GameQuery
        soak = float(getattr(target, "hp", 0) or 0)
        soak += 2.0 * GameQuery.count_outer_armor(target)
        soak += float(GameQuery.count_inner_armor(target))
        return float(_g3_collapse_base(talent)) >= soak
    except Exception:
        return False


def _g2_terminal_commit_pending(player: Any) -> bool:
    """G2：已有影身且未交终曲 → 本次 T0 是终曲承诺（不可逆）。"""
    talent = getattr(player, "talent", None)
    if talent is None:
        return False
    try:
        sh = talent._shadow()
    except Exception:
        return False
    return sh is not None and not getattr(sh, "is_terminal_singer", False)


def _g2_terminal_threshold(state: Any) -> int:
    """G2 终曲承诺的最早轮次：0.35 × min(max_rounds, 40+2×人数)。"""
    max_rounds = getattr(state, "max_rounds", 50) if state is not None else 50
    players = len(getattr(state, "player_order", []) or []) \
        if state is not None else 6
    expected_end = min(max_rounds, 40 + 2 * max(2, players))
    return max(12, int(expected_end * 0.35))


def _g0_public_worthwhile(state: Any, player: Any) -> bool:
    """十字炮火划算性：至少能击杀一人或命中两名敌人。

    注意：本函数只回答“炮火是否划算”；遗物支援技是否值得走公演由调用方
    单独判断，不能把“有遗物”直接等价成“炮火划算”。
    """
    talent = getattr(player, "talent", None)
    if talent is None or getattr(talent, "drone", None) is None:
        return False
    location = getattr(player, "location", None)
    if not location:
        return False
    try:
        from engine.balance import get as bget
        damage = float(bget(
            "m9_talents_extended", "g0", "crossfire_damage", default=3))
    except Exception:
        damage = 3.0
    targets = []
    for pid in getattr(state, "player_order", []):
        if pid == getattr(player, "player_id", None):
            continue
        other = state.get_player(pid)
        if (other is not None and other.is_alive()
                and getattr(other, "location", None) == location):
            targets.append(other)
    return len(targets) >= 2 or any(
        float(getattr(target, "hp", 0) or 0) <= damage for target in targets)


def _is_public_holder(state: Any, player: Any) -> bool:
    m9 = getattr(state, "m9_system", None)
    if m9 is None:
        return False
    round_num = int(getattr(state, "current_round", 0) or 0)
    holders = getattr(m9, "_public_holder_by_round", {}) or {}
    return holders.get(round_num) == getattr(player, "player_id", None)


def should_activate_t0(slot_id: str, sp: int, personality: str,
                       hp: float, max_hp: float,
                       state: Any = None, player: Any = None) -> bool:
    """T0 发动判定（引擎已保证合法性门；此处做预算/守卫/人格决策）。"""
    # G1 次级燃烧的 T0 同时承载“免费卸甲”，但不能每轮无条件开菜单：
    # 无公演位时内部策略只能卸甲，会形成无意义的反复穿脱。仅在真实完全燃烧
    # 窗口、失熵临界或低血线时打开。
    if slot_id == "G1" and player is not None:
        talent = getattr(player, "talent", None)
        form = str(getattr(talent, "form", ""))
        if form == "secondary":
            entropy = float(getattr(talent, "entropy", 0.0) or 0.0)
            # 失熵阈值 6，次级燃烧每轮 +2：entropy≤4 时进满燃仍能覆盖完整
            # 窗口；旧门 ≤2 过窄，实测完全燃烧仅 0.5 次/局。
            burn_window = (sp >= 2 and entropy <= 4
                           and state is not None
                           and _is_public_holder(state, player))
            # 登台激励：未登台时不卡 entropy 门（第一章优先，c_policy 同步放宽）
            if not burn_window and sp >= 2 and state is not None \
                    and _is_public_holder(state, player) \
                    and not _has_debut(state, player):
                burn_window = True
            # 低血线不再强制卸甲：secondary 的攻防修正全面优于 armorless，
            # 低血时没有公演位就维持战甲，而不是反向削弱自己。
            must_unload = entropy >= 4
            return burn_window or must_unload
    # G5 的公演和微澜共用一个 T0 入口。只在当前确有可结算
    # 选项时打开菜单，避免无公演位时反复尝试锚定。
    if slot_id == "G5" and player is not None:
        talent = getattr(player, "talent", None)
        if getattr(talent, "form", "") != "demiurge" or getattr(
                talent, "active_anchor", False):
            return False
        ripple = (sp >= 1
                  and getattr(talent, "ripple_available", lambda: False)()
                  and state is not None
                  and _same_location_threat(state, player))
        sealed = float(getattr(talent, "sealed_reminiscence", 0) or 0)
        public = (sp >= 2 and _is_public_holder(state, player)
                  and sealed >= _g5_anchor_min_k())
        if public:
            # 张力规则：锚定脚本必须含可落空预言，否则引擎拒绝且浪费点击。
            # 构造不出真实预言（无武器可击倒/无地面遗落物可拾）时只保留微澜。
            try:
                from controllers.ai.decision.c_policy import anchor_script
                script = anchor_script(player, state)
            except Exception:
                script = []
            if not script:
                public = False
        return public or ripple
    # G3 神话之外：投影/结界内行动只耗魔力不耗 SP。无公演位时引擎仍会
    # 展示「展开固有结界（公演）」入口，但内部菜单有「投影魔术」——必须
    # 放行，否则投影停摆；SP=0 的结界内行动同样是合法魔力行动。
    if slot_id == "G3" and player is not None and state is not None:
        talent = getattr(player, "talent", None)
        if talent is None:
            return False
        if getattr(talent, "barrier_active", False):
            return True
        return True
    if sp < 1:
        return False
    # T4 六爻：裁决 A 后即演不消费回合（RPS 与普通行动共存，纯额外资源），
    # SP≥1 即值得开菜单（引擎 get_t0_option 已保证有对手可选）；公演仍
    # consume_turn，仅在持有公演位且有天机可指定时由「六爻演出」选择面分流。
    if slot_id == "T4":
        return sp >= 1
    # T1 旧 hook 的磨刀/蓄力门被 M9 通用面短路（审计）：M9 T1 没有次数
    # 限制，SP 才是唯一资源。引擎 get_t0_option 已经保证近战武器 + 合法
    # 目标，所以只要有任意真实近战武器就开 T0；磨刀只是伤害提升，不再是
    # 发动前提。
    if slot_id == "T1" and player is not None and state is not None:
        from models.equipment import WeaponRange
        weapons = [
            w for w in (getattr(player, "weapons", []) or [])
            if w is not None and getattr(w, "name", "") != "拳击"
            and getattr(w, "weapon_range", None) == WeaponRange.MELEE
        ]
        return bool(weapons)
    # T3 天星只有公演入口：无公演位时点击只会失败（审计：7.4 次激活只有
    # 3.42 次真实星落，46% 空转）。
    if slot_id == "T3":
        return (sp >= 2 and state is not None and player is not None
                and _is_public_holder(state, player))
    # G4 焚诏拉条只在完整形态值得开：残缺形态硬开是负收益（时机门）。
    # 人形态：12 火种主动燃尽恒放行；即演/公演需同地点 engaged 目标。
    if slot_id == "G4" and player is not None:
        talent = getattr(player, "talent", None)
        form = str(getattr(talent, "form", ""))
        if form in ("full_savior", "incomplete_savior"):
            return (form == "full_savior" and sp >= 2
                    and state is not None
                    and _is_public_holder(state, player))
        # 12 火种 + 负世解锁：主动燃尽不需要 engaged 目标（审计发现的误阻断）。
        if (int(getattr(talent, "divinity", 0) or 0) >= 12
                and getattr(talent, "m9_burden_unlocked", False)):
            return True
        if sp < 1 or state is None:
            return False
        markers = getattr(state, "markers", None)
        if markers is None:
            return False
        my_loc = getattr(player, "location", None)
        for eid in markers.get_related(
                getattr(player, "player_id", ""), "ENGAGED_WITH"):
            actor = None
            if hasattr(state, "get_actor"):
                actor = state.get_actor(eid)
            if actor is None:
                actor = state.get_player(eid)
            if actor is not None and actor.is_alive() \
                    and getattr(actor, "location", None) == my_loc:
                return True
        return False
    # G6 插入式笑话：
    # - 持公演位 + SP≥2 → 公演（借用核心；无核心时 C 层走召唤援助）；
    # - 否则只有窗口内能重演 attack 才花 1 SP 即演（避免 move/find
    #   即演把 SP 打光、arc/公演节奏瘫痪——R17 教训）。
    if slot_id == "G6":
        if sp < 1 or state is None or player is None:
            return False
        if sp >= 2 and _is_public_holder(state, player):
            return True
        try:
            from engine.m9.talents.g6 import G6Mechanics
            talent = getattr(player, "talent", None)
            joy = bool(getattr(talent, "joy_extend", False)) \
                if talent is not None else False
            mech = G6Mechanics(getattr(state, "g6_template_pool", None))
            cats = mech.improvise_legal_categories(
                player, int(getattr(state, "current_round", 0) or 0),
                state, joy_extended=joy)
            return "attack" in cats
        except Exception:
            return False
    # HP 成本门（G0 自残类）：残血不发动（20% 当前 HP 成本）
    if slot_id in _T0_HP_COST_SLOTS and hp < 10:
        return False
    # G0 首次召唤是后续协同攻击、公演和遗物循环的共同前置；合法血线下
    # 不应因 defensive/builder 的“保留最后 1 SP”通用偏好而整局漏掉入口。
    # 但无脑召唤（SP=1 即招）会让无人机在 3 tick 内过期而从未开火：
    # 只在 SP≥2（召唤后留 1 SP，次轮回 2 可接炮火链）且血线允许时开。
    if slot_id == "G0" and player is not None:
        if getattr(getattr(player, "talent", None), "drone", None) is None:
            return sp >= 2 and hp >= 10
        return (sp >= 2 and state is not None
                and _is_public_holder(state, player)
                and _g0_public_worthwhile(state, player))
    # G1 卸甲常态的失熵守卫：接近 R4 结算阈值（6）时不再重新着装。
    if slot_id == "G1" and player is not None:
        talent = getattr(player, "talent", None)
        entropy = float(getattr(talent, "entropy", 0.0) or 0.0)
        if entropy >= 4:
            return False
    # G2 终曲承诺：不可逆，非中后期不交。旧公式用 max_rounds=300 做分母
    # （阈值 120 轮）而 6 人局平均 40 轮，终曲实际不可达。R60 尝试提前时
    # 终曲收益弱且 G7 顶部污染；R68 起终曲 arc 2/无易伤/共享减半后重试
    # 期望局长门槛：threshold = 0.35 × min(max_rounds, 40+2×人数)。
    if slot_id == "G2" and player is not None and _g2_terminal_commit_pending(player):
        round_num = getattr(state, "current_round", 0) if state is not None else 0
        threshold = _g2_terminal_threshold(state)
        if round_num < threshold:
            return False
        # 终曲承诺只能走公演；没有本轮公演位时点击只会失败并污染风洞。
        if state is None or not _is_public_holder(state, player):
            return False
    # T7 保险是死亡前挂载的伏笔。首轮观察一轮目标威胁，R2 起及时挂载；
    # 不能等到有人已经死亡才决定，否则一次性机制通常永远不会发动。
    # 登台激励（arc RFC v0.1）：未登台且 SP<2 时，低风险窗口先蓄势到
    # SP2 走公演挂载（第一章）；R5 或危险状态放弃等待、立即即演。
    if slot_id == "T7" and state is not None and player is not None:
        round_num = int(getattr(state, "current_round", 0) or 0)
        if round_num < 2 and hp > max_hp * 0.5:
            return False
        if (sp < 2 and not _has_debut(state, player)
                and round_num < 5 and hp > max_hp * 0.5):
            return False
        return True
    # T6 联防整备：未登台时保留 SP1 等关注事件把 SP 顶到 2 走公演
    # （审计：political T6 逢 SP1 即演，导致 SP 永远 0/1 振荡、公演
    # 报名 0.52、arc 0.38）；R6 后或已登台则不再等待。
    if slot_id == "T6" and state is not None and player is not None:
        round_num = int(getattr(state, "current_round", 0) or 0)
        if sp >= 2:
            return True
        if sp == 1 and not _has_debut(state, player) and round_num < 6:
            return False
        return True
    # G7 在 SP=2 时引擎展示的是公演入口；没有本轮公演位时点击只会失败。
    # SP=1 的“小准备”本身 consumes_turn，只是下轮 Cost +1 的豁免，SP 终值
    # 被打到 0.02，属于系统性漏回合，不再发动。
    # 审计追加：战术未解锁时公演补给=死库存，不花 2 SP。
    if slot_id == "G7":
        talent = getattr(player, "talent", None)
        if talent is not None and not getattr(talent, "tactical_unlocked", False):
            return False
        return sp >= 2 and state is not None and player is not None \
            and _is_public_holder(state, player)
    # 花光最后 1 SP 的取舍（人格差异）
    if sp == 1 and personality in _HOLDERS:
        return False
    return True


def should_register_public(slot_id: str, sp: int, personality: str,
                           state: Any = None, player: Any = None) -> bool:
    """R0 公演报名判定（引擎已保证 SP≥2 且资格满足）。"""
    if sp < 2:
        return False
    # T4 无天机时内部只会即演，公演位等于白占：唯一公演出口被挤死。
    # 只有持有阴阳诗天机（公演可指定卦象）才报名。
    if slot_id == "T4" and player is not None:
        talent = getattr(player, "talent", None)
        tianji = bool(getattr(talent, "m9_poem_markers", {}).get(
            "yin_yang_tianji", 0) if talent is not None else False)
        return tianji
    # T6 公演只服务第一章登台；登台后内部选择面恒走即演，
    # 继续报名只会占着唯一公演位不消费（审计发现）。
    if slot_id == "T6" and state is not None and player is not None:
        return not _has_debut(state, player)
    # G3 的公演位只服务于结界展开。没有可捕捉对象时仍可在 T0 走普通
    # 投影，但不应占用唯一公演位并反复建立空结界。
    if slot_id == "G3" and state is not None and player is not None:
        return _g3_has_capture_target(state, player)
    # G2：创建影身需要公演位；已有影身时只有终曲窗口临近才继续报名，
    # 避免前期占着唯一公演位又因门槛拒绝使用（审计：浪费公演位）。
    if slot_id == "G2" and player is not None:
        talent = getattr(player, "talent", None)
        try:
            shadow = talent._shadow() if talent is not None else None
        except Exception:
            shadow = None
        if shadow is not None and getattr(shadow, "is_terminal_singer", False):
            return False
        if shadow is not None and state is not None:
            round_num = int(getattr(state, "current_round", 0) or 0)
            if round_num < _g2_terminal_threshold(state) - 3:
                return False
        return True
    # G4：人形态演出与完整形态焚诏都要公演位；登记后首章也不能停。
    if slot_id == "G4" and player is not None:
        talent = getattr(player, "talent", None)
        form = str(getattr(talent, "form", ""))
        if form in ("full_savior", "incomplete_savior"):
            return int(getattr(talent, "ruin_damage", 0) or 0) > 0
        return True
    # G5 的保留型人格也需要报名；否则 T0 只会看到一个永远
    # 失败的公演菜单。这只修正资源/合法性认知，不改人格选天赋偏好。
    if slot_id == "G5" and state is not None and player is not None:
        talent = getattr(player, "talent", None)
        return (getattr(talent, "form", "") == "demiurge"
                and not getattr(talent, "active_anchor", False)
                and float(getattr(talent, "sealed_reminiscence", 0) or 0)
                >= _g5_anchor_min_k())
    # G7 的补给价值以战术解锁为前提；未解锁前报名只会把 2 SP 换成死库存。
    if slot_id == "G7" and player is not None:
        talent = getattr(player, "talent", None)
        return bool(talent is not None
                    and getattr(talent, "tactical_unlocked", False))
    if slot_id == "G0" and state is not None and player is not None:
        max_hp = float(getattr(player, "max_hp", 20) or 20)
        talent = getattr(player, "talent", None)
        # 公演计划：炮火划算（≥2 同点敌人/可击杀）、或无人机在场（炮火链）、
        # 或持有遗物（遗物支援技）
        plan = (_g0_public_worthwhile(state, player)
                or (talent is not None
                    and getattr(talent, "drone", None) is not None)
                or (talent is not None and bool(getattr(talent, "relics", []))))
        return (float(getattr(player, "hp", 0) or 0) >= max(10.0, max_hp * 0.5)
                and plan)
    # G1 完全燃烧必须在“仍处于次级形态”时提前报名；其他形态没有公演位
    # 的使用价值（着装是即演宣言），不报名（含登台激励——第一章等穿甲后再演）。
    if slot_id == "G1" and player is not None:
        talent = getattr(player, "talent", None)
        entropy = float(getattr(talent, "entropy", 0.0) or 0.0)
        if getattr(talent, "form", "") == "secondary" and entropy <= 4:
            return True
        return False
    # 登台优先（arc RFC v0.1）：未点亮第一章的 AI 在引擎资格门通过后
    # 主动报名一次公演——T7/T6 等低频公演槽位也因此获得一个登台理由。
    # 结构无收益的槽位（G3 空结界、G5 无脚本、G0 低血线）已被上方专属门拦下。
    if not _has_debut(state, player):
        return True
    if slot_id in _PUBLIC_REQUIRED:
        return True
    if slot_id in _PUBLIC_HEAVY:
        return personality not in _HOLDERS
    return personality in _SPENDERS + ("political",)


def should_attempt_breakout(sp: int, personality: str,
                            threat_near: bool) -> bool:
    """M9 石化挣脱判定（1 SP/次，50%）。"""
    if sp < 1:
        return False
    if personality in _PETRIFY_HOLD_PERSONALITIES and not threat_near:
        return False
    return True


def should_continue_breakout(sp: int) -> bool:
    """挣脱失败后"是否再尝试一次"（同一预算纪律）。"""
    return sp >= 1


def should_accept_burn_challenge(personality: str, hp: float,
                                 max_hp: float) -> bool:
    """G4 焚诏拉条：攻击 or 拒战（对手侧最小策略）。"""
    if personality in _SPENDERS:
        return True
    if personality in _HOLDERS:
        return False
    return hp > max_hp * 0.6


def _find_option(options: List[str], *needles: str) -> Optional[str]:
    for opt in options:
        for needle in needles:
            if needle in opt:
                return opt
    return None


def _g6_has_borrowable_core(player: Any, state: Any) -> bool:
    """G6 是否存在至少一枚预检通过的借用核心。"""
    if player is None or state is None:
        return False
    try:
        from engine.m9.talents.g6 import G6Mechanics
        mech = G6Mechanics(getattr(state, "g6_template_pool", None))
        for key in mech.borrowable_core_keys(state):
            if mech.precheck_borrow(player, key, state):
                return True
    except Exception:
        pass
    return False


def m9_decide_choose(controller: Any, prompt: str, options: List[str],
                     context: Optional[dict], state: Any) -> Optional[str]:
    """M9 choose 表面统一入口。返回 None = 放行给旧层（v2exp hook/通用默认）。

    controller 需提供：personality、_player（含 talent）、_game_state（可选）。
    """
    if not options:
        return None
    player = getattr(controller, "_player", None)
    if player is None and isinstance(context, dict):
        player = context.get("player")
    if isinstance(context, dict) and context.get("game_state") is not None:
        state = context["game_state"]
    if state is None:
        state = getattr(controller, "_game_state", None)
    if player is None or state is None:
        return None
    personality = str(getattr(controller, "personality", "balanced") or "balanced")
    talent = getattr(player, "talent", None)
    slot_id = _slot_id_for(talent)
    sp = _sp_of(state, player)
    hp = float(getattr(player, "hp", 0))
    max_hp = float(getattr(player, "max_hp", 20) or 20)
    situation = (context or {}).get("situation", "")
    phase = (context or {}).get("phase", "")
    improvise = m9_text("ai.t0_policy.options.improvise")
    public = m9_text("ai.t0_policy.options.public")

    # 1. talent_t0（M9 下按 slot_id 分派，替代显示名匹配）
    if situation == "talent_t0":
        activate = should_activate_t0(
            slot_id, sp, personality, hp, max_hp,
            state=state, player=player)
        if activate:
            return _find_option(options,
                                m9_text("ai.t0_policy.options.activate")) or options[0]
        return _find_option(options,
                            m9_text("ai.t0_policy.options.not_activate"),
                            m9_text("ai.t0_policy.options.normal")) or options[-1]

    # 2. R0 公演报名（phase 键，无 situation）
    if phase == "M9_PUBLIC_REGISTRATION":
        if should_register_public(
                slot_id, sp, personality, state=state, player=player):
            return _find_option(options,
                                m9_text("ai.t0_policy.options.register")) or options[0]
        return _find_option(options,
                            m9_text("ai.t0_policy.options.keep")) or options[0]

    # 3. M9 石化（双表面：M9 options 含"尝试挣脱"；legacy 由旧层处理）
    attempt_breakout = m9_text("ai.t0_policy.options.attempt_breakout")
    if situation == "petrified" and any(attempt_breakout in o for o in options):
        if should_attempt_breakout(sp, personality,
                                   _same_location_threat(state, player)):
            return _find_option(options, attempt_breakout) or options[0]
        return _find_option(options,
                            m9_text("ai.t0_policy.options.stay_petrified")) or options[0]

    # 4. 挣脱失败裸提示（无 situation）
    if m9_text("ai.t0_policy.prompts.retry_breakout") in str(prompt):
        if should_continue_breakout(sp):
            return _find_option(options,
                                m9_text("ai.t0_policy.options.continue")) or options[0]
        return _find_option(options,
                            m9_text("ai.t0_policy.options.give_up")) or options[-1]

    # 5. G4 焚诏拉条裸提示（无 situation）
    if m9_text("ai.t0_policy.prompts.burn_challenge") in str(prompt):
        if should_accept_burn_challenge(personality, hp, max_hp):
            return _find_option(options,
                                m9_text("ai.t0_policy.options.attack")) or options[0]
        return _find_option(options,
                            m9_text("ai.t0_policy.options.refuse_battle")) or options[-1]

    # 5b. G4 人形态演出方式：未登台或 ≥2 个 engaged 目标 → 公演（AoE+debut）；
    #     否则即演（1 SP 单体 +2 火种）。
    if m9_text("ai.t0_policy.prompts.choose_human_performance") in str(prompt):
        engaged = 0
        markers = getattr(state, "markers", None)
        my_loc = getattr(player, "location", None)
        if markers is not None:
            for eid in markers.get_related(
                    getattr(player, "player_id", ""), "ENGAGED_WITH"):
                actor = None
                if hasattr(state, "get_actor"):
                    actor = state.get_actor(eid)
                if actor is None:
                    actor = state.get_player(eid)
                if actor is not None and actor.is_alive() \
                        and getattr(actor, "location", None) == my_loc:
                    engaged += 1
        if sp >= 2 and _is_public_holder(state, player) \
                and (not _has_debut(state, player) or engaged >= 2):
            return _find_option(options, public) or options[0]
        return _find_option(options, improvise) or options[0]

    # 6. G0 公演选项：十字炮火 vs 遗物支援技——
    #    炮火划算（≥2 同点敌人/可击杀）时开火；有遗物且炮火不划算时用遗物
    crossfire = m9_text("ai.t0_policy.options.crossfire")
    if m9_text("ai.t0_policy.prompts.g0_public") in str(prompt) \
            and crossfire in options:
        talent = getattr(player, "talent", None)
        relics = bool(getattr(talent, "relics", []) if talent is not None else [])
        if relics and not _g0_public_worthwhile(state, player):
            return _find_option(options,
                                m9_text("ai.t0_policy.options.relic_support")) or options[0]
        return _find_option(options, crossfire) or options[0]

    # 7. T1 一刀缭断演出方式：默认即演（1 SP 与 2 SP 结算同一核心斩击）；
    #     只有游侠诗在手且追猎目标异地点时，公演的 chase-move 才值 2 SP。
    if m9_text("ai.t0_policy.prompts.choose_t1_performance") in str(prompt):
        talent = getattr(player, "talent", None)
        try:
            marker = talent._ranger_marker() if talent else None
            chase = talent._ranger_chase_target(player) \
                if marker is not None and talent is not None else None
        except Exception:
            marker = None
            chase = None
        my_loc = getattr(player, "location", None)
        if (marker is not None and chase is not None
                and getattr(chase, "location", None) != my_loc
                and sp >= 2 and _is_public_holder(state, player)):
            return _find_option(options, public) or options[0]
        return _find_option(options, improvise) or options[0]

    # 8. T4 六爻演出：即演 vs 公演——即演不消费回合（裁决 A），默认即演；
    #    公演（consume_turn）只在持有公演位且阴阳诗天机可用时走。
    #    （第 1 轮登台激励经 500 局确认对 T4 净负，R52 回退，报名激励保留）
    if m9_text("ai.t0_policy.prompts.choose_t4_performance") in str(prompt):
        talent = getattr(player, "talent", None)
        tianji = bool(getattr(talent, "m9_poem_markers", {}).get(
            "yin_yang_tianji", 0) if talent is not None else False)
        if tianji and _is_public_holder(state, player):
            return _find_option(options, public) or options[0]
        return _find_option(options, improvise) or options[0]

    # 8b. T6 联防整备：未登台且持公演位 → 公演换第一章；否则即演。
    #     （旧“恒即演”让 debut 永远不亮，arc 全废。）
    if m9_text("ai.t0_policy.prompts.choose_t6_performance") in str(prompt):
        if sp >= 2 and _is_public_holder(state, player) \
                and not _has_debut(state, player):
            return _find_option(options, public) or options[0]
        return _find_option(options, improvise) or options[0]

    # 8c. G2 创建影身：未登台且持公演位 → 公演创建（第一章）；否则即演。
    if m9_text("ai.t0_policy.prompts.create_shadow") in str(prompt):
        debut_public = (not _has_debut(state, player)
                        and sp >= 2 and _is_public_holder(state, player))
        if debut_public:
            return _find_option(options, public) or options[0]
        return _find_option(options, improvise) or options[0]

    # 9. G6 即演/公演：持公演位 + SP≥2 走公演（借用核心/召唤援助），
    #    保住 arc 与第一章节奏；否则即演（T0 门已保证能重演 attack）。
    if m9_text("ai.t0_policy.prompts.choose_g6_performance") in str(prompt):
        if sp >= 2 and _is_public_holder(state, player):
            return _find_option(options, public) or options[0]
        return _find_option(options, improvise) or options[-1]
    # 9b. 公演路径：无核心可借时走召唤援助（公演位不空转）。
    if m9_text("ai.t0_policy.prompts.choose_g6_public_path") in str(prompt):
        if _g6_has_borrowable_core(player, state):
            return _find_option(options,
                                m9_text("ai.t0_policy.options.borrow_core")) or options[0]
        return _find_option(options,
                            m9_text("ai.t0_policy.options.summon_aid")) or options[-1]
    # 9c. 借用核心：按攻击核心优先顺序 + 预检。
    if m9_text("ai.t0_policy.prompts.choose_borrow_core") in str(prompt):
        from engine.m9.talents.g6 import G6Mechanics
        mech = G6Mechanics(getattr(state, "g6_template_pool", None))
        preferred = ("t3_heavenly_star", "t1_one_slash", "t2_scissor_rush",
                     "g4_savior", "t4_hexagram", "g3_reality_marble")
        for want in preferred:
            if any(str(o) == want for o in options):
                try:
                    if mech.precheck_borrow(player, want, state):
                        return want
                except Exception:
                    pass
        return options[0]
    # 9d. 欢愉双借用第二核心：首枚通常是攻击核心 → 第二枚必须选
    #     预检通过的非攻击核心，否则引擎会拒绝第二枚。
    if m9_text("ai.t0_policy.prompts.joy_double_borrow") in str(prompt):
        from engine.m9.talents.g6 import CutawayJoke9, G6Mechanics
        mech = G6Mechanics(getattr(state, "g6_template_pool", None))
        for opt in options:
            try:
                if not CutawayJoke9._is_attack_core(str(opt)) \
                        and mech.precheck_borrow(player, str(opt), state):
                    return opt
            except Exception:
                pass
        return options[0]
    # 9e. G6 借用六爻的猜拳：目标按威胁选存活者；出拳复用六爻 maximin/
    #     minimax（审计：未处理时恒双石头，效果退化）。
    if m9_text("ai.t0_policy.prompts.choose_rps_target") in str(prompt):
        scores = getattr(controller, "_threat_scores", {}) or {}
        alive_names = set()
        for pid in getattr(state, "player_order", []):
            other = state.get_player(pid)
            if other is not None and other.is_alive():
                alive_names.add(getattr(other, "name", ""))
        valid = [o for o in options if o in alive_names]
        if not valid:
            return options[0]
        return max(valid, key=lambda name: float(scores.get(name, 0.0)))
    if str(prompt) == m9_text("ai.t0_policy.prompts.rps") and options:
        caster_pick = getattr(controller, "_hexagram_pick_caster", None)
        if callable(caster_pick):
            try:
                return caster_pick(player, state, options) or options[0]
            except Exception:
                pass
        return options[0]
    if str(prompt).endswith(m9_text("ai.t0_policy.prompts.opponent_rps")) and options:
        opp_pick = getattr(controller, "_hexagram_pick_opponent", None)
        if callable(opp_pick):
            caster = None
            for pid in getattr(state, "player_order", []):
                other = state.get_player(pid)
                t_name = getattr(getattr(other, "talent", None), "name", "")
                if other is not None and other.is_alive() \
                        and "笑声" in str(t_name):
                    caster = other
                    break
            try:
                return opp_pick(caster, state, options) or options[0]
            except Exception:
                pass
        return options[0]

    # 10. T2 剪刀手一突演出方式：公演附赠追猎（位移到目标）；已被我方
    #     LOCKED_BY 的远程目标本来就可以直接核心攻击，选即演即可，不必
    #     花 2 SP 追过去。
    if m9_text("ai.t0_policy.prompts.choose_t2_performance") in str(prompt):
        talent = getattr(player, "talent", None)
        try:
            targets = talent._core_targets(player) if talent else []
        except Exception:
            targets = []
        my_loc = getattr(player, "location", None)
        markers = getattr(state, "markers", None)
        for t in targets:
            if getattr(t, "location", None) == my_loc:
                continue
            locked = bool(markers is not None and markers.has_relation(
                getattr(t, "player_id", ""), "LOCKED_BY",
                getattr(player, "player_id", "")))
            return (_find_option(options, improvise)
                    if locked else _find_option(options, public)) or options[0]
        return _find_option(options, improvise) or options[0]

    # 11. T7 死者苏生：挂载方式统一到本 policy（不再由 talent 内联决策）。
    #     未登台且持公演位 → 公演换第一章；否则即演（1 SP，效果等价）。
    #     挂载目标一律自己——保险兑现=目标死亡后家中复活，挂给别人
    #     是给对手送第二条命（全局唯一），自挂=一次免死。
    if m9_text("ai.t0_policy.prompts.choose_t7_mount_mode") in str(prompt):
        if sp >= 2 and _is_public_holder(state, player) \
                and not _has_debut(state, player):
            return _find_option(options, public) or options[0]
        return _find_option(options, improvise) or options[0]
    if situation == "resurrection_pick_target":
        my_name = getattr(player, "name", None)
        for opt in options:
            if str(opt) == my_name:
                return opt
        return options[0]

    # 12. G3 结界外行动：只有本轮公演位在手且确有可捕捉对象才展开结界；
    #     其余情况走只耗魔力的投影魔术，不点必定失败的展开。
    if m9_text("ai.t0_policy.prompts.choose_g3_outside") in str(prompt):
        if sp >= 2 and _is_public_holder(state, player) \
                and _g3_has_capture_target(state, player):
            return _find_option(options,
                                m9_text("ai.t0_policy.options.expand_barrier")) or options[0]
        return _find_option(options,
                            m9_text("ai.t0_policy.options.projection_magic")) or options[-1]

    # 12b. G3 结界内行动：只有能击杀主目标时才幻想崩坏（否则清空魔力 +
    #      解除结界=自毁）；有兵装池就零成本兵装攻击；否则螺旋剑连发；
    #      空场破界离场。避免永远连发打空魔力被 R4 维持费强制解除。
    if m9_text("ai.t0_policy.prompts.choose_g3_inside") in str(prompt):
        talent = getattr(player, "talent", None)
        alive = []
        try:
            alive = list(talent._captured_alive() or []) if talent else []
        except Exception:
            pass
        if not alive:
            return _find_option(options,
                                m9_text("ai.t0_policy.options.break_barrier")) or options[0]
        try:
            if talent._collapse_legal() and _g3_collapse_lethal(talent, state):
                pick = _find_option(options, m9_text("ai.t0_policy.options.fantasy_collapse"))
                if pick:
                    return pick
        except Exception:
            pass
        try:
            if getattr(talent, "armament_pool", None):
                pick = _find_option(options, m9_text("ai.t0_policy.options.armament_attack"))
                if pick:
                    return pick
        except Exception:
            pass
        return _find_option(options,
                            m9_text("ai.t0_policy.options.spiral_sword")) or options[0]

    # 12c. G3 螺旋剑连发追段：必须给下一轮结界维持费留足预算。
    #      旧默认（options[0]=继续连发）每 T0 把魔力打光，随后 R4
    #      维持费付不出被强制解除，G3 全程“空转”。
    if m9_text("ai.t0_policy.prompts.continue_spiral") in str(prompt):
        talent = getattr(player, "talent", None)
        keep = False
        try:
            chain = getattr(talent, "chain", None)
            budget = float(getattr(talent, "magic", 0) or 0) \
                + float(getattr(talent, "temp_magic", 0) or 0)
            spent = float(getattr(chain, "cumulative_magic", 0) or 0)
            next_cost = chain.next_segment_cost()
            if next_cost is not None:
                upkeep = int(talent._upkeep_cost())
                keep = (budget - spent - float(next_cost)) >= max(1, upkeep)
        except Exception:
            keep = False
        if keep:
            return _find_option(options,
                                m9_text("ai.t0_policy.options.continue_spiral")) or options[0]
        return _find_option(options,
                            m9_text("ai.t0_policy.options.stop")) or options[-1]

    # 12d. G3 终段幻想崩坏：与独立发动同门，非击杀不结算（结算必解除结界）。
    if m9_text("ai.t0_policy.prompts.terminal_collapse") in str(prompt):
        if talent is not None and _g3_collapse_lethal(talent, state):
            return _find_option(options,
                                m9_text("ai.t0_policy.options.yes")) or options[0]
        return _find_option(options,
                            m9_text("ai.t0_policy.options.no")) or options[-1]

    # 13. R0 开市窗口（死者投注决策，B4 §四）：押注综合最强存活者，
    #    下注一半预算（至少 1）；无余额则不交易
    if m9_text("ai.t0_policy.prompts.betting_window") in str(prompt):
        pp = getattr(state, "m9_pp", None)
        budget = int(pp.balance(player.player_id)) if pp is not None else 0
        if budget < 1:
            return _find_option(options,
                                m9_text("ai.t0_policy.options.no_trade")) or options[0]
        return _find_option(options,
                            m9_text("ai.t0_policy.options.bet")) or options[0]
    if m9_text("ai.t0_policy.prompts.bet_target") in str(prompt):
        pick = _best_bet_target(player, state, options)
        return pick if pick is not None else (options[0] if options else None)
    if m9_text("ai.t0_policy.prompts.bet_amount") in str(prompt):
        pp = getattr(state, "m9_pp", None)
        budget = int(pp.balance(player.player_id)) if pp is not None else 0
        nums = sorted(int(o) for o in options if str(o).isdigit())
        if budget < 1 or not nums:
            return options[0] if options else None
        want = max(1, min(nums[-1], (budget + 1) // 2))
        return str(want)
    if m9_text("ai.t0_policy.prompts.transfer_out") in str(prompt) \
            or m9_text("ai.t0_policy.prompts.transfer_in") in str(prompt):
        return options[0] if options else None

    return None


def _best_bet_target(player: Any, state: Any, options: List[str]) -> Optional[str]:
    """死者押注对象：击杀数×10 + 血线×0.5 综合最高的存活者。

    分散策略：1/4 概率押次强（按押注者 player_id 哈希确定，保证风洞可复现）——
    全员押同一强者的集中押注会被黑马加成反制，且押注对象=被动援助收入渠道，
    适度分散更接近真实博弈（B4 §4.3 囚徒困境）。
    """
    scored = []
    for pid in getattr(state, "player_order", []):
        p = state.get_player(pid)
        if p is None or not p.is_alive():
            continue
        if pid == getattr(player, "player_id", None):
            continue
        if getattr(p, "name", "") not in options:
            continue
        kills = float(getattr(p, "kill_count", 0) or 0)
        hp = float(getattr(p, "hp", 0) or 0)
        scored.append((kills * 10.0 + hp * 0.5, getattr(p, "name", "")))
    if not scored:
        return None
    scored.sort(key=lambda item: -item[0])
    bettor_pid = str(getattr(player, "player_id", "") or "")
    if len(scored) >= 2 and sum(ord(c) for c in bettor_pid) % 4 == 0:
        return scored[1][1]
    return scored[0][1]
