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

from cli import display

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
        if not ish or ish.phase not in ("active", "duet"):
            return "forfeit"

        # v2.0 T0 物料阶段：委托 StageAI 统一评估 + 决策
        from controllers.ai.stage import StageAI
        assessment = StageAI.assess(chorus, game_state)
        if ish.deck and available_actions:
            cid = chorus.player_id
            # 摸牌
            if not ish.deck.chorus_slots.get(cid):
                ish.deck.chorus_draw(cid)
                card = ish.deck.chorus_slots.get(cid)
                if card:
                    display.show_info(f"🃏 {chorus.name} 摸到「{card}」")
            # 决策出牌
            chosen = StageAI.decide_t0(chorus, ish, game_state, assessment)
            if chosen and ish.deck.chorus_slots.get(cid) == chosen:
                ish.deck.chorus_play_card(chorus, chosen)
                display.show_info(f"🎴 {chorus.name} 使用「{chosen}」")
                self._apply_card_effect(chorus, ish, chosen)

        # v2.0 StageAI: 舞台内攻击决策（Chorus + BasicAI 共用）
        if "attack" in available_actions or "move" in available_actions:
            # G2 Sognando 可指定 Chorus 攻击目标 → 仍优先
            commanded_id = getattr(chorus, '_g2_commanded_target_id', None)
            if commanded_id:
                legal_targets = self._get_legal_targets(game_state, chorus, ish)
                commanded_target = next(
                    (t for t in legal_targets if t.player_id == commanded_id), None)
                if commanded_target:
                    weapons = getattr(chorus, 'weapons', [])
                    if weapons:
                        weapon = random.choice(weapons)
                        return f"attack {commanded_target.name} {weapon.name}"
                    return f"attack {commanded_target.name}"

            # 委托 StageAI 决策（复用 T0 的 assessment）
            from controllers.ai.stage import StageAI
            ctx = {"chorus_unit": chorus, "game_state": game_state,
                   "assessment": assessment}
            cmd = StageAI.get_command(chorus, game_state, available_actions, ctx)
            if cmd:
                return cmd

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
        """v0.6 声部限制下的合法攻击目标（含 duet 按钮）。

        @deprecated: 新逻辑已迁移至 controllers.ai.stage.target_filter。
        当前仅保留用于 G2 指挥指令 (commanded_id) 的目标匹配路径。
        未来应改为直接调用 target_filter.get_legal_normal_targets()。
        """
        from engine.ish_bosheth import ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO

        voice = getattr(chorus, 'emotion', None)
        targets = []

        # unreachable: ChorusController 仅在 active 阶段运行，duet 已委托 StageAI
        # (get_command() L39: phase != "active" → forfeit)
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
    def _apply_card_effect(chorus, ish, card_name: str):
        """Chorus 打出物料牌后应用效果。"""
        # 需要复杂目标选择的牌 Chorus 无法处理，安全阻止
        if card_name in ("应援连呼", "调停", "改签票"):
            display.show_info(f"  → Chorus 无法使用「{card_name}」（需选择目标）")
            return
        if card_name in ("荧光棒", "聚光合影"):
            chorus._card_damage_bonus = 0.5
            display.show_info("  → 伤害+0.5")
        elif card_name == "耳塞":
            chorus._card_earplug = True
            ent = getattr(chorus, 'stage_entangle', [])
            if ent:
                ent.pop()
            display.show_info("  → 解除1层牵连")
        elif card_name == "后台通行证":
            ish.regard = max(0, ish.regard - 1.0)
            display.show_info(f"  → Regard -1（当前 {ish.regard}）")
        elif card_name in ("花束", "反光板", "场刊整理", "和弦谱"):
            ish.offer_heat(chorus, 1.0, card_name)
            display.show_info(f"  → 上供舞台 +1.0 热力")
        elif card_name == "前排票":
            btn_seats = [b.location for b in getattr(ish, 'duet_buttons', [])]
            if btn_seats:
                import random
                chorus.location = random.choice(btn_seats)
                display.show_info(f"  → 移动到 {chorus.location}")
        elif card_name == "空白票根":
            if ish.deck:
                ish.deck.chorus_draw(chorus.player_id)
                display.show_info("  → 摸1张牌")
        else:
            display.show_info(f"  → 效果未实现（{card_name}）")
