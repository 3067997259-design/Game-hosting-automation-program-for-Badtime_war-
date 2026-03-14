"""  
警察数据模型：独立警察单位模型（ver1.9重构）。  
执法逻辑在 engine/police_system.py 中。  
"""  
from __future__ import annotations   
from models.equipment import make_armor, ArmorLayer  
from typing import Optional  
from models.equipment import make_armor, ArmorPiece, ArmorLayer 
  
class PoliceUnit:  
    """独立警察个体单位（ver1.9新模型）"""  
  
    def __init__(self, unit_id):  
        self.unit_id = unit_id  
        self.hp = 1.0  
        self.weapon_name = "警棍"   # 可被队长更换  
  
        # 护甲槽位：最多1件外层 + 1件内层，新装备替换旧的同层护甲  
        self.outer_armor_name = "盾牌"  
        self.outer_armor: Optional[ArmorPiece] = make_armor("盾牌") 
        self.inner_armor_name: str | None = None  
        self.inner_armor: Optional[ArmorPiece] = None                  # 内层，初始无  
  
        # 四种行动阻碍状态（debuff）- 根据README第10.5节及补充说明  
        # 1. 眩晕（Stunned）：无法执行任务，不提供警察保护  
        # 2. 震荡（Shocked）：与眩晕等价，无法执行任务，不提供警察保护  
        # 3. 石化（Petrified）：无法执行任务，不提供警察保护，唤醒时额外扣0.5生命值  
        # 4. 沉沦（Submerged）：无法执行命令，无法移动，不提供警察保护，在全息影像范围内时无法被玩家唤醒  
        self.is_stunned = False     # 眩晕  
        self.is_shocked = False     # 震荡（与眩晕等价）  
        self.is_petrified = False   # 石化  
        self.is_submerged = False   # 沉沦  
  
        # 位置信息：None表示不在地图上（集结前/威信归零后）  
        self.location: str | None = None  
  
        # 攻击记录（用于某些天赋效果）  
        self.last_attacker_id: str | None = None
        self.last_innocent_attacked: str | None = None  # 最后被警察攻击的无辜者ID（用于威信归零）  
  
    def is_alive(self):  
        """是否存活"""  
        return self.hp > 0  
  
    def is_active(self):  
        """是否可行动（存活且无行动阻碍状态）- 对应README 10.5节"""  
        return self.is_alive() and not self.is_disabled()  
  
    def is_disabled(self):  
        """是否因debuff无法执行任务（眩晕/震荡/石化/沉沦）- 对应README 10.3和10.5节  
        四种debuff中任一都会使警察无法执行任务且不提供保护"""  
        return self.is_stunned or self.is_shocked or self.is_petrified or self.is_submerged  
  
    def is_on_map(self):  
        """是否在地图上（location不为None）"""  
        return self.location is not None  
  
    def can_be_wakened(self, is_in_hologram_range=False):  
        """检查该警察单位能否被唤醒（用于沉沦状态的特殊限制）  
  
        Args:  
            is_in_hologram_range: 是否在全息影像范围内（由外部天赋系统提供）  
        Returns:  
            bool: 能否被唤醒  
        """  
        if not self.is_alive():  
            return False  # 已死亡无法唤醒  
        if not self.is_disabled():  
            return False  # 没有debuff无需唤醒  
        if self.is_submerged and is_in_hologram_range:  
            return False  # 沉沦状态且在全息影像范围内，无法被唤醒  
        return True  
  
    def wake_up(self):  
        """执行唤醒操作 - 对应README 10.7节及补充说明  
        1. 如果是石化状态，先扣0.5生命值  
        2. 清除所有debuff状态  
        3. 如果仍然存活，恢复HP至1.0  
  
        Returns:  
            dict: 唤醒结果信息  
        """  
        result = {  
            "unit_id": self.unit_id,  
            "was_petrified": False,  
            "petrified_damage": 0.0,  
            "old_hp": self.hp,  
            "new_hp": 1.0,  
            "killed_by_petrify": False,  
        }  
  
        # 1. 石化状态额外扣血  
        if self.is_petrified and self.is_alive():  
            result["was_petrified"] = True  
            result["petrified_damage"] = 0.5  
            result["old_hp"] = self.hp  
            self.hp = max(0, self.hp - 0.5)  
  
        # 2. 清除所有debuff  
        self.clear_all_debuffs()  
  
        # 3. 如果仍然存活，恢复HP至1.0；否则标记死亡  
        if self.is_alive():  
            self.hp = 1.0  
            result["new_hp"] = 1.0  
        else:  
            result["new_hp"] = 0  
            result["killed_by_petrify"] = True  
  
        return result  
  
    def clear_all_debuffs(self):  
        """清除所有行动阻碍状态"""  
        self.is_stunned = False  
        self.is_shocked = False  
        self.is_petrified = False  
        self.is_submerged = False  
  

  
    def equip_armor(self, armor_piece: ArmorPiece):  
        """装备护甲，根据层自动替换对应槽位"""  
        if armor_piece.layer == ArmorLayer.OUTER:  
            self.outer_armor_name = armor_piece.name  
            self.outer_armor = armor_piece  
        elif armor_piece.layer == ArmorLayer.INNER:  
            self.inner_armor_name = armor_piece.name  
            self.inner_armor = armor_piece  
  
    def get_armor_pieces(self) -> list[ArmorPiece]:  
        """返回当前所有护甲对象列表（用于伤害结算）"""  
        pieces: list[ArmorPiece] = []  
        if self.outer_armor is not None and not self.outer_armor.is_broken:  
            pieces.append(self.outer_armor)  
        if self.inner_armor is not None and not self.inner_armor.is_broken:  
            pieces.append(self.inner_armor)  
        return pieces   
    
    
    
    def take_damage(self, damage, attacker_id=None):  
        """警察受到伤害（绕过护甲的直接伤害，如天赋效果）  
  
        Returns:  
            dict: 伤害结果信息  
        """  
        old_hp = self.hp  
        self.hp = max(0, self.hp - damage)  
  
        # 记录攻击者（用于天赋效果等）  
        if attacker_id:  
            self.last_attacker_id = attacker_id  
  
        return {  
            "damage": damage,  
            "old_hp": old_hp,  
            "new_hp": self.hp,  
            "killed": self.hp <= 0,  
        }  
  
    def reset_to_initial(self):  
        """重置为初始状态（威信归零/队长下台时调用）"""  
        self.hp = 1.0  
        self.weapon_name = "警棍"  
        self.outer_armor_name = "盾牌"  
        self.outer_armor = make_armor("盾牌")  
        self.inner_armor_name = None  
        self.inner_armor = None  
        self.location = None  # 不在地图上  
        self.clear_all_debuffs()  
        self.last_attacker_id = None  
  
    def __repr__(self):  
        status_parts = []  
        if not self.is_alive():  
            status_parts.append("💀已死亡")  
        else:  
            status_parts.append(f"HP:{self.hp}")  
        if self.is_stunned:  
            status_parts.append("眩晕")  
        if self.is_shocked:  
            status_parts.append("震荡")  
        if self.is_petrified:  
            status_parts.append("石化")  
        if self.is_submerged:  
            status_parts.append("沉沦")  
  
        armor_str = ""  
        armor_parts = []  
        if self.outer_armor_name:  
            armor_parts.append(f"外:{self.outer_armor_name}")  
        if self.inner_armor_name:  
            armor_parts.append(f"内:{self.inner_armor_name}")  
        if armor_parts:  
            armor_str = f" [{', '.join(armor_parts)}]"  
  
        loc_str = self.location if self.location else "不在地图上"  
        return (f"🚔 {self.unit_id} ({', '.join(status_parts)}) "  
                f"武器:{self.weapon_name}{armor_str} 位置:{loc_str}")  
  
  
class PoliceData:  
    """警察系统数据层（ver1.9重构）"""  
  
    def __init__(self):  
        # 警察单位列表：初始1个单位，队长上任后变为3个  
        self.units = [PoliceUnit("police1")]  
  
        # 队长信息  
        self.captain_id = None  
        self.authority = 0              # 威信值  
  
        # 举报与执法状态  
        self.report_phase = "idle"      # idle / reported / assembled / dispatched  
        self.reporter_id = None  
        self.reported_target_id = None  
        self.dispatch_countdown = 0  
  
        # 永久禁用标记 - 对应README 10.10节  
        self.permanently_disabled = False  
  
        # 犯罪记录  
        self.crime_records = {}         # player_id → set of 犯罪类型字符串  
  
    def get_unit(self, unit_id):  
        """根据ID获取警察单位"""  
        for unit in self.units:  
            if unit.unit_id == unit_id:  
                return unit  
        return None  
  
    def alive_units(self):  
        """获取所有存活的警察单位"""  
        return [unit for unit in self.units if unit.is_alive()]  
  
    def active_units(self):  
        """获取所有可行动的警察单位（存活且无debuff）- 对应README 10.5节"""  
        return [unit for unit in self.units if unit.is_active()]  
  
    def units_at(self, location, alive_only=True):  
        """获取某地点的警察单位  
  
        Args:  
            location: 地点名称  
            alive_only: 是否只返回存活单位（默认True）  
        """  
        if alive_only:  
            return [u for u in self.units  
                    if u.location == location and u.is_alive()]  
        return [u for u in self.units if u.location == location]  
  
    def active_units_at(self, location):  
        """获取某地点可行动的警察单位（存活且无debuff）"""  
        return [u for u in self.units  
                if u.location == location and u.is_active()]  
  
    def any_alive(self):  
        """是否有任何存活的警察单位"""  
        return any(unit.is_alive() for unit in self.units)  
  
    def check_all_dead(self):  
        """检查是否所有警察单位都已死亡，如果是则设置永久禁用状态 - 对应README 10.10节"""  
        if not self.any_alive():  
            self.permanently_disabled = True  
            return True  
        return False  
  
    def has_captain(self):  
        """是否有队长"""  
        return self.captain_id is not None  
  
    def add_crime(self, player_id, crime_type):  
        """记录犯罪"""  
        if player_id not in self.crime_records:  
            self.crime_records[player_id] = set()  
        self.crime_records[player_id].add(crime_type)  
  
    def is_criminal(self, player_id):  
        """检查是否有犯罪记录"""  
        return bool(self.crime_records.get(player_id))  
  
    def get_crimes(self, player_id):  
        """获取犯罪记录"""  
        return self.crime_records.get(player_id, set())  
  
    def clear_all_crimes_except(self, except_id):  
        """清空除指定玩家外所有人的犯罪记录"""  
        for pid in list(self.crime_records.keys()):  
            if pid != except_id:  
                self.crime_records[pid] = set()  
  
    def describe(self):  
        """返回警察系统状态描述"""  
        lines = []  
  
        # 永久禁用状态  
        if self.permanently_disabled:  
            lines.append("  ⚠️ 警察系统永久禁用")  
  
        # 警察单位统计  
        alive_count = len(self.alive_units())  
        active_count = len(self.active_units())  
        total_count = len(self.units)  
        lines.append(f"  警察单位：{alive_count}/{total_count}存活，{active_count}可行动")  
  
        # 列出所有存活警察单位  
        for unit in self.units:  
            if unit.is_alive():  
                lines.append(f"    {unit}")  
  
        # 队长信息  
        if self.captain_id:  
            lines.append(f"  队长：{self.captain_id}（威信：{self.authority}）")  
        else:  
            lines.append("  队长：无")  
  
        # 举报状态  
        lines.append(f"  举报状态：{self.report_phase}")  
        if self.reported_target_id:  
            lines.append(f"  执法目标：{self.reported_target_id}")  
  
        return "\n".join(lines)