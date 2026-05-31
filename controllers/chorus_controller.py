"""ChorusController —— G2 ish-bosheth 临时观众的简易 AI 控制器

Chorus 在舞台内遵循情绪限制：
  - Accarezzevole / Indifferenza: 只能攻击非 G2 单位
  - Strappando: 只能攻击 G2 发动者

攻击选择：随机从合法目标中选取。无合法目标时 forfeit。
"""

from __future__ import annotations
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from models.player import Player


class ChorusController:
    """Chorus 简易决策控制器。不需要继承 PlayerController。"""

    def __init__(self):
        pass

    def get_command(self, available_actions: List[str] = None,
                    context: Dict[str, Any] = None) -> str:
        """随机选择一个合法行动。

        context 中应包含:
          - "game_state": GameState 引用
          - "chorus_unit": ChorusUnit 自身引用
        """
        if not available_actions:
            return "forfeit"

        ctx = context or {}
        game_state = ctx.get("game_state")
        chorus = ctx.get("chorus_unit")
        g2_owner_id = None

        if game_state and getattr(game_state, 'ish_bosheth', None):
            g2_owner_id = game_state.ish_bosheth.g2_owner_id

        # 优先 attack
        if "attack" in available_actions and game_state and chorus:
            legal_targets = self._get_legal_targets(game_state, chorus, g2_owner_id)
            if legal_targets:
                target = random.choice(legal_targets)
                # 随机选择一件武器
                weapons = getattr(chorus, 'weapons', [])
                if weapons:
                    weapon = random.choice(weapons)
                    return f"attack {target.name} with {weapon.name}"
                return f"attack {target.name}"
            return "forfeit"

        return "forfeit"

    def choose(self, prompt: str, options: List[str],
               context: Dict[str, Any] = None) -> str:
        """随机选择"""
        if not options:
            return ""
        return random.choice(options)

    def confirm(self, prompt: str,
                context: Dict[str, Any] = None) -> bool:
        """Chorus 总是返回 False（保守）"""
        return False

    def _get_legal_targets(self, game_state, chorus, g2_owner_id: Optional[str]):
        """根据情绪过滤合法攻击目标。"""
        from engine.ish_bosheth import STRAPPANDO, ACCAREZZEVOLE, INDIFFERENZA

        emotion = getattr(chorus, 'emotion', None)
        targets = []

        # 真实玩家
        for pid in game_state.player_order:
            p = game_state.get_player(pid)
            if not p or not p.is_alive() or not p.is_on_map():
                continue
            if p.player_id == chorus.player_id:
                continue

            if emotion == STRAPPANDO:
                if p.player_id == g2_owner_id:
                    targets.append(p)
            elif emotion in (ACCAREZZEVOLE, INDIFFERENZA):
                if p.player_id != g2_owner_id:
                    targets.append(p)
            else:
                # 无情绪（不应该出现）：不攻击 G2
                if p.player_id != g2_owner_id:
                    targets.append(p)

        # Chorus 之间也可互殴
        if game_state.ish_bosheth:
            for c in game_state.ish_bosheth.chorus_list:
                if not c.is_alive() or c.player_id == chorus.player_id:
                    continue
                if emotion == STRAPPANDO:
                    continue  # Strappando Chorus 只攻击 G2
                targets.append(c)

        return targets
