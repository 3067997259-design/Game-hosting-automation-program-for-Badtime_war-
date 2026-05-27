"""
OneSlashAIHook —— T1「一刀缭断」天赋AI钩子

核心职责：
  - 通过 get_development_needs_override 将「磨刀石」注入发育需求列表
  - 让 DevelopMind 自然地规划：拿小刀→拿凭证→去商店买磨刀石→磨刀
  - 磨刀的 special 指令由 Orchestrator._handle_develop 通用检测生成

设计原则：
  - 不接管命令生成（should_override_candidates 返回 None）
  - 只在发育需求列表中插入 "whetstone"，让正常发育系统处理一切
  - 武器就绪后返回空列表，不干预发育
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any
from controllers.ai.talents.base_hook import BaseTalentAIHook
from controllers.ai.game_query import GameQuery


class OneSlashAIHook(BaseTalentAIHook):
    talent_name = "一刀缭断"

    def __init__(self, controller: Any):
        self._ctrl = controller

    # ════════════════════════════════════════════════════════
    #  Choose 决策
    # ════════════════════════════════════════════════════════

    def handle_choose(
        self, player: Any, state: Any, situation: str,
        options: List[str], context: Dict,
    ) -> Optional[str]:
        threat_scores = context.get("threat_scores", {})

        if situation == "oneslash_pick_weapon":
            if player:
                best_name = None
                best_dmg = -1
                best_is_sharpened_knife = False
                for w in getattr(player, 'weapons', []):
                    if w and w.name in options:
                        dmg = GameQuery.get_weapon_damage(w)
                        is_sharpened_knife = (
                            w.name == "小刀"
                            and getattr(w, 'base_damage', 0) >= 2
                        )
                        if (is_sharpened_knife and not best_is_sharpened_knife) or \
                           (is_sharpened_knife == best_is_sharpened_knife and dmg > best_dmg):
                            best_dmg = dmg
                            best_name = w.name
                            best_is_sharpened_knife = is_sharpened_knife
                if best_name:
                    return best_name
            return options[0]

        if situation == "oneslash_pick_target":
            return max(options, key=lambda name: threat_scores.get(name, 0), default=options[0])

        if situation == "talent_t0":
            talent_name = context.get("talent_name", "")
            if talent_name != "一刀缭断":
                return None
            if player and state:
                alive_count = sum(
                    1 for pid in state.player_order
                    if state.get_player(pid) and state.get_player(pid).is_alive()
                )
                danger = getattr(self._ctrl, '_danger_mode', False)

                # ★ 残局/危险模式：无条件发动，绕开所有限制
                if alive_count == 2 or danger:
                    for opt in options:
                        if "发动" in opt:
                            return opt

                has_sharpened_knife = any(
                    w.name == "小刀" and getattr(w, 'base_damage', 0) >= 2
                    for w in getattr(player, 'weapons', [])
                )
                has_charged_gauss = any(
                    w.name == "高斯步枪" and getattr(w, 'is_charged', False)
                    for w in getattr(player, 'weapons', [])
                )
                if not (has_sharpened_knife or has_charged_gauss):
                    for opt in options:
                        if "不发动" in opt or "正常" in opt:
                            return opt
                    return options[-1]

                talent = getattr(player, 'talent', None)
                uses_left = getattr(talent, 'uses_remaining', 0) if talent else 0

                markers = getattr(state, 'markers', None)
                engaged_target = None
                if markers:
                    for pid in state.player_order:
                        if pid == player.player_id:
                            continue
                        t = state.get_player(pid)
                        if t and t.is_alive() and markers.has_relation(
                                player.player_id, "ENGAGED_WITH", pid):
                            engaged_target = t
                            break

                alive_count = sum(
                    1 for pid in state.player_order
                    if state.get_player(pid) and state.get_player(pid).is_alive()
                )

                terror_found = any(
                    getattr(getattr(state.get_player(pid), 'talent', None), 'is_terror', False)
                    for pid in state.player_order
                    if pid != player.player_id
                    and state.get_player(pid) and state.get_player(pid).is_alive()
                )

                if uses_left >= 2:
                    for opt in options:
                        if "发动" in opt:
                            return opt

                if uses_left == 1:
                    should_activate = False
                    if alive_count == 2 and engaged_target is not None:
                        should_activate = True
                    if not should_activate and getattr(self._ctrl, '_danger_mode', False):
                        if terror_found:
                            should_activate = True
                    if not should_activate and terror_found:
                        should_activate = True
                    # 同地点有受警察保护的目标，且天赋伤害能绕过阈值 → 发动
                    if not should_activate:
                        pe = getattr(state, 'police_engine', None)
                        if pe:
                            best_dmg = max(
                                (GameQuery.get_weapon_damage(w)
                                 for w in getattr(player, 'weapons', []) if w),
                                default=0.5
                            )
                            for pid in state.player_order:
                                if pid == player.player_id:
                                    continue
                                t = state.get_player(pid)
                                if not t or not t.is_alive():
                                    continue
                                if t.location != player.location:
                                    continue
                                if not pe.is_protected_by_police(pid):
                                    continue
                                threshold = pe.get_protection_threshold(pid)
                                if best_dmg * 2.0 > threshold:
                                    should_activate = True
                                    break
                    if should_activate and engaged_target:
                        outer = GameQuery.count_outer_armor(engaged_target)
                        inner = GameQuery.count_inner_armor(engaged_target)
                        total_def = engaged_target.hp + outer + inner
                        if total_def < 4:
                            should_activate = False
                    if should_activate:
                        for opt in options:
                            if "发动" in opt:
                                return opt

            for opt in options:
                if "不发动" in opt or "正常" in opt:
                    return opt
            return options[-1]

        return None

    # ════════════════════════════════════════════════════════
    #  get_development_needs_override：注入发育需求
    # ════════════════════════════════════════════════════════

    def get_development_needs_override(self, player: Any) -> Optional[List[str]]:
        """T1 发育引导：注入额外需求到发育优先级列表。

        逻辑：
          - 已有磨过的刀 (base_damage >= 2) → 不需要，返回 []
          - 已有蓄力高斯 (is_charged) → 不需要，返回 []
          - 已有小刀但没磨 → 需要磨刀石 → 返回 ["whetstone"]
          - 还没小刀 → 返回 []（正常的 "weapon" 需求已经覆盖了）
        """
        weapons = getattr(player, 'weapons', [])

        # 武器就绪 → 不干预
        has_sharpened = any(
            w.name == "小刀" and getattr(w, 'base_damage', 0) >= 2
            for w in weapons if w
        )
        has_charged_gauss = any(
            w.name == "高斯步枪" and getattr(w, 'is_charged', False)
            for w in weapons if w
        )
        if has_sharpened or has_charged_gauss:
            return []

        # 有刀但没磨 → 需要磨刀石
        has_knife = any(w.name == "小刀" for w in weapons if w)
        if has_knife:
            return ["whetstone"]

        # 还没刀 → 让正常 "weapon" 需求处理
        return []
