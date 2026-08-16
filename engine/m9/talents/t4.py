"""M9 T4 六爻天赋（profile: m9-rfc，T4 合同 v0.1）。

继承 v2exp Hexagram（复用 `_resolve` 六结果映射、`is_immune_to_debuff`、
`disabled_weapons` 清理与控制器提示），覆写 M9 差异：
- 即演（1 SP）/公演（2 SP）双演出；**即演不消费回合（裁决 A：六爻=额外资源，
  与普通行动共存），公演仍消费回合**；删除充能/回合计数与 SP 返还
  （charges/max_charges/round_counter 仅随 super().__init__ 保留以支撑
  legacy 回退路径，M9 分支不读不写不门控）；
- 六结果：潜龙勿用穿甲（qianlong_pierce_damage，pierce 0.5，非
  DIRECT_DAMAGE，死亡由 M9 管线裁决）、飞龙在天夺甲、元亨利贞金身
  （m9_modify_incoming 归零 + m9_on_lethal 免死）、亢龙有悔禁武
  （weapon_disable_rounds；仅有拳击 → 震荡，眩晕已退役）、或跃在渊
  完整额外行动（白名单源 t4_hexagram_hojump，绝不写 hexagram_extra_turn）、
  群龙无首遁走+强制位移；
- 阴阳诗天机（m9_poem_markers["yin_yang_tianji"]）：公演可指定非或跃结果，
  每次 −1，0 后标记移除；或跃在渊禁止指定；
- G6 借用核心 hexagram_cast：或跃重掷至非或跃，绝不创建完整额外行动。
数值一律读 `m9_talents_extended.t4.*`（[待风洞]）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from engine.balance import get as bget
from engine.m9.combat import resolve_damage
from engine.m9.text import m9_text
from talents.t4_hexagram import Hexagram

# 即演/公演 SP 成本（与 action_system.SP_IMPROVISE_COST/SP_PUBLIC_COST 同值）
SP_IMPROVISE_COST = 1
SP_PUBLIC_COST = 2

# 六结果键 → 执行方法（与 legacy `_resolve` 分发同构）
RESULT_METHODS: Dict[str, str] = {
    "both_scissors": "_both_scissors",
    "both_rock": "_both_rock",
    "both_paper": "_both_paper",
    "scissors_rock": "_scissors_rock",
    "scissors_paper": "_scissors_paper",
    "rock_paper": "_rock_paper",
}

# 阴阳诗天机可指定结果（或跃在渊禁止指定）
TIANJI_SPECIFIABLE: Dict[str, str] = {
    "潜龙勿用": "both_scissors",
    "飞龙在天": "both_rock",
    "元亨利贞": "both_paper",
    "亢龙有悔": "scissors_rock",
    "群龙无首": "rock_paper",
}


def _t4(key: str, default):
    return bget("m9_talents_extended", "t4", key, default=default)


class Hexagram9(Hexagram):
    """M9 T4（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。"""

    name = "六爻"

    def __init__(self, player_id: str, game_state: Any) -> None:
        super().__init__(player_id, game_state)
        from engine.m9.gate import m9_enabled
        if m9_enabled(game_state):
            for attr in ("charges", "max_charges", "round_counter"):
                if hasattr(self, attr):
                    delattr(self, attr)
        self.m9_poem_markers: Dict[str, Any] = {}  # 阴阳诗天机等标记

    # ════════════════════════════════════════════════════════
    #  T0 入口：即演（1 SP）/ 公演（2 SP）
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        """只消费 R0 已固化的公演位；T0 不得补报名。"""
        if m9.assign_public_slot(round_num) != player.player_id:
            return False
        return m9.dispatch_public(player.player_id, round_num) is not None

    def _others(self, player: Any) -> List[Any]:
        return [p for p in self.state.alive_players()
                if p.player_id != player.player_id]

    def get_t0_option(self, player: Any) -> Optional[dict]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().get_t0_option(player)
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        if not self._others(player):
            return None
        if m9.get_sp(self.player_id) >= SP_IMPROVISE_COST:
            return {"name": m9_text("talents.t4.t0.name"),
                    "description": m9_text("talents.t4.t0.description"),
                    "m9_kind": "t4_hexagram"}
        return None

    def execute_t0(self, player: Any):
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().execute_t0(player)
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return m9_text("talents.t4.err_m9_not_mounted"), False
        round_num = getattr(self.state, "current_round", 1)
        others = self._others(player)
        if not others:
            return m9_text("talents.t4.err_no_target"), False
        # SP 合法性预检先于任何 SP/公演位消费
        if m9.get_sp(self.player_id) < SP_IMPROVISE_COST:
            return m9_text("talents.t4.err_sp_insufficient_cancel"), False
        ctrl = getattr(player, "controller", None)
        public_ready = (m9.get_sp(self.player_id) >= SP_PUBLIC_COST
                        and m9.assign_public_slot(round_num)
                        == self.player_id)
        option_improvise = m9_text("talents.t4.option_improvise")
        options = [option_improvise]
        if public_ready:
            options.append(m9_text("talents.t4.option_public"))
        try:
            want = ctrl.choose(m9_text("talents.t4.choose_performance_prompt"),
                               options) if ctrl else option_improvise
        except Exception:
            want = option_improvise
        if want == "公演":
            if m9.get_sp(self.player_id) < SP_PUBLIC_COST:
                return m9_text("talents.t4.err_sp_insufficient_public"), False
            if not self._ensure_public_seat(player, m9, round_num):
                return m9_text("talents.t4.err_sp_or_public_seat"), False
            msg = self._perform_cast(player, others, allow_specify=True)
            return msg, True
        if m9.dispatch_improvise(self.player_id, round_num,
                                 "t4_hexagram_improvise") is None:
            return m9_text("talents.t4.err_sp_insufficient_cancel"), False
        msg = self._perform_cast(player, others, allow_specify=False)
        # 裁决 A：即演不消费回合——RPS 与普通行动共存（六爻 = 额外资源）；
        # 公演（2 SP、可指定卦象）仍消费回合。
        return msg, False

    def _perform_cast(self, player: Any, others: List[Any],
                      allow_specify: bool = False) -> str:
        """六爻演出编排：选猜拳对手 →（阴阳诗指定）→ 出拳判定 → 记录事件。"""
        names = [p.name for p in others]
        target_name = player.controller.choose(
            m9_text("talents.t4.choose_opponent_prompt"), names,
            context={"phase": "T0", "situation": "hexagram_pick_opponent"})
        target = next(p for p in others if p.name == target_name)

        if allow_specify:
            key = self._maybe_specify_tianji(player)
            if key is not None:
                msg = self._apply_result_key(player, target, key)
                self.state.log_event("hexagram_cast", player=player.player_id,
                                     target=target.player_id, specified=key)
                return msg

        my_choice = player.controller.choose(
            m9_text("talents.t4.rps_prompt", name=player.name), self.CHOICES,
            context={"phase": "T0", "situation": "hexagram_my_choice"})
        opp_choice = target.controller.choose(
            m9_text("talents.t4.rps_prompt", name=target.name), self.CHOICES,
            context={"phase": "T0", "situation": "hexagram_opp_choice"})
        msg = self._resolve(player, target, my_choice, opp_choice)
        self.state.log_event("hexagram_cast", player=player.player_id,
                             target=target.player_id,
                             my_choice=my_choice, opp_choice=opp_choice)
        return msg

    # ════════════════════════════════════════════════════════
    #  阴阳诗天机：公演指定非或跃结果
    # ════════════════════════════════════════════════════════

    def _maybe_specify_tianji(self, player: Any) -> Optional[str]:
        """天机指定：或跃在渊被禁（回退正常出拳，不消耗次数）。"""
        if self.m9_poem_markers.get("yin_yang_tianji", 0) <= 0:
            return None
        ctrl = getattr(player, "controller", None)
        specify_label = m9_text("talents.t4.tianji_option_specify")
        normal_label = m9_text("talents.t4.tianji_option_normal")
        try:
            want = ctrl.choose(m9_text("talents.t4.tianji_choose_prompt"),
                               [specify_label, normal_label]) if ctrl else normal_label
        except Exception:
            want = normal_label
        if want != "指定卦象":
            return None
        names = [m9_text("talents.t4.tianji_result_qianlong"),
                 m9_text("talents.t4.tianji_result_feilong"),
                 m9_text("talents.t4.tianji_result_yuanheng"),
                 m9_text("talents.t4.tianji_result_kanglong"),
                 m9_text("talents.t4.tianji_result_qunlong"),
                 m9_text("talents.t4.tianji_result_huoyue")]
        try:
            pick = ctrl.choose(m9_text("talents.t4.tianji_pick_prompt"),
                               names) if ctrl else names[0]
        except Exception:
            pick = names[0]
        key = TIANJI_SPECIFIABLE.get(pick)
        if key is None:  # 或跃在渊禁止指定
            return None
        self._spend_tianji()
        return key

    def _spend_tianji(self) -> None:
        """天机 −1；归零后标记移除。"""
        count = self.m9_poem_markers.get("yin_yang_tianji", 0)
        if count <= 1:
            self.m9_poem_markers.pop("yin_yang_tianji", None)
        else:
            self.m9_poem_markers["yin_yang_tianji"] = count - 1

    def _apply_result_key(self, player: Any, target: Any, key: str) -> str:
        method = getattr(self, RESULT_METHODS.get(key, "_both_paper"))
        return method(player, target)

    # ════════════════════════════════════════════════════════
    #  轮次钩子：只清理金身与武器禁用（无充能/回合计数）
    # ════════════════════════════════════════════════════════

    def on_round_start(self, round_num):
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().on_round_start(round_num)
        # 元亨利贞：到期失效（下轮 R0）
        if self.immunity_active and round_num >= self.immunity_expire_round:
            self.immunity_active = False
        # 亢龙有悔：清理过期的武器禁用
        still_active = []
        for pid, wname, expire_round in self.disabled_weapons:
            if round_num >= expire_round:
                p = self.state.get_player(pid)
                if p:
                    for w in getattr(p, "weapons", []):
                        if w and w.name == wname \
                                and getattr(w, "_hexagram_disabled", False):
                            w._hexagram_disabled = False
            else:
                still_active.append((pid, wname, expire_round))
        self.disabled_weapons = still_active

    # ════════════════════════════════════════════════════════
    #  潜龙勿用（双剪刀）：天雷穿甲
    # ════════════════════════════════════════════════════════

    def _both_scissors(self, player, target):
        """潜龙勿用：天雷穿甲（目标可与猜拳对手不同；死亡由 M9 管线裁决）。"""
        others = self._others(player)
        if not others:
            return m9_text("talents.t4.qianlong_no_target")
        names = [p.name for p in others]
        choice = player.controller.choose(
            m9_text("talents.t4.qianlong_choose_target_prompt"), names,
            context={"phase": "T0", "situation": "hexagram_thunder_target"})
        thunder_target = next(p for p in others if p.name == choice)
        dmg = int(_t4("qianlong_pierce_damage", 6))
        result = resolve_damage(
            attacker=player,
            target=thunder_target,
            weapon=None,
            game_state=self.state,
            raw_damage_override=dmg,
            damage_attribute_override="普通",
            armor_pierce_factor=0.5,
            is_talent_attack=True,
            source_kind="t4_hexagram_qianlong",
        )
        lines = [m9_text("talents.t4.qianlong_cast_header",
                         name=thunder_target.name)]
        lines.append(m9_text("talents.t4.qianlong_damage_line",
                             name=thunder_target.name, damage=f"{dmg}"))
        for detail in result.get("details", []):
            lines.append(f"   {detail}")
        return "\n".join(lines)

    # ════════════════════════════════════════════════════════
    #  飞龙在天（双石头）：夺甲
    # ════════════════════════════════════════════════════════

    def _both_rock(self, player, target):
        """飞龙在天：复制目标 1 层外甲给自己（不破坏目标的甲）。"""
        from models.equipment import ArmorLayer, ArmorPiece

        if target is None:
            others = self._others(player)
            if not others:
                return m9_text("talents.t4.feilong_no_target")
            names = [p.name for p in others]
            choice = player.controller.choose(
                m9_text("talents.t4.feilong_choose_target_prompt"), names,
                context={"phase": "T0", "situation": "hexagram_steal_target"})
            target = next(p for p in others if p.name == choice)

        outer_active = target.armor.get_active(ArmorLayer.OUTER)
        if not outer_active:
            return m9_text("talents.t4.feilong_no_outer_armor",
                           name=target.name)

        if len(outer_active) == 1:
            chosen_piece = outer_active[0]
        else:
            armor_names = [m9_text("talents.t4.feilong_armor_option",
                                   name=a.name, attribute=a.attribute.value)
                           for a in outer_active]
            choice = player.controller.choose(
                m9_text("talents.t4.feilong_choose_armor_prompt"), armor_names,
                context={"phase": "T0", "situation": "hexagram_steal_pick"})
            idx = armor_names.index(choice)
            chosen_piece = outer_active[idx]

        new_armor = ArmorPiece(
            chosen_piece.name, chosen_piece.attribute, ArmorLayer.OUTER, 1.0,
            priority=chosen_piece.priority,
            can_regen=chosen_piece.can_regen,
            special_tags=list(chosen_piece.special_tags),
        )
        success, reason = player.add_armor(new_armor)
        lines = [m9_text("talents.t4.feilong_header")]
        if success:
            lines.append(m9_text("talents.t4.feilong_copy_success",
                                 player=player.name, target=target.name,
                                 armor=chosen_piece.name,
                                 attribute=chosen_piece.attribute.value))
        else:
            lines.append(m9_text("talents.t4.feilong_copy_fail",
                                 player=player.name, reason=reason))
        return "\n".join(lines)

    # ════════════════════════════════════════════════════════
    #  元亨利贞（双布）：金身
    # ════════════════════════════════════════════════════════

    def _both_paper(self, player, _target):
        """元亨利贞：金身（免疫伤害/debuff 至下轮 R0；无视属性伤害可穿透）。"""
        self.immunity_active = True
        self.immunity_expire_round = getattr(self.state, "current_round", 1) + 1
        return m9_text("talents.t4.yuanheng_result", name=player.name)

    # ════════════════════════════════════════════════════════
    #  亢龙有悔（剪刀vs石头）：禁武
    # ════════════════════════════════════════════════════════

    def _scissors_rock(self, player, target):
        """亢龙有悔：禁用 1 件非拳击武器 weapon_disable_rounds 轮；
        仅有拳击 → 震荡（眩晕已退役；金身免疫震荡）。"""
        import random

        if target is None:
            others = self._others(player)
            if not others:
                return m9_text("talents.t4.kanglong_no_target")
            names = [p.name for p in others]
            choice = player.controller.choose(
                m9_text("talents.t4.kanglong_choose_target_prompt"), names,
                context={"phase": "T0", "situation": "hexagram_disarm_target"})
            target = next(p for p in others if p.name == choice)

        real_weapons = [w for w in getattr(target, "weapons", [])
                        if w and getattr(w, "name", "") != "拳击"
                        and not getattr(w, "_hexagram_disabled", False)]
        if not real_weapons:
            if target.talent and hasattr(target.talent, "is_immune_to_debuff") \
                    and target.talent.is_immune_to_debuff("shock"):
                return m9_text("talents.t4.kanglong_no_weapon_immune_shock",
                               name=target.name)
            target.is_shocked = True
            self.state.markers.add(target.player_id, "SHOCKED")
            return m9_text("talents.t4.kanglong_no_weapon_shock",
                           name=target.name)

        weapon_to_disable = random.choice(real_weapons)
        weapon_to_disable._hexagram_disabled = True
        rounds = int(_t4("weapon_disable_rounds", 2))
        expire_round = getattr(self.state, "current_round", 1) + rounds
        self.disabled_weapons.append(
            (target.player_id, weapon_to_disable.name, expire_round))
        return m9_text("talents.t4.kanglong_disable_result",
                       name=target.name, weapon=weapon_to_disable.name,
                       rounds=f"{rounds}", expire_round=f"{expire_round}")

    # ════════════════════════════════════════════════════════
    #  或跃在渊（剪刀vs布）：完整额外行动
    # ════════════════════════════════════════════════════════

    def _scissors_paper(self, player, _target):
        """或跃在渊：完整额外行动（白名单源 t4_hexagram_hojump；
        绝不写 hexagram_extra_turn）。"""
        m9 = getattr(self.state, "m9_system", None)
        round_num = getattr(self.state, "current_round", 1)
        grant = None
        if m9 is not None:
            grant = m9.dispatch_full_extra(
                self.player_id, round_num, "t4_hexagram_hojump",
                allow_instant=True, allow_public=False)
        if grant is None:
            return m9_text("talents.t4.huoyue_no_grant", name=player.name)
        return m9_text("talents.t4.huoyue_grant", name=player.name)

    # ════════════════════════════════════════════════════════
    #  群龙无首（石头vs布）：遁走
    # ════════════════════════════════════════════════════════

    def _rock_paper(self, player, target):
        """群龙无首：清锁定/探测 + 隐身 + 强制猜拳对手位移（D6）。"""
        from actions.move import ALL_LOCATIONS, get_location_display_name
        from utils.dice import roll_d6

        lines = [m9_text("talents.t4.qunlong_header")]
        markers = getattr(self.state, "markers", None)

        for lid in list(markers.get_related(player.player_id, "LOCKED_BY")):
            markers.remove_relation(player.player_id, "LOCKED_BY", lid)
        for did in list(markers.get_related(player.player_id, "DETECTED_BY")):
            markers.remove_relation(player.player_id, "DETECTED_BY", did)
        lines.append(m9_text("talents.t4.qunlong_clear", name=player.name))

        player.is_invisible = True
        markers.add(player.player_id, "INVISIBLE")
        lines.append(m9_text("talents.t4.qunlong_invisible", name=player.name))

        if target is not None and target.is_alive():
            # 星野架盾：免疫强制放逐
            if (target.talent and hasattr(target.talent, "shield_mode")
                    and target.talent.shield_mode == "架盾"):
                lines.append(m9_text("talents.t4.shield_immune_exile",
                                     name=target.name))
            else:
                available_locs = [loc for loc in ALL_LOCATIONS
                                  if loc != target.location]
                for pid in self.state.player_order:
                    home_loc = f"home_{pid}"
                    if home_loc != target.location:
                        available_locs.append(home_loc)
                if available_locs:
                    roll = roll_d6()
                    destination = available_locs[(roll - 1) % len(available_locs)]
                    old_loc = target.location or m9_text("talents.t4.unknown_location")
                    target.location = destination
                    if markers is not None and hasattr(markers, "on_player_move"):
                        markers.on_player_move(target.player_id)
                    dest_display = get_location_display_name(destination, self.state)
                    lines.append(m9_text("talents.t4.qunlong_teleport",
                                         roll=f"{roll}", name=target.name,
                                         destination=dest_display,
                                         old_loc=old_loc))
                else:
                    lines.append(m9_text("talents.t4.qunlong_nowhere",
                                         name=target.name))
        else:
            lines.append(m9_text("talents.t4.qunlong_no_target_line"))
        return "\n".join(lines)

    # ════════════════════════════════════════════════════════
    #  M9 结算协议：金身（元亨利贞）
    # ════════════════════════════════════════════════════════

    def m9_modify_incoming(self, hit: Any) -> None:
        """金身：非无视属性伤害归零（H 阶段挂载）。"""
        if not self.immunity_active:
            return
        if getattr(hit, "attribute", "普通") not in ("无视属性克制", "__无视__"):
            hit.damage = 0

    def m9_on_lethal(self, target: Any, attacker: Any,
                     source_kind: Optional[str]) -> Optional[str]:
        """金身：致死预防（HP 保 1）。"""
        if not self.immunity_active:
            return None
        target.hp = max(1.0, getattr(target, "hp", 0))
        return "t4_hexagram_golden_body"

    # ════════════════════════════════════════════════════════
    #  G6 借用核心（或跃重掷，绝不创建完整额外行动）
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _outcome_key(my: str, opp: str) -> str:
        """六爻组合判定（与 legacy `_resolve` 同构）。"""
        if my == opp:
            return {"剪刀": "both_scissors", "石头": "both_rock",
                    "布": "both_paper"}.get(my, "both_rock")
        pair = frozenset([my, opp])
        if pair == frozenset(["剪刀", "石头"]):
            return "scissors_rock"
        if pair == frozenset(["剪刀", "布"]):
            return "scissors_paper"
        return "rock_paper"

    def hexagram_cast(self, target: Any) -> str:
        """G6 借用核心：猜拳直至非或跃结果（绝不派发完整额外行动）。"""
        player = self.state.get_player(self.player_id)
        ctrl = getattr(player, "controller", None)
        choices = list(self.CHOICES)
        for _ in range(6):
            my = ctrl.choose(m9_text("talents.t4.borrow_rps_prompt"),
                             list(choices)) if ctrl else "剪刀"
            opp = target.controller.choose(
                m9_text("talents.t4.borrow_opp_rps_prompt", name=target.name),
                list(choices))
            if my not in choices:
                my = "剪刀"
            if opp not in choices:
                opp = "石头"
            outcome = self._outcome_key(my, opp)
            if outcome == "scissors_paper":
                continue  # 或跃在渊 → 重掷，不授额外行动
            msg = self._resolve(player, target, my, opp)
            return m9_text("talents.t4.borrow_result_line", my=my, opp=opp,
                           outcome=outcome, msg=msg)
        return m9_text("talents.t4.borrow_reroll_limit")

    def describe_status(self):
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().describe_status()
        parts = [m9_text("talents.t4.status_sp")]
        if self.immunity_active:
            parts.append(m9_text("talents.t4.status_immunity"))
        if self.disabled_weapons:
            for pid, wname, expire in self.disabled_weapons:
                p = self.state.get_player(pid)
                pname = p.name if p else pid
                parts.append(m9_text("talents.t4.status_sealed", name=pname,
                                     weapon=wname, round=f"{expire}"))
        return " | ".join(parts)
