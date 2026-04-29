"""游戏日志区 —— 可滚动的事件日志"""

from textual.widgets import RichLog


class GameLogWidget(RichLog):
    """中部游戏日志区，追加游戏事件文本。"""

    def append_event(self, event: dict):
        func = event.get("event", "")
        args = event.get("args", [])

        text = self._format_event(func, args)
        if text:
            self.write(text)

    def _format_event(self, func: str, args: list) -> str:
        if func == "show_round_header":
            rn = args[0] if args else "?"
            return f"{'='*50}\n  全局轮次 {rn}\n{'='*50}"
        elif func == "show_phase":
            return f"\n--- {args[0] if args else ''} ---"
        elif func == "show_action_turn_header":
            name = args[0] if args else "?"
            return f"\n{'─'*40}\n  轮到 {name} 行动\n{'─'*40}"
        elif func == "show_result":
            return f"  {args[0]}" if args else ""
        elif func == "show_error":
            return f"  [错误] {args[0]}" if args else ""
        elif func == "show_info":
            return f"  {args[0]}" if args else ""
        elif func == "show_victory":
            name = args[0] if args else "?"
            return f"\n  {name} 获得了最终胜利！游戏结束！"
        elif func == "show_death":
            name = args[0] if args else "?"
            cause = args[1] if len(args) > 1 else "未知"
            return f"  {name} 死亡！原因：{cause}"
        elif func == "show_d4_results":
            return "  D4 投掷完毕"
        elif func == "show_police_enforcement":
            msgs = args[0] if args else []
            if isinstance(msgs, list):
                return "\n".join(f"  {m}" for m in msgs)
            return ""
        elif func == "show_player_status":
            if args and isinstance(args[0], dict):
                return f"  {args[0].get('status', '')}"
            return ""
        elif func == "show_available_actions":
            return ""  # 不在日志中显示
        elif func == "clear_screen":
            return ""
        else:
            if args:
                return f"  [{func}] {' '.join(str(a) for a in args)}"
            return ""
