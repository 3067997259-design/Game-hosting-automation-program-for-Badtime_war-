"""HelpersMixin —— 装备查询、位置判断、工具方法"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional, Any, Dict
from controllers.ai.constants import (
    EFFECTIVE_AGAINST, POLICE_AOE_WEAPONS, LOCATIONS,
    debug_ai_basic
)

if TYPE_CHECKING:
    from controllers.ai.controller import BasicAIController

# Pylance 辅助基类：TYPE_CHECKING 时继承 BasicAIController 获取完整类型，
# 运行时退化为 object，不影响 MRO。
_Base = BasicAIController if TYPE_CHECKING else object


class HelpersMixin(_Base): # type: ignore

    # ════════════════════════════════════════════════════════
    #  基础工具
    # ════════════════════════════════════════════════════════

    def _pname(self) -> str:
        return self.player_name or "AI"
    @staticmethod
    def _safe_float(value) -> float:
        if value is None:
            return 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0
    # ════════════════════════════════════════════════════════
    #  天赋状态检查
    # ════════════════════════════════════════════════════════

    def _has_firefly_talent(self, player) -> bool:
        """检查玩家是否持有火萤IV型天赋"""
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'name') and talent.name == "火萤IV型-完全燃烧":
            return True
        return False
    def _firefly_debuff_active(self, player) -> bool:
        """检查火萤IV型的 debuff 是否已生效"""
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'debuff_started'):
            return talent.debuff_started
        return False
    def _is_in_savior_state(self, player) -> bool:
        """检查玩家是否处于救世主状态"""
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'is_savior'):
            return talent.is_savior
        return False
    def _has_savior_talent(self, player) -> bool:
        """检查玩家是否持有愿负世天赋（不论是否在救世主状态）"""
        talent = getattr(player, 'talent', None)
        return bool(talent and hasattr(talent, 'name') and talent.name == "愿负世，照拂黎明")

    def _get_divinity(self, player) -> int:
        """获取愿负世持有者的当前火种数"""
        talent = getattr(player, 'talent', None)
        return getattr(talent, 'divinity', 0) if talent else 0

    def _target_is_firefly(self, player) -> bool:
        """检查目标是否持有火萤IV型天赋"""
        return self._has_firefly_talent(player)

    def _has_hologram_talent(self, player) -> bool:
        """检查玩家是否持有全息影像天赋"""
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'name') and talent.name == "请一直，注视着我":
            return True
        return False

    def _has_active_hologram(self, player) -> bool:
        """检查玩家是否有激活中的全息影像"""
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'name') and talent.name == "请一直，注视着我":
            return getattr(talent, 'active', False)
        return False

    def _hologram_exhausted(self, player) -> bool:
        """检查全息影像次数是否已用完且不在激活中"""
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'name') and talent.name == "请一直，注视着我":
            return (getattr(talent, 'used', False)
                    and getattr(talent, 'max_uses', 0) <= 0
                    and not getattr(talent, 'active', False))
        return False

    def _is_being_burned(self, player, state) -> bool:
        """检查玩家是否正在被灼烧"""
        for pid in state.player_order:
            p = state.get_player(pid)
            if p and p.is_alive() and p.talent and hasattr(p.talent, 'burn_targets'):
                if player.player_id in p.talent.burn_targets:
                    if p.talent.burn_targets[player.player_id] > 0:
                        return True
        return False

    def _firefly_exists_in_game(self, state) -> bool:
        """检查场上是否有存活的火萤"""
        for pid in state.player_order:
            p = state.get_player(pid)
            if p and p.is_alive() and self._has_firefly_talent(p):
                return True
        return False

    def _firefly_supernova_threat(self, player, state) -> bool:
        """检测场上是否有火萤持有超新星（对所有非火萤AI构成威胁）"""
        if self._has_firefly_talent(player):
            return False  # 火萤自己不怕自己的超新星
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            t = state.get_player(pid)
            if t and t.is_alive() and t.talent:
                if getattr(t.talent, 'has_supernova', False):
                    return True
        return False
    # ════════════════════════════════════════════════════════
    #  装备查询：护甲
    # ════════════════════════════════════════════════════════

    def _has_armor_by_name(self, player, armor_name: str) -> bool:
        """检查玩家是否已有指定名称的活跃护甲"""
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_all_active'):
            for piece in armor.get_all_active():
                if piece.name == armor_name:
                    return True
        return False
    def _count_outer_armor(self, player) -> int:
        """统计玩家活跃的外层护甲数量"""
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return len(armor.get_active(ArmorLayer.OUTER))
        return 0
    def _count_inner_armor(self, player) -> int:
        """统计玩家活跃的内层护甲数量"""
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return len(armor.get_active(ArmorLayer.INNER))
        return 0
    def _get_outer_armor_attr(self, player) -> list:
        """获取玩家所有活跃外层护甲的属性列表"""
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return [a.attribute for a in armor.get_active(ArmorLayer.OUTER)]
        return []
    def _get_inner_armor_attr(self, player) -> list:
        """获取玩家所有活跃内层护甲的属性列表"""
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return [a.attribute for a in armor.get_active(ArmorLayer.INNER)]
        return []
    # ════════════════════════════════════════════════════════
    #  装备查询：武器
    # ════════════════════════════════════════════════════════

    def _get_weapon_damage(self, weapon) -> float:
        """获取武器有效伤害"""
        if not weapon:
            return 0.0
        if hasattr(weapon, 'get_effective_damage'):
            return weapon.get_effective_damage()
        return getattr(weapon, 'base_damage', 1.0)

    def _get_weapon_range(self, weapon) -> str:
        """获取武器的射程类型"""
        from models.equipment import WeaponRange
        if not weapon:
            return "melee"
        wr = getattr(weapon, 'weapon_range', None)
        if wr == WeaponRange.MELEE:
            return "melee"
        elif wr == WeaponRange.RANGED:
            return "ranged"
        elif wr == WeaponRange.AREA:
            return "area"
        return "melee"

    def _captain_has_police_escort(self, captain, state) -> bool:
        """检查队长所在地点是否有活跃的警察单位"""
        police = getattr(state, 'police', None)
        if not police:
            return False
        captain_loc = self._get_location_str(captain)
        for unit in getattr(police, 'units', []):
            if (unit.is_alive() and not unit.is_disabled()
                    and getattr(unit, 'location', None) == captain_loc):
                return True
        return False

    def _get_weapon_attr(self, weapon):
        """获取武器属性（返回 Attribute 枚举）"""
        from utils.attribute import Attribute
        attr = getattr(weapon, 'attribute', None)
        if isinstance(attr, Attribute):
            return attr
        return Attribute.ORDINARY

    def _has_melee_only(self, player) -> bool:
        """是否只有近战武器"""
        weapons = getattr(player, 'weapons', [])
        for w in weapons:
            if self._get_weapon_range(w) != "melee":
                return False
        return True

    def _has_two_aoe_types(self, player) -> bool:
        """检查玩家是否拥有两种不同属性的AOE武器（魔法+科技）"""
        from utils.attribute import Attribute
        aoe_attrs = set()
        # Check weapon list
        for w in getattr(player, 'weapons', []):
            if w and w.name in ("地震", "地动山摇", "电磁步枪"):
                aoe_attrs.add(self._get_weapon_attr(w))
        # Check learned spells (地震/地动山摇 are learned spells, not always in weapons list)
        learned = getattr(player, 'learned_spells', set())
        if "地震" in learned or "地动山摇" in learned:
            aoe_attrs.add(Attribute.MAGIC)
        return Attribute.MAGIC in aoe_attrs and Attribute.TECH in aoe_attrs

    def _count_distinct_aoe_attrs(self, player) -> int:
        """Count distinct attribute types among player's AOE weapons"""
        from utils.attribute import Attribute
        attrs = set()
        aoe_names = self._get_all_aoe_weapon_names(player)
        for aoe_name in aoe_names:
            aoe_weapon = next((w for w in getattr(player, 'weapons', [])
                            if w and w.name == aoe_name), None)
            if not aoe_weapon:
                from models.equipment import make_weapon
                aoe_weapon = make_weapon(aoe_name)
            if aoe_weapon:
                attrs.add(self._get_weapon_attr(aoe_weapon))
        return len(attrs)
    # ════════════════════════════════════════════════════════
    #  状态检查：隐身 / 病毒免疫 / 法术
    # ════════════════════════════════════════════════════════

    def _has_stealth(self, player) -> bool:
        """检查玩家是否有隐身能力"""
        # 检查隐身状态
        if getattr(player, 'is_invisible', False):
            return True
        # 检查物品（player.items 列表）
        items = getattr(player, 'items', [])
        for item in items:
            name = getattr(item, 'name', '')
            if name in ("隐身衣", "隐形涂层"):
                return True
        # 检查已学法术（player.learned_spells 是set）
        learned = getattr(player, 'learned_spells', set())
        if "隐身术" in learned:
            return True
        return False
    def _has_virus_immunity(self, player) -> bool:
        """检查玩家是否有病毒免疫"""
        # 检查物品
        items = getattr(player, 'items', [])
        for item in items:
            name = getattr(item, 'name', '')
            if name == "防毒面具":
                return True
        # 检查已学法术
        learned = getattr(player, 'learned_spells', set())
        if "封闭" in learned:
            return True
        # 检查 has_seal 标记
        if getattr(player, 'has_seal', False):
            return True
        return False
    def _get_learned_spells(self, player) -> set:
        """获取玩家已学法术集合"""
        return getattr(player, 'learned_spells', set())

    def _has_unused_mythland(self, target) -> bool:
        """检查目标是否持有未使用的神话之外天赋"""
        talent = getattr(target, 'talent', None)
        if talent and getattr(talent, 'name', '') == "神话之外":
            if not getattr(talent, 'used', True):
                return True
        return False
    # ════════════════════════════════════════════════════════
    #  位置查询
    # ════════════════════════════════════════════════════════

    def _get_location_str(self, player) -> str:
        """获取玩家位置字符串"""
        loc = getattr(player, 'location', None)
        if loc is None:
            return "unknown"
        if isinstance(loc, str):
            return loc
        if hasattr(loc, 'name'):
            return loc.name
        return str(loc)

    def _is_at_home(self, player) -> bool:
        """是否在自己家"""
        loc = self._get_location_str(player)
        pid = getattr(player, 'player_id', '')
        return loc == "home" or loc == f"home_{pid}" or "家" in loc

    def _same_location(self, player1, player2) -> bool:
        """两个玩家是否在同一地点"""
        loc1 = self._get_location_str(player1)
        loc2 = self._get_location_str(player2)
        return loc1 == loc2 and loc1 != "unknown"

    def _get_same_location_targets(self, player, state) -> List[Any]:
        """获取同地点的敌方玩家"""
        result = []
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.is_alive() and self._same_location(player, target):
                result.append(target)
        return result

    def _find_nearest_enemy_location(self, player, state) -> Optional[str]:
            """找到威胁度最大的敌人所在位置（因为这游戏没有距离概念啦）
            aggressive 人格会优先去发育者（从未攻击过任何人的玩家）所在位置
            """
            # 预计算全场最强玩家的 power（避免在循环内重复计算）
            max_power = max(
                (self._estimate_power(state.get_player(other_pid))
                 for other_pid in state.player_order
                 if other_pid != player.player_id
                 and state.get_player(other_pid) and state.get_player(other_pid).is_alive()),
                default=0
            )
            candidates = []
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and target.is_alive():
                    target_loc = self._get_location_str(target)
                    threat = self._threat_scores.get(target.name, 0)
                    target_power = self._estimate_power(target)
                    # aggressive 优先骚扰发育者
                    if self.personality == "aggressive":
                        target_name = getattr(target, 'name', '')
                        target_pid = getattr(target, 'player_id', '')
                        is_passive = (target_name not in self._players_who_attacked
                                    and target_pid not in self._players_who_attacked)
                        if is_passive:
                            threat += 30 + target_power * 0.3  # 越肉的发育者越危险
                    # 全场最强玩家额外加分
                    # 甲最多的人是最大的后期威胁，所有人都应该优先针对
                    if target_power >= max_power:
                        threat += 40  # 最强玩家额外 +40 优先级
                    candidates.append((target_loc, threat))
            if not candidates:
                return None
            # 超新星分散：如果场上有火萤持有超新星，优先去人少的地点
            if self._firefly_supernova_threat(player, state):
                # 统计每个地点的玩家数（含自己）
                for i, (loc, threat) in enumerate(candidates):
                    player_count = self._count_enemies_at(loc, player, state)
                    if player_count >= 2:
                        # 多人扎堆的地点大幅降低吸引力
                        candidates[i] = (loc, threat - 200 * player_count)
                    elif player_count == 0:
                        # 空地点加分（分散到没人的地方）
                        candidates[i] = (loc, threat + 50)
            # 按威胁分排序
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0]

    def _find_safe_location(self, player, state) -> Optional[str]:
        """找到最安全的位置（按敌人数量排序）"""
        loc = self._get_location_str(player)
        enemies_here = len(self._get_same_location_targets(player, state))
        if enemies_here == 0:
            return None  # 当前已安全
        # 收集所有候选地点及其敌人数
        candidates = []
        all_locations = ["home", "商店", "医院", "魔法所", "军事基地", "警察局"]
        for test_loc in all_locations:
            if test_loc == loc:
                continue
            enemies_at = self._count_enemies_at(test_loc, player, state)
            candidates.append((test_loc, enemies_at))
        # 超新星分散：如果场上有火萤持有超新星，优先去人少的地点
        if self._firefly_supernova_threat(player, state):
            # 统计每个地点的玩家数（含自己）
            for i, (loc, enemy_count) in enumerate(candidates):
                player_count = self._count_enemies_at(loc, player, state)
                if player_count >= 2:
                    # 多人扎堆的地点大幅提高排序值（升序中排更后）
                    candidates[i] = (loc, enemy_count + 200 * player_count)
                elif player_count == 0:
                    # 空地点降低排序值（升序中排更前）
                    candidates[i] = (loc, enemy_count - 50)
        # 按敌人数升序排序，优先去没人的地方
        candidates.sort(key=lambda x: x[1])
        if candidates:
            return candidates[0][0]
        return "home"

    def _count_enemies_at(self, location: str, player, state) -> int:
        count = 0
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.is_alive():
                target_loc = self._get_location_str(target)
                if target_loc == location:
                    count += 1
                elif location == "home" and target_loc == f"home_{player.player_id}":
                    count += 1
        return count
    # ════════════════════════════════════════════════════════
    #  命令格式化与验证
    # ════════════════════════════════════════════════════════

    def _format_command(self, raw_cmd: str) -> str:
        """格式化命令"""
        return raw_cmd.strip()
    def _validate_command(self, cmd: str, available: List[str]) -> bool:
        """验证命令是否合法"""
        if not cmd:
            return False
        parts = cmd.split()
        if not parts:
            return False
        action = parts[0]
        # 检查行动类型是否可用
        if action in available:
            return True
        # 特殊命令
        if action in ("police", "talent_activate", "special"):
            return True
        return False
    def _fallback_command(self, player, state, available: List[str]) -> str:
        """
        Bug15修复：所有策略都失败时的兜底命令
        优先选择安全的行动
        """
        debug_ai_basic(player.name, "使用兜底命令")
        # 1) forfeit（放弃行动）永远合法
        if "forfeit" in available:
            return "forfeit"
        # 2) 移动到安全位置
        if "move" in available:
            safe = self._find_safe_location(player, state)
            if safe:
                return f"move {safe}"
            return "move home"
        # 3) 起床
        if "wake" in available:
            return "wake"
        # 4) 交互
        if "interact" in available:
            return "interact"
        # 5) 实在没办法
        return "forfeit"
