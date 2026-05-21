"""DevelopCommandBuilder —— 发育指令、病毒应急

从 develop_mixin.py 复制，所有 self._xxx 属性访问改为通过 GameQuery / ctx 参数。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from controllers.ai.constants import debug_ai_development_plan

if TYPE_CHECKING:
    from controllers.ai.game_query import GameQuery
    from controllers.ai.context import OrchestratorContext


class DevelopCommandBuilder:
    """发育指令构建器。"""

    def __init__(self, query: "GameQuery"):
        self._query = query

    # ════════════════════════════════════════════════════════
    #  通用发育入口
    # ════════════════════════════════════════════════════════

    def build_develop(
        self,
        player: Any,
        state: Any,
        strategy: Any,
        available: List[str],
        ctx: "OrchestratorContext",
        develop_assessment: Any = None,
        combat_assessment: Any = None,
        talent_hooks: Optional[Dict] = None,
        combat_builder: Any = None,
    ) -> List[str]:
        """将 DevelopMind 的评估结果转换为发育命令。"""
        Q = self._query
        commands: List[str] = []
        loc = Q.get_location_str(player)
        personality = getattr(ctx, 'personality', None) or getattr(
            strategy, 'personality_name', 'balanced')
        develop_data = getattr(develop_assessment, 'data', {}) if develop_assessment else {}
        if develop_data.get("development_complete"):
            return []

        talent_cmds = self._get_talent_develop_commands(
            player, state, available, talent_hooks)
        if talent_cmds is not None:
            return talent_cmds

        weapons = getattr(player, 'weapons', [])
        outer = Q.count_outer_armor(player)
        vouchers = getattr(player, 'vouchers', 0)

        # 磨刀优先
        sharpen = self._build_sharpen_command(player, available)
        if sharpen:
            return sharpen

        debug_ai_development_plan(player.name,
            f"状态: loc={loc} vouchers={vouchers} "
            f"outer={outer} needs={develop_data.get('needs', [])}")

        # Political 特殊处理
        if personality == "political":
            fallback = getattr(ctx, 'political_fallback_level', 'none')
            if (fallback == "none"
                    and not getattr(player, 'is_captain', False)
                    and outer >= 1):
                if loc == "警察局":
                    if not getattr(player, 'is_police', False) and "recruit" in available:
                        commands.append("recruit")
                    elif getattr(player, 'is_police', False) and "election" in available:
                        commands.append("election")
                elif "move" in available:
                    commands.append("move 警察局")
                if commands:
                    return commands

        # 当前地点可交互：直接消费 DevelopMind 的 current_location_actions
        current_interact = develop_data.get("current_location_actions", [])
        if current_interact and "interact" in available:
            commands.extend(current_interact)

        # 蓄力：与当前地点交互同级，交给最终选择逻辑排序。
        if "special" in available:
            if combat_builder is not None:
                charge_cmds = combat_builder.build_charge(player, available)
                for cmd in charge_cmds:
                    if cmd not in commands:
                        commands.append(cmd)
            else:
                for weapon_name in ("电磁步枪", "高斯步枪"):
                    weapon = next((w for w in weapons
                                   if w and getattr(w, 'name', '') == weapon_name), None)
                    if weapon and not getattr(weapon, 'is_charged', False):
                        cmd = f"special 蓄力{weapon_name}"
                        if cmd not in commands:
                            commands.append(cmd)

        # 移动到 DevelopMind 选出的最优地点
        if "move" in available and not commands:
            best_move = develop_data.get("best_move")
            if best_move:
                dest = best_move.replace("move ", "", 1)
                if Q.normalize_location(dest) != Q.normalize_location(loc):
                    commands.append(best_move)

        # 发育受阻：攻击型人格可转进攻；否则移动到安全兜底地点。
        if not commands:
            if personality in ("aggressive", "assassin", "balanced"):
                combat_data = getattr(combat_assessment, 'data', {}) if combat_assessment else {}
                target = combat_data.get("best_target") if combat_data.get("combat_ready") else None
                if target and combat_builder is not None:
                    commands.extend(combat_builder.build_attack(
                        player, state, strategy, available, ctx,
                        forced_target=target,
                    ))
                    if commands:
                        return commands
            fallback = Q.find_safe_location(player, state)
            if fallback and "move" in available:
                if Q.normalize_location(fallback) != Q.normalize_location(loc):
                    commands.append(f"move {fallback}")

        return commands

    # ════════════════════════════════════════════════════════
    #  唤醒
    # ════════════════════════════════════════════════════════

    def build_wake(
        self, player: Any, state: Any,
        strategy: Any, available: List[str],
        ctx: "OrchestratorContext",
    ) -> List[str]:
        """唤醒命令（简单 wrapper）。"""
        commands: List[str] = []
        if "special" in available:
            commands.append("special 唤醒")
        return commands

    # ════════════════════════════════════════════════════════
    #  危险发育
    # ════════════════════════════════════════════════════════

    def build_danger_develop(
        self, player: Any, state: Any,
        strategy: Any, available: List[str],
        ctx: "OrchestratorContext",
    ) -> List[str]:
        """危险模式下的发育指令。"""
        Q = self._query
        commands: List[str] = []
        loc = Q.get_location_str(player)
        outer = Q.count_outer_armor(player)
        inner = Q.count_inner_armor(player)
        vouchers = getattr(player, 'vouchers', 0)

        if "interact" in available:
            if loc == "home" or Q.is_at_home(player):
                if outer == 0 and not Q.has_armor_by_name(player, "盾牌"):
                    commands.append("interact 盾牌")
            elif loc == "商店":
                if (not Q.has_virus_immunity(player)
                        and getattr(state, 'virus', None)
                        and getattr(state.virus, 'is_active', False)):
                    commands.insert(0, "interact 防毒面具")
                if vouchers >= 1 and outer < 2 and not Q.has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")
                if vouchers < 1:
                    commands.append("interact 打工")
            elif loc == "魔法所":
                learned = Q.get_learned_spells(player)
                if "魔法护盾" not in learned and outer < 2:
                    commands.append("interact 魔法护盾")
            elif loc == "医院":
                if (not Q.has_virus_immunity(player)
                        and getattr(state, 'virus', None)
                        and getattr(state.virus, 'is_active', False)):
                    if vouchers >= 1:
                        commands.insert(0, "interact 防毒面具")
                    else:
                        commands.insert(0, "interact 打工")
                if inner == 0:
                    commands.append("interact 晶化皮肤手术")
                if vouchers < 1:
                    commands.append("interact 打工")
            elif loc == "军事基地":
                has_pass = getattr(player, 'has_military_pass', False)
                if has_pass and outer < 2 and not Q.has_armor_by_name(player, "AT力场"):
                    commands.append("interact AT力场")

        safe_loc = Q.find_safe_location(player, state)
        if safe_loc and safe_loc != loc and "move" in available:
            commands.append(f"move {safe_loc}")
        return commands

    # ════════════════════════════════════════════════════════
    #  病毒应急
    # ════════════════════════════════════════════════════════

    def build_virus(
        self, player: Any, state: Any,
        strategy: Any, available: List[str],
        ctx: "OrchestratorContext",
    ) -> List[str]:
        """病毒应急命令（复制自 DevelopMixin._cmd_virus）。"""
        Q = self._query
        commands: List[str] = []
        loc = Q.get_location_str(player)
        vouchers = getattr(player, 'vouchers', 0)
        virus = getattr(state, 'virus', None)
        virus_active = getattr(virus, 'is_active', False) if virus else False
        if loc == "商店" and "interact" in available and (vouchers >= 1 or virus_active):
            commands.append("interact 防毒面具")
        elif loc == "医院" and "interact" in available and vouchers >= 1:
            commands.append("interact 防毒面具")
        elif loc in ("商店", "医院") and "interact" in available and vouchers < 1:
            commands.append("interact 打工")
        elif loc == "魔法所" and "interact" in available:
            learned = Q.get_learned_spells(player)
            if "封闭" not in learned:
                commands.append("interact 封闭")
        elif "move" in available:
            candidates = []
            for dest in ["商店", "医院", "魔法所"]:
                if dest == loc:
                    continue
                enemies = Q.count_enemies_at(dest, player, state)
                candidates.append((dest, enemies))
            candidates.sort(key=lambda x: x[1])
            if candidates:
                commands.append(f"move {candidates[0][0]}")
            else:
                commands.append("move 商店")
        return commands

    # ════════════════════════════════════════════════════════
    #  内部辅助
    # ════════════════════════════════════════════════════════

    def _get_talent_develop_commands(
        self, player: Any, state: Any, available: List[str],
        talent_hooks: Optional[Dict],
    ) -> Optional[List[str]]:
        if not talent_hooks:
            return None
        talent_name = getattr(getattr(player, 'talent', None), 'name', '')
        hook = talent_hooks.get(talent_name) if talent_name else None
        if not hook or not hasattr(hook, 'get_develop_commands'):
            return None
        return hook.get_develop_commands(player, state, available)

    @staticmethod
    def _build_sharpen_command(player: Any, available: List[str]) -> List[str]:
        if "special" not in available:
            return []
        has_stone = any(
            getattr(item, 'name', '') == "磨刀石"
            for item in getattr(player, 'items', [])
        )
        has_unsharpened = any(
            weapon.name == "小刀" and getattr(weapon, 'base_damage', 0) < 2
            for weapon in getattr(player, 'weapons', []) if weapon
        )
        return ["special 磨刀"] if has_stone and has_unsharpened else []
