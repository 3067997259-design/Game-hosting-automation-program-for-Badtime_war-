"""
天赋基类（Phase 4 正确版）
定义所有钩子接口，子类重写需要的方法。
"""

from engine.prompt_manager import prompt_manager, PromptLevel


class BaseTalent:
    name = "未命名天赋"
    description = "无描述"
    tier = "原初"  # "原初" 或 "神代"

    # 诗意文案（子类可覆盖）
    lore = []

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
        if getattr(player, '_eternity_blocked', False):
            return None
        return None

    def execute_t0(self, player):
        """
        执行T0天赋能力。
        返回 (消息str, 是否消耗本行动回合bool)
        """
        return "未实现", False

    def on_d4_bonus(self, player):
        return 0

    def on_d6_bonus(self, player):
        return 0

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

    # ---- 诗意文案显示 ----

    def show_lore(self, level=None):
        """
        显示天赋的叙事文案。

        Args:
            level: 提示级别，None表示使用配置的talent_lore_level
                  默认为None，让prompt_manager根据配置决定显示级别
        """
        talent_key = self._get_talent_key()
        prompt_manager.show_talent_lore(talent_key, level)

    def _get_talent_key(self):
        """
        获取天赋在prompts.json中的键名。
        类名到prompt键的映射表。
        """
        # 类名到prompt键的映射
        CLASS_TO_PROMPT_KEY = {
            # 原初天赋
            "OneSlash": "t1oneslash",
            "OilTheRoad": "t2oiltheroad",
            "ScissorRush": "t2scissorrush",
            "Star": "t3star",
            "Hexagram": "t4hexagram",
            "Delinquent": "t5delinquent",
            "GoodCitizen": "t6goodcitizen",
            "Resurrection": "t7resurrection",
            # 神代天赋
            "G1MythFire": "g1mythfire",
            "Hologram": "g2eternity",      # 注意：类名是Hologram，但prompt键是g2eternity
            "Mythland": "g3mythland",
            "Savior": "g4savior",
            "Ripple": "g5ripple",
            "CutawayJoke": "g6cutaway",
            "Hoshino": "g7hoshino",
        }

        class_name = self.__class__.__name__
        # 优先使用映射表
        if class_name in CLASS_TO_PROMPT_KEY:
            return CLASS_TO_PROMPT_KEY[class_name]

        # 备用规则：类名转小写
        return class_name.lower()

    def get_full_description(self):
        """
        获取天赋的完整描述（叙事+机制）。
        返回字典：{"lore": [...], "mechanic": "...", "rules": "..."}
        # TODO: Wire up to a 'help <talent>' command in the CLI
        """
        talent_key = self._get_talent_key()
        lore = prompt_manager.get_prompt("talent", f"{talent_key}.lore", default=[])
        return {
            "lore": lore if lore else self.lore,
            "mechanic": self.description,
            "rules": self._get_rules_text()
        }

    def _get_rules_text(self):
        """# TODO: Wire up to a 'help <talent>' command in the CLI
        子类可覆盖以提供规则文本"""
        return ""

    # ---- 天赋激活提示 ----

    def show_activation(self, player_name=None, show_lore=True, **kwargs):
        """
        显示天赋激活提示。
        默认使用talent.{key}.activate模板。

        Args:
            player_name: 玩家名称，默认为天赋持有者
            show_lore: 是否显示天赋叙事文案（lore）
            **kwargs: 传递给激活文本的变量
        """
        # 显示天赋叙事文案（如果配置允许）
        if show_lore:
            self.show_lore(level=None)

        # 显示激活文本
        talent_key = self._get_talent_key()
        if player_name is None:
            player_name = self.state.get_player(self.player_id).name
        return prompt_manager.show("talent", f"{talent_key}.activate",
                                  player_name=player_name, **kwargs,
                                  level=PromptLevel.IMPORTANT)

    # ---- 描述 ----

    def describe(self):
        return f"【{self.name}】{self.description}"

    def describe_status(self):
        """返回天赋当前状态（充能/次数等）"""
        return ""
