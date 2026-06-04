"""ChorusController —— G2 ish-bosheth v0.6 临时观众 AI 控制器

v0.6 规则：
- 声部锁定，Chorus 攻击按声部限制
- Acc: 攻击 Str 真实玩家/Chorus，持有牌的 Chorus
- Str: 攻击 Acc 真实玩家/Chorus，G2（或 G2 投影）
- Ind: 攻击持有牌的 Chorus，不攻击真实玩家
- Chorus 不会主动攻击处于 Indifferenza 且无舞台牵连/聚光灯/安可的单位
- T0 物料阶段：摸牌 / 使用牌
"""

from __future__ import annotations
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from models.player import Player


class ChorusController:
    """Chorus v0.6 决策控制器。"""

    def __init__(self):
        pass

    def get_command(self, available_actions: List[str] = None,
                    context: Dict[str, Any] = None) -> str:
        if not available_actions:
            return "forfeit"

        ctx = context or {}
        game_state = ctx.get("game_state")
        chorus = ctx.get("chorus_unit")

        if not game_state or not chorus:
            return "forfeit"

        ish = getattr(game_state, 'ish_bosheth', None)
        if not ish or ish.phase != "active":
            return "forfeit"

        # v0.6 T0 物料阶段
        if ish.deck and available_actions:
            # Chorus T0: 若没持牌，摸1张
            cid = chorus.player_id
            if not ish.deck.chorus_slots.get(cid):
                ish.deck.chorus_draw(cid)

            # Chorus 尝试使用持有的牌
            card = ish.deck.chorus_slots.get(cid)
            if card and self._can_chorus_use_card(chorus, card):
                # Chorus 使用牌（简单策略：有牌就用）
                ish.deck.chorus_play_card(chorus, card)
                # 牌效果在此处理（简化：只处理战斗相关牌）
                if card in ("荧光棒", "聚光合影"):
                    chorus._card_damage_bonus = 0.5
                elif card == "耳塞":
                    chorus._card_earplug = True
                    ent = getattr(chorus, 'stage_entangle', [])
                    if ent:
                        ent.pop()
                elif card == "后台通行证":
                    # Chorus 只能造成 Regard -1，不触发破幕
                    ish.regard = max(0, ish.regard - 1.0)

        # 优先 attack
        if "attack" in available_actions:
            legal_targets = self._get_legal_targets(game_state, chorus, ish)
            if legal_targets:
                # G2 Sognando 可指定 Chorus 攻击目标
                commanded_id = getattr(chorus, '_g2_commanded_target_id', None)
                if commanded_id:
                    commanded_target = next(
                        (t for t in legal_targets if t.player_id == commanded_id), None)
                    if commanded_target:
                        # G2 指挥指令仅在成功执行后清除，避免目标在指令与执行间死亡导致指令丢失
                        weapons = getattr(chorus, 'weapons', [])
                        if weapons:
                            weapon = random.choice(weapons)
                            return f"attack {commanded_target.name} with {weapon.name}"
                        return f"attack {commanded_target.name}"
                target = random.choice(legal_targets)
                weapons = getattr(chorus, 'weapons', [])
                if weapons:
                    weapon = random.choice(weapons)
                    return f"attack {target.name} with {weapon.name}"
                return f"attack {target.name}"
            return "forfeit"

        return "forfeit"

    def choose(self, prompt: str, options: List[str],
               context: Dict[str, Any] = None) -> str:
        if not options:
            return ""
        # 简单启发式：有"不打"/"不"选项优先选它（保守）
        for opt in options:
            if "不打" in opt or "不拾取" in opt:
                return opt
        return random.choice(options)

    def confirm(self, prompt: str, context: Dict[str, Any] = None) -> bool:
        return False

    def _get_legal_targets(self, game_state, chorus, ish) -> list:
        """v0.6 声部限制下的合法攻击目标（含 duet 按钮）。"""
        from engine.ish_bosheth import ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO

        voice = getattr(chorus, 'emotion', None)
        targets = []

        # v2.0 duet 模式：按钮始终是合法目标（优先于 PvP）
        if ish.phase == "duet" and ish.duet_buttons:
            for btn in ish.duet_buttons:
                if getattr(btn, 'is_alive', lambda: True)():
                    targets.append(btn)
            if targets:
                return targets  # duet 中 Chorus 优先打按钮

        g2_owner_id = ish.g2_owner_id

        # Str 可攻击 G2（G2 不在 participants 中，需单独处理）
        if voice == STRAPPANDO:
            g2p = game_state.get_player(g2_owner_id)
            if g2p and g2p.is_alive() and g2p.is_on_map():
                targets.append(g2p)

        # 真实玩家
        for pid in ish.participants:
            p = game_state.get_player(pid)
            if not p or not p.is_alive() or not p.is_on_map():
                continue
            if p.player_id == chorus.player_id:
                continue

            p_voice = getattr(p, 'emotion', None)

            if voice == ACCAREZZEVOLE:
                # Acc 攻击 Str
                if p_voice == STRAPPANDO and p.player_id != g2_owner_id:
                    targets.append(p)
            elif voice == STRAPPANDO:
                # Str 攻击 Acc（G2 已在上面单独添加）
                if p_voice == ACCAREZZEVOLE:
                    targets.append(p)
            elif voice == INDIFFERENZA:
                # Ind 不能攻击真实玩家
                pass
            else:
                # 无声部：不攻击 G2
                if p.player_id != g2_owner_id:
                    targets.append(p)

        # 同声部 Chorus 不互打，不同声部可以
        for c in ish.chorus_list:
            if not c.is_alive() or c.player_id == chorus.player_id:
                continue
            c_voice = getattr(c, 'emotion', None)

            if voice == ACCAREZZEVOLE:
                if c_voice == STRAPPANDO:
                    targets.append(c)
            elif voice == STRAPPANDO:
                if c_voice == ACCAREZZEVOLE:
                    targets.append(c)
            elif voice == INDIFFERENZA:
                # Ind 攻击持有牌的 Chorus
                if ish.deck and ish.deck.chorus_slots.get(c.player_id):
                    targets.append(c)
            else:
                targets.append(c)

        # 过滤：不攻击 Indifferenza + 无牵连/聚光灯/安可的目标
        targets = [t for t in targets
                   if not (getattr(t, 'emotion', None) == INDIFFERENZA
                           and "spotlight" not in getattr(t, 'stage_statuses', set())
                           and getattr(t, 'encore_layers', 0) == 0
                           and not (hasattr(t, 'stage_entangle') and t.stage_entangle))]

        return targets

    @staticmethod
    def _can_chorus_use_card(chorus, card_name: str) -> bool:
        """Chorus 能否使用该物料牌。"""
        if card_name == "改签票":
            return False
        # 改签票只能真实玩家使用；应援连呼/调停需要选择目标的复杂交互
        chorus_only = {"应援连呼", "调停"}
        if card_name in chorus_only:
            return False
        return True
