"""火萤IV型(G1) + 全息影像(G2) + 救世主(G4) 天赋AI钩子"""

from __future__ import annotations
from typing import List, Optional, Any
import random
from controllers.ai.talents.base_hook import BaseTalentAIHook
from controllers.ai.constants import debug_ai_basic


class FireflyAIHook(BaseTalentAIHook):
    """火萤IV型(G1)天赋AI钩子"""
    talent_name = "火萤IV型-完全燃烧"

    def __init__(self, controller: Any):
        self._ctrl = controller

    def is_development_complete(self, player: Any, state: Any) -> Optional[bool]:
        if not self._is_my_talent(player):
            return None
        real_weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
        if not self._ctrl._firefly_debuff_active(player):
            return len(real_weapons) >= 1
        has_sharpened = any(w.name == "小刀" and getattr(w, 'base_damage', 0) >= 2 for w in real_weapons)
        has_gauss = any(w.name == "高斯步枪" for w in real_weapons)
        return has_sharpened and has_gauss

    def modify_target_score(self, target: Any, base_score: float, player: Any) -> float:
        target_name = getattr(target, 'name', '')
        s = base_score
        is_passive = target_name not in getattr(self._ctrl, '_players_who_attacked', set())
        if is_passive:
            s += 70 + self._ctrl._estimate_power(target) * 0.5
        t_talent = getattr(target, 'talent', None)
        if t_talent and getattr(t_talent, 'name', '') == "愿负世，照拂黎明":
            if not getattr(t_talent, 'is_savior', False):
                if self._ctrl._get_divinity(target) <= 6:
                    s += 120
                else:
                    s += 60
            else:
                s += 40
        enemy_best = self._ctrl._best_weapon_damage(target)
        if enemy_best >= 2.0:
            s += 80
        return s

    def should_override_candidates(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        """处理火萤超新星/Phase专用逻辑（替代controller.py L1189-1240）"""
        if not self._is_my_talent(player):
            return None

        candidates = []

        # 超新星优先
        if self._ctrl._has_supernova(player) and "move" in available:
            best_loc = self._pick_supernova_target(player, state)
            if best_loc:
                debug_ai_basic(player.name, f"火萤：超新星过载，目标地点={best_loc}")
                candidates.insert(0, f"move {best_loc}")
                candidates.append("forfeit")
                return candidates

        # Phase 1（debuff前）：拿到刀就冲
        if not self._ctrl._firefly_debuff_active(player):
            has_knife = any(w.name == "小刀" for w in player.weapons if w)
            if has_knife:
                debug_ai_basic(player.name, "火萤Phase1：有刀就冲")
                attack_cmds = self._ctrl._cmd_attack(player, state, available)
                if attack_cmds:
                    candidates.extend(attack_cmds)
                    dev = self._ctrl._cmd_develop_firefly_minimal(player, state, available)
                    candidates.extend(dev)
                    candidates.append("forfeit")
                    return candidates

        # Phase 2/3（debuff后）：攻击优先
        if self._ctrl._firefly_debuff_active(player):
            debug_ai_basic(player.name, "火萤Phase2/3：debuff已生效，攻击优先")
            attack_cmds = self._ctrl._cmd_attack(player, state, available)
            if attack_cmds:
                candidates.extend(attack_cmds)
            dev = self._ctrl._cmd_develop(player, state, available)
            for cmd in dev:
                if cmd not in candidates:
                    candidates.append(cmd)
            candidates.append("forfeit")
            return candidates

        # 击杀机会
        kill_target = self._ctrl._find_firefly_kill_target(player, state)
        if kill_target:
            debug_ai_basic(player.name, "火萤发现击杀机会，打断发育！")
            kill_cmds = self._ctrl._cmd_attack(player, state, available, forced_target=kill_target)
            if kill_cmds:
                candidates.extend(kill_cmds)
                dev = self._ctrl._cmd_develop(player, state, available)
                if dev:
                    candidates.append(dev[0])
                candidates.append("forfeit")
                return candidates

        return None  # 不接管，走常规流程

    def _pick_supernova_target(self, player: Any, state: Any) -> Optional[str]:
        """选择敌人最多的地点"""
        my_loc = str(getattr(player, 'location', ''))
        best_loc = None
        best_count = 0
        for loc in ["home", "商店", "医院", "魔法所", "军事基地", "警察局"]:
            count = 0
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                p = state.get_player(pid)
                if p and p.is_alive() and str(getattr(p, 'location', '')) == loc:
                    count += 1
            if count > best_count:
                best_count = count
                best_loc = loc
        if best_loc and best_count > 0:
            return best_loc
        return None

    def _is_my_talent(self, player: Any) -> bool:
        t = getattr(player, 'talent', None)
        return bool(t and getattr(t, 'name', '') == self.talent_name)


class HologramAIHook(BaseTalentAIHook):
    """全息影像(G2)天赋AI钩子"""
    talent_name = "请一直，注视着我"

    def __init__(self, controller: Any):
        self._ctrl = controller

    def is_development_complete(self, player: Any, state: Any) -> Optional[bool]:
        if not self._is_my_talent(player):
            return None
        talent = player.talent
        exhausted = (getattr(talent, 'used', False) and getattr(talent, 'max_uses', 0) <= 0
                     and not getattr(talent, 'active', False))
        if exhausted:
            real_weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
            has_ranged = any(w.name in ("魔法弹幕", "远程魔法弹幕", "高斯步枪") for w in real_weapons)
            return has_ranged and self._ctrl._count_outer_armor(player) >= 2
        has_two_aoe = self._ctrl._count_distinct_aoe_attrs(player) >= 2
        return has_two_aoe and self._ctrl._count_outer_armor(player) >= 1

    def should_override_candidates(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        """全息影像激活中的AOE扫场模式"""
        if not self._is_my_talent(player) or not getattr(player.talent, 'active', False):
            return None
        my_loc = str(getattr(player, 'location', ''))
        hologram_loc = str(getattr(player.talent, 'location', ''))
        if my_loc != hologram_loc:
            if "move" in available:
                return [f"move {hologram_loc}", "forfeit"]
            return ["forfeit"]
        # 在影像区域内：AOE扫场
        same_loc = self._ctrl._get_same_location_targets(player, state)
        if same_loc:
            emr = next((w for w in player.weapons if w and w.name == "电磁步枪"), None)
            if emr and not getattr(emr, 'is_charged', False) and "special" in available:
                return ["special 蓄力电磁步枪", "forfeit"]
            attack_cmds = self._ctrl._cmd_attack(player, state, available)
            if attack_cmds:
                return [*attack_cmds, "forfeit"]
        return None  # 走常规流程

    def _is_my_talent(self, player: Any) -> bool:
        t = getattr(player, 'talent', None)
        return bool(t and getattr(t, 'name', '') == self.talent_name)


class SaviorAIHook(BaseTalentAIHook):
    """愿负世(G4)天赋AI钩子"""
    talent_name = "愿负世，照拂黎明"

    def __init__(self, controller: Any):
        self._ctrl = controller

    def modify_target_score(self, target: Any, base_score: float, player: Any) -> float:
        t_talent = getattr(target, 'talent', None)
        if not t_talent or getattr(t_talent, 'name', '') != self.talent_name:
            return base_score
        if getattr(t_talent, 'is_savior', False):
            s = base_score + 200
            temp_hp = getattr(t_talent, 'temp_hp', 0)
            s += temp_hp * 20
            duration = getattr(t_talent, 'savior_duration', 0)
            if duration <= 3:
                s += 100
            return s
        divinity = getattr(t_talent, 'divinity', 0)
        if divinity >= 8:
            if self._ctrl._has_firefly_talent(player):
                return base_score + 120
            return base_score - 40
        if divinity <= 4:
            return base_score + 30
        return base_score

    def should_override_candidates(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        """救世主状态：优先攻击"""
        if not self._ctrl._is_in_savior_state(player) or self._ctrl._get_effective_hp(player) <= 0.5:
            return None
        debug_ai_basic(player.name, "救世主状态激活，优先攻击")
        last_attacker = self._ctrl._get_last_attacker(player, state)
        if last_attacker:
            attack_cmds = self._ctrl._cmd_attack(player, state, available, last_attacker)
            if attack_cmds:
                return [*attack_cmds, "forfeit"]
        attack_cmds = self._ctrl._cmd_attack(player, state, available)
        if attack_cmds:
            return [*attack_cmds, "forfeit"]
        return None
