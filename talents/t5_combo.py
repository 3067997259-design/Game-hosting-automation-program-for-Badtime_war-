"""
天赋5：Combo（原初）
常驻被动效果：
  - 连续3个全局轮次获得行动权后，下一轮 D4=4, D6=6
  - 奖励回合中获得 +1 HP 和 +1 攻击力（不叠加）
"""

from talents.base_talent import BaseTalent
from engine.prompt_manager import prompt_manager
from cli import display


class Combo(BaseTalent):
    name = "combo"
    description = "连续行动3轮后，下一轮必定行动且获得+1HP/+1攻击力"
    tier = "原初"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)

        # 连续行动计数
        self.consecutive_actions = 0    # 当前连续行动轮数
        self.trigger_threshold = 3      # 触发所需连续轮数（献诗可临时改为2）

        # D4/D6 强制标记
        self._d4_force = False
        self._d6_force = False

        # 奖励回合标记
        self._bonus_round_active = False    # 当前是否处于奖励回合
        self._bonus_hp_applied = False      # 是否已应用 +1 HP

    # ---- D4/D6 钩子 ----

    def on_d4_bonus(self, player):
        """D4 加成：强制时 +3（保证 min(1+3,4)=4）"""
        if player.player_id == self.player_id and self._d4_force:
            return 3
        return 0

    def on_d6_bonus(self, player):
        """D6 加成：强制时 +5（保证 min(1+5,6)=6）"""
        if player.player_id == self.player_id and self._d6_force:
            return 5
        return 0

    # ---- 轮次钩子 ----

    def on_round_start(self, round_num):
        """R0：如果是奖励回合，应用 +1 HP"""
        if not self._d4_force:
            return
        player = self.state.get_player(self.player_id)
        if not player or not player.is_alive():
            return
        if getattr(player, '_mythland_talent_suppressed', False):
            return

        self._bonus_round_active = True

        # 应用 +1 HP（不叠加）
        if not self._bonus_hp_applied:
            player.max_hp += 1.0
            player.hp += 1.0
            self._bonus_hp_applied = True
            display.show_info(
                f"🔥 Combo！{player.name} 本轮获得 +1 HP 和 +1 攻击力！")

    def on_round_end(self, round_num):
        """R4：追踪连续行动，检查是否触发下一轮奖励"""
        player = self.state.get_player(self.player_id)
        if not player or not player.is_alive():
            return

        # 移除奖励回合的临时 HP
        if self._bonus_hp_applied:
            player.max_hp = max(1.0, player.max_hp - 1.0)
            if player.hp > player.max_hp:
                player.hp = player.max_hp
            self._bonus_hp_applied = False
            self._bonus_round_active = False

        # 清除本轮的 D4/D6 强制（已使用）
        self._d4_force = False
        self._d6_force = False

        # 幻想乡压制时不追踪
        if getattr(player, '_mythland_talent_suppressed', False):
            self.consecutive_actions = 0
            return

        # 追踪连续行动
        if player.acted_this_round:
            self.consecutive_actions += 1
        else:
            self.consecutive_actions = 0

        # 检查是否达到阈值
        if self.consecutive_actions >= self.trigger_threshold:
            self.consecutive_actions = 0
            self._d4_force = True
            self._d6_force = True
            display.show_info(
                f"🔥 {player.name} 连续行动{self.trigger_threshold}轮！"
                f"下一轮 D4 必为 4，D6 必为 6！")

            # 如果献诗临时降低了阈值，恢复为默认值
            if self.trigger_threshold != 3:
                self.trigger_threshold = 3

    # ---- 战斗钩子 ----

    def modify_outgoing_damage(self, attacker, target, weapon, base_damage):
        """奖励回合中 +1 攻击力"""
        if (attacker.player_id == self.player_id
                and self._bonus_round_active
                and not getattr(attacker, '_mythland_talent_suppressed', False)):
            return {"bonus_damage": 1}
        return None

    # ---- 献诗支持：立刻进入奖励状态 ----

    def activate_poem_bonus(self, player):
        """
        献诗调用：立刻给予奖励状态（+1 HP, +1 ATK）。
        在献诗的立刻行动之前调用。
        """
        if not self._bonus_hp_applied:
            player.max_hp += 1.0
            player.hp += 1.0
            self._bonus_hp_applied = True
            self._bonus_round_active = True

    def deactivate_poem_bonus(self, player):
        """
        献诗调用：移除奖励状态。
        在献诗的立刻行动之后调用。
        """
        if self._bonus_hp_applied:
            player.max_hp = max(1.0, player.max_hp - 1.0)
            if player.hp > player.max_hp:
                player.hp = player.max_hp
            self._bonus_hp_applied = False
            self._bonus_round_active = False

    # ---- 状态描述 ----

    def describe_status(self):
        parts = [f"连续行动：{self.consecutive_actions}/{self.trigger_threshold}"]
        if self._d4_force:
            parts.append("⚡ 下一轮必定行动+奖励")
        if self._bonus_round_active:
            parts.append("🔥 奖励回合中（+1HP/+1ATK）")
        return " | ".join(parts)