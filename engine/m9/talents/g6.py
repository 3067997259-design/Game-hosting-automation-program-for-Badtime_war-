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

# 或跃在渊结果键（六爻：剪刀 vs 布 → 或跃在渊 = 额外行动；借用时重掷到非或跃）
HOJUMP_RESULT_KEY = "scissors_paper"


def template_window_rounds(joy_extended: bool = False) -> int:
    """模板池窗口：默认 1 轮；欢愉延展 2 轮（结构常量）。"""
    return 2 if joy_extended else 1


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
        """当前窗口内的去重类别模板列表（含地点/actor 展示信息；非破坏）。"""
        keep_from = current_round - template_window_rounds(joy_extended)
        return [dict(e) for e in self._log if e["round"] >= keep_from]

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
        """类别合法性预检（合同 §3.2）：装备/目标/地点参数必须可用。"""
        if category == "attack":
            has_weapon = any(w for w in getattr(player, "weapons", []) if w)
            has_target = bool(game_state) and any(
                p for p in game_state.player_order
                if p != player.player_id
                and game_state.get_player(p).is_alive())
            return has_weapon and has_target
        if category in ("find", "lock"):
            return bool(game_state) and any(
                p for p in game_state.player_order
                if p != player.player_id
                and game_state.get_player(p).is_alive())
        if category == "interact":
            from actions.interact import get_location_menu
            menu = get_location_menu(getattr(player, "location", None))
            return bool(menu and [k for k in menu.keys() if not k.startswith("_")])
        if category == "move":
            return bool(getattr(player, "location", None))
        return True

    # ── 公演路径 1：借用核心 ──
    def borrowable_core_keys(self) -> List[str]:
        return list(G6_BORROWABLE_CORE)

    def precheck_borrow(self, player: Any, core_key: str) -> bool:
        """核心预检：G6 当前状态无法满足核心前置 → 消费 SP 前取消。"""
        if core_key not in G6_BORROWABLE_CORE:
            return False
        core = G6_BORROWABLE_CORE[core_key]
        if core in ("core_slash", "core_attack", "starfall", "enhanced_basic"):
            return any(w for w in getattr(player, "weapons", []) if w)
        if core == "simple_projection":
            return any(w for w in getattr(player, "weapons", []) if w)
        if core == "hexagram_cast":
            return True  # 猜拳不依赖装备
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


class CutawayJoke9:
    """M9 G6 天赋（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。

    T0 入口：SP≥1 即演（重演模板类别）、SP≥2 公演（借用核心/召唤援助）。
    不继承 v2exp CutawayJoke 的 laugh_points/charges 字段（合同删除项）。
    """

    name = "要有笑声！"
    description = "模板池重演（即演）或借用核心/援助（公演）"

    def __init__(self, player_id: str, game_state: Any) -> None:
        self.player_id = player_id
        self.state = game_state
        self.joy_extend = False  # 欢愉延展标记（诗篇接线，阶段 7）

    # ── 供既有引擎钩子兼容的桩（v2exp 挂点不读字段即安全）──
    def on_round_end(self, *args, **kwargs):
        return None

    def on_round_start(self, *args, **kwargs):
        return None

    def on_turn_end(self, *args, **kwargs):
        return None

    def get_t0_option(self, player: Any) -> Optional[dict]:
        """M9 T0 入口：即演（1 SP）/ 公演（2 SP）。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        sp = m9.get_sp(self.player_id)
        current_round = getattr(self.state, "current_round", 1)
        pool = getattr(self.state, "g6_template_pool", None)
        if pool is None:
            return None
        mech = G6Mechanics(pool)
        legal = mech.improvise_legal_categories(player, current_round,
                                                self.state,
                                                joy_extended=self.joy_extend)
        if sp >= 1 and legal:
            return {
                "name": "即演：重演上一轮行动",
                "description": "可重演类别: " + "、".join(legal),
                "m9_kind": "g6_improvise",
            }
        if sp >= 2:
            return {
                "name": "公演：插入式笑话",
                "description": "借用天赋核心或召唤往世层援助",
                "m9_kind": "g6_public",
            }
        return None

    def execute_t0(self, player: Any):
        """即演/公演编排：预检先于 SP 消费；成功返回 (消息, True) 占槽。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return "❌ M9 天赋未启用", False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return "❌ M9 机制未挂载", False
        round_num = getattr(self.state, "current_round", 1)
        mech = G6Mechanics(self.state.g6_template_pool)
        ctrl = getattr(player, "controller", None)

        # ── 即演（1 SP）：重演模板类别 ──
        cats = mech.improvise_legal_categories(player, round_num, self.state,
                                               joy_extended=self.joy_extend)
        if cats and m9.get_sp(self.player_id) >= 1:
            try:
                want = ctrl.choose("即演重演或公演？", ["即演", "公演"])
            except Exception:
                want = "即演"
            if want == "即演":
                choice = mech._pick(ctrl, "选择重演类别", cats)
                if choice is None or choice not in cats:
                    return "❌ 类别不合法，演出取消", False
                if m9.dispatch_improvise(self.player_id, round_num) is None:
                    return "❌ SP 不足，演出取消", False
                from engine.m9.executor import execute_category
                msg, ok = execute_category(player, self.state, choice)
                return (msg, ok) if ok else (msg or "❌ 重演失败", True)

        # ── 公演（2 SP）：借用核心 / 召唤援助 ──
        if m9.get_sp(self.player_id) >= 2:
            try:
                path = ctrl.choose("公演路径：", ["借用核心", "召唤援助"])
            except Exception:
                path = "借用核心"
            if path == "召唤援助":
                return self._public_aid(player, m9, round_num, mech)
            return self._public_borrow(player, m9, round_num, mech)

        return "❌ SP 不足，无可用的演出选项", False

    def _public_borrow(self, player: Any, m9: Any, round_num: int,
                       mech: "G6Mechanics"):
        """公演·借用核心：预检先于 SP/公演位消费；T4 或跃重掷不授额外行动。"""
        ctrl = getattr(player, "controller", None)
        cores = mech.borrowable_core_keys()
        choice = mech._pick(ctrl, "选择借用核心", cores)
        if choice is None or not mech.precheck_borrow(player, choice):
            return "❌ 核心预检失败，演出在消费前取消", False
        if not self._ensure_public_seat(player, m9, round_num):
            return "❌ SP/公演位不足，演出取消", False
        core = G6_BORROWABLE_CORE[choice]
        if core == "hexagram_cast":
            msg = self._borrow_hexagram(player, self.state, ctrl)
        else:
            # 其余核心执行器随对应天赋阶段落地（t1/t2/t3/g3/g4 → 阶段 3-7）
            msg = (f"借用核心「{core}」执行器随天赋阶段落地"
                   f"（当前骨架已扣 SP/公演位）")
        return msg, True

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        """公演位：未在队列/队首非己时先报名（SP≥2 预检），再派发（预检先于消费）。"""
        if m9.assign_public_slot(round_num) != player.player_id:
            if not m9.register_performance(player.player_id, round_num):
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
            return "❌ 无猜拳目标"
        target = state.get_player(others[0])
        if ctrl is not None:
            names = [state.get_player(p).name for p in state.player_order
                     if p != player.player_id]
            picked = None
            try:
                picked = ctrl.choose("选择猜拳目标：", names)
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
            my = ctrl.choose("出拳：", list(choices)) if ctrl else "剪刀"
            opp = target.controller.choose(f"{target.name} 出拳：", list(choices))
            if my not in choices:
                my = "剪刀"
            if opp not in choices:
                opp = "石头"
            outcome = CutawayJoke9._hexagram_outcome(my, opp)
            if outcome == "scissors_paper":
                continue  # 或跃在渊 → 重掷，不授额外行动
            msg = hexagram._resolve(player, target, my, opp)
            return f"借六爻：{my} vs {opp} → {outcome}（借用结算）{msg}"
        return "借六爻：或跃重掷超限，演出取消（SP/公演位不返还）"

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
        """公演·召唤往世层援助：无 PP/无额度；提供者系统被动奖励（框架）。"""
        ctrl = getattr(player, "controller", None)
        if not self._ensure_public_seat(player, m9, round_num):
            return "❌ SP/公演位不足，演出取消", False
        pp = getattr(self.state, "m9_pp", None)
        reward = mech.provider_reward()
        return (f"召唤援助（无 PP/无额度，提供者系统奖励 {reward} PP）——"
                f"26 项援助执行器随阶段 7-8 落地"), True

