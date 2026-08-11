"""M9 G1 火萤·燃烧循环天赋（profile: m9-rfc，G1 合同 v0.3）。

继承 v2exp G1MythFire（复用炽愿 temp-HP 吸收、supernova 载荷），覆写 M9 差异：
- 三形态状态机（卸甲常态/次级燃烧/完全燃烧/繁育）+ 失熵量表；
- R4 冻结结算序：繁育倒计时(绝对死) → 失熵累积(形态速率) → 飞萤回响减 →
  卸甲调息减 → 阈值结算(炽愿抵扣→碎外甲→无甲扣HP) → 完全燃烧窗口过期；
- 着装(1 SP 即演式)/卸甲(免费)/完全燃烧(2 SP 公演) 入口；
- 繁育：完全燃烧窗口内正常致死替代（绝对死亡来源绕过）；倒计时归零=绝对死亡；
- 繁育超新星：每轮第一次合法 move 根行动触发（AOE + 灼烧 + 地点摧毁骨架）。
数值一律读 `m9_talents_extended.g1.*`（[待风洞]）。
"""
from __future__ import annotations

from typing import Any, Optional

from engine.balance import get as bget
from talents.g1_firefly import G1MythFire

FORM_ARMORLESS = "armorless"
FORM_SECONDARY = "secondary"
FORM_FULL_BURN = "full_burn"
FORM_PROPAGATION = "propagation"


def _g1(key: str, default):
    return bget("m9_talents_extended", "g1", key, default=default)


class G1MythFire9(G1MythFire):
    """M9 G1（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。"""

    name = "火萤IV型-完全燃烧"

    def __init__(self, player_id: str, game_state: Any) -> None:
        super().__init__(player_id, game_state)
        self.form = FORM_ARMORLESS
        self.entropy = 0.0
        self.full_burn_until = None       # 完全燃烧窗口截止轮
        self.propagation_rounds = None    # 繁育倒计时（None=未进入）
        self.propagation_start_round = None
        self._move_supernova_used_round = -1
        self._restricted_followup_round = -1  # 每全局轮一次受限追加

    def on_register(self):
        """M9：炽愿按 ardent_initial 起步；形态=卸甲常态。"""
        super().on_register()
        self.form = FORM_ARMORLESS
        self.ardent_wish_charges = min(
            int(_g1("ardent_initial", 1)), int(_g1("ardent_cap", 6)))

    def on_turn_start(self, player):
        """M9：0.5 血自愈退役（合同 §3.4）；无操作。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return super().on_turn_start(player)
        return None

    def on_turn_end(self, player, action_type):
        """M9：v2exp 行动计数退役（失熵改 R4 冻结序驱动）。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return super().on_turn_end(player, action_type)
        return None

    # ════════════════════════════════════════════════════════
    #  T0 入口：着装 / 卸甲 / 完全燃烧
    # ════════════════════════════════════════════════════════

    def get_t0_option(self, player: Any) -> Optional[dict]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        sp = m9.get_sp(self.player_id)
        if self.form == FORM_ARMORLESS and sp >= 1:
            return {"name": "着装宣言：次级燃烧", "description": "消耗 1 SP",
                    "m9_kind": "g1_dress"}
        if self.form == FORM_SECONDARY:
            options = []
            if sp >= 2:
                options.append("完全燃烧（公演 2 SP）")
            if sp >= 1:
                options.append("着装宣言（已着装，可再选择）")
            return {"name": "火萤宣言", "description": "；".join(options) or "卸甲宣言（免费）",
                    "m9_kind": "g1_dress"}
        return None

    def execute_t0(self, player: Any):
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return "❌ M9 天赋未启用", False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return "❌ M9 机制未挂载", False
        ctrl = getattr(player, "controller", None)
        round_num = getattr(self.state, "current_round", 1)
        if self.form == FORM_ARMORLESS:
            if m9.get_sp(self.player_id) < 1:
                return "❌ SP 不足，着装取消", False
            if m9.dispatch_improvise(self.player_id, round_num) is None:
                return "❌ SP 不足，着装取消", False
            self.form = FORM_SECONDARY
            self._grant_supernova(player)
            return f"✨ {player.name} 着装：次级燃烧！", True
        if self.form == FORM_SECONDARY:
            try:
                want = ctrl.choose("火萤宣言：", ["完全燃烧（公演 2 SP）",
                                                  "卸甲宣言（免费）"])
            except Exception:
                want = "卸甲宣言（免费）"
            if "完全燃烧" in want:
                if m9.get_sp(self.player_id) < 2:
                    return "❌ SP 不足，完全燃烧取消", False
                if not self._ensure_public_seat(player, m9, round_num):
                    return "❌ SP/公演位不足", False
                self.form = FORM_FULL_BURN
                self.full_burn_until = round_num + int(_g1("full_burn_rounds", 3))
                heal = int(_g1("full_burn_heal", 2))
                player.hp = min(getattr(player, "max_hp", 20),
                                getattr(player, "hp", 0) + heal)
                return f"🔥 {player.name} 完全燃烧！（窗口 {self.full_burn_until} 轮）", True
            self.form = FORM_ARMORLESS
            return f"🍃 {player.name} 卸甲：回归常态。", True
        return "❌ 当前形态不可宣言", False

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        if m9.assign_public_slot(round_num) != player.player_id:
            if not m9.register_performance(player.player_id, round_num):
                return False
        return m9.dispatch_public(player.player_id, round_num) is not None

    # ════════════════════════════════════════════════════════
    #  M9 结算协议：形态加成 / 致死替代
    # ════════════════════════════════════════════════════════

    def m9_modify_outgoing(self, attacker: Any, target: Any, weapon: Any,
                           raw: float) -> float:
        """形态攻击加成：卸甲惩罚 / 次级+ / 完全燃烧+。"""
        if self.form == FORM_ARMORLESS:
            return raw - int(_g1("unarmored_atk_penalty", 2))
        if self.form == FORM_SECONDARY:
            return raw + int(_g1("sam_atk_bonus", 3))
        if self.form == FORM_FULL_BURN:
            return raw + int(_g1("full_burn_atk_bonus", 4))
        return raw

    def m9_modify_incoming(self, hit: Any) -> None:
        """形态受击：卸甲更脆 / 次级减伤。"""
        if self.form == FORM_ARMORLESS:
            hit.damage += int(_g1("unarmored_def_penalty", 2))
        elif self.form == FORM_SECONDARY:
            hit.damage = max(0, hit.damage - int(_g1("sam_def_bonus", 2)))

    def m9_on_lethal(self, target: Any, attacker: Any,
                     source_kind: Optional[str]) -> Optional[str]:
        """完全燃烧窗口内正常致死 → 繁育替代（绝对死亡来源不触发）。"""
        from engine.m9.combat import is_absolute_death_source
        if self.form == FORM_FULL_BURN and not is_absolute_death_source(source_kind):
            self._enter_propagation(target)
            return "g1_propagation"
        return None

    def _enter_propagation(self, player: Any) -> None:
        """进入繁育：替换并结束完全燃烧窗口；HP 按 propagation_hp。"""
        self.form = FORM_PROPAGATION
        self.full_burn_until = None
        self.propagation_start_round = getattr(self.state, "current_round", 1)
        self.propagation_rounds = int(_g1("propagation_rounds", 3))
        player.hp = max(0, int(_g1("propagation_hp", 1)))
        player.is_awake = True

    # ════════════════════════════════════════════════════════
    #  R4 冻结结算序（合同 §3.3）
    # ════════════════════════════════════════════════════════

    def on_round_end(self, round_num):
        me = self.state.get_player(self.player_id)
        if not me or not me.is_alive():
            return

        # 2. 繁育倒计时（绝对死亡；先于一切失熵）
        if self.form == FORM_PROPAGATION:
            self.propagation_rounds -= 1
            if self.propagation_rounds <= 0:
                self._absolute_death_by_propagation(me)
                return
            return  # 繁育期间不累积失熵

        if self.form == FORM_ARMORLESS or self.form == FORM_SECONDARY \
                or self.form == FORM_FULL_BURN:
            # 3. 失熵累积（形态速率）
            rate = {FORM_ARMORLESS: int(_g1("entropy_gain_unarmored", 1)),
                    FORM_SECONDARY: int(_g1("entropy_gain_sam", 2)),
                    FORM_FULL_BURN: int(_g1("entropy_gain_full_burn", 3))}[self.form]
            # 终焉阶段：失熵停止累积（m5_clock 黄昏/终焉）
            try:
                from engine import world_clock
                phase = world_clock.active_value(self.state, "phase",
                                                 default="day")
                if phase in ("dusk", "apocalypse"):
                    rate = 0
            except Exception:
                pass
            self.entropy = min(float(_g1("entropy_cap", 12)),
                               self.entropy + rate)

            # 4. 飞萤的回响：诗篇施放后才减失熵（阶段 7 由 G5 诗篇设标记）
            poem_reduce = 0
            if getattr(self, "_firefly_echo_active", False) \
                    and self.form == FORM_ARMORLESS:
                poem_reduce = int(bget("m9_talents_extended", "g5",
                                       "poem_firefly_entropy_reduction",
                                       default=0))
            if poem_reduce > 0:
                self.entropy = max(0.0, self.entropy - poem_reduce)

            # 5. 卸甲调息：卸甲常态 + 本轮无攻击行动 → 失熵回落
            last = getattr(me, "last_action_type", "")
            if self.form == FORM_ARMORLESS and last not in ("attack", "shoot",
                                                            "hook"):
                self.entropy = max(0.0, self.entropy - int(_g1("entropy_recover", 1)))

            # 6. 阈值结算：炽愿抵扣 → 碎外甲 → 无甲扣 HP
            if self.entropy >= float(_g1("entropy_threshold", 6)):
                self._entropy_settle(me)

            # 7. 完全燃烧窗口过期：强制回卸甲常态 + 立即结算
            if self.form == FORM_FULL_BURN and self.full_burn_until is not None \
                    and round_num >= self.full_burn_until:
                self.form = FORM_ARMORLESS
                self.full_burn_until = None
                if self.entropy >= float(_g1("entropy_threshold", 6)):
                    self._entropy_settle(me)

    def _entropy_settle(self, me: Any) -> None:
        """阈值结算：炽愿可选抵扣（有甲时）；否则碎一件外甲；无甲扣 HP。"""
        from models.equipment import ArmorLayer
        outer = me.armor.get_active(ArmorLayer.OUTER) if me.armor else []
        if outer and self.ardent_wish_charges > 0:
            self.ardent_wish_charges -= 1
        elif outer:
            me.armor.remove_piece(outer[0])
        else:
            me.hp = max(0, me.hp - int(_g1("entropy_hp_loss", 4)))
            if me.hp <= 0:
                me.hp = 0
        self.entropy = max(0.0, self.entropy - int(_g1("entropy_reset_amount", 4)))

    def _absolute_death_by_propagation(self, me: Any) -> None:
        """繁育倒计时归零：绝对死亡（跳过 T7/保险；不进往世层）。"""
        me.hp = 0
        self.state.markers.on_player_death(me.player_id)
        if self.state.police_engine:
            self.state.police_engine.on_player_death(me.player_id)
        from engine.round_manager import RoundManager
        RoundManager.notify_all_talents_of_death(
            self.state, me.player_id, killer_id=None)
        self.state.log_event("g1_propagation_death", player=self.player_id)

    # ════════════════════════════════════════════════════════
    #  繁育超新星：每轮第一次合法 move 根行动触发（合同 §5.3）
    # ════════════════════════════════════════════════════════

    def m9_on_root_move(self, player: Any) -> None:
        """引擎 move 根行动后调用：繁育形态每全局轮第一次 move 触发超新星。"""
        if self.form != FORM_PROPAGATION:
            return
        round_num = getattr(self.state, "current_round", 1)
        if self._move_supernova_used_round >= round_num:
            return
        self._move_supernova_used_round = round_num
        self._m9_supernova_burst(player)

    def _m9_supernova_burst(self, player: Any) -> None:
        """繁育超新星：地点 AOE（m9 结算）+ 灼烧 + 地点摧毁（合同 §5.4）。

        摧毁：标记永久不可进入、逐出全部存活单位（玩家与 G2 影身）回 home
        （home 永不摧毁，作最后安全地点兜底）、警察局停机。
        """
        from engine.m9.combat import resolve_damage
        dmg = int(_g1("supernova_damage", 8))
        loc = getattr(player, "location", None)
        for t in self.state.players_at_location(loc):
            if t.player_id == self.player_id:
                continue
            resolve_damage(player, t, weapon=None, game_state=self.state,
                           raw_damage_override=dmg,
                           damage_attribute_override="__无视__",
                           source_kind="g1_propagation_supernova")
        # 地点摧毁
        if loc and loc != "home":
            destroyed = getattr(self.state, "m9_destroyed_locations", None)
            if destroyed is not None and loc not in destroyed:
                destroyed.add(loc)
                self.state.log_event("location_destroyed", player=self.player_id,
                                     location=loc)
                self._evict_location(loc)
        # 警察局停机
        if loc == "警察局":
            pe = getattr(self.state, "police_engine", None)
            if pe is not None and hasattr(pe, "permanently_disable"):
                pe.permanently_disable("被繁育超新星摧毁")

    def _evict_location(self, loc: str) -> None:
        """逐出：该地点全部存活单位回 home（G2 影身一并逐出）。"""
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and getattr(p, "location", None) == loc:
                p.location = "home"
        shadows = getattr(self.state, "m9_shadows", {})
        for actor in list(shadows.values()):
            if actor.is_alive() and getattr(actor, "location", None) == loc:
                actor.location = "home"
