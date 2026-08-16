"""M9 G6 插入式笑话·模板池机制（profile: m9-rfc，G6 合同 v0.2）。

- 常驻被动：每 R4 记录本轮已完成公共 root 行动的类别模板（move/interact/find/
  lock/attack），按类别去重，窗口默认 1 轮（欢愉延展 2 轮）；
  演出/完整额外/反应/控制替代/wake/forfeit/警察命令/未登记 special 不入池。
- 即演（1 SP）：从窗口模板选一个类别，用 G6 自身状态重执行（不自带原行动
  天赋/数值/责任）；类别全部不合法时在消费 SP 前取消。
- 公演（2 SP）双路径互斥：借用核心（G6_BORROWABLE_CORE 白名单，T4 或跃重掷
  不授额外行动）或召唤往世层援助（无 PP/无额度，提供者系统被动奖励）。
- 借用预检先于 SP 消费；G2 永不出现在借用白名单/兼容别名。

数值：模板窗口/借用数为结构常量；欢愉诗篇数值读 m9_talents_extended.g5.*。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from engine.balance import get as bget
from engine.m9.talents.stub import M9TalentStub
from engine.m9.text import m9_text

# 模板池类别白名单（talent_action v0.3 §七：第一版正向白名单）
TEMPLATE_CATEGORIES: Tuple[str, ...] = ("move", "interact", "find", "lock", "attack")

# action_type → 类别归一（shoot/hook 属 attack 大类）
ACTION_TYPE_TO_CATEGORY: Dict[str, str] = {
    "move": "move", "interact": "interact", "find": "find",
    "lock": "lock", "attack": "attack", "shoot": "attack", "hook": "attack",
}

# 不入池的 action_type（合同 §3.1 排除清单）
EXCLUDED_ACTION_TYPES: Tuple[str, ...] = ("wake", "forfeit", "status",
                                          "police_status", "allstatus",
                                          "shock_recover", "petrify_skip",
                                          "petrify_hold")

# 借用核心白名单（G6 合同 §四，冻结结构；G2 明确不在其中）
G6_BORROWABLE_CORE: Dict[str, str] = {
    "t1_one_slash": "core_slash",
    "t2_scissor_rush": "core_attack",
    "t3_heavenly_star": "starfall",
    "t4_hexagram": "hexagram_cast",
    "g3_reality_marble": "simple_projection",
    "g4_savior": "enhanced_basic",
}

G6_CORE_SOURCE_SLOT: Dict[str, str] = {
    "t1_one_slash": "T1",
    "t2_scissor_rush": "T2",
    "t3_heavenly_star": "T3",
    "t4_hexagram": "T4",
    "g3_reality_marble": "G3",
    "g4_savior": "G4",
}

# G4 强化普攻借用固定候选倍率（G6 合同 §4.1；数值 [待风洞]，外提至 balance）
G4_BORROW_BASIC_MULTIPLIER = float(bget(
    "m9_talents_extended", "g6", "g4_borrow_basic_multiplier", default=1.5))

# 或跃在渊结果键（六爻：剪刀 vs 布 → 或跃在渊 = 额外行动；借用时重掷到非或跃）
HOJUMP_RESULT_KEY = "scissors_paper"


def template_window_rounds(joy_extended: bool = False) -> int:
    """模板池窗口：默认 1 轮；欢愉延展 2 轮（数值外提至 balance）。"""
    key = "joy_template_window_rounds" if joy_extended \
        else "template_window_rounds"
    return int(bget("m9_talents_extended", "g6", key, default=2 if joy_extended else 1))


class G6TemplatePool:
    """R4 记录模板池：按类别去重 + 窗口裁剪。"""

    def __init__(self) -> None:
        self._log: List[Dict[str, Any]] = []

    def record(self, global_round: int, action_type: str,
               location: str, actor_id: str) -> bool:
        """记录一条已完成公共 root 行动；返回是否入池（False = 排除/重复）。"""
        category = ACTION_TYPE_TO_CATEGORY.get(action_type)
        if category is None or action_type in EXCLUDED_ACTION_TYPES:
            return False
        for entry in self._log:
            if entry["round"] == global_round and entry["category"] == category:
                return False  # 同轮同类别去重
        self._log.append({"round": global_round, "category": category,
                          "location": location, "actor": actor_id})
        return True

    def trim(self, current_round: int, joy_extended: bool = False) -> None:
        """窗口裁剪：只保留最近 N 轮（含上一轮；显式清理用）。"""
        keep_from = current_round - template_window_rounds(joy_extended)
        self._log = [e for e in self._log if e["round"] >= keep_from]

    def categories(self, current_round: int,
                   joy_extended: bool = False) -> List[Dict[str, Any]]:
        """上一轮（欢愉时最近两轮）的模板列表；本轮尚未到 R4，不可见。"""
        keep_from = current_round - template_window_rounds(joy_extended)
        return [dict(e) for e in self._log
                if keep_from <= e["round"] < current_round]

    def has_category(self, current_round: int, category: str,
                     joy_extended: bool = False) -> bool:
        return any(e["category"] == category
                   for e in self.categories(current_round, joy_extended))

    def clear(self) -> None:
        self._log.clear()


class G6Mechanics:
    """G6 机制纯逻辑：即演/公演预检与仲裁（接入层执行具体命令）。"""

    def __init__(self, pool: Optional[G6TemplatePool] = None) -> None:
        self.pool = pool or G6TemplatePool()

    @staticmethod
    def _pick(ctrl: Any, prompt: str, options: List[str]) -> Optional[str]:
        """选择器：choose 失败/返回非法项时取默认（首个）。"""
        if not options:
            return None
        try:
            choice = ctrl.choose(prompt, list(options))
            if choice in options:
                return choice
        except Exception:
            pass
        return options[0]

    # ── 即演（1 SP）──
    def improvise_legal_categories(self, player: Any, current_round: int,
                                   game_state: Any = None,
                                   joy_extended: bool = False) -> List[str]:
        """窗口内类别，按 G6 自身状态过滤合法性（含参数可用性，预检先于 SP 消费）。"""
        available = {e["category"]
                     for e in self.pool.categories(current_round, joy_extended)}
        legal = []
        for category in TEMPLATE_CATEGORIES:  # 白名单稳定顺序输出
            if category in available and self._category_legal(
                    player, category, game_state):
                legal.append(category)
        return legal

    @staticmethod
    def _category_legal(player: Any, category: str,
                        game_state: Any = None) -> bool:
        """类别合法性预检与实际重演共用公共动作枚举器。"""
        if game_state is None or category not in TEMPLATE_CATEGORIES:
            return False
        from engine.action_enumerator import build_action_options
        try:
            return bool(build_action_options(
                player, game_state, [category]).get(category))
        except Exception:
            return False

    # ── 公演路径 1：借用核心 ──
    def borrowable_core_keys(self, game_state: Any = None,
                             *, source_pid: Optional[str] = None) -> List[str]:
        """只列出实际在场来源玩家持有的白名单核心。"""
        if game_state is None:
            return list(G6_BORROWABLE_CORE)
        available_slots = set()
        for pid in getattr(game_state, "player_order", []):
            if source_pid is not None and pid != source_pid:
                continue
            source = game_state.get_player(pid)
            if source is None or not source.is_alive():
                continue
            available_slots.add(str(getattr(source, "talent_slot_id", "") or ""))
        return [key for key in G6_BORROWABLE_CORE
                if G6_CORE_SOURCE_SLOT[key] in available_slots]

    def precheck_borrow(self, player: Any, core_key: str,
                        game_state: Any = None) -> bool:
        """核心预检：G6 当前状态无法满足核心前置 → 消费 SP 前取消。"""
        if core_key not in G6_BORROWABLE_CORE:
            return False
        core = G6_BORROWABLE_CORE[core_key]
        if core in ("core_slash", "core_attack", "enhanced_basic"):
            return self._category_legal(player, "attack", game_state)
        if core == "starfall":
            if game_state is None:
                return bool(getattr(player, "location", None))
            from engine.m9.talents.t3 import _aoe_targets
            return bool(_aoe_targets(
                game_state, getattr(player, "location", None),
                exclude_pid=getattr(player, "player_id", "")))
        if core == "simple_projection":
            markers = getattr(game_state, "markers", None)
            return bool(markers) and any(
                pid != getattr(player, "player_id", "")
                and game_state.get_player(pid) is not None
                and game_state.get_player(pid).is_alive()
                and markers.has_relation(
                    pid, "LOCKED_BY", getattr(player, "player_id", ""))
                for pid in getattr(game_state, "player_order", []))
        if core == "hexagram_cast":
            return game_state is None or any(
                pid != getattr(player, "player_id", "")
                and game_state.get_player(pid) is not None
                and game_state.get_player(pid).is_alive()
                for pid in getattr(game_state, "player_order", []))
        return False

    @staticmethod
    def hexagram_reroll_until_legal(results: List[str]) -> str:
        """借用的六爻：或跃在渊必须重掷到非或跃（绝不创建完整额外行动）。"""
        for r in results:
            if r != HOJUMP_RESULT_KEY:
                return r
        return "rock_scissors"  # 兜底非或跃结果（结构上必达）

    # ── 公演路径 2：往世层援助 ──
    def aid_summon_cost(self) -> int:
        """G6 召唤援助不消耗 PP（天赋效果）；提供者得系统被动奖励。"""
        return 0

    def provider_reward(self) -> float:
        return float(bget("m9_system", "pp", "aid_passive_reward", default=1))


class CutawayJoke9(M9TalentStub):
    """M9 G6 天赋（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。

    T0 入口：SP≥1 即演（重演模板类别）、SP≥2 公演（借用核心/召唤援助）。
    不继承 v2exp CutawayJoke 的 laugh_points/charges 字段（合同删除项）。
    """

    name = "要有笑声！"
    description = m9_text("talents.g6.description")

    def __init__(self, player_id: str, game_state: Any) -> None:
        self.player_id = player_id
        self.state = game_state
        self.joy_extend = False  # 欢愉延展标记（诗篇接线，阶段 7）

    def describe_status(self) -> str:
        """M9 状态口径：模板窗口类别/欢愉延展/可借用核心。"""
        try:
            me = self.state.get_player(self.player_id)
            pool = getattr(self.state, "g6_template_pool", None)
            mech = G6Mechanics(pool)
            cats = mech.improvise_legal_categories(
                me, int(getattr(self.state, "current_round", 0) or 0),
                self.state, joy_extended=self.joy_extend)
        except Exception:
            cats = []
        parts = [m9_text("talents.g6.status_improvise_categories",
                         categories=("、".join(cats) if cats else m9_text("talents.g6.none")))]
        if self.joy_extend:
            parts.append(m9_text("talents.g6.status_joy_extended"))
        try:
            cores = G6Mechanics(getattr(
                self.state, "g6_template_pool", None)).borrowable_core_keys(
                self.state)
            parts.append(m9_text("talents.g6.status_borrowable_cores",
                                 count=len(cores)))
        except Exception:
            pass
        return " | ".join(parts)

    def get_t0_option(self, player: Any) -> Optional[dict]:
        """M9 T0 入口：即演（1 SP）/ 公演（2 SP）。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        sp = m9.get_sp(self.player_id)
        current_round = getattr(self.state, "current_round", 1)
        phase = getattr(self.state, "current_phase", "")
        seated = m9._public_holder_by_round.get(current_round) == self.player_id
        public_ready = sp >= 2 and (phase != "r3_actions" or seated)
        pool = getattr(self.state, "g6_template_pool", None)
        if pool is None:
            return None
        mech = G6Mechanics(pool)
        legal = mech.improvise_legal_categories(player, current_round,
                                                self.state,
                                                joy_extended=self.joy_extend)
        if sp >= 1 and legal:
            return {
                "name": m9_text("talents.g6.t0_improvise_name"),
                "description": m9_text("talents.g6.t0_improvise_description",
                                       categories="、".join(legal)),
                "m9_kind": "g6_improvise",
            }
        if public_ready:
            return {
                "name": m9_text("talents.g6.t0_public_name"),
                "description": m9_text("talents.g6.t0_public_description"),
                "m9_kind": "g6_public",
            }
        return None

    def on_round_end(self, round_num):
        """R4：欢愉延展标记 6 ticks 到期清理（诗篇合同 §7）。"""
        markers = getattr(self, "m9_poem_markers", None)
        if markers and "joy_extend" in markers:
            left = int(markers["joy_extend"]) - 1
            if left <= 0:
                markers.pop("joy_extend", None)
                self.joy_extend = False
            else:
                markers["joy_extend"] = left

    def execute_t0(self, player: Any):
        """即演/公演编排：预检先于 SP 消费；成功返回 (消息, True) 占槽。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return m9_text("talents.g6.err_m9_disabled"), False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return m9_text("talents.g6.err_m9_not_mounted"), False
        round_num = getattr(self.state, "current_round", 1)
        mech = G6Mechanics(self.state.g6_template_pool)
        ctrl = getattr(player, "controller", None)

        # ── 即演（1 SP）：重演模板类别 ──
        cats = mech.improvise_legal_categories(player, round_num, self.state,
                                               joy_extended=self.joy_extend)
        sp = m9.get_sp(self.player_id)
        public_ready = sp >= 2 \
            and m9.assign_public_slot(round_num) == self.player_id
        options = []
        if cats and sp >= 1:
            options.append(m9_text("talents.g6.option_improvise"))
        if public_ready:
            options.append(m9_text("talents.g6.option_public"))
        if not options:
            return m9_text("talents.g6.err_no_performance_option"), False
        if len(options) > 1:
            try:
                want = ctrl.choose(m9_text("talents.g6.choose_performance_prompt"),
                                   options)
            except Exception:
                want = options[0]
        else:
            want = options[0]
        if want == "即演":
            choice = mech._pick(ctrl, m9_text("talents.g6.choose_replay_category_prompt"),
                                cats)
            if choice is None or choice not in cats:
                return m9_text("talents.g6.err_category_illegal"), False
            if m9.dispatch_improvise(self.player_id, round_num) is None:
                return m9_text("talents.g6.err_sp_insufficient_cancel"), False
            from engine.m9.executor import execute_category
            msg, ok = execute_category(player, self.state, choice)
            return (msg, ok) if ok else (msg or m9_text("talents.g6.err_replay_failed"), True)

        # ── 公演（2 SP）：借用核心 / 召唤援助 ──
        if public_ready:
            try:
                path = ctrl.choose(
                    m9_text("talents.g6.choose_public_path_prompt"),
                    [m9_text("talents.g6.option_borrow_core"),
                     m9_text("talents.g6.option_summon_aid")])
            except Exception:
                path = "借用核心"
            if path == "召唤援助":
                return self._public_aid(player, m9, round_num, mech)
            return self._public_borrow(player, m9, round_num, mech)

        return m9_text("talents.g6.err_sp_insufficient_no_option"), False

    # 攻击型核心（欢愉双借用至多一个；G6 合同 §四 + 欢愉诗 §7）
    _ATTACK_CORES: frozenset = frozenset(
        {"core_slash", "core_attack", "starfall",
         "enhanced_basic", "simple_projection"})

    def _public_borrow(self, player: Any, m9: Any, round_num: int,
                       mech: "G6Mechanics"):
        """公演·借用核心：预检先于 SP/公演位消费；T4 或跃重掷不授额外行动。

        欢愉诗激活时顺序借用两名不同合格玩家/核心（至多一个攻击型核心），
        共用本次公演的 SP/公演位。
        """
        ctrl = getattr(player, "controller", None)
        # 借用核心必须来源在场：这里显式传 game_state，否则无状态调用会
        # 退化为“全部白名单核心可选”，T1/T3 等来源不在场时也能借用。
        cores = mech.borrowable_core_keys(self.state)
        choice = mech._pick(ctrl, m9_text("talents.g6.choose_borrow_core_prompt"), cores)
        if choice is None or not mech.precheck_borrow(player, choice, self.state):
            return m9_text("talents.g6.err_core_precheck_failed"), False
        second = None
        if self.joy_extend:
            remaining = [c for c in cores if c != choice]
            if remaining:
                chosen = mech._pick(ctrl,
                                    m9_text("talents.g6.choose_second_core_prompt"),
                                    remaining)
                if chosen is not None and chosen != choice \
                        and mech.precheck_borrow(player, chosen, self.state):
                    if (self._is_attack_core(choice)
                            and not self._is_attack_core(chosen)) \
                            or not self._is_attack_core(choice):
                        second = chosen
        if not self._ensure_public_seat(player, m9, round_num):
            return m9_text("talents.g6.err_sp_or_seat_cancel"), False
        msgs = [self._run_borrow_core(player, self.state, choice)]
        if second is not None:
            msgs.append(self._run_borrow_core(player, self.state, second))
        self.state.log_event("g6_borrow_core", player=player.player_id,
                             core=choice, second=second)
        return "\n".join(m for m in msgs if m), True

    @classmethod
    def _is_attack_core(cls, core_key: str) -> bool:
        return G6_BORROWABLE_CORE.get(core_key) in cls._ATTACK_CORES

    def _run_borrow_core(self, player: Any, state: Any, choice: str) -> str:
        """单枚借用核心结算（SP/公演位已消费；返回消息文案）。"""
        ctrl = getattr(player, "controller", None)
        core = G6_BORROWABLE_CORE[choice]
        if core == "hexagram_cast":
            return self._borrow_hexagram(player, state, ctrl)
        if core == "starfall":
            from engine.m9.talents.t3 import Star9
            return Star9.borrow_starfall(player, state)
        if core == "core_slash":
            return self._borrow_core_slash(player, state)
        if core == "core_attack":
            msg, ok = self._borrow_core_attack(player, state)
            return msg or m9_text("talents.g6.err_attack_failed")
        if core == "simple_projection":
            from engine.m9.talents.g3 import Mythland9
            return Mythland9.borrow_simple_projection(player, state)
        if core == "enhanced_basic":
            return self._borrow_enhanced_basic(player, state)
        return m9_text("talents.g6.err_unknown_borrow_core", core=core)

    # ── 借用核心执行器（合同 §四：武器/加值/费用与责任用 G6）──

    def _borrow_target(self, player: Any, state: Any):
        """选择借用核心的合法目标（存活、非自己）。"""
        others = [pid for pid in state.player_order
                  if pid != player.player_id
                  and state.get_player(pid).is_alive()]
        if not others:
            return None
        ctrl = getattr(player, "controller", None)
        if len(others) > 1 and ctrl is not None:
            names = [state.get_player(pid).name for pid in others]
            try:
                picked = ctrl.choose(m9_text("talents.g6.choose_borrow_target_prompt"),
                                     names)
                for pid in others:
                    if state.get_player(pid).name == picked:
                        return pid
            except Exception:
                pass
        return others[0]

    @staticmethod
    def _borrow_weapon(player: Any):
        """G6 自身武器（借用核心不继承来源天赋数值/装备）。"""
        return next((w for w in getattr(player, "weapons", []) if w), None)

    def _borrow_core_slash(self, player: Any, state: Any) -> str:
        """T1 核心斩击：`skill_core_multiplier=2`、`defense_coefficient=0.5`；
        武器与责任用 G6，不继承 T1 其他被动。"""
        from engine.m9.combat import resolve_damage
        target_id = self._borrow_target(player, state)
        if target_id is None:
            return m9_text("talents.g6.err_no_legal_target")
        target = state.get_player(target_id)
        weapon = self._borrow_weapon(player)
        if weapon is None:
            return m9_text("talents.g6.err_no_weapon")
        result = resolve_damage(
            player, target, weapon, state,
            damage_multiplier=float(bget(
                "m9_talents_extended", "g6", "core_slash_multiplier",
                default=2.0)),
            armor_pierce_factor=float(bget(
                "m9_talents_extended", "g6", "core_slash_defense_coefficient",
                default=0.5)),
            is_talent_attack=True, source_kind="t1_core_slash")
        lines = [m9_text("talents.g6.slash_header", player=player.name,
                         weapon=weapon.name, target=target.name)]
        lines += [f"   {d}" for d in result.get("details", [])]
        if result.get("killed"):
            lines.append(m9_text("talents.g6.slash_killed", target=target.name))
        return "\n".join(lines)

    def _borrow_core_attack(self, player: Any, state: Any) -> Tuple[str, bool]:
        """T2 核心攻击：直接攻击；不附带追猎移动或 find 前置。"""
        from engine.m9.combat import resolve_damage
        target_id = self._borrow_target(player, state)
        if target_id is None:
            return m9_text("talents.g6.err_no_legal_target"), True
        target = state.get_player(target_id)
        weapon = self._borrow_weapon(player)
        if weapon is None:
            return m9_text("talents.g6.err_no_weapon"), True
        result = resolve_damage(
            player, target, weapon, state, source_kind="t2_core_attack")
        lines = [m9_text("talents.g6.core_attack_header", player=player.name,
                         weapon=weapon.name, target=target.name)]
        lines += [f"   {d}" for d in result.get("details", [])]
        if result.get("killed"):
            lines.append(m9_text("talents.g6.core_attack_killed",
                                 target=target.name))
        return "\n".join(lines), True

    def _borrow_enhanced_basic(self, player: Any, state: Any) -> str:
        """G4 强化普攻：固定候选倍率；不取得毁伤、不继承毁伤获取。"""
        from engine.m9.combat import resolve_damage
        target_id = self._borrow_target(player, state)
        if target_id is None:
            return m9_text("talents.g6.err_no_legal_target")
        target = state.get_player(target_id)
        weapon = self._borrow_weapon(player)
        if weapon is None:
            return m9_text("talents.g6.err_no_weapon")
        result = resolve_damage(
            player, target, weapon, state,
            damage_multiplier=G4_BORROW_BASIC_MULTIPLIER,
            is_talent_attack=True, source_kind="g4_enhanced_basic")
        lines = [m9_text("talents.g6.enhanced_basic_header", player=player.name,
                         weapon=weapon.name, target=target.name,
                         multiplier=G4_BORROW_BASIC_MULTIPLIER)]
        lines += [f"   {d}" for d in result.get("details", [])]
        if result.get("killed"):
            lines.append(m9_text("talents.g6.core_attack_killed",
                                 target=target.name))
        return "\n".join(lines)

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        """只消费 R0 已固化的公演位；T0 不得补报名。"""
        if m9.assign_public_slot(round_num) != player.player_id:
            return False
        return m9.dispatch_public(player.player_id, round_num) is not None

    @staticmethod
    def _borrow_hexagram(player: Any, state: Any, ctrl: Any) -> str:
        """借六爻：真实猜拳流程（G6 出拳 vs 目标出拳）；或跃在渊必须重掷到
        非或跃（绝不创建完整额外行动/不转移 full_extra 来源）。"""
        from talents.t4_hexagram import Hexagram
        others = [p for p in state.player_order
                  if p != player.player_id
                  and state.get_player(p).is_alive()]
        if not others:
            return m9_text("talents.g6.err_no_rps_target")
        target = state.get_player(others[0])
        if ctrl is not None:
            names = [state.get_player(p).name for p in state.player_order
                     if p != player.player_id]
            picked = None
            try:
                picked = ctrl.choose(m9_text("talents.g6.choose_rps_target_prompt"),
                                     names)
            except Exception:
                picked = None
            if picked is not None:
                for pid in state.player_order:
                    if state.get_player(pid).name == picked:
                        target = state.get_player(pid)
                        break
        hexagram = Hexagram("g6_borrow", state)
        choices = Hexagram.CHOICES
        for _ in range(6):
            my = ctrl.choose(m9_text("talents.g6.rps_prompt"),
                             list(choices)) if ctrl else "剪刀"
            opp = target.controller.choose(
                m9_text("talents.g6.rps_opponent_prompt", name=target.name),
                list(choices))
            if my not in choices:
                my = "剪刀"
            if opp not in choices:
                opp = "石头"
            outcome = CutawayJoke9._hexagram_outcome(my, opp)
            if outcome == "scissors_paper":
                continue  # 或跃在渊 → 重掷，不授额外行动
            msg = hexagram._resolve(player, target, my, opp)
            return m9_text("talents.g6.hexagram_result", my=my, opp=opp,
                           outcome=outcome, msg=msg)
        return m9_text("talents.g6.hexagram_reroll_limit")

    @staticmethod
    def _hexagram_outcome(my: str, opp: str) -> str:
        """六爻组合判定（与 talents/t4_hexagram 同构的纯函数）。"""
        if my == opp:
            return {"剪刀": "both_scissors", "石头": "both_rock",
                    "布": "both_paper"}.get(my, "both_rock")
        pair = frozenset([my, opp])
        if pair == frozenset(["剪刀", "石头"]):
            return "scissors_rock"
        if pair == frozenset(["剪刀", "布"]):
            return "scissors_paper"
        return "rock_paper"

    def _public_aid(self, player: Any, m9: Any, round_num: int,
                    mech: "G6Mechanics") -> Tuple[str, bool]:
        """公演·召唤往世层援助：无 PP/无额度；提供者系统被动奖励（S4 接线）。

        G6 援助效果（B4 §5.3）：以 G6 自身状态重演一次可复制普通行动大类模板
        （不另收 SP、不进 T0、不授予完整额外行动；不调用天赋公演）。
        """
        ctrl = getattr(player, "controller", None)
        if not self._ensure_public_seat(player, m9, round_num):
            return m9_text("talents.g6.err_sp_or_seat_cancel"), False
        from engine.m9.aids import run_aid
        category = "attack"
        if ctrl is not None:
            try:
                category = ctrl.choose(
                    m9_text("talents.g6.choose_aid_category_prompt"),
                    ["attack", "move", "interact", "find", "lock"])
            except Exception:
                category = "attack"
        msg = run_aid("G6", "aid", player, self.state, {"category": category})
        return msg, True
