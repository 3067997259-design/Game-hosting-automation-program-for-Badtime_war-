"""DevelopCommandBuilder —— 发育指令、病毒应急

从 develop_mixin.py 复制，所有 self._xxx 属性访问改为通过 GameQuery / ctx 参数。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from controllers.ai.constants import (
    NEED_PROVIDERS, PERSONALITY_NEEDS, need_providers_for,
    ai_wallet, m4_item_price,
    debug_ai_development_plan,
)

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

        # 磨刀优先
        sharpen = self._build_sharpen_command(player, available)
        if sharpen:
            return sharpen

        talent_cmds = self._get_talent_develop_commands(
            player, state, available, talent_hooks)
        if talent_cmds is not None:
            return talent_cmds

        weapons = getattr(player, 'weapons', [])
        outer = Q.count_outer_armor(player)
        vouchers = getattr(player, 'vouchers', 0)

        debug_ai_development_plan(player.name,
            f"状态: loc={loc} vouchers={vouchers} "
            f"outer={outer} needs={develop_data.get('needs', [])}")

        # Political 特殊处理
        if personality == "political":
            fallback = ctx.political_fallback_level
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
        # ★ 只有在 unmet 非空时才进入受阻路径；unmet 为空说明已满足，不抢跑 FALLBACK
        unmet = develop_data.get("unmet_needs", [])
        if not commands and unmet:
            if strategy and strategy.should_attack_when_develop_blocked():
                combat_data = getattr(combat_assessment, 'data', {}) if combat_assessment else {}
                target = combat_data.get("best_target") if combat_data.get("combat_ready") else None
                if target and combat_builder is not None:
                    commands.extend(combat_builder.build_attack(
                        player, state, strategy, available, ctx,
                        forced_target=target,
                    ))
                    if commands:
                        return commands
            fallback = self.pick_fallback_destination(
                player, state, strategy, personality, ctx)
            if fallback and "move" in available:
                if Q.normalize_location(fallback) != Q.normalize_location(loc):
                    commands.append(f"move {fallback}")

        return commands

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
        wallet = ai_wallet(player)
        from engine import experiments
        m4 = experiments.is_enabled("m4_gear")

        if "interact" in available:
            if loc == "home" or Q.is_at_home(player):
                if outer == 0 and not Q.has_armor_by_name(player, "盾牌"):
                    commands.append("interact 盾牌")
            elif loc == "商店":
                if (not Q.has_virus_immunity(player)
                        and getattr(state, 'virus', None)
                        and getattr(state.virus, 'is_active', False)):
                    commands.insert(0, "interact 防毒面具")
                armor_price = m4_item_price("商店", "陶瓷护甲") if m4 else 1
                if wallet >= armor_price and outer < 2 \
                        and not Q.has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")
                elif wallet < armor_price:
                    commands.append("interact 打工")
            elif loc == "魔法所":
                learned = Q.get_learned_spells(player)
                if "魔法护盾" not in learned and outer < 2:
                    commands.append("interact 魔法护盾")
            elif loc == "医院":
                if (not Q.has_virus_immunity(player)
                        and getattr(state, 'virus', None)
                        and getattr(state.virus, 'is_active', False)):
                    mask_price = m4_item_price("医院", "防毒面具") if m4 else 1
                    if wallet >= mask_price:
                        commands.insert(0, "interact 防毒面具")
                    else:
                        commands.insert(0, "interact 打工")
                if inner == 0:
                    if not m4:
                        commands.append("interact 晶化皮肤手术")
                    else:
                        surgery = m4_item_price("医院", "晶化皮肤手术")
                        if wallet >= surgery:
                            commands.append("interact 晶化皮肤手术")
                        else:
                            commands.append("interact 打工")
                elif not m4 and wallet < 1:
                    commands.append("interact 打工")
            elif loc == "军事基地":
                has_pass = getattr(player, 'has_military_pass', False)
                if has_pass and outer < 2 and not Q.has_armor_by_name(player, "AT力场"):
                    commands.append("interact AT力场")

        safe_loc = self.pick_safe_armor_destination(player, state)
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
        wallet = ai_wallet(player)
        virus = getattr(state, 'virus', None)
        virus_active = getattr(virus, 'is_active', False) if virus else False
        from engine import experiments
        m4 = experiments.is_enabled("m4_gear")
        mask_price = m4_item_price(loc, "防毒面具") if m4 else 1
        if loc == "商店" and "interact" in available \
                and (wallet >= mask_price or virus_active):
            commands.append("interact 防毒面具")
        elif loc == "医院" and "interact" in available and wallet >= mask_price:
            commands.append("interact 防毒面具")
        elif loc in ("商店", "医院") and "interact" in available \
                and wallet < mask_price:
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

    def pick_virus_cure_location(self, player: Any, state: Any) -> str:
        """选择获取病毒免疫的最佳地点（人最少 + 能获取）。"""
        Q = self._query
        wallet = ai_wallet(player)
        virus = getattr(state, 'virus', None)
        virus_active = getattr(virus, 'is_active', False) if virus else False
        from engine import experiments
        mask_price = (m4_item_price("商店", "防毒面具")
                      if experiments.is_enabled("m4_gear") else 1)
        candidates = []
        if virus_active or wallet >= mask_price:
            candidates.append("商店")
        if wallet >= mask_price:
            candidates.append("医院")
        candidates.append("魔法所")
        candidates.sort(key=lambda dest: Q.count_enemies_at(dest, player, state))
        return candidates[0] if candidates else "商店"

    def pick_safe_armor_destination(self, player, state) -> Optional[str]:
        """危险模式下选择目的地：安全 + 能拿护甲。"""
        Q = self._query
        loc = Q.get_location_str(player)
        outer = Q.count_outer_armor(player)
        inner = Q.count_inner_armor(player)
        has_pass = getattr(player, 'has_military_pass', False)
        armor_locations = []
        if outer < 1 and loc != "home":
            armor_locations.append("home")
        if outer < 2 and loc != "商店":
            armor_locations.append("商店")
        if outer < 2 and loc != "魔法所":
            armor_locations.append("魔法所")
        if inner < 1 and loc != "医院":
            armor_locations.append("医院")
        if has_pass and outer < 2 and loc != "军事基地":
            armor_locations.append("军事基地")
        if not armor_locations:
            return Q.find_safe_location(player, state)
        armor_locations.sort(key=lambda dest: Q.count_enemies_at(dest, player, state))
        return armor_locations[0]

    def pick_captain_safe_destination(self, player, state, police_cache: Optional[Dict] = None) -> Optional[str]:
        """队长危险模式目的地：优先去有最强活跃警察的地点。"""
        Q = self._query
        pc = police_cache or {}
        my_loc = Q.get_location_str(player)
        loc_strength = {}
        for unit in pc.get("units", []):
            if not unit.get("is_alive") or not unit.get("is_active"):
                continue
            uloc = unit.get("location")
            if not uloc:
                continue
            hp = unit.get("hp", 1) + (1 if unit.get("outer_armor") else 0)
            loc_strength[uloc] = loc_strength.get(uloc, 0) + hp
        if loc_strength:
            best = max(loc_strength, key=loc_strength.get)
            if best != my_loc:
                return best
        return self.pick_safe_armor_destination(player, state)

    def pick_fallback_destination(
        self, player, state, strategy, personality: str,
        ctx: "OrchestratorContext",
    ) -> Optional[str]:
        """发育受阻时，在能满足需求的地点中选敌人最少的。"""
        Q = self._query
        unmet_needs = self._get_unmet_needs(player, state, personality, ctx, strategy)
        if not unmet_needs:
            return Q.find_nearest_enemy_location(
                player, state, ctx.threat_scores, personality,
                ctx.players_who_attacked)
        loc = Q.get_location_str(player)
        useful_locs = set()
        for need_key, _ in unmet_needs:
            for ploc, item_name, _ in need_providers_for(need_key):
                if not self._already_has_item(player, item_name):
                    useful_locs.add(ploc)
        useful_locs.discard(loc)
        if Q.is_at_home(player):
            useful_locs.discard("home")
        if not useful_locs:
            return Q.find_nearest_enemy_location(
                player, state, ctx.threat_scores, personality,
                ctx.players_who_attacked)
        return sorted(useful_locs, key=lambda dest: Q.count_enemies_at(dest, player, state))[0]

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
        from models.equipment import has_unsharpened_knife
        has_stone = any(
            getattr(item, 'name', '') == "磨刀石"
            for item in getattr(player, 'items', [])
        )
        return ["special 磨刀"] if has_stone and has_unsharpened_knife(player) else []

    def _get_unmet_needs(self, player, state, personality, ctx=None, strategy=None) -> list:
        """返回当前未满足的需求列表。"""
        Q = self._query
        effective_personality = personality
        if ctx and getattr(ctx, 'political_fallback_level', 'none') == "full_balanced":
            effective_personality = "balanced"
        needs_order = PERSONALITY_NEEDS.get(effective_personality, PERSONALITY_NEEDS["balanced"])
        weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
        has_weapon = len(weapons) > 0
        outer = Q.count_outer_armor(player)
        inner = Q.count_inner_armor(player)
        wallet = ai_wallet(player)
        has_detection = getattr(player, 'has_detection', False)
        has_stealth = Q.has_stealth(player)
        unmet = []
        for need in needs_order:
            if need == "voucher" and wallet < 1:
                unmet.append(("voucher", 3))
            elif need == "weapon" and not has_weapon:
                unmet.append(("weapon", 5))
            elif need == "outer_armor" and outer < 1:
                unmet.append(("outer_armor", 4))
            elif need == "second_outer_armor" and outer < 2:
                unmet.append(("second_outer_armor", 3))
            elif need == "inner_armor" and inner < 1:
                unmet.append(("inner_armor", 2))
            elif need == "detection" and not has_detection:
                unmet.append(("detection", 2))
            elif need == "stealth" and not has_stealth:
                unmet.append(("stealth", 3))
            elif need == "second_weapon" and len(weapons) < 2:
                unmet.append(("second_weapon", 3))
        if strategy and "military_pass" in strategy.get_development_needs_order():
            has_pass = getattr(player, 'has_military_pass', False)
            if not has_pass:
                unmet.append(("military_pass", 4))
        return unmet

    def _already_has_item(self, player, item_name) -> bool:
        """检查玩家是否已拥有某物品/装备/法术。"""
        Q = self._query
        if item_name in ("小刀", "高斯步枪", "电磁步枪"):
            return any(w.name == item_name for w in player.weapons if w)
        learned = Q.get_learned_spells(player)
        if item_name in ("魔法护盾", "魔法弹幕", "远程魔法弹幕", "封闭",
                         "地震", "地动山摇", "隐身术", "探测魔法"):
            if item_name == "魔法弹幕":
                return (item_name in learned
                        or any(w.name == item_name for w in player.weapons if w))
            return item_name in learned
        if item_name in ("盾牌", "陶瓷护甲", "AT力场"):
            return Q.has_armor_by_name(player, item_name)
        surgery_armor_map = {
            "晶化皮肤手术": "晶化皮肤",
            "额外心脏手术": "额外心脏",
            "不老泉手术": "不老泉",
        }
        if item_name in surgery_armor_map:
            return Q.has_armor_by_name(player, surgery_armor_map[item_name])
        if item_name in ("热成像仪", "隐身衣", "隐形涂层", "雷达"):
            if item_name in ("热成像仪", "雷达"):
                return getattr(player, 'has_detection', False)
            if item_name in ("隐身衣", "隐形涂层", "隐身术"):
                return Q.has_stealth(player)
        if item_name == "通行证":
            return getattr(player, 'has_military_pass', False)
        if item_name == "凭证":
            return ai_wallet(player) >= 1
        if item_name == "打工":
            return ai_wallet(player) >= 1
        return False
