"""GameQuery —— 纯读取查询服务（新架构基础设施层）

无状态类，所有信息通过参数传入。提供装备查询、护甲统计、武器属性、
位置判断、天赋状态检查、战力估算等只读方法。

新架构的 Mind / CommandBuilder / TalentHook 统一通过 GameQuery 访问
游戏数据，不再依赖 mixin 的 self._xxx 属性。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Set

from controllers.ai.constants import (
    EFFECTIVE_AGAINST, POLICE_AOE_WEAPONS, LOCATIONS,
    debug_ai_basic,
)


class GameQuery:
    """无状态的游戏查询服务。所有方法均为纯函数（或 staticmethod）。"""

    # ════════════════════════════════════════════════════════
    #  基础工具
    # ════════════════════════════════════════════════════════

    @staticmethod
    def pname(player) -> str:
        return getattr(player, 'name', None) or "AI"

    @staticmethod
    def safe_float(value) -> float:
        if value is None:
            return 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    # ════════════════════════════════════════════════════════
    #  天赋状态检查
    # ════════════════════════════════════════════════════════

    @staticmethod
    def has_talent(player, talent_name: str) -> bool:
        talent = getattr(player, 'talent', None)
        return talent is not None and getattr(talent, 'name', '') == talent_name

    @staticmethod
    def has_firefly_talent(player) -> bool:
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'name') and talent.name == "火萤IV型-完全燃烧":
            return True
        return False

    @staticmethod
    def firefly_debuff_active(player) -> bool:
        talent = getattr(player, 'talent', None)
        if not talent or getattr(talent, 'name', '') != "火萤IV型-完全燃烧":
            return False
        return bool(
            getattr(talent, 'debuff_active', False)
            or getattr(talent, 'debuff_started', False)
        )

    @staticmethod
    def is_in_savior_state(player) -> bool:
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'is_savior'):
            return talent.is_savior
        return False

    @staticmethod
    def has_savior_talent(player) -> bool:
        talent = getattr(player, 'talent', None)
        return bool(talent and hasattr(talent, 'name') and talent.name == "愿负世，照拂黎明")

    @staticmethod
    def get_divinity(player) -> int:
        talent = getattr(player, 'talent', None)
        return getattr(talent, 'divinity', 0) if talent else 0

    @staticmethod
    def has_hologram_talent(player) -> bool:
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'name') and talent.name == "请一直，注视着我":
            return True
        return False

    @staticmethod
    def has_active_hologram(player) -> bool:
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'name') and talent.name == "请一直，注视着我":
            return getattr(talent, 'active', False)
        return False

    @staticmethod
    def hologram_exhausted(player) -> bool:
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'name') and talent.name == "请一直，注视着我":
            return (getattr(talent, 'used', False)
                    and getattr(talent, 'max_uses', 0) <= 0
                    and not getattr(talent, 'active', False))
        return False

    @staticmethod
    def has_hoshino_talent(player) -> bool:
        talent = getattr(player, 'talent', None)
        return bool(talent and hasattr(talent, 'name') and talent.name == "大叔我啊，剪短发了")

    @staticmethod
    def hoshino_is_self_doubt(target) -> bool:
        talent = getattr(target, 'talent', None)
        return bool(talent and getattr(talent, 'self_doubt_pending', False))

    @staticmethod
    def hoshino_shield_mode(player):
        talent = getattr(player, 'talent', None)
        return getattr(talent, 'shield_mode', None) if talent else None

    @staticmethod
    def is_being_burned(player, state) -> bool:
        for pid in state.player_order:
            p = state.get_player(pid)
            if p and p.is_alive() and p.talent and hasattr(p.talent, 'burn_targets'):
                if player.player_id in p.talent.burn_targets:
                    if p.talent.burn_targets[player.player_id] > 0:
                        return True
        return False

    @staticmethod
    def firefly_exists_in_game(state) -> bool:
        for pid in state.player_order:
            p = state.get_player(pid)
            if p and p.is_alive():
                talent = getattr(p, 'talent', None)
                if talent and hasattr(talent, 'name') and talent.name == "火萤IV型-完全燃烧":
                    return True
        return False

    @staticmethod
    def firefly_supernova_threat(player, state) -> bool:
        if GameQuery.has_firefly_talent(player):
            return False
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            t = state.get_player(pid)
            if t and t.is_alive() and t.talent:
                if getattr(t.talent, 'has_supernova', False):
                    return True
        return False

    @staticmethod
    def has_unused_mythland(target) -> bool:
        talent = getattr(target, 'talent', None)
        if talent and getattr(talent, 'name', '') == "神话之外":
            if not getattr(talent, 'used', True):
                return True
        return False

    # ════════════════════════════════════════════════════════
    #  装备查询：护甲
    # ════════════════════════════════════════════════════════

    @staticmethod
    def has_armor_by_name(player, armor_name: str) -> bool:
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_all_active'):
            for piece in armor.get_all_active():
                if piece.name == armor_name:
                    return True
        return False

    @staticmethod
    def count_outer_armor(player) -> int:
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return len(armor.get_active(ArmorLayer.OUTER))
        return 0

    @staticmethod
    def count_inner_armor(player) -> int:
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return len(armor.get_active(ArmorLayer.INNER))
        return 0

    @staticmethod
    def get_outer_armor_attr(player) -> list:
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return [a.attribute for a in armor.get_active(ArmorLayer.OUTER)]
        return []

    @staticmethod
    def get_inner_armor_attr(player) -> list:
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return [a.attribute for a in armor.get_active(ArmorLayer.INNER)]
        return []

    # ════════════════════════════════════════════════════════
    #  装备查询：武器
    # ════════════════════════════════════════════════════════

    @staticmethod
    def get_weapon_damage(weapon) -> float:
        if not weapon:
            return 0.0
        if hasattr(weapon, 'get_effective_damage'):
            return weapon.get_effective_damage()
        return getattr(weapon, 'base_damage', 1.0)

    @staticmethod
    def get_weapon_range(weapon) -> str:
        from models.equipment import WeaponRange
        if not weapon:
            return "melee"
        legacy_range = getattr(weapon, 'range', None)
        if legacy_range in ("melee", "ranged", "area"):
            return legacy_range
        wr = getattr(weapon, 'weapon_range', None)
        if wr == WeaponRange.MELEE:
            return "melee"
        elif wr == WeaponRange.RANGED:
            return "ranged"
        elif wr == WeaponRange.AREA:
            return "area"
        return "melee"

    @staticmethod
    def can_reach(player, target) -> bool:
        """同地点，或拥有远程/范围武器、远程/范围法术时视为可触及。"""
        if GameQuery.same_location(player, target):
            return True
        for weapon in getattr(player, 'weapons', []):
            if weapon and GameQuery.get_weapon_range(weapon) in ("ranged", "area"):
                return True
        learned = getattr(player, 'learned_spells', set())
        return any(
            spell in learned
            for spell in ("魔法弹幕", "远程魔法弹幕", "地震", "地动山摇")
        )

    @staticmethod
    def can_attack_target(player, target, state=None) -> bool:
        """只读攻击可达性判断；如传入 state，同时检查基础目标合法性。"""
        if state is not None and not GameQuery.is_valid_attack_target(player, target, state):
            return False
        return GameQuery.can_reach(player, target)

    @staticmethod
    def get_weapon_attr(weapon):
        from utils.attribute import Attribute
        attr = getattr(weapon, 'attribute', None)
        if isinstance(attr, Attribute):
            return attr
        return Attribute.ORDINARY

    @staticmethod
    def has_melee_only(player) -> bool:
        weapons = getattr(player, 'weapons', [])
        for w in weapons:
            if GameQuery.get_weapon_range(w) != "melee":
                return False
        return True

    @staticmethod
    def has_two_aoe_types(player) -> bool:
        from utils.attribute import Attribute
        aoe_attrs = set()
        for w in getattr(player, 'weapons', []):
            if w and w.name in ("地震", "地动山摇", "电磁步枪"):
                aoe_attrs.add(GameQuery.get_weapon_attr(w))
        learned = getattr(player, 'learned_spells', set())
        if "地震" in learned or "地动山摇" in learned:
            aoe_attrs.add(Attribute.MAGIC)
        return Attribute.MAGIC in aoe_attrs and Attribute.TECH in aoe_attrs

    @staticmethod
    def count_distinct_aoe_attrs(player) -> int:
        attrs = set()
        aoe_names = GameQuery.get_all_aoe_weapon_names(player)
        for aoe_name in aoe_names:
            aoe_weapon = next((w for w in getattr(player, 'weapons', [])
                               if w and w.name == aoe_name), None)
            if not aoe_weapon:
                from models.equipment import make_weapon
                aoe_weapon = make_weapon(aoe_name)
            if aoe_weapon:
                attrs.add(GameQuery.get_weapon_attr(aoe_weapon))
        return len(attrs)

    @staticmethod
    def has_aoe_weapon(player) -> bool:
        for w in getattr(player, 'weapons', []):
            if w and w.name in POLICE_AOE_WEAPONS:
                return True
        learned = getattr(player, 'learned_spells', set())
        for spell_name in POLICE_AOE_WEAPONS:
            if spell_name in learned:
                return True
        return False

    @staticmethod
    def get_all_aoe_weapon_names(player) -> List[str]:
        names = []
        for w in getattr(player, 'weapons', []):
            if w and w.name in POLICE_AOE_WEAPONS:
                names.append(w.name)
        learned = getattr(player, 'learned_spells', set())
        for spell_name in POLICE_AOE_WEAPONS:
            if spell_name in learned and spell_name not in names:
                names.append(spell_name)
        return names

    @staticmethod
    def get_aoe_weapon_name(player) -> Optional[str]:
        for w in getattr(player, 'weapons', []):
            name = w.name if hasattr(w, 'name') else str(w)
            if name in POLICE_AOE_WEAPONS:
                return name
        learned = getattr(player, 'learned_spells', set())
        if "地动山摇" in learned:
            return "地动山摇"
        if "地震" in learned:
            return "地震"
        return None

    @staticmethod
    def has_effective_aoe_against(player, target) -> bool:
        target_armor_attrs = GameQuery.get_outer_armor_attr(target)
        if not target_armor_attrs:
            target_armor_attrs = GameQuery.get_inner_armor_attr(target)
        if not target_armor_attrs:
            return True
        aoe_names = GameQuery.get_all_aoe_weapon_names(player)
        for aoe_name in aoe_names:
            aoe_weapon = next((w for w in getattr(player, 'weapons', [])
                               if w and w.name == aoe_name), None)
            if not aoe_weapon:
                from models.equipment import make_weapon
                aoe_weapon = make_weapon(aoe_name)
            if not aoe_weapon:
                continue
            if (getattr(aoe_weapon, 'requires_charge', False)
                    and getattr(aoe_weapon, 'charge_mandatory', True)
                    and not getattr(aoe_weapon, 'is_charged', False)):
                continue
            w_attr = GameQuery.get_weapon_attr(aoe_weapon)
            effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
            if any(a in effective_set for a in target_armor_attrs):
                return True
        return False

    @staticmethod
    def captain_has_police_escort(captain, state) -> bool:
        police = getattr(state, 'police', None)
        if not police:
            return False
        captain_loc = GameQuery.get_location_str(captain)
        for unit in getattr(police, 'units', []):
            if (unit.is_alive() and not unit.is_disabled()
                    and getattr(unit, 'location', None) == captain_loc):
                return True
        return False

    # ════════════════════════════════════════════════════════
    #  状态检查：隐身 / 病毒免疫 / 法术
    # ════════════════════════════════════════════════════════

    @staticmethod
    def has_stealth(player) -> bool:
        if getattr(player, 'is_invisible', False):
            return True
        for weapon in getattr(player, 'weapons', []):
            if weapon and getattr(weapon, 'name', '') == "隐形涂层":
                return True
        items = getattr(player, 'items', [])
        for item in items:
            name = getattr(item, 'name', item if isinstance(item, str) else '')
            if name in ("隐身衣", "隐形涂层"):
                return True
        learned = getattr(player, 'learned_spells', set())
        if "隐身术" in learned:
            return True
        return False

    @staticmethod
    def has_virus_immunity(player) -> bool:
        items = getattr(player, 'items', [])
        for item in items:
            name = getattr(item, 'name', item if isinstance(item, str) else '')
            if "防毒面具" in (name or ""):
                return True
        talent = getattr(player, 'talent', None)
        if talent:
            if getattr(talent, 'virus_immune', False):
                return True
            if getattr(talent, 'is_terror', False):
                return True
            name = getattr(talent, 'name', '')
            if name == "你们，由我守护":
                return True
            if name == "愿负世，照拂黎明" and getattr(talent, 'is_savior', False):
                return True
        learned = getattr(player, 'learned_spells', set())
        if "封闭" in learned:
            return True
        if getattr(player, 'has_seal', False):
            return True
        return False

    @staticmethod
    def get_learned_spells(player) -> set:
        return getattr(player, 'learned_spells', set())

    # ════════════════════════════════════════════════════════
    #  位置查询
    # ════════════════════════════════════════════════════════

    @staticmethod
    def get_location_str(player) -> str:
        loc = getattr(player, 'location', None)
        if loc is None:
            return "unknown"
        if isinstance(loc, str):
            return loc
        if hasattr(loc, 'name'):
            return loc.name
        return str(loc)

    @staticmethod
    def is_at_home(player) -> bool:
        loc = GameQuery.get_location_str(player)
        pid = getattr(player, 'player_id', '')
        return loc == "home" or loc == f"home_{pid}" or "家" in loc

    @staticmethod
    def is_home_location(loc: str) -> bool:
        """检查是否在任何形式的家位置（home / home_p1 / 某某的家）"""
        loc = loc or ""
        return loc == "home" or loc.startswith("home_") or "家" in loc

    @staticmethod
    def normalize_location(loc: str) -> str:
        """将 home_* 变体标准化为 'home'。"""
        return "home" if GameQuery.is_home_location(loc or "") else loc

    @staticmethod
    def same_location(player1, player2) -> bool:
        loc1 = GameQuery.get_location_str(player1)
        loc2 = GameQuery.get_location_str(player2)
        return loc1 == loc2 and loc1 != "unknown"

    @staticmethod
    def is_home_location(loc: str) -> bool:
        return loc == "home" or str(loc).startswith("home_") or "家" in str(loc)

    @staticmethod
    def normalize_location(loc: str) -> str:
        return "home" if GameQuery.is_home_location(str(loc)) else str(loc)

    @staticmethod
    def get_same_location_targets(player, state) -> List[Any]:
        result = []
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.is_alive() and GameQuery.same_location(player, target):
                result.append(target)
        return result

    @staticmethod
    def count_enemies_at(location: str, player, state) -> int:
        count = 0
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.is_alive():
                target_loc = GameQuery.get_location_str(target)
                if target_loc == location:
                    count += 1
                elif location == "home" and target_loc == f"home_{player.player_id}":
                    count += 1
        return count

    @staticmethod
    def get_alive_enemy_ids(state, my_id) -> List[str]:
        """存活敌人 ID 列表。"""
        enemies = []
        for pid in state.player_order:
            if pid == my_id:
                continue
            p = state.get_player(pid)
            if p and p.is_alive():
                enemies.append(p.player_id)
        return enemies

    @staticmethod
    def count_players_at(location: str, state, my_id) -> int:
        """指定地点其他存活玩家数。"""
        count = 0
        for pid in state.player_order:
            if pid == my_id:
                continue
            p = state.get_player(pid)
            if not p or not p.is_alive():
                continue
            if GameQuery.get_location_str(p) == location:
                count += 1
        return count

    @staticmethod
    def find_nearest_enemy_location(player, state, threat_scores: Optional[Dict[str, float]] = None,
                                    personality: str = "balanced",
                                    players_who_attacked: Optional[Set[str]] = None) -> Optional[str]:
        threat_scores = threat_scores or {}
        players_who_attacked = players_who_attacked or set()
        max_power = max(
            (GameQuery.estimate_power(state.get_player(other_pid))
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
                target_loc = GameQuery.get_location_str(target)
                threat = threat_scores.get(target.name, 0)
                target_power = GameQuery.estimate_power(target)
                if personality == "aggressive":
                    target_name = getattr(target, 'name', '')
                    target_pid = getattr(target, 'player_id', '')
                    is_passive = (target_name not in players_who_attacked
                                  and target_pid not in players_who_attacked)
                    if is_passive:
                        threat += 30 + target_power * 0.3
                if target_power >= max_power:
                    threat += 40
                candidates.append((target_loc, threat))
        if not candidates:
            return None
        if GameQuery.firefly_supernova_threat(player, state):
            for i, (loc, threat) in enumerate(candidates):
                player_count = GameQuery.count_enemies_at(loc, player, state)
                if player_count >= 2:
                    candidates[i] = (loc, threat - 200 * player_count)
                elif player_count == 0:
                    candidates[i] = (loc, threat + 50)
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    @staticmethod
    def find_safe_location(player, state) -> Optional[str]:
        loc = GameQuery.get_location_str(player)
        enemies_here = len(GameQuery.get_same_location_targets(player, state))
        if enemies_here == 0:
            return None
        candidates = []
        all_locations = ["home", "商店", "医院", "魔法所", "军事基地", "警察局"]
        for test_loc in all_locations:
            if test_loc == loc:
                continue
            enemies_at = GameQuery.count_enemies_at(test_loc, player, state)
            candidates.append((test_loc, enemies_at))
        if GameQuery.firefly_supernova_threat(player, state):
            for i, (test_loc, enemy_count) in enumerate(candidates):
                player_count = GameQuery.count_enemies_at(test_loc, player, state)
                if player_count >= 2:
                    candidates[i] = (test_loc, enemy_count + 200 * player_count)
                elif player_count == 0:
                    candidates[i] = (test_loc, enemy_count - 50)
        candidates.sort(key=lambda x: x[1])
        if candidates:
            return candidates[0][0]
        return "home"

    # ════════════════════════════════════════════════════════
    #  战力与伤害估算（从 evaluation_mixin 复制）
    # ════════════════════════════════════════════════════════

    @staticmethod
    def get_effective_hp(player) -> float:
        hp = player.hp
        talent = getattr(player, 'talent', None)
        if talent:
            temp_hp = getattr(talent, 'temp_hp', 0.0)
            if temp_hp > 0:
                hp += temp_hp
            charges = getattr(talent, 'ardent_wish_charges', 0)
            if charges > 0:
                hp += charges * 0.5
        return hp

    @staticmethod
    def estimate_talent_adjusted_damage(player, weapon=None) -> float:
        if weapon is not None:
            base_dmg = GameQuery.get_weapon_damage(weapon)
        else:
            weapons = getattr(player, 'weapons', [])
            if not weapons:
                return 0.0
            base_dmg = max((GameQuery.get_weapon_damage(w) for w in weapons if w), default=0.0)
        talent = getattr(player, 'talent', None)
        if not talent:
            return base_dmg
        if hasattr(talent, 'name') and talent.name == "火萤IV型-完全燃烧":
            return base_dmg * 2.0
        if hasattr(talent, 'is_savior') and talent.is_savior:
            bonus = getattr(talent, 'temp_attack_bonus', 0.0)
            aoe_bonus = getattr(talent, 'aoe_bonus', 0.0)
            if weapon and GameQuery.get_weapon_range(weapon) == "area":
                return base_dmg + aoe_bonus
            return base_dmg + bonus
        return base_dmg

    @staticmethod
    def best_weapon_damage(player) -> float:
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return 0.0
        best = 0.0
        for w in weapons:
            if not w:
                continue
            dmg = GameQuery.estimate_talent_adjusted_damage(player, w)
            if dmg > best:
                best = dmg
        return best

    @staticmethod
    def best_weapon_damage_raw(player) -> float:
        """最强武器的原始伤害（不含天赋加成，含已学法术临时武器）。"""
        best = 0.0
        for weapon in getattr(player, 'weapons', []):
            if not weapon:
                continue
            dmg = getattr(weapon, 'base_damage', 0)
            if isinstance(dmg, (int, float)) and dmg > best:
                best = float(dmg)
        for spell in getattr(player, 'learned_spells', set()):
            from models.equipment import make_weapon
            temp_w = make_weapon(spell)
            if not temp_w:
                continue
            dmg = getattr(temp_w, 'base_damage', 0)
            if isinstance(dmg, (int, float)) and dmg > best:
                best = float(dmg)
        return best

    @staticmethod
    def has_real_weapon(player) -> bool:
        """是否有非拳击武器。"""
        return any(
            weapon and getattr(weapon, 'name', '') != "拳击"
            for weapon in getattr(player, 'weapons', [])
        )

    @staticmethod
    def count_real_weapons(player) -> int:
        """非拳击武器数量。"""
        return sum(
            1 for weapon in getattr(player, 'weapons', [])
            if weapon and getattr(weapon, 'name', '') != "拳击"
        )

    @staticmethod
    def estimate_power(player) -> float:
        power = 0.0
        power += GameQuery.get_effective_hp(player) * 10
        weapons = getattr(player, 'weapons', [])
        for w in weapons:
            power += GameQuery.estimate_talent_adjusted_damage(player, w) * 15 if w else 0
        outer = GameQuery.count_outer_armor(player)
        inner = GameQuery.count_inner_armor(player)
        power += outer * 20
        power += inner * 15
        if GameQuery.has_stealth(player):
            power += 10
        if getattr(player, 'has_detection', False):
            power += 5
        t_talent = getattr(player, 'talent', None)
        if t_talent and hasattr(t_talent, 'iron_horus_hp'):
            iron_hp = getattr(t_talent, 'iron_horus_hp', 0)
            if iron_hp > 0:
                power += iron_hp * 15
        return power

    # ════════════════════════════════════════════════════════
    #  危险判定（从 evaluation_mixin 复制）
    # ════════════════════════════════════════════════════════

    @staticmethod
    def is_critical(
        player, state,
        police_cache: Optional[Dict] = None,
        strategy: Optional[Any] = None,
    ) -> bool:
        if GameQuery.has_firefly_talent(player):
            return GameQuery.is_critical_firefly(player, state, police_cache)
        if GameQuery.has_hoshino_talent(player):
            return GameQuery.is_critical_hoshino(player, state, police_cache)
        if (
            getattr(player, 'is_captain', False)
            and strategy
            and hasattr(strategy, 'assess_captain_danger')
        ):
            if strategy.assess_captain_danger(
                player, state,
                police_cache=police_cache or {},
                count_outer_armor_fn=GameQuery.count_outer_armor,
            ):
                return True
        if player.hp <= 0.5:
            return True
        if player.hp <= 1.0 and GameQuery.count_outer_armor(player) == 0:
            return True
        pc = police_cache or {}
        if pc.get("report_target") == player.player_id:
            phase = pc.get("report_phase", "idle")
            if phase == "dispatched":
                return True
        locked_count = GameQuery.count_locked_by(player, state)
        if locked_count >= 2:
            total_armor = GameQuery.count_outer_armor(player) + GameQuery.count_inner_armor(player)
            if total_armor <= 1:
                return True
        if GameQuery.is_anchored(player, state):
            return True
        for pid in state.player_order:
            p = state.get_player(pid)
            if (p and p.is_alive() and p.talent
                    and hasattr(p.talent, 'burn_targets')
                    and player.player_id in p.talent.burn_targets):
                if GameQuery.count_outer_armor(player) + GameQuery.count_inner_armor(player) == 0:
                    return True
                break
        return False

    @staticmethod
    def is_critical_firefly(player, state, police_cache: Optional[Dict] = None) -> bool:
        pc = police_cache or {}
        if pc.get("report_target") == player.player_id:
            phase = pc.get("report_phase", "idle")
            if phase == "dispatched":
                return True
        if GameQuery.is_anchored(player, state):
            return True
        if GameQuery.firefly_debuff_active(player):
            return False
        else:
            outer = GameQuery.count_outer_armor(player)
            if outer > 0:
                return False
            markers = getattr(state, 'markers', None)
            if markers:
                engaged = markers.get_related(player.player_id, "ENGAGED_WITH")
                for eid in engaged:
                    enemy = state.get_player(eid)
                    if enemy and enemy.is_alive():
                        enemy_best_dmg = GameQuery.best_weapon_damage(enemy)
                        if enemy_best_dmg > 1.0:
                            return True
            locked_count = GameQuery.count_locked_by(player, state)
            if locked_count > 1:
                return True
            return False

    @staticmethod
    def is_critical_hoshino(player, state, police_cache: Optional[Dict] = None) -> bool:
        if player.hp <= 0.5:
            return True
        talent = getattr(player, 'talent', None)
        iron_horus_hp = getattr(talent, 'iron_horus_hp', 0) if talent else 0
        has_horus_protection = iron_horus_hp > 0
        regular_armor = GameQuery.count_outer_armor(player) + GameQuery.count_inner_armor(player)
        effective_armor = regular_armor + (2 if has_horus_protection else 0)
        if player.hp <= 1.0 and effective_armor == 0:
            return True
        pc = police_cache or {}
        if pc.get("report_target") == player.player_id:
            phase = pc.get("report_phase", "idle")
            if phase == "dispatched":
                return True
        locked_count = GameQuery.count_locked_by(player, state)
        if locked_count >= 1 and effective_armor <= 1:
            return True
        if GameQuery.is_anchored(player, state):
            return True
        for pid in state.player_order:
            p = state.get_player(pid)
            if (p and p.is_alive() and p.talent
                    and hasattr(p.talent, 'burn_targets')
                    and player.player_id in p.talent.burn_targets):
                if effective_armor <= 1:
                    return True
                break
        return False

    @staticmethod
    def is_anchored(player, state) -> bool:
        markers = getattr(state, 'markers', None)
        if not markers or not hasattr(markers, 'has_relation'):
            return False
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            if markers.has_relation(player.player_id, "ANCHORED_BY", pid):
                return True
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.talent and hasattr(target.talent, 'is_anchoring'):
                if target.talent.is_anchoring(player):
                    return True
        return False

    @staticmethod
    def count_locked_by(player, state) -> int:
        count = 0
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.is_alive():
                locked = getattr(target, 'locked_target', None)
                if locked and (locked == player.name or locked == player.player_id):
                    count += 1
        return count

    @staticmethod
    def is_valid_attack_target(player, target, state) -> bool:
        if getattr(player, 'is_police', False) and not getattr(player, 'is_captain', False):
            if not getattr(target, 'is_criminal', False):
                return False
        if (target.talent and hasattr(target.talent, 'has_love_wish')
                and target.talent.has_love_wish(player.player_id)):
            return False
        if getattr(target, 'is_invisible', False) and not getattr(player, 'has_detection', False):
            markers_obj = getattr(state, 'markers', None)
            if markers_obj and hasattr(markers_obj, 'is_visible_to'):
                if not markers_obj.is_visible_to(target.player_id, player.player_id, player.has_detection):
                    return False
        return True

    # ════════════════════════════════════════════════════════
    #  战斗查询（从 combat_mixin 复制）
    # ════════════════════════════════════════════════════════

    @staticmethod
    def is_passive_player(target, state) -> bool:
        """无战斗关系的被动玩家。"""
        markers = getattr(state, 'markers', None)
        if not markers:
            return True
        tid = getattr(target, 'player_id', None)
        if not tid:
            return True
        engaged = markers.get_related(tid, "ENGAGED_WITH")
        if engaged:
            return False
        for pid in getattr(state, 'player_order', []):
            if pid == tid:
                continue
            if markers.has_relation(pid, "LOCKED_BY", tid):
                return False
        return True

    @staticmethod
    def all_weapons_countered(player, target) -> bool:
        weapons = [
            weapon for weapon in getattr(player, 'weapons', [])
            if weapon and not getattr(weapon, '_hexagram_disabled', False)
        ]
        if not weapons:
            return True
        target_outer = GameQuery.get_outer_armor_attr(target)
        target_inner = GameQuery.get_inner_armor_attr(target)
        target_attrs = target_outer if target_outer else target_inner
        if not target_attrs:
            return False
        for w in weapons:
            w_attr = GameQuery.get_weapon_attr(w)
            effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
            if any(a in effective_set for a in target_attrs):
                return False
        return True

    @staticmethod
    def is_at_disadvantage(player, target) -> bool:
        return GameQuery.estimate_power(player) < GameQuery.estimate_power(target) * 0.7

    @staticmethod
    def should_continue_combat(
        player, target, state, strategy=None,
        personality: str = "balanced",
        political_fallback_level: str = "none",
    ) -> bool:
        if not target or not target.is_alive():
            return False
        if GameQuery.has_firefly_talent(player):
            return not GameQuery.all_weapons_countered(player, target)
        total_armor = GameQuery.count_outer_armor(player) + GameQuery.count_inner_armor(player)
        if GameQuery.has_hoshino_talent(player):
            talent = getattr(player, 'talent', None)
            if getattr(talent, 'iron_horus_hp', 0) > 0:
                total_armor += 2
        if total_armor == 0 and GameQuery.get_effective_hp(player) <= 1.0:
            return False
        disadvantage = GameQuery.is_at_disadvantage(player, target)
        if strategy and hasattr(strategy, 'should_continue_combat'):
            try:
                result = strategy.should_continue_combat(
                    player, target, is_at_disadvantage=disadvantage)
                if result is not None:
                    return result
            except Exception:
                pass
        if disadvantage and personality == "defensive":
            return False
        if state and GameQuery.firefly_supernova_threat(player, state):
            if len(GameQuery.get_same_location_targets(player, state)) >= 2:
                return False
        if GameQuery.all_weapons_countered(player, target):
            return False
        if (personality == "political"
                and political_fallback_level != "full_balanced"
                and not getattr(player, 'is_captain', False)):
            return False
        return True

    @staticmethod
    def is_danger_resolved(
        player, state,
        police_cache: Optional[Dict] = None,
        strategy: Optional[Any] = None,
    ) -> bool:
        """判断危险状态是否已解除。"""
        if GameQuery.is_critical(player, state, police_cache, strategy):
            return False
        if GameQuery.has_firefly_talent(player) and GameQuery.firefly_debuff_active(player):
            return True
        if GameQuery.has_hoshino_talent(player):
            talent = getattr(player, 'talent', None)
            if getattr(talent, 'iron_horus_hp', 0) > 0:
                return True
            if getattr(talent, 'tactical_unlocked', False):
                return True
        total_armor = GameQuery.count_outer_armor(player) + GameQuery.count_inner_armor(player)
        return total_armor >= 2

    # ════════════════════════════════════════════════════════
    #  警察状态读取（从 police_mixin 复制，返回 dict 不写 self）
    # ════════════════════════════════════════════════════════

    @staticmethod
    def read_police_state(state, my_id: Optional[str] = None) -> Dict:
        cache: Dict[str, Any] = {
            "has_police": False,
            "captain_id": None,
            "is_captain": False,
            "authority": 0,
            "report_phase": "idle",
            "report_target": None,
            "units": [],
            "alive_count": 0,
            "active_count": 0,
        }
        if not hasattr(state, 'police') or not state.police:
            return cache
        police = state.police
        cache["has_police"] = True
        cache["captain_id"] = getattr(police, 'captain_id', None)
        cache["is_captain"] = (cache["captain_id"] == my_id) if my_id else False
        cache["authority"] = getattr(police, 'authority', 0)
        cache["report_phase"] = getattr(police, 'report_phase', "idle")
        cache["report_target"] = getattr(police, 'reported_target_id', None)
        units_info = []
        alive_count = 0
        active_count = 0
        for unit in getattr(police, 'units', []):
            alive = unit.is_alive() if hasattr(unit, 'is_alive') else False
            active = (
                unit.is_active() if hasattr(unit, 'is_active') else False
            ) if cache["captain_id"] is not None else False
            info = {
                "id": getattr(unit, 'unit_id', 'unknown'),
                "location": getattr(unit, 'location', None),
                "hp": getattr(unit, 'hp', 0),
                "weapon": getattr(unit, 'weapon_name', '警棍'),
                "outer_armor": getattr(unit, 'outer_armor_name', '盾牌'),
                "is_alive": alive,
                "is_active": active,
                "is_submerged": getattr(unit, 'is_submerged', False),
            }
            units_info.append(info)
            if alive:
                alive_count += 1
            if active:
                active_count += 1
        cache["units"] = units_info
        cache["alive_count"] = alive_count
        cache["active_count"] = active_count
        return cache

    # ════════════════════════════════════════════════════════
    #  其他评估（从 evaluation_mixin 复制）
    # ════════════════════════════════════════════════════════

    @staticmethod
    def best_effective_weapon_damage(player, target) -> float:
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return 0.0
        target_armor_attrs = GameQuery.get_outer_armor_attr(target)
        if not target_armor_attrs:
            target_armor_attrs = GameQuery.get_inner_armor_attr(target)
        best = 0.0
        for w in weapons:
            if not w:
                continue
            dmg = GameQuery.get_weapon_damage(w)
            if GameQuery.has_firefly_talent(player):
                talent = getattr(player, 'talent', None)
                if talent and hasattr(talent, 'modify_outgoing_damage'):
                    mod = talent.modify_outgoing_damage(player, target, w, dmg)
                    if mod and "damage_multiplier_override" in mod:
                        dmg = dmg * mod["damage_multiplier_override"]
                    if mod and "bonus_damage" in mod:
                        dmg += mod["bonus_damage"]
            if target_armor_attrs:
                w_attr = GameQuery.get_weapon_attr(w)
                effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
                if not any(a in effective_set for a in target_armor_attrs):
                    continue
            if dmg > best:
                best = dmg
        return best

    @staticmethod
    def get_police_protected_ids(state) -> Set[str]:
        protected: Set[str] = set()
        pe = getattr(state, 'police_engine', None)
        if pe:
            for pid in state.player_order:
                if pe.is_protected_by_police(pid):
                    protected.add(pid)
        return protected

    @staticmethod
    def get_all_protected_armor_attrs(state, player_id) -> set:
        """收集所有警察保护目标的护甲属性。"""
        attrs: set = set()
        pe = getattr(state, 'police_engine', None)
        if not pe:
            return attrs
        for pid in state.player_order:
            if pid == player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive() or not pe.is_protected_by_police(pid):
                continue
            outer = GameQuery.get_outer_armor_attr(target)
            if outer:
                attrs.update(outer)
            else:
                attrs.update(GameQuery.get_inner_armor_attr(target))
        return attrs

    @staticmethod
    def can_reach_target_in_barrier(player, state, target) -> bool:
        """结界内只能接触同一结界内目标；非结界状态默认可接触。"""
        barrier = getattr(state, 'active_barrier', None)
        if not barrier:
            return True
        if hasattr(barrier, 'is_in_barrier'):
            in_barrier = barrier.is_in_barrier(player.player_id)
        else:
            in_barrier = player.player_id in getattr(barrier, 'barrier_players', [])
        if not in_barrier:
            return True
        if hasattr(barrier, 'barrier_players'):
            return target.player_id in barrier.barrier_players
        return False

    @staticmethod
    def political_should_fallback(player, state) -> str:
        """Political 人格在警察路线不可用时的降级级别。"""
        police = getattr(state, 'police', None)
        if not police:
            return "full_balanced"
        if getattr(police, 'permanently_disabled', False):
            return "full_balanced"
        if getattr(police, 'captain_id', None) == player.player_id:
            return "none"
        if police.has_captain():
            return "full_balanced"
        is_criminal = getattr(player, 'is_criminal', False)
        if not is_criminal and hasattr(police, 'is_criminal'):
            is_criminal = police.is_criminal(player.player_id)
        if is_criminal:
            return "develop_only"
        pe = getattr(state, 'police_engine', None)
        if pe:
            existing = pe.get_current_police_member_id()
            if existing is not None and existing != player.player_id:
                return "full_balanced"
        return "none"
