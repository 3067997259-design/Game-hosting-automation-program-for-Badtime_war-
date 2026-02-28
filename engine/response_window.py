"""
响应窗口管理器（Phase 4 正确版）。
R3期间每个胜者行动后，检查所有天赋的check_response_window。
触发时私密询问持有者是否发动。
"""

from cli import display


class ResponseWindowManager:
    def __init__(self, game_state):
        self.state = game_state

    def process_after_action(self, actor, action_type):
        """
        R3中某胜者行动后调用。
        检查所有其他玩家的天赋是否触发响应窗口。
        返回 (是否有人响应bool, 响应者player或None)
        """
        for pid in self.state.player_order:
            if pid == actor.player_id:
                continue
            player = self.state.get_player(pid)
            if not player or not player.is_alive() or not player.talent:
                continue

            talent = player.talent
            if not talent.check_response_window(actor, action_type):
                continue

            # 触发响应窗口：私密询问
            display.show_info(
                f"⏸️ 响应窗口！请将屏幕交给 {player.name}")
            input(f"  [仅 {player.name} 可看] 按回车继续...")

            answer = display.prompt_choice(
                f"🔔 {actor.name} 刚刚执行了「{action_type}」。\n"
                f"   {player.name}，是否发动「{talent.name}」？",
                ["是", "否"]
            )

            if answer == "是":
                result_msg = talent.execute_response(player)
                display.show_result(result_msg)
                return True, player

        return False, None
