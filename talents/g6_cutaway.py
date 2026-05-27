"""神代天赋6：要有笑声！（削弱版 — 仅层次1行动）"""

from talents.base_talent import BaseTalent
from engine.prompt_manager import prompt_manager


class CutawayJoke(BaseTalent):
    name = "要有笑声！"
    description = "被动积攒笑点，满后触发「插入式笑话」：借用其他玩家的合法行动"
    tier = "神代"

    lore = [
        "「满足条件后自动触发」",
        "「插入式笑话——在这个行动中，你可以进行任意一个当前场上存在的合法行动」",
    ]

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)

        # 笑点系统
        self.laugh_points = 0           # 当前笑点
        self.laugh_threshold = 6       # 触发阈值（可被献诗减少）
        self.forfeit_reduction = 0      # 献诗累计减少量（供 poem_mixin._poem_joy 使用）

        # 插入式笑话充能
        self.cutaway_charges = 0        # 当前可用的插入式笑话次数

        # D4/D6 强制标记
        self._d4_force = False
        self._d6_force = False

    # ---- 轮次钩子 ----

    def on_round_start(self, round_num):
        """R0：如果有待触发的插入式笑话，保持 D4/D6 强制标记"""
        # D4/D6 标记在 on_round_end 中设置，在 R1 阶段通过 on_d4_bonus/on_d6_bonus 生效
        pass

    def on_round_end(self, round_num):
        """R4：检查本轮是否未行动或 forfeit，积累笑点"""
        player = self.state.get_player(self.player_id)
        if not player or not player.is_alive():
            return

        # 判断是否应该获得笑点：
        # 1. 本轮未获得行动回合（acted_this_round == False）
        # 2. 或者本轮行动类型为 forfeit
        should_gain = False
        if not player.acted_this_round:
            should_gain = True
        elif getattr(player, 'last_action_type', None) == "forfeit":
            should_gain = True

        if should_gain:
            self.laugh_points += 1

        # 检查是否达到阈值
        effective_threshold = max(1, self.laugh_threshold - self.forfeit_reduction)
        if self.laugh_points >= effective_threshold:
            self.cutaway_charges += 1
            self.laugh_points = 0  # 重置笑点（重新积累）
            self._d4_force = True
            self._d6_force = True
            self.state.log_event("cutaway_charge", player=self.player_id,
                                 charges=self.cutaway_charges)

    # ---- D4/D6 钩子 ----

    def on_d4_bonus(self, player):
        """D4 加成：强制时 +3（保证 min(1+3,4)=4）"""
        if player.player_id == self.player_id and self._d4_force:
            return 3
        return 0

    def on_d6_bonus(self, player):
        """D6 加成：强制时 +5（保证 min roll 1+5=6）"""
        if player.player_id == self.player_id and self._d6_force:
            return 5
        return 0

    # ---- 行动回合钩子 ----

    def on_turn_start(self, player):
        """T0：仅做提示，不设置标记（标记由 _phase_t1 在确认进入 T1 后设置）"""
        if player.player_id != self.player_id:
            return None
        if self.cutaway_charges > 0 and not getattr(player, '_mythland_talent_suppressed', False):
            from cli import display
            display.show_info(f"🎭 {player.name} 的「插入式笑话」即将发动！")
        return None  # 不消耗回合，继续进入 T1

    def on_turn_end(self, player, action_type):
        """T2：插入式笑话回合结束，消耗充能"""
        if getattr(player, '_in_cutaway_joke', False):
            self.cutaway_charges -= 1
            self.state.log_event("cutaway_joke", player=self.player_id,
                                 charges_remaining=self.cutaway_charges)
            player._in_cutaway_joke = False

    # ---- 状态描述 ----

    def describe_status(self):
        effective_threshold = max(1, self.laugh_threshold - self.forfeit_reduction)
        parts = [f"笑点: {self.laugh_points}/{effective_threshold}"]
        if self.cutaway_charges > 0:
            parts.append(f"插入式笑话充能: {self.cutaway_charges}")
        return " | ".join(parts)