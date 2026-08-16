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
from engine.m9.text import m9_text
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
        self.max_ardent_wish_charges = int(_g1("ardent_cap", 6))
        self.form = FORM_ARMORLESS
        self.entropy = 0.0
        self.full_burn_until = None       # 完全燃烧窗口截止轮
        self.propagation_rounds = None    # 繁育倒计时（None=未进入）
        self.propagation_start_round = None
        self._propagation_used = False    # 繁育每局至多一次（合同 §5.3）
        self._move_supernova_used_round = -1
        self._restricted_followup_round = -1  # 每全局轮一次受限追加
        self._armorless_free_find_round = -1  # 卸甲每轮一次免费 find
        self._entropy_form_round = -1         # R4 按本轮首次行动开始形态结算
        self._entropy_form_at_action_start = FORM_ARMORLESS
        self._lifesteal_round = -1            # 攻击自愈：每全局轮至多一次
        self._dress_supernova_grants = 0      # 着装授予超新星次数（裁决：每局限 cap）
        self._dress_cooldown_until = -1       # 卸甲后着装冷却（裁决 C）
        self._burnout_lockout_until = -1      # 燃烧殆尽后着甲锁定（裁决：虚弱期）

    def on_register(self):
        """M9：炽愿按 ardent_initial 起步；形态=卸甲常态。"""
        super().on_register()
        self.form = FORM_ARMORLESS
        self.max_ardent_wish_charges = int(_g1("ardent_cap", 6))
        self.ardent_wish_charges = min(
            int(_g1("ardent_initial", 1)), self.max_ardent_wish_charges)

    def on_turn_start(self, player):
        """M9：0.5 血自愈退役；冻结本轮行动开始时的失熵档位。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().on_turn_start(player)
        round_num = getattr(self.state, "current_round", 1)
        if self._entropy_form_round != round_num:
            self._entropy_form_round = round_num
            self._entropy_form_at_action_start = self.form
        return None

    def m9_blocked_action_types(self) -> set[str]:
        """战甲形态禁止发育类根行动（合同 §2.2，完全燃烧继承次级限制）。"""
        if self.form in (FORM_SECONDARY, FORM_FULL_BURN):
            return {"find", "interact"}
        return set()

    def describe_status(self) -> str:
        """M9 状态口径：形态/失熵/炽愿/超新星/繁育倒计时。"""
        form_map = {
            FORM_ARMORLESS: m9_text("talents.g1.status_form_armorless"),
            FORM_SECONDARY: m9_text("talents.g1.status_form_secondary"),
            FORM_FULL_BURN: m9_text("talents.g1.status_form_full_burn"),
            FORM_PROPAGATION: m9_text("talents.g1.status_form_propagation"),
        }
        parts = [form_map.get(getattr(self, "form", ""),
                              str(getattr(self, "form", "")))]
        entropy = float(getattr(self, "entropy", 0.0) or 0.0)
        threshold = int(_g1("entropy_threshold", 6))
        parts.append(m9_text("talents.g1.status_entropy",
                             entropy=f"{entropy:g}", threshold=threshold))
        charges = int(getattr(self, "ardent_wish_charges", 0) or 0)
        if charges:
            parts.append(m9_text("talents.g1.status_ardent_wish",
                                 charges=charges))
        if getattr(self, "has_supernova", False):
            parts.append(m9_text("talents.g1.status_supernova_ready"))
        if self.form == FORM_PROPAGATION \
                and getattr(self, "propagation_rounds", None) is not None:
            parts.append(m9_text(
                "talents.g1.status_propagation_rounds",
                rounds=int(self.propagation_rounds)))
        elif self.form == FORM_FULL_BURN and getattr(
                self, "full_burn_until", None) is not None:
            parts.append(m9_text(
                "talents.g1.status_full_burn_until",
                round=int(self.full_burn_until)))
        return " | ".join(parts)

    def can_use_legacy_supernova(self) -> bool:
        """旧 move 过载载荷只在次级/完全燃烧可用（合同 §4）。"""
        return self.form in (FORM_SECONDARY, FORM_FULL_BURN)

    def supernova_payload(self):
        """M9 载荷；完全燃烧按合同把加成同时上探伤害与灼烧。"""
        damage = int(_g1("supernova_damage", 8))
        burns = int(_g1("supernova_burn", 2))
        if self.form == FORM_FULL_BURN:
            bonus = int(_g1("full_burn_supernova_bonus", 2))
            damage += bonus
            burns += bonus
        return damage, float(_g1("supernova_pierce", 0.5)), burns

    def trigger_supernova(self, player: Any, destination: str,
                          game_state: Any) -> None:
        """M9 超新星统一走当前玩家/固定警力管线。

        旧实现只遍历 legacy 警察容器，且会在 M9 ``finalize_death`` 已记击杀后
        再手工加一次击杀。本覆写只修正管线与归属，载荷仍完全来自 balance。
        """
        if not self.supernova_free_use:
            self.has_supernova = False
        damage, pierce, burns = self.supernova_payload()
        from engine.m9.combat import resolve_damage

        hit_count = 0
        kill_count = 0
        for target in list(game_state.players_at_location(destination)):
            if target.player_id == player.player_id or not target.is_alive():
                continue
            result = resolve_damage(
                player, target, weapon=None, game_state=game_state,
                raw_damage_override=damage,
                damage_attribute_override="普通",
                armor_pierce_factor=pierce,
                source_kind="g1_supernova",
            )
            if result.get("success"):
                hit_count += 1
                self.apply_burn(target.player_id, stacks=burns)
            if result.get("killed"):
                kill_count += 1

        station = getattr(game_state, "m9_police", None)
        if station is not None:
            for unit in list(station.units_at(destination)):
                result = station.attack_unit(
                    player, unit.unit_id,
                    raw_damage_override=damage,
                    damage_attribute_override="普通",
                    armor_pierce_factor=pierce,
                    source_kind="g1_supernova",
                )
                if result.get("success"):
                    hit_count += 1
                if result.get("killed"):
                    kill_count += 1

        gain = hit_count + kill_count
        if gain > 0:
            self._grant_ardent_wish_from_supernova(gain)
        game_state.log_event(
            "firefly_supernova", player=self.player_id,
            location=destination, hits=hit_count, kills=kill_count,
            damage=damage, burn=burns,
        )

    def on_turn_end(self, player, action_type):
        """M9：v2exp 行动计数退役（失熵改 R4 冻结序驱动）。

        完全燃烧窗口内：标准根行动结算后 → 一次仅限 move/attack 的受限追加
        `ActionGrant(kind=restricted_followup, source_id=g1_full_burn)`（§2.3）。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().on_turn_end(player, action_type)
        if self.form != FORM_FULL_BURN:
            return None
        if action_type in ("wake", "forfeit", "status", "help", "police_status"):
            return None
        round_num = getattr(self.state, "current_round", 1)
        if self._restricted_followup_round >= round_num:
            return None  # 每全局轮一次
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        grant = m9.dispatch_restricted_followup(
            self.player_id, round_num, "g1_full_burn")
        if grant is not None:
            self._restricted_followup_round = round_num
        return None

    # ════════════════════════════════════════════════════════
    #  T0 入口：着装 / 卸甲 / 完全燃烧
    # ════════════════════════════════════════════════════════

    def _dress_available(self, round_num: int) -> bool:
        """着装可用性：卸甲冷却（裁决 C）与燃烧殆尽虚弱锁定（裁决：虚弱期）
        均须期满。繁育形态不可着装（形态单向）。"""
        return (self.form not in (FORM_SECONDARY, FORM_FULL_BURN,
                                  FORM_PROPAGATION)
                and round_num > self._dress_cooldown_until
                and round_num > self._burnout_lockout_until)

    def _dress_grant_supernova(self, player: Any) -> bool:
        """着装授予超新星：每局至多 supernova_grant_cap 次（裁决 A）；
        繁育形态下解除上限（用户裁决；繁育自动爆发的超新星另走
        m9_on_root_move，本计数器从不限制它）。返回是否授予。"""
        cap = int(_g1("supernova_grant_cap", 3))
        if self.form == FORM_PROPAGATION or self._dress_supernova_grants < cap:
            self._dress_supernova_grants += 1
            self._grant_supernova(player)
            return True
        return False

    def get_t0_option(self, player: Any) -> Optional[dict]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        sp = m9.get_sp(self.player_id)
        round_num = getattr(self.state, "current_round", 1)
        if self.form == FORM_ARMORLESS and sp >= 1 and \
                self._dress_available(round_num):
            return {"name": m9_text("talents.g1.t0_dress_name"),
                    "description": m9_text("talents.g1.t0_dress_description"),
                    "m9_kind": "g1_dress"}
        if self.form == FORM_SECONDARY:
            options = []
            if sp >= 2:
                options.append(m9_text("talents.g1.option_full_burn_public"))
            if sp >= 1:
                options.append(m9_text("talents.g1.option_dress_again"))
            return {"name": m9_text("talents.g1.t0_declare_name"),
                    "description": "；".join(options) or m9_text(
                        "talents.g1.option_unarmor_free"),
                    "m9_kind": "g1_dress"}
        return None

    def execute_t0(self, player: Any):
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return m9_text("talents.g1.err_m9_disabled"), False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return m9_text("talents.g1.err_m9_not_mounted"), False
        ctrl = getattr(player, "controller", None)
        round_num = getattr(self.state, "current_round", 1)
        if self.form == FORM_ARMORLESS:
            if not self._dress_available(round_num):
                return m9_text("talents.g1.err_dress_unavailable"), False
            if m9.get_sp(self.player_id) < 1:
                return m9_text("talents.g1.err_sp_insufficient_dress_cancel"), False
            if m9.dispatch_improvise(self.player_id, round_num) is None:
                return m9_text("talents.g1.err_sp_insufficient_dress_cancel"), False
            self.form = FORM_SECONDARY
            granted = self._dress_grant_supernova(player)
            # 换装宣言不消费行动槽（合同 §2.0；仅耗 1 SP，与 G4 转折口径统一）
            note = "" if granted else m9_text("talents.g1.note_supernova_exhausted")
            return m9_text("talents.g1.dress_success",
                           name=player.name, note=note), False
        if self.form == FORM_SECONDARY:
            public_ready = (m9.get_sp(self.player_id) >= 2
                            and m9.assign_public_slot(round_num)
                            == self.player_id)
            options = [m9_text("talents.g1.option_unarmor_free")]
            if public_ready:
                options.insert(0, m9_text("talents.g1.option_full_burn_public"))
            try:
                want = ctrl.choose(
                    m9_text("talents.g1.declare_choose_prompt"), options)
            except Exception:
                want = options[-1]
            if "完全燃烧" in want:
                if m9.get_sp(self.player_id) < 2:
                    return m9_text("talents.g1.err_sp_insufficient_full_burn_cancel"), False
                if not self._ensure_public_seat(player, m9, round_num):
                    return m9_text("talents.g1.err_sp_or_public_seat"), False
                self.form = FORM_FULL_BURN
                self.full_burn_until = round_num + int(_g1("full_burn_rounds", 3))
                heal = int(_g1("full_burn_heal", 2))
                player.hp = min(getattr(player, "max_hp", 20),
                                getattr(player, "hp", 0) + heal)
                return m9_text("talents.g1.full_burn_success",
                               name=player.name,
                               until=self.full_burn_until), True
            self.form = FORM_ARMORLESS
            # 卸甲宣言免费且不消费行动槽（合同 §2.0）；卸甲启动着装冷却（裁决 C）
            cooldown = int(_g1("dress_cooldown_rounds", 1))
            self._dress_cooldown_until = round_num + cooldown
            return m9_text("talents.g1.unarmor_success",
                           name=player.name, cooldown=cooldown), False
        return m9_text("talents.g1.err_form_cannot_declare"), False

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        if m9.assign_public_slot(round_num) != player.player_id:
            return False
        return m9.dispatch_public(player.player_id, round_num) is not None

    # ════════════════════════════════════════════════════════
    #  M9 结算协议：形态加成 / 致死替代
    # ════════════════════════════════════════════════════════

    def m9_modify_outgoing(self, attacker: Any, target: Any, weapon: Any,
                           raw: float) -> float:
        """形态攻击加成：卸甲惩罚 / 次级+ / 完全燃烧+；超击破对受控/灼烧目标增伤。"""
        base = raw
        if self.form == FORM_ARMORLESS:
            base = raw - int(_g1("unarmored_atk_penalty", 2))
        elif self.form == FORM_SECONDARY:
            base = raw + int(_g1("sam_atk_bonus", 3))
        elif self.form == FORM_FULL_BURN:
            base = raw + int(_g1("full_burn_atk_bonus", 4))
        # 超击破（RFC §5.1）：对处于灼烧/受控状态的合法目标增伤
        burning = getattr(target, "burn_stacks", 0) > 0
        controlled = (getattr(target, "is_shocked", False)
                      or getattr(target, "is_stunned", False)
                      or getattr(target, "is_petrified", False))
        if burning or controlled:
            base = base + int(_g1("break_bonus_damage", 2))
        return base

    def m9_modify_incoming(self, hit: Any) -> None:
        """形态受击：卸甲更脆 / 次级减伤。"""
        if self.form == FORM_ARMORLESS:
            hit.damage += int(_g1("unarmored_def_penalty", 2))
        elif self.form == FORM_SECONDARY:
            hit.damage = max(0, hit.damage - int(_g1("sam_def_bonus", 2)))

    def m9_initiative_bonus(self) -> int:
        """繁育形态先攻加成（RFC §5.3 `propagation_initiative`）。"""
        if self.form == FORM_PROPAGATION:
            return int(_g1("propagation_initiative", 10))
        return 0

    def m9_on_lethal(self, target: Any, attacker: Any,
                     source_kind: Optional[str]) -> Optional[str]:
        """完全燃烧窗口内正常致死 → 繁育替代（绝对死亡来源不触发；每局至多一次）。"""
        from engine.m9.combat import is_absolute_death_source
        if (self.form == FORM_FULL_BURN
                and not is_absolute_death_source(source_kind)
                and not self._propagation_used):
            self._enter_propagation(target)
            return "g1_propagation"
        return None

    def _enter_propagation(self, player: Any) -> None:
        """进入繁育：替换并结束完全燃烧窗口；HP 按 propagation_hp；每局至多一次。"""
        self.form = FORM_PROPAGATION
        self.full_burn_until = None
        self._propagation_used = True
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
            # 建立轮 R4 不 tick；固定轮数从下一全局轮开始消耗。
            if (self.propagation_start_round is not None
                    and round_num <= self.propagation_start_round):
                return
            self.propagation_rounds -= 1
            if self.propagation_rounds <= 0:
                self._absolute_death_by_propagation(me)
                return
            return  # 繁育期间不累积失熵

        if self.form == FORM_ARMORLESS or self.form == FORM_SECONDARY \
                or self.form == FORM_FULL_BURN:
            # 3. 失熵累积（形态速率）
            rate_form = self.form
            if self._entropy_form_round == round_num:
                rate_form = self._entropy_form_at_action_start
            rate = {FORM_ARMORLESS: int(_g1("entropy_gain_unarmored", 1)),
                    FORM_SECONDARY: int(_g1("entropy_gain_sam", 2)),
                    FORM_FULL_BURN: int(_g1("entropy_gain_full_burn", 3))}[rate_form]
            # 黄昏阶段：失熵停止累积；终焉恢复累积（失熵 + 终焉真伤是
            # 完全燃烧在长局末段的应有代价，冻结会让 G1 零代价白嫖终焉）
            try:
                from engine import world_clock
                phase = world_clock.active_value(self.state, "phase",
                                                 default="day")
                if phase == "dusk":
                    rate = 0
            except Exception:
                pass
            self.entropy = min(float(_g1("entropy_cap", 12)),
                               self.entropy + rate)

            # 4. 飞萤的回响：诗篇标记存续期减失熵（m9_poem_markers，tick 递减）
            markers = getattr(self, "m9_poem_markers", {})
            echo_left = int(markers.get("firefly_echo", 0))
            poem_reduce = 0
            if echo_left > 0 and self.form == FORM_ARMORLESS:
                poem_reduce = int(bget("m9_talents_extended", "g5",
                                       "poem_firefly_entropy_reduction",
                                       default=0))
            if echo_left > 0:
                markers["firefly_echo"] = echo_left - 1
            if poem_reduce > 0:
                self.entropy = max(0.0, self.entropy - poem_reduce)

            # 5. 卸甲调息：卸甲常态 + 本轮无攻击行动 → 失熵回落（飞萤标记加成）
            last = getattr(me, "last_action_type", "")
            if self.form == FORM_ARMORLESS and last not in ("attack", "shoot",
                                                            "hook"):
                rest = int(_g1("entropy_recover", 1))
                if echo_left > 0:
                    rest += int(bget("m9_talents_extended", "g5",
                                     "poem_firefly_rest_boost", default=0))
                self.entropy = max(0.0, self.entropy - rest)

            # 6. 阈值结算：炽愿抵扣 → 碎外甲 → 无甲扣 HP
            if self.entropy >= float(_g1("entropy_threshold", 6)):
                self._entropy_settle(me)

            # 7. 完全燃烧窗口过期：强制回卸甲常态 + 立即结算 +
            #    虚弱期着甲锁定（裁决：燃烧殆尽后 N 轮不可再着装）
            if self.form == FORM_FULL_BURN and self.full_burn_until is not None \
                    and round_num >= self.full_burn_until:
                self.form = FORM_ARMORLESS
                self.full_burn_until = None
                lockout = int(_g1("burnout_dress_lockout_rounds", 2))
                self._burnout_lockout_until = round_num + lockout
                self._entropy_settle(me)

    def _entropy_settle(self, me: Any) -> None:
        """阈值结算：炽愿可选抵扣（有甲时）；否则碎一件外甲；无甲扣 HP。"""
        from models.equipment import ArmorLayer
        outer = me.armor.get_active(ArmorLayer.OUTER) if me.armor else []
        if self.ardent_wish_charges > 0:
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
        from engine.m9.combat import finalize_death
        finalize_death(
            self.state, me, None,
            source_kind="g1_propagation", cause="g1_propagation")
        self.state.log_event("g1_propagation_death", player=self.player_id)

    # ════════════════════════════════════════════════════════
    #  R0 / 攻击自愈（完全燃烧，裁决：自愈随伤害而非随轮次）
    # ════════════════════════════════════════════════════════

    def on_round_start(self, round_num):
        """M9：R0 自愈已改为攻击造成伤害后自愈（m9_on_attack），
        此处仅保留 v2exp 兼容入口；卸甲免费 find（§2.1）由玩家主动发起。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().on_round_start(round_num)
        return None

    def m9_on_attack(self, hit: Any, target: Any) -> None:
        """完全燃烧：攻击造成伤害后自愈 full_burn_heal（每段有效命中一次）。

        取代旧"每轮 R0 无条件自愈"——不打架就没有续航，且终焉挂机
        不再白嫖回血。"""
        if self.form != FORM_FULL_BURN:
            return
        round_num = int(getattr(self.state, "current_round", 0) or 0)
        if self._lifesteal_round >= round_num:
            return  # 每全局轮至多一次（完全燃烧多段攻击不得刷回血）
        if float(getattr(hit, "damage", 0) or 0) < 1 and not getattr(
                hit, "broken", []):
            return  # 无有效伤害（被闪避/护甲全挡且未破甲）不自愈
        me = self.state.get_player(self.player_id)
        if me is None or not me.is_alive():
            return
        heal = int(_g1("full_burn_heal", 2))
        if heal > 0:
            me.hp = min(getattr(me, "max_hp", 20), getattr(me, "hp", 0) + heal)
            self._lifesteal_round = round_num
            self.state.log_event("g1_fullburn_lifesteal",
                                 player=self.player_id, heal=heal)

    def free_find_available(self, round_num: int) -> bool:
        """卸甲常态每轮一次免费 find（§2.1）是否可用。"""
        if (self.form != FORM_ARMORLESS
                or self._armorless_free_find_round >= round_num):
            return False
        me = self.state.get_player(self.player_id)
        if me is None or not me.is_alive():
            return False
        return bool(self._free_find_targets(me))

    def _free_find_targets(self, me: Any) -> list[str]:
        """复用普通 find 的同地点/存活/未交战边界，禁止跨地图远程 find。"""
        out = []
        engaged = self.state.markers.get_related(
            self.player_id, "ENGAGED_WITH")
        for pid in self.state.player_order:
            if pid == self.player_id or pid in engaged:
                continue
            target = self.state.get_player(pid)
            if (target is not None and target.is_alive()
                    and getattr(target, "location", None)
                    == getattr(me, "location", None)):
                out.append(pid)
        return out

    def do_free_find(self, me: Any) -> Tuple[str, bool]:
        """卸甲免费 find：不占行动槽；每轮一次（风筝循环的"撤离—再定位"）。"""
        round_num = getattr(self.state, "current_round", 1)
        if not self.free_find_available(round_num):
            return m9_text("talents.g1.err_free_find_unavailable"), False
        others = self._free_find_targets(me)
        if not others:
            return m9_text("talents.g1.err_no_free_find_target"), False
        ctrl = getattr(me, "controller", None)
        target = others[0]
        if ctrl is not None:
            try:
                picked = ctrl.choose(
                    m9_text("talents.g1.free_find_choose_prompt"),
                    [self.state.get_player(pid).name for pid in others])
                for pid in others:
                    if self.state.get_player(pid).name == picked:
                        target = pid
                        break
            except Exception:
                pass
        from actions.find_target import execute as _find
        me._m9_suppress_attention = True  # 免费 find 不触发关注/SP
        try:
            _find(me, target, self.state)
        finally:
            me._m9_suppress_attention = False
        self._armorless_free_find_round = round_num
        return m9_text("talents.g1.free_find_success",
                       name=me.name,
                       target=self.state.get_player(target).name), False

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
        burns = int(_g1("supernova_burn", 2))
        loc = getattr(player, "location", None)
        for t in self.state.players_at_location(loc):
            if t.player_id == self.player_id:
                continue
            resolve_damage(player, t, weapon=None, game_state=self.state,
                           raw_damage_override=dmg,
                           damage_attribute_override="__无视__",
                           source_kind="g1_propagation_supernova")
            # 超新星挂灼烧（RFC §5.1；走 M4 通用灼烧层，防双扣）
            t.burn_stacks = getattr(t, "burn_stacks", 0) + burns
        # 地点摧毁（合同 §5.4 + B5-W8 最后安全地点兜底）
        if loc and not str(loc).startswith("home"):
            destroyed = getattr(self.state, "m9_destroyed_locations", None)
            if destroyed is not None and loc not in destroyed:
                standable = self._standable_locations()
                if len(standable) <= 1 and loc in standable:
                    # 最后一个可站立地点不可被普通繁育超新星摧毁：伤害照常，
                    # 摧毁失败并广播（B5-W8 确定性兜底）
                    self.state.log_event(
                        "location_destroy_failed", player=self.player_id,
                        location=loc)
                else:
                    destroyed.add(loc)
                    self.state.log_event("location_destroyed",
                                         player=self.player_id, location=loc)
                    self._evict_location(loc)
        # 警察局停机（§3.4：关闭新举报/自动推进、清通缉/队长/掩体、中立 NPC 化）
        if loc == "警察局":
            m9_police = getattr(self.state, "m9_police", None)
            if m9_police is not None:
                m9_police.set_state_ref(self.state)
                m9_police.shut_down(m9_text("talents.g1.police_shutdown_reason"))
            pe = getattr(self.state, "police_engine", None)
            if pe is not None and hasattr(pe, "permanently_disable"):
                pe.permanently_disable(m9_text("talents.g1.police_shutdown_reason"))

    def _standable_locations(self) -> list:
        """当前可站立地点：全部合法地点减去已摧毁（home 永不摧毁）。"""
        from actions.move import get_all_valid_locations
        destroyed = getattr(self.state, "m9_destroyed_locations", set())
        return [l for l in get_all_valid_locations(self.state)
                if l not in destroyed]

    def _evict_location(self, loc: str) -> None:
        """逐出（B5-W8）：优先回 home；home 已毁 → 就地回退（当前地点若仍可
        站立）；否则 → 最后安全地点兜底（首个未毁地点）。G2 影身一并逐出。"""
        standable = self._standable_locations()

        def fallback_for(actor: Any) -> str:
            owner_pid = getattr(actor, "owner_pid", None)
            actor_pid = owner_pid or getattr(actor, "player_id", "")
            own_home = f"home_{actor_pid}"
            if own_home in standable:
                return own_home
            if loc in standable:
                return loc  # 就地回退
            return standable[0] if standable else own_home

        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and getattr(p, "location", None) == loc:
                p.location = fallback_for(p)
        shadows = getattr(self.state, "m9_shadows", {})
        for actor in list(shadows.values()):
            if actor.is_alive() and getattr(actor, "location", None) == loc:
                actor.location = fallback_for(actor)
