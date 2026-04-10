from typing import Any
from engine.prompt_manager import prompt_manager
class FusionMixin:
    """装备融合系统 Mixin"""
    state: Any
    fusion_shield_done: bool
    fusion_weapon_done: bool
    iron_horus_hp: float
    iron_horus_max_hp: float
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
        # 铁之荷鲁斯：无属性特殊护盾，初始护甲值2，手提箱形态（不提供保护直到架盾/持盾）
        self.iron_horus_hp = 2
        self.iron_horus_max_hp = 2
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
        if (self.fusion_shield_done and self.fusion_weapon_done
                and not self.tactical_unlocked and not self.is_terror):
            self.tactical_unlocked = True
            from cli import display
            msg = prompt_manager.get_prompt("talent", "g7hoshino.tactical_unlocked")
            display.show_info(msg)

            # 解锁时自动配发：2次自由选择
            from talents.g7.items import TACTICAL_ITEMS, MEDICINES
            player = self.state.get_player(self.player_id)
            if player:
                for pick_round in range(2):
                    options = []
                    # 战术道具（最多2个）
                    if len(self.tactical_items) < 2:
                        for item_name in TACTICAL_ITEMS:
                            options.append(f"道具：{item_name}")
                    # 药物（肾上腺素全局1次，其他可重复）
                    for med_name in MEDICINES:
                        if med_name == "肾上腺素" and "肾上腺素" in self.medicines:
                            continue  # 已有肾上腺素就不再提供
                        options.append(f"药物：{med_name}")
                    # 子弹（弹匣未满时）
                    if len(self.ammo) + 2 <= self.max_ammo:
                        for attr in ["普通", "科技", "魔法"]:
                            options.append(f"子弹：2发{attr}属性")

                    if not options:
                        break

                    choice = player.controller.choose(
                        f"战术解锁配发（第{pick_round+1}/2次选择）：",
                        options,
                        context={"phase": "T0", "situation": "hoshino_tactical_equip"}
                    )

                    if choice.startswith("道具："):
                        item_name = choice[3:]
                        self.tactical_items.append(item_name)
                    elif choice.startswith("药物："):
                        med_name = choice[3:]
                        self.medicines.append(med_name)
                    elif choice.startswith("子弹："):
                        # 解析属性："子弹：2发普通属性" → "普通"
                        for attr in ["普通", "科技", "魔法"]:
                            if attr in choice:
                                self.ammo.extend([{"attribute": attr}, {"attribute": attr}])
                                break

                    display.show_info(prompt_manager.get_prompt("talent", "g7hoshino.tactical_auto_equip",
                        default="  ✓ 获得：{choice}").format(choice=choice))

    def _repair_horus(self, player, sacrifice_name):
        """special 修复 <护甲名>：消耗一件盾牌/AT力场修复铁之荷鲁斯"""
        if not self.fusion_shield_done:
            return prompt_manager.get_prompt("talent", "g7hoshino.repair_no_horus")
        if self.iron_horus_hp >= self.iron_horus_max_hp:
            return prompt_manager.get_prompt("talent", "g7hoshino.repair_full")
        # 空参数时自动检测可用材料
        if not sacrifice_name:
            valid = [a.name for a in player.armor.get_all_active()
                    if a.name in ("盾牌", "AT力场")]
            if not valid:
                return prompt_manager.get_prompt("talent", "g7hoshino.repair_no_material",
                                                sacrifice_name="盾牌/AT力场")
            if len(valid) == 1:
                sacrifice_name = valid[0]
            else:
                sacrifice_name = player.controller.choose(
                    "选择消耗哪件护甲修复铁之荷鲁斯：", valid,
                    context={"phase": "T1", "situation": "hoshino_repair_material"})
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