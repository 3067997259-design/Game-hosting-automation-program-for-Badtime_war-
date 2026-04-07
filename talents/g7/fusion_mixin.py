from typing import Any
from engine import prompt_manager
class FusionMixin:
    """装备融合系统 Mixin"""
    state: Any
    fusion_shield_done: bool
    fusion_weapon_done: bool
    iron_horus_hp: int
    iron_horus_max_hp: int
    eye_of_horus: Any
    ammo: list
    tactical_unlocked: bool
    is_terror: bool

    def _check_fusion(self, player):
        """检查是否满足融合条件，执行融合"""
        if not self.fusion_shield_done:
            has_shield = any(a.name == "盾牌" and not a.is_broken
                           for a in player.armor.get_all_active())
            has_at = any(a.name == "AT力场" and not a.is_broken
                        for a in player.armor.get_all_active())
            if has_shield and has_at:
                self._fuse_iron_horus(player)

        if not self.fusion_weapon_done:
            has_emr = player.has_weapon("电磁步枪")
            has_gauss = player.has_weapon("高斯步枪")
            if has_emr and has_gauss:
                self._fuse_eye_of_horus(player)

        self._check_tactical_unlock()

    def _fuse_iron_horus(self, player):
        """盾牌 + AT力场 → 铁之荷鲁斯"""
        # 移除盾牌和AT力场
        for armor in player.armor.get_all_active():
            if armor.name in ("盾牌", "AT力场"):
                player.armor.remove_piece(armor)
        # 铁之荷鲁斯：无属性特殊护盾，初始护甲值3，手提箱形态（不提供保护直到架盾/持盾）
        self.iron_horus_hp = 3
        self.iron_horus_max_hp = 3
        self.fusion_shield_done = True
        from cli import display
        msg = prompt_manager.get_prompt("talent", "g7hoshino.fuse_iron_horus",
                                        iron_horus_hp=self.iron_horus_hp)
        display.show_info(msg)

    def _fuse_eye_of_horus(self, player):
        """电磁步枪 + 高斯步枪 → 荷鲁斯之眼"""
        player.weapons = [w for w in player.weapons
                         if w and w.name not in ("电磁步枪", "高斯步枪")]
        self.eye_of_horus = True  # 标记持有
        self.ammo = []  # 初始无子弹
        self.fusion_weapon_done = True
        from cli import display
        msg = prompt_manager.get_prompt("talent", "g7hoshino.fuse_eye_of_horus")
        display.show_info(msg)

    def _check_tactical_unlock(self):
        """同时持有两件融合装备 → 解锁战术指令"""
        if (self.fusion_shield_done and self.fusion_weapon_done
                and not self.tactical_unlocked and not self.is_terror):
            self.tactical_unlocked = True
            from cli import display
            msg = prompt_manager.get_prompt("talent", "g7hoshino.tactical_unlocked")
            display.show_info(msg)

    def _repair_horus(self, player, sacrifice_name):
        """special 修复 <护甲名>：消耗一件盾牌/AT力场修复铁之荷鲁斯"""
        if not self.fusion_shield_done:
            return prompt_manager.get_prompt("talent", "g7hoshino.repair_no_horus")
        if self.iron_horus_hp >= self.iron_horus_max_hp:
            return prompt_manager.get_prompt("talent", "g7hoshino.repair_full")
        # 找到要消耗的护甲
        target_armor = None
        for armor in player.armor.get_all_active():
            if armor.name == sacrifice_name:
                target_armor = armor
                break
        if not target_armor:
            return prompt_manager.get_prompt("talent", "g7hoshino.repair_no_material",
                                            sacrifice_name=sacrifice_name)
        if sacrifice_name not in ("盾牌", "AT力场"):
            return prompt_manager.get_prompt("talent", "g7hoshino.repair_wrong_material")
        player.armor.remove_piece(target_armor)
        self.iron_horus_hp = min(self.iron_horus_hp + 1, self.iron_horus_max_hp)
        return prompt_manager.get_prompt("talent", "g7hoshino.repair_ok",
                                        sacrifice_name=sacrifice_name,
                                        iron_horus_hp=self.iron_horus_hp,
                                        iron_horus_max_hp=self.iron_horus_max_hp)