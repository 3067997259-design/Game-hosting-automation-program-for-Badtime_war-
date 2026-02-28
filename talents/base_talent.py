"""
天赋基类（Phase 4 正确版）
定义所有钩子接口，子类重写需要的方法。
"""


class BaseTalent:
    name = "未命名天赋"
    description = "无描述"
    tier = "原初"  # "原初" 或 "神代"

    def __init__(self, player_id, game_state):
        self.player_id = player_id
        self.state = game_state

    def on_register(self):
        """天赋注册时调用（开局）"""
        pass

    # ---- 轮次钩子 ----

    def on_round_start(self, round_num):
        """R0：轮次开始结算"""
        pass

    def on_round_end(self, round_num):
        """R4：轮次结束结算"""
        pass

    # ---- 行动回合钩子 ----

    def on_turn_start(self, player):
        """
        T0：行动回合开始。
        返回 dict 可干预回合：
          {"consume_turn": True, "message": "..."} → 消耗本回合，不进T1
          None → 正常继续
        """
        return None

    def on_turn_end(self, player, action_type):
        """T2：行动回合结束"""
        pass

    # ---- T0可用的主动天赋 ----

    def get_t0_option(self, player):
        """
        返回T0阶段可用的天赋选项，None表示无。
        返回格式：{"name": 显示名, "description": 描述} 或 None
        """
        return None

    def execute_t0(self, player):
        """
        执行T0天赋能力。
        返回 (消息str, 是否消耗本行动回合bool)
        """
        return "未实现", False

    # ---- 战斗钩子 ----

    def modify_outgoing_damage(self, attacker, target, weapon, base_damage):
        """
        修改输出伤害。
        返回 dict: {"damage": 新伤害, "ignore_counter": bool, "ignore_last_inner_absorb": bool}
        或 None 不修改。
        """
        return None

    def on_death_check(self, player, damage_source):
        """
        死亡判定钩子。按优先级：
        1. 免死效果（prevent_death）
        2. 复活效果（resurrect）→ 死者苏生在这里
        返回 {"prevent_death": True, "new_hp": X} 或 None
        """
        return None

    # ---- 犯罪钩子 ----

    def on_crime_check(self, player_id, crime_type):
        """
        犯罪检测钩子。
        返回 {"immune": True} 免除犯罪。
        返回 {"extra_turn": True} 犯罪后获得额外行动。
        """
        return None

    # ---- R3 响应窗口 ----

    def check_response_window(self, actor, action_type):
        """
        R3期间，每个胜者行动后检查。
        返回 True 表示要触发响应窗口。
        """
        return False

    def execute_response(self, player):
        """
        执行响应能力。
        返回消息字符串。
        """
        return ""

    # ---- 描述 ----

    def describe(self):
        return f"【{self.name}】{self.description}"

    def describe_status(self):
        """返回天赋当前状态（充能/次数等）"""
        return ""
