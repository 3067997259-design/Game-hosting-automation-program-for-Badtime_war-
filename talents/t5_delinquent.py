"""
天赋5：不良少年（原初）
常驻效果：
  - 开局获得「热那亚之刃」（视为小刀，攻击不构成犯罪）
  - 每触发一种新犯罪类型 → 获得1额外行动回合
  - 每种犯罪类型只触发1次
"""

from talents.base_talent import BaseTalent
from models.equipment import Weapon, WeaponRange
from utils.attribute import Attribute
from engine.prompt_manager import prompt_manager


class Delinquent(BaseTalent):
    name = "不良少年"
    description = "开局获得热那亚之刃(攻击不犯罪)。每种新犯罪+1额外行动。"
    tier = "原初"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.triggered_crime_types = set()  # 已触发过额外行动的犯罪类型

    def on_register(self):
        """开局给热那亚之刃"""
        player = self.state.get_player(self.player_id)
        if player:
            blade = Weapon(
                "热那亚之刃", Attribute.ORDINARY, 1.0, WeaponRange.MELEE,
                special_tags=["genoa_blade", "no_crime_on_attack"]
            )
            player.add_weapon(blade)

    def on_crime_check(self, player_id, crime_type):
        """
        犯罪检测钩子：
        1. 用热那亚之刃攻击 → 不构成犯罪
        2. 其他犯罪 → 正常记录，但如果是新类型则额外给行动回合
        """
        if player_id != self.player_id:
            return None

        # 检查是否是热那亚之刃的攻击（通过事件日志判断）
        # 如果最近的攻击用的是热那亚之刃，则免罪
        if crime_type == "伤害玩家":
            last_attack = self._get_last_attack_weapon()
            if last_attack and "genoa_blade" in last_attack.special_tags:
                return {"immune": True}

        # 不是热那亚之刃攻击的犯罪 → 正常记录
        # 但检查是否是新犯罪类型
        if crime_type not in self.triggered_crime_types:
            self.triggered_crime_types.add(crime_type)
            return {"extra_turn": True, "message":
                    prompt_manager.get_prompt(
                        "talent", "t5delinquent.crime_trigger",
                        default="🔥 不良少年：首次触发犯罪「{crime_type}」→ 获得额外行动回合！"
                    ).format(crime_type=crime_type)}

        return None

    def _get_last_attack_weapon(self):
        """从事件日志获取最近一次攻击使用的武器"""
        player = self.state.get_player(self.player_id)
        if not player:
            return None
        for event in reversed(self.state.event_log):
            if (event.get("type") == "attack"
                    and event.get("attacker") == self.player_id
                    and event.get("round") == self.state.current_round):
                weapon_name = event.get("weapon")
                if weapon_name:
                    return player.get_weapon(weapon_name)
        return None

    def describe_status(self):
        triggered = ", ".join(self.triggered_crime_types) if self.triggered_crime_types else "无"
        return f"已触发犯罪类型：{triggered}"