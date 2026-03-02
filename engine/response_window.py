"""
响应窗口管理器（Phase 4 正确版 + Controller 接入）。
R3期间每个胜者行动后，检查所有天赋的check_response_window。
触发时通过 controller 询问持有者是否发动。
Human：保留交屏幕+私密询问体验。
AI：跳过交屏幕，直接由策略决策。
"""

from cli import display
from controllers.human import HumanController


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

            # ══ CONTROLLER 改动：交屏幕 + 私密询问 走 controller ══

            # 仅 Human 需要交屏幕暂停
            if isinstance(player.controller, HumanController):
                display.show_info(
                    f"⏸️ 响应窗口！请将屏幕交给 {player.name}")
                input(f"  [仅 {player.name} 可看] 按回车继续...")

            # 通过 controller 询问是否发动
            answer = player.controller.confirm(
                f"🔔 {actor.name} 刚刚执行了「{action_type}」。\n"
                f"   {player.name}，是否发动「{talent.name}」？",
                context={
                    "phase": "response_window",
                    "actor_id": actor.player_id,
                    "actor_name": actor.name,
                    "action_type": action_type,
                    "talent_name": talent.name,
                    "responder_id": player.player_id,
                    "responder_name": player.name,
                }
            )

            # ══ CONTROLLER 改动结束 ══

            if answer:
                result_msg = talent.execute_response(player)
                display.show_result(result_msg)
                return True, player

        return False, None