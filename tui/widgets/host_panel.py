"""房主管理面板 —— 仅房主端显示"""

from textual.widgets import Static, Button, RichLog
from textual.containers import Vertical, Horizontal
from textual.message import Message
from typing import Any


class SlotConfigRequest(Message):
    """请求配置某个槽位。"""
    def __init__(self, slot_id: int, action: str, **kwargs):
        super().__init__()
        self.slot_id = slot_id
        self.action = action
        self.extra = kwargs


class HostPanel(Static):
    """
    房主管理面板：游戏开始前以「全屏模式」展示房间槽位列表 + 管理命令帮助 + 操作按钮。

    游戏开始后由 app.py 通过 CSS class 切换隐藏（`lobby-mode` class 移除即隐藏）。
    """

    DEFAULT_CSS = """
    HostPanel {
        height: 100%;
        border: solid green;
        padding: 0 1;
    }
    #host-title {
        height: 1;
        content-align: center middle;
    }
    #host-slots {
        height: 1fr;
        min-height: 6;
        border: solid grey;
    }
    #host-help {
        height: auto;
    }
    #host-buttons {
        height: 3;
        align: center middle;
    }
    #host-buttons Button {
        margin-right: 2;
        min-width: 12;
        max-width: 16;
    }
    """

    def __init__(self, lobby: Any = None, **kwargs):
        super().__init__(**kwargs)
        self.lobby = lobby

    def compose(self):
        # AI 性格列表（避免在 import 阶段失败）
        try:
            from engine.game_setup import AI_PERSONALITIES
            personalities = " / ".join(AI_PERSONALITIES)
        except Exception:
            personalities = "balanced / aggressive / defensive / political / assassin / builder"

        with Vertical():
            yield Static("  ═══ 房间管理 ═══", id="host-title")
            yield RichLog(id="host-slots", wrap=True, markup=True)
            yield Static(
                "\n"
                "  管理命令（在下方输入框中输入即可）：\n"
                "    ai <slot号> [性格]         - 设置基础 AI\n"
                "    rl <slot号>                - 设置 RL AI（需检测到模型）\n"
                "    policy <slot号> <wait|ai>  - 设置断线策略\n"
                "    status                     - 刷新状态\n"
                "    start                      - 开始游戏\n"
                "\n"
                f"  可选 AI 性格：{personalities}\n"
                "\n"
                "  也可以用「/chat 内容」、「/whisper <玩家名> 内容」与玩家聊天。",
                id="host-help",
            )
            with Horizontal(id="host-buttons"):
                yield Button("开始游戏", id="btn-start", variant="success")
                yield Button("刷新", id="btn-refresh", variant="default")

    def refresh_slots(self):
        if self.lobby is None:
            return
        try:
            log = self.query_one("#host-slots", RichLog)
        except Exception:
            return
        log.clear()
        for slot in self.lobby.slots:
            status = "已连接" if slot.is_connected else "未连接"
            stype = slot.slot_type.value
            name = slot.player_name or "空"
            policy = slot.disconnect_policy.value
            log.write(
                f"  [{slot.slot_id}] {stype:12s} | {name:10s} | {status} | 断线策略: {policy}"
            )

    def log_feedback(self, text: str):
        """在槽位日志区追加一条命令反馈（不清除槽位列表）。"""
        try:
            log = self.query_one("#host-slots", RichLog)
            log.write(text)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-start":
            self.post_message(SlotConfigRequest(slot_id=0, action="start_game"))
        elif event.button.id == "btn-refresh":
            self.refresh_slots()
