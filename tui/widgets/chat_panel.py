"""聊天面板 —— 公屏和私聊"""

from textual.widgets import RichLog, Static, TabbedContent, TabPane


class ChatPanel(Static):
    """底部聊天面板，包含公屏和私聊 Tab。"""

    DEFAULT_CSS = """
    ChatPanel {
        height: 100%;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._private_tabs: dict[str, RichLog] = {}
        # 用递增序号生成合法 widget ID（Textual 仅允许 ASCII 字母/数字/下划线/连字符）
        self._tab_counter: int = 0

    def compose(self):
        with TabbedContent():
            with TabPane("公屏", id="tab-public"):
                yield RichLog(id="chat-public", wrap=True, markup=True)

    def add_message(self, sender: str, content: str, channel: str = "public",
                    target: str = None):
        if channel == "public":
            log = self.query_one("#chat-public", RichLog)
            log.write(f"[bold]{sender}[/bold]: {content}")
        elif channel == "private":
            label = target or sender
            if label not in self._private_tabs:
                self._add_private_tab(label)
            log = self._private_tabs.get(label)
            if log:
                log.write(f"[bold]{sender}[/bold]: {content}")

    def _add_private_tab(self, player_name: str):
        tabs = self.query_one(TabbedContent)
        self._tab_counter += 1
        # widget ID 仅使用合法字符（数字后缀），玩家名仍作为 Tab 标题展示
        tab_id = f"tab-private-{self._tab_counter}"
        log_id = f"chat-private-{self._tab_counter}"
        log = RichLog(id=log_id, wrap=True, markup=True)
        pane = TabPane(f"私聊:{player_name}", log, id=tab_id)
        tabs.add_pane(pane)
        self._private_tabs[player_name] = log
