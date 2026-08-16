"""M9 G4 救世主轮回天赋（profile: m9-rfc，G4 合同 v0.3）。

继承 v2exp Savior（复用 on_being_attacked/on_positive_talent_used 入口与
receive_damage_to_temp_hp 吸收链），覆写 M9 差异：
- 火种（W2 冻结）：每全局轮至多 +2、只人形态、外来敌对/正面转移各首个 +1
  （限定次数来源额外 +1 退役；m9 结算路径经 m9_on_hit 喂敌对来源）；
- 形态：完整（12 烬）/ 残缺（每局仅首次人形态致死可 <12，消耗全部烬）；
  后续人形态致死必须满 12 烬；首次 0 烬致死 → ember_floor 残缺；
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
from engine.m9.text import m9_text
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
        # 每局只有第一次进入救世主形态享有“不满 12 火种也可残缺进入”的宽免。
        # 无论第一次是致命伤转折还是满 12 火种主动燃尽，都会消费该资格。
        self._has_entered_savior = False

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
        self.divinity = min(self.divinity + amount,
                            int(_g4("ember_cap", 12)))
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
            self._gain_ember(1, m9_text("talents.g4.ember.reason_hostile"))

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
            self._gain_ember(1, m9_text("talents.g4.ember.reason_positive"))

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
        """M9：首次人形态致死可残缺进入；后续必须满 12 火种。"""
        if player.player_id != self.player_id:
            return None
        if self.form != FORM_HUMAN:
            return None

        if self.divinity >= self.MAX_DIVINITY:
            return self._enter_savior_state(player, is_manual=False, full=True)
        if not self._has_entered_savior:
            return self._enter_savior_state(player, is_manual=False, full=False)
        return None

    # ════════════════════════════════════════════════════════
    #  形态维护
    # ════════════════════════════════════════════════════════

    def _enter_savior_state(self, player, is_manual=False, full=None):
        """M9 进入：消耗全部火种；余烬生命/毁伤/SP2；建立轮不 tick。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super()._enter_savior_state(player, is_manual=is_manual)
        full = full if full is not None else (self.divinity >= 12)
        consumed = self.divinity
        self._has_entered_savior = True
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
        if not m9_enabled(self.state):
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
    #  人形态即演/公演（2026-09 风洞批次：火种主动获取）
    # ════════════════════════════════════════════════════════

    def _human_melee_weapons(self, player: Any) -> list:
        """人形态演出可用的近战武器（六爻封印武器不可用）。"""
        from models.equipment import WeaponRange
        return [
            w for w in getattr(player, "weapons", [])
            if w is not None and getattr(w, "weapon_range", None)
            == WeaponRange.MELEE
            and not getattr(w, "_hexagram_disabled", False)
        ]

    def _human_engaged_targets(self, player: Any) -> list:
        """与 G4 存在 ENGAGED_WITH 关系的存活、同地点单位。"""
        markers = getattr(self.state, "markers", None)
        if markers is None or not hasattr(markers, "get_related"):
            return []
        my_loc = getattr(player, "location", None)
        targets = []
        for eid in markers.get_related(self.player_id, "ENGAGED_WITH"):
            if eid == self.player_id:
                continue
            actor = None
            if hasattr(self.state, "get_actor"):
                actor = self.state.get_actor(eid)
            if actor is None:
                actor = self.state.get_player(eid)
            if actor is None or not actor.is_alive():
                continue
            if getattr(actor, "location", None) != my_loc:
                continue
            targets.append(actor)
        return targets

    def _human_performance_available(self, player: Any, m9: Any) -> bool:
        """即演/公演预检：人形态 + SP≥1 + 近战武器 + 同地点 engaged 目标。"""
        if self.form != FORM_HUMAN or m9 is None:
            return False
        if m9.get_sp(self.player_id) < 1:
            return False
        return bool(self._human_melee_weapons(player)) \
            and bool(self._human_engaged_targets(player))

    def _pick_human_weapon(self, player: Any) -> Optional[Any]:
        weapons = self._human_melee_weapons(player)
        if not weapons:
            return None
        if len(weapons) == 1:
            return weapons[0]
        ctrl = getattr(player, "controller", None)
        names = [getattr(w, "name", "?") for w in weapons]
        try:
            choice = ctrl.choose(
                m9_text("talents.g4.performance.choose_weapon_prompt"), names,
                context={"phase": "T0", "situation": "g4_strike_pick_weapon"})
        except Exception:
            choice = names[0]
        return next((w for w in weapons if getattr(w, "name", "?") == choice),
                    weapons[0])

    def _pick_human_target(self, player: Any) -> Optional[Any]:
        targets = self._human_engaged_targets(player)
        if not targets:
            return None
        if len(targets) == 1:
            return targets[0]
        ctrl = getattr(player, "controller", None)
        names = [getattr(t, "name", getattr(t, "player_id", "?")) for t in targets]
        try:
            choice = ctrl.choose(
                m9_text("talents.g4.performance.choose_target_prompt"), names,
                context={"phase": "T0", "situation": "g4_strike_pick_target"})
        except Exception:
            choice = names[0]
        return next(
            (t for t in targets
             if getattr(t, "name", getattr(t, "player_id", "?")) == choice),
            targets[0])

    def _human_strike(self, player: Any, target: Any, weapon: Any,
                      public: bool = False) -> dict:
        from engine.m9.combat import resolve_damage
        kwargs = {}
        if public:
            kwargs["bonus_damage"] = float(_g4("human_public_bonus", 1))
        return resolve_damage(
            attacker=player,
            target=target,
            weapon=weapon,
            game_state=self.state,
            is_talent_attack=True,
            source_kind="g4_human_public" if public else "g4_human_improvise",
            **kwargs,
        )

    def _do_human_improvise(self, player: Any, m9: Any,
                            round_num: int) -> Tuple[str, bool]:
        if not self._human_performance_available(player, m9):
            return m9_text("talents.g4.performance.err_improvise_precheck_failed"), False
        if m9.dispatch_improvise(self.player_id, round_num,
                                 source_id="g4_human_improvise") is None:
            return m9_text("talents.g4.performance.err_sp_insufficient_improvise"), False
        weapon = self._pick_human_weapon(player)
        target = self._pick_human_target(player)
        if weapon is None or target is None:
            return m9_text("talents.g4.performance.err_no_weapon_or_target"), False
        result = self._human_strike(player, target, weapon, public=False)
        self._gain_ember(int(_g4("human_performance_ember", 2)),
                         m9_text("talents.g4.ember.reason_improvise"))
        lines = [m9_text("talents.g4.performance.improvise_header",
                         player=player.name, weapon=weapon.name,
                         target=getattr(target, 'name', target.player_id))]
        lines.extend(f"   {d}" for d in result.get("details", []))
        return "\n".join(lines), True

    def _do_human_public(self, player: Any, m9: Any,
                         round_num: int) -> Tuple[str, bool]:
        if not self._human_performance_available(player, m9):
            return m9_text("talents.g4.performance.err_public_precheck_failed"), False
        if not self._ensure_public_seat(player, m9, round_num):
            return m9_text("talents.g4.performance.err_sp_or_public_seat_cancel"), False
        weapon = self._pick_human_weapon(player)
        if weapon is None:
            return m9_text("talents.g4.performance.err_no_melee_weapon"), False
        targets = self._human_engaged_targets(player)
        if not targets:
            return m9_text("talents.g4.performance.err_no_engaged_target"), False
        lines = [m9_text("talents.g4.performance.public_header",
                         player=player.name, weapon=weapon.name,
                         count=len(targets))]
        for target in list(targets):
            if not target.is_alive():
                continue
            result = self._human_strike(player, target, weapon, public=True)
            lines.extend(f"   {d}" for d in result.get("details", []))
        self._gain_ember(int(_g4("human_performance_ember", 2)),
                         m9_text("talents.g4.ember.reason_public"))
        self.state.log_event("g4_human_public_performed",
                             player=self.player_id,
                             weapon=weapon.name,
                             targets=[getattr(t, "player_id",
                                              getattr(t, "unit_id", ""))
                                      for t in targets])
        return "\n".join(lines), True

    # ════════════════════════════════════════════════════════
    #  负世主动燃尽（完整额外行动来源 g4_savior_active_burn）
    # ════════════════════════════════════════════════════════

    def describe_status(self) -> str:
        """M9 状态口径：火种/形态/余烬生命/毁伤/tick。"""
        form_map = {
            FORM_HUMAN: m9_text("talents.g4.status.form_human"),
            FORM_FULL: m9_text("talents.g4.status.form_full"),
            FORM_INCOMPLETE: m9_text("talents.g4.status.form_incomplete"),
        }
        parts = [form_map.get(self.form, str(self.form)),
                 m9_text("talents.g4.status.ember",
                         ember=int(getattr(self, 'divinity', 0) or 0))]
        if self.form != FORM_HUMAN:
            parts.append(m9_text(
                "talents.g4.status.ember_hp",
                hp=f"{float(getattr(self, 'ember_hp', 0) or 0):g}"))
            parts.append(m9_text("talents.g4.status.ruin",
                                 ruin=int(getattr(self, 'ruin_damage', 0) or 0),
                                 cap=int(_g4('ruin_cap', 9))))
            parts.append(m9_text(
                "talents.g4.status.ticks",
                ticks=int(getattr(self, 'form_ticks', 0) or 0)))
        return " | ".join(parts)

    def get_t0_option(self, player: Any) -> Optional[dict]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().get_t0_option(player)
        m9 = getattr(self.state, "m9_system", None)
        if self.form == FORM_HUMAN:
            if self.divinity >= 12 and getattr(self, "m9_burden_unlocked", False):
                return {"name": m9_text("talents.g4.t0.active_burn_name"),
                        "description": m9_text(
                            "talents.g4.t0.active_burn_description"),
                        "m9_kind": "g4_active_burn"}
            if self._human_performance_available(player, m9):
                sp = m9.get_sp(self.player_id)
                ember_gain = int(_g4("human_performance_ember", 1))
                if sp >= 2:
                    return {"name": m9_text("talents.g4.t0.performance_name"),
                            "description": m9_text(
                                "talents.g4.t0.performance_description",
                                ember_gain=ember_gain),
                            "m9_kind": "g4_human_performance"}
                return {"name": m9_text("talents.g4.t0.improvise_name"),
                        "description": m9_text(
                            "talents.g4.t0.improvise_description",
                            ember_gain=ember_gain),
                        "m9_kind": "g4_human_performance"}
            return None
        if self.form in (FORM_FULL, FORM_INCOMPLETE):
            phase = getattr(self.state, "current_phase", "")
            seated = (m9 is not None and m9._public_holder_by_round.get(
                getattr(self.state, "current_round", 1)) == self.player_id)
            if m9 is not None and m9.get_sp(self.player_id) >= 2 \
                    and self.ruin_damage > 0 \
                    and (phase != "r3_actions" or seated):
                return {"name": m9_text("talents.g4.t0.challenge_name"),
                        "description": m9_text(
                            "talents.g4.t0.challenge_description"),
                        "m9_kind": "g4_challenge"}
        return None

    def execute_t0(self, player: Any):
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().execute_t0(player)
        m9 = getattr(self.state, "m9_system", None)
        round_num = getattr(self.state, "current_round", 1)
        if self.form == FORM_HUMAN:
            if self.divinity >= 12 and m9 is not None \
                    and getattr(self, "m9_burden_unlocked", False):
                grant = m9.dispatch_full_extra(self.player_id, round_num,
                                               "g4_savior_active_burn")
                if grant is None:
                    return m9_text("talents.g4.t0.err_full_extra_unavailable"), False
                self._enter_savior_state(player, is_manual=True, full=True)
                return m9_text("talents.g4.t0.active_burn_result",
                               player=player.name), True
            if not self._human_performance_available(player, m9):
                return m9_text(
                    "talents.g4.t0.err_performance_precheck_failed"), False
            ctrl = getattr(player, "controller", None)
            public_ready = (m9.get_sp(self.player_id) >= 2
                            and m9.assign_public_slot(round_num)
                            == self.player_id)
            options = [m9_text("talents.g4.t0.option_public"),
                       m9_text("talents.g4.t0.option_improvise")] \
                if public_ready else [m9_text("talents.g4.t0.option_improvise")]
            try:
                mode = ctrl.choose(
                    m9_text("talents.g4.t0.choose_mode_prompt"), options,
                    context={"phase": "T0",
                             "situation": "g4_human_performance_mode"})
            except Exception:
                mode = options[0]
            if "公演" in mode:
                return self._do_human_public(player, m9, round_num)
            return self._do_human_improvise(player, m9, round_num)
        if self.form in (FORM_FULL, FORM_INCOMPLETE) and m9 is not None \
                and m9.get_sp(self.player_id) >= 2 and self.ruin_damage > 0:
            if not self._ensure_public_seat(player, m9, round_num):
                return m9_text("talents.g4.t0.err_sp_or_public_seat"), False
            msg = self._run_challenge(player)
            return msg, True
        return m9_text("talents.g4.t0.err_condition_not_met"), False

    # ════════════════════════════════════════════════════════
    #  焚诏拉条（合同 §3.2-§5）：快照 → 秘密承诺 → 响应 → 反击 → 天裁
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        if m9.assign_public_slot(round_num) != player.player_id:
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
                snapshot.append((pid, p.get_initiative_bonus(), has_attack))
        if not snapshot:
            return m9_text("talents.g4.challenge.err_no_target")
        # 拉条期间 G4 获得救世主减伤（合同 §4.1 第 3 步；[待风洞]）
        self._challenge_reduction = int(_g4("challenge_reduction", 3))
        try:
            return self._run_challenge_responses(player, me, snapshot)
        finally:
            self._challenge_reduction = 0

    def _run_challenge_responses(self, player: Any, me: Any,
                                 snapshot: List[Tuple[str, int, bool]]) -> str:
        """焚诏响应编排：秘密承诺 → 先攻降序响应攻击 → 统一反击 → 死星天裁。"""
        from engine.m9.combat import resolve_damage
        commitments = {}
        for pid, _, _ in snapshot:
            p = self.state.get_player(pid)
            try:
                choice = p.controller.choose(
                    m9_text("talents.g4.challenge.choose_prompt", name=p.name),
                    [m9_text("talents.g4.challenge.option_attack"),
                     m9_text("talents.g4.challenge.option_refuse")])
            except Exception:
                choice = "拒战"
            commitments[pid] = "attack" if choice == "攻击" else "refuse"

        d = max(1, self.ruin_damage)
        # 反击池 = 毁伤 × 每点池倍率；J = 天裁每段伤害（数值外提 [待风洞]）
        counter_total = float(d) * float(_g4("counter_pool_per_ruin", 1.0))
        judgment_per_segment = float(_g4("judgment_per_segment", 2.0))
        adjudicator = ChallengeAdjudicator(snapshot, counter_total,
                                           judgment_per_segment)
        result = adjudicator.resolve(commitments)

        lines = [m9_text("talents.g4.challenge.header")]
        # 响应：攻击者按先攻降序（快照 init 降序，确定性兜底 ID）执行合法攻击载体
        for pid, _, _ in sorted(snapshot,
                                key=lambda s: (s[1], s[0]), reverse=True):
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
                lines.append(m9_text("talents.g4.challenge.response_attack_line",
                                     attacker=attacker.name, weapon=weapon.name,
                                     damage=r['hp_damage']))
            else:
                r = resolve_damage(attacker, me, weapon=None,
                                   game_state=self.state,
                                   raw_damage_override=int(
                                       _g4("challenge_punch", 2)),
                                   damage_attribute_override="__无视__",
                                   source_kind="g4_challenge_attack")
                lines.append(m9_text("talents.g4.challenge.response_punch_line",
                                     attacker=attacker.name,
                                     damage=r['hp_damage']))
            if (getattr(me, "hp", 0) <= 0
                    or self.form not in (FORM_FULL, FORM_INCOMPLETE)):
                # G4 真正打断（审计 v0.1 场景 15）：死亡或被迫退出形态 →
                # 停止后续响应并取消反击与天裁，只执行无额外载荷退场清理
                lines.append(m9_text("talents.g4.challenge.forced_exit_line"))
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
            lines.append(m9_text("talents.g4.challenge.counter_line",
                                 target=target.name, damage=r['hp_damage']))

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
            suffix = m9_text("talents.g4.challenge.absolute_death_suffix") \
                if r.get("absolute_dead") else ""
            lines.append(m9_text("talents.g4.challenge.judgment_line",
                                 target=target.name, damage=r['hp_damage'],
                                 suffix=suffix))
        self.judgment_segments = max(0, d - sum(
            1 for c in commitments.values() if c == "attack"))
        self.state.log_event("g4_judgment_completed", player=self.player_id,
                             attackers=sum(
                                 1 for c in commitments.values()
                                 if c == "attack"),
                             refusers=sum(
                                 1 for c in commitments.values()
                                 if c != "attack"))
        return "\n".join(lines)

    def _forced_exit(self, me: Any) -> None:
        """强制退场：停止后续响应、取消反击与天裁，只执行无额外载荷的退场清理。"""
        self._exit_savior_state()
        if getattr(me, "hp", 0) <= 0:
            me.hp = 1.0

    # ════════════════════════════════════════════════════════
    #  m9 结算协议：强化普攻产毁伤 / 拉条减伤（数值挂载，阶段 8 校准）
    # ════════════════════════════════════════════════════════

    def m9_modify_outgoing(self, attacker: Any, target: Any, weapon: Any,
                           raw: float) -> float:
        """强化普攻 = 正常单体攻击载荷（合同 §3.1，不把毁伤池当攻击加值）。"""
        return raw

    def m9_on_attack(self, hit: Any, target: Any) -> None:
        """强化普攻结算后取得毁伤（合同 §2.2/§3.1：不使用强化普攻不得毁伤）。"""
        if self.form not in (FORM_FULL, FORM_INCOMPLETE):
            return
        gain = int(_g4("ruin_gain_per_attack", 2))
        cap = int(_g4("ruin_cap", 12))
        if self.ruin_damage < cap:
            self.ruin_damage = min(cap, self.ruin_damage + gain)
            self.state.log_event("g4_ruin_gain", player=self.player_id,
                                 gain=gain, ruin=self.ruin_damage)

    def m9_modify_incoming(self, hit: Any) -> None:
        """形态内减伤：常规形态减伤 + 焚诏拉条期间救世主减伤（§4.1 第 3 步）。"""
        if self.form in (FORM_FULL, FORM_INCOMPLETE):
            reduction = int(getattr(self, "_challenge_reduction", 0))
            hit.damage = max(0, hit.damage - reduction)
