"""房主管理面板 —— 仅房主端显示"""

from textual.widgets import Static, Button, Select, RichLog
from textual.containers import Vertical, Horizontal
from textual.message import Message
from typing import Any, Optional


class SlotConfigRequest(Message):
    """请求配置某个槽位。"""
    def __init__(self, slot_id: int, action: str, **kwargs):
        super().__init__()
        self.slot_id = slot_id
        self.action = action
        self.extra = kwargs


class HostPanel(Static):
    """房主管理面板：显示所有玩家连接状态、AI配置、断线处理选项。"""

    DEFAULT_CSS = """
    HostPanel {
        height: 100%;
        border: solid green;
        padding: 1;
    }
    """

    def __init__(self, lobby: Any = None, **kwargs):
        super().__init__(**kwargs)
        self.lobby = lobby

    def compose(self):
        yield Static("  房主管理面板", id="host-title")
        yield RichLog(id="host-slots", wrap=True, markup=True)
        with Horizontal():
            yield Button("开始游戏", id="btn-start", variant="success")
            yield Button("刷新", id="btn-refresh", variant="default")

    def refresh_slots(self):
        if self.lobby is None:
            return
        log = self.query_one("#host-slots", RichLog)
        log.clear()
        for slot in self.lobby.slots:
            status = "已连接" if slot.is_connected else "未连接"
            stype = slot.slot_type.value
            name = slot.player_name or "空"
            policy = slot.disconnect_policy.value
            log.write(
                f"  [{slot.slot_id}] {stype:12s} | {name:10s} | {status} | 断线策略: {policy}"
            )

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-start":
            self.post_message(SlotConfigRequest(slot_id=0, action="start_game"))
        elif event.button.id == "btn-refresh":
            self.refresh_slots()
