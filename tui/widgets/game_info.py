"""游戏信息区 —— 显示轮次/阶段/当前行动者/自己的状态"""

from textual.widgets import Static
from textual.reactive import reactive


class GameInfoWidget(Static):
    """顶部游戏信息区。"""

    round_num = reactive(0)
    phase = reactive("")
    current_actor = reactive("")
    my_status = reactive("")

    def render(self) -> str:
        lines = []
        lines.append(f"  轮次: {self.round_num}  |  阶段: {self.phase}")
        if self.current_actor:
            lines.append(f"  当前行动者: {self.current_actor}")
        if self.my_status:
            lines.append(f"  {self.my_status}")
        return "\n".join(lines) if lines else "  等待游戏开始..."

    def update_from_event(self, event: dict):
        func = event.get("event", "")
        args = event.get("args", [])
        if func == "show_round_header" and args:
            self.round_num = args[0] if isinstance(args[0], int) else self.round_num
        elif func == "show_phase" and args:
            self.phase = str(args[0])
        elif func == "show_action_turn_header" and args:
            self.current_actor = str(args[0])
        elif func == "show_player_status" and args:
            if isinstance(args[0], dict) and args[0].get("_type") == "player":
                self.my_status = args[0].get("status", "")
