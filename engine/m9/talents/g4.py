"""M9 G4 救世主轮回天赋（profile: m9-rfc，G4 合同 v0.3）。

继承 v2exp Savior（复用 on_being_attacked/on_positive_talent_used 入口与
receive_damage_to_temp_hp 吸收链），覆写 M9 差异：
- 火种（W2 冻结）：每全局轮至多 +2、只人形态、外来敌对/正面转移各首个 +1
  （限定次数来源额外 +1 退役；m9 结算路径经 m9_on_hit 喂敌对来源）；
- 形态：完整（12 烬）/ 残缺（<12 致死，消耗全部烬）；0 烬致死 → ember_floor 残缺；
  进入即 SP 置 2（非 +2）；建立轮 R4 不 tick，完整形态 6 tick；
- 余烬生命池 + 毁伤预算；形态内致死 = 消耗（非死亡、无击杀、不进往世层）；
  absolute_death 直死（不走本类）；
- 退场：ember→0 / 形态到期 → 回人形态，不永久落幕（spent 退役）；
- 负世主动燃尽：12 烬人形态 T0 → 完整形态（合同登记为完整额外行动来源
  g4_savior_active_burn，经 m9 ActionSystem 派发）；
- 焚诏拉条（公演）：ChallengeAdjudicator 全桌快照 → 秘密承诺 → 先攻降序响应 →
  统一反击池 → 死星天裁（DIRECT_DAMAGE + absolute_death）。
数值一律读 `m9_talents_extended.g4.*`（[待风洞]）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from engine.balance import get as bget
from talents.g4_savior import Savior

FORM_HUMAN = "human"
FORM_FULL = "full_savior"
FORM_INCOMPLETE = "incomplete_savior"


def _g4(key: str, default):
    return bget("m9_talents_extended", "g4", key, default=default)


class ChallengeAdjudicator:
    """焚诏拉条裁决（合同 §4/§5，纯逻辑可测）。

    快照 = [(player_id, initiative, has_legal_attack)]（公演时冻结）；
    承诺 = {player_id: "attack" | "refuse"}（秘密提交后一次性公开）。
    反击池 = counter_total(D) 均分给攻击者；天裁池 = S × J 均分给拒战者
    （S = max(D − a, 0)，a = 实际攻击者数）；余数按先攻降序 + actor ID 依次 +1。
    """

    def __init__(self, snapshot: List[Tuple[str, int, bool]],
                 counter_total: float, judgment_per_segment: float) -> None:
        self.snapshot = snapshot
        self.counter_total = counter_total
        self.judgment_per_segment = judgment_per_segment

    def _ordered(self, pids: List[str]) -> List[str]:
        order = {pid: (init, pid) for pid, init, _ in self.snapshot}
        return sorted(pids, key=lambda p: (order[p][0], p), reverse=True)

    def resolve(self, commitments: Dict[str, str]) -> Dict[str, Dict[str, float]]:
        attackers = [pid for pid, _, _ in self.snapshot
                     if commitments.get(pid) == "attack"]
        refusers = [pid for pid, _, _ in self.snapshot
                    if commitments.get(pid) == "refuse"]
        if not attackers:
            # 全员拒战：天裁池 = S(=D) × J 全部分给拒战者
            attackers = []
            pool = self.judgment_per_segment * len(self.snapshot)
            return {"counters": {}, "judgments": self._split(
                refusers, pool)}
        counter_split = self._split(attackers, self.counter_total)
        segments = max(0, len(self.snapshot) - len(attackers))
        judgment_split = self._split(refusers,
                                     segments * self.judgment_per_segment)
        return {"counters": counter_split, "judgments": judgment_split}

    def _split(self, pids: List[str], pool: float) -> Dict[str, float]:
        """固定池均分 + 余数按先攻降序 + ID 依次 +1（确定性，无随机）。"""
        if not pids or pool <= 0:
            return {pid: 0.0 for pid in pids}
        base = int(pool) // len(pids)
        remainder = int(pool) % len(pids)
        out = {}
        for i, pid in enumerate(self._ordered(pids)):
            out[pid] = float(base + (1 if i < remainder else 0))
        return out


class Savior9(Savior):
    """M9 G4（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。"""

    name = "愿负世，照拂黎明"

    def __init__(self, player_id: str, game_state: Any) -> None:
        super().__init__(player_id, game_state)
        self.form = FORM_HUMAN
        self.ember_hp = 0.0          # 余烬生命池
        self.ruin_damage = 0         # 毁伤预算（形态内）
        self.form_ticks = 0          # 形态寿命（建立轮不 tick）
        self.judgment_segments = 0   # 天裁段数
        self._ember_round = -1
        self._hostile_used_this_round = False
        self._positive_used_this_round = False

    # ════════════════════════════════════════════════════════
    #  火种（W2 冻结：每轮至多 +2、只人形态、敌对/正面各首个 +1）
    # ════════════════════════════════════════════════════════

    def _ember_round_tick(self) -> None:
        round_num = getattr(self.state, "current_round", 1)
        if round_num != self._ember_round:
            self._ember_round = round_num
            self._hostile_used_this_round = False
            self._positive_used_this_round = False

    def _gain_ember(self, amount: int, reason: str) -> None:
        if self.form != FORM_HUMAN:
            return
        old = self.divinity
        self.divinity = min(self.divinity + amount, 12)
        self.ember = self.divinity
        if self.divinity > old:
            self.state.log_event("g4_ember", player=self.player_id,
                                 reason=reason, ember=self.divinity)

    def on_being_attacked(self, attacker, weapon, is_limited_talent=False):
        """M9：外来敌对首次（每轮）→ +1；限定来源额外 +1 退役。"""
        if self.spent or self.is_savior or self.form != FORM_HUMAN:
            return
        self._ember_round_tick()
        if not self._hostile_used_this_round:
            self._hostile_used_this_round = True
            self._gain_ember(1, "外来敌对")

    def m9_on_hit(self, hit: Any) -> None:
        """m9 结算路径喂敌对火种（本人被合法攻击命中/未命中皆计——攻击存在即计）。"""
        self.on_being_attacked(getattr(hit, "_attacker", None), None)

    def on_positive_talent_used(self, source_player, is_limited=False):
        """M9：外来正面转移首次（每轮）→ +1。"""
        if self.spent or self.is_savior or self.form != FORM_HUMAN:
            return
        self._ember_round_tick()
        if not self._positive_used_this_round:
            self._positive_used_this_round = True
            self._gain_ember(1, "外来正面转移")

    # ════════════════════════════════════════════════════════
    #  致死：人形态进入 / 形态内消耗 / absolute_death 直死
    # ════════════════════════════════════════════════════════

    def m9_on_lethal(self, target: Any, attacker: Any,
                     source_kind: Optional[str]) -> Optional[str]:
        """形态内致死 → 余烬生命消耗（非死亡）；空池 → 1 HP 退场。"""
        if self.form in (FORM_FULL, FORM_INCOMPLETE):
            self.ember_hp = max(0.0, self.ember_hp - 1.0)
            if self.ember_hp <= 0:
                target.hp = 1.0
                self._exit_savior_state()
            else:
                target.hp = max(1.0, getattr(target, "hp", 0))
            return "g4_savior_consume"
        return None

    def on_death_check(self, player, damage_source):
        """M9：人形态致死 → 完整/残缺/ember_floor 进入（v2exp 路径也复用）。"""
        if player.player_id != self.player_id:
            return None
        if self.form != FORM_HUMAN:
            return None
        if self.divinity >= 12:
            return self._enter_savior_state(player, is_manual=False, full=True)
        return self._enter_savior_state(player, is_manual=False, full=False)

    # ════════════════════════════════════════════════════════
    #  形态维护
    # ════════════════════════════════════════════════════════

    def _enter_savior_state(self, player, is_manual=False, full=None):
        """M9 进入：消耗全部火种；余烬生命/毁伤/SP2；建立轮不 tick。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return super()._enter_savior_state(player, is_manual=is_manual)
        full = full if full is not None else (self.divinity >= 12)
        consumed = self.divinity
        self.divinity = 0
        self.ember = 0
        self.form = FORM_FULL if full else FORM_INCOMPLETE
        floor = int(_g4("ember_floor", 1))
        self.ember_hp = float(max(
            floor,
            consumed * 2 if full else max(floor, consumed)))  # 余烬生命（数值待风洞）
        self.ruin_damage = int(_g4("ruin_start", 3))
        self.form_ticks = int(_g4("full_duration_r4", 6)) if full else max(
            1, int(_g4("full_duration_r4", 6)) * floor // max(1, consumed))
        self.is_savior = True
        player.hp = max(getattr(player, "hp", 0), 1.0)
        m9 = getattr(self.state, "m9_system", None)
        if m9 is not None:
            m9.set_sp(self.player_id, 2)  # SP 置 2（不是 +2）
        self.state.log_event("g4_savior_enter", player=self.player_id,
                             form=self.form, ember=consumed)
        return {"prevent_death": True, "new_hp": player.hp}

    def on_round_end(self, round_num):
        """M9：建立轮不 tick；到期退场。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return super().on_round_end(round_num)
        if self.form not in (FORM_FULL, FORM_INCOMPLETE):
            return
        if getattr(self, "_g4_established_round", None) is None:
            self._g4_established_round = round_num  # 建立轮不 tick
            return
        self.form_ticks -= 1
        if self.form_ticks <= 0:
            self._exit_savior_state()

    def _exit_savior_state(self):
        """M9 退场：ember→0、毁伤清、回人形态；不永久落幕（spent 退役）。"""
        if self.form == FORM_HUMAN:
            return
        self.form = FORM_HUMAN
        self.ember_hp = 0.0
        self.ruin_damage = 0
        self.judgment_segments = 0
        self.is_savior = False
        self.state.log_event("g4_savior_exit", player=self.player_id)

    # ════════════════════════════════════════════════════════
    #  负世主动燃尽（完整额外行动来源 g4_savior_active_burn）
    # ════════════════════════════════════════════════════════

    def get_t0_option(self, player: Any) -> Optional[dict]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return super().get_t0_option(player)
        if self.form == FORM_HUMAN and self.divinity >= 12:
            return {"name": "负世·主动燃尽", "description": "完整形态进入（完整额外行动）",
                    "m9_kind": "g4_active_burn"}
        if self.form in (FORM_FULL, FORM_INCOMPLETE):
            m9 = getattr(self.state, "m9_system", None)
            if m9 is not None and m9.get_sp(self.player_id) >= 2 \
                    and self.ruin_damage > 0:
                return {"name": "灾厄·弑魂焚诏", "description": "全桌拉条公演",
                        "m9_kind": "g4_challenge"}
        return None

    def execute_t0(self, player: Any):
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return super().execute_t0(player)
        m9 = getattr(self.state, "m9_system", None)
        round_num = getattr(self.state, "current_round", 1)
        if self.form == FORM_HUMAN and self.divinity >= 12 and m9 is not None:
            grant = m9.dispatch_full_extra(self.player_id, round_num,
                                           "g4_savior_active_burn")
            if grant is None:
                return "❌ 完整额外行动已满/递归超限", False
            self._enter_savior_state(player, is_manual=True, full=True)
            return f"🌅 {player.name} 负世主动燃尽：完整救世主形态！", True
        if self.form in (FORM_FULL, FORM_INCOMPLETE) and m9 is not None \
                and m9.get_sp(self.player_id) >= 2 and self.ruin_damage > 0:
            if not self._ensure_public_seat(player, m9, round_num):
                return "❌ SP/公演位不足", False
            msg = self._run_challenge(player)
            return msg, True
        return "❌ 条件不满足", False

    # ════════════════════════════════════════════════════════
    #  焚诏拉条（合同 §3.2-§5）：快照 → 秘密承诺 → 响应 → 反击 → 天裁
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        if m9.assign_public_slot(round_num) != player.player_id:
            if not m9.register_performance(player.player_id, round_num):
                return False
        return m9.dispatch_public(player.player_id, round_num) is not None

    def _run_challenge(self, player: Any) -> str:
        """焚诏公演编排：全桌快照（除 G4）→ 秘密承诺（攻击/拒战）→
        先攻降序响应（合法攻击载体，无武器退化基础拳击 challenge_punch）→
        统一反击池（攻击者）→ 死星天裁（拒战者，DIRECT_DAMAGE + absolute_death）。
        数值 J/counter 为 [待风洞] 占位。"""
        from engine.m9.combat import resolve_damage
        me = player
        snapshot = []
        for pid in self.state.player_order:
            if pid == self.player_id:
                continue
            p = self.state.get_player(pid)
            if p and p.is_alive():
                has_attack = any(
                    w for w in getattr(p, "weapons", []) if w)
                snapshot.append((pid, 0, has_attack))  # 先攻：player_order 序（简化）
        if not snapshot:
            return "❌ 无拉条对象"
        commitments = {}
        for pid, _, _ in snapshot:
            p = self.state.get_player(pid)
            try:
                choice = p.controller.choose(
                    f"焚诏拉条：{p.name} 选择攻击或拒战？", ["攻击", "拒战"])
            except Exception:
                choice = "拒战"
            commitments[pid] = "attack" if choice == "攻击" else "refuse"

        d = max(1, self.ruin_damage)
        counter_total = float(d)                       # 反击池（[待风洞]）
        judgment_per_segment = 2.0                     # J（[待风洞]）
        adjudicator = ChallengeAdjudicator(snapshot, counter_total,
                                           judgment_per_segment)
        result = adjudicator.resolve(commitments)

        lines = ["⚔️ 灾厄·弑魂焚诏！"]
        # 响应：攻击者按快照序各执行一次合法攻击载体（先攻降序）
        for pid, _, _ in sorted(snapshot, key=lambda s: s[0], reverse=True):
            if commitments.get(pid) != "attack":
                continue
            attacker = self.state.get_player(pid)
            weapon = max(
                [w for w in getattr(attacker, "weapons", []) if w],
                key=lambda w: w.get_effective_damage(), default=None)
            if weapon is not None:
                from controllers.ai.game_query import GameQuery
                attr = GameQuery.get_weapon_attr(weapon).value
                r = resolve_damage(attacker, me, weapon,
                                   game_state=self.state,
                                   source_kind="g4_challenge_attack")
                lines.append(f"  [{attacker.name}] 响应攻击 {weapon.name}"
                             f" → {r['hp_damage']} 伤")
            else:
                r = resolve_damage(attacker, me, weapon=None,
                                   game_state=self.state,
                                   raw_damage_override=int(
                                       _g4("challenge_punch", 2)),
                                   damage_attribute_override="__无视__",
                                   source_kind="g4_challenge_attack")
                lines.append(f"  [{attacker.name}] 基础拳击 → {r['hp_damage']} 伤")
            if getattr(me, "hp", 0) <= 0:
                lines.append("  💥 挑战迫使 G4 退出形态！响应与天裁取消。")
                self._forced_exit(me)
                return "\n".join(lines)

        # 统一反击：攻击者
        for pid, dmg in result["counters"].items():
            if dmg <= 0:
                continue
            target = self.state.get_player(pid)
            r = resolve_damage(me, target, weapon=None,
                               game_state=self.state,
                               raw_damage_override=int(dmg),
                               damage_attribute_override="__无视__",
                               source_kind="g4_counter")
            lines.append(f"  [反击] {target.name} 受 {r['hp_damage']} 伤")

        # 死星天裁：拒战者（DIRECT_DAMAGE + absolute_death）
        for pid, dmg in result["judgments"].items():
            if dmg <= 0:
                continue
            target = self.state.get_player(pid)
            r = resolve_damage(me, target, weapon=None,
                               game_state=self.state,
                               raw_damage_override=int(dmg),
                               damage_attribute_override="__无视__",
                               source_kind="g4_judgment")
            lines.append(f"  [天裁] {target.name} 受 {r['hp_damage']} 伤"
                         + ("（绝对死亡）" if r.get("absolute_dead") else ""))
        self.judgment_segments = max(0, d - sum(
            1 for c in commitments.values() if c == "attack"))
        return "\n".join(lines)

    def _forced_exit(self, me: Any) -> None:
        """强制退场：停止后续响应、取消反击与天裁，只执行无额外载荷的退场清理。"""
        self._exit_savior_state()
        if getattr(me, "hp", 0) <= 0:
            me.hp = 1.0

    # ════════════════════════════════════════════════════════
    #  m9 结算协议：强化普攻 / 毁伤（数值挂载，阶段 8 校准）
    # ════════════════════════════════════════════════════════

    def m9_modify_outgoing(self, attacker: Any, target: Any, weapon: Any,
                           raw: float) -> float:
        if self.form in (FORM_FULL, FORM_INCOMPLETE):
            return raw + self.ruin_damage
        return raw
