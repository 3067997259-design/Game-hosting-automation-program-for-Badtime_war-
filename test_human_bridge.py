"""
test_human_bridge.py
═══════════════════
人类测试员通过 bot_bridge 管道接入游戏，取代 AIRI 的位置。

用 HumanTerminal（stdin/stdout）替换 AiriConnection，其余逻辑
全部继承 BotBridge，实现零代码重复。

启动方式：
  python test_human_bridge.py
  python test_human_bridge.py --config config/airi_bridge_config.json
"""

import argparse
import json
import logging
import queue
import sys
import threading
import time
from datetime import datetime
from typing import Optional

from bot_bridge import BotBridge

log = logging.getLogger("human_bridge")

# ──────────────────────────────────────────────────────────────────
#  HumanTerminal — 替代 AiriConnection 的 stdin/stdout 终端
# ──────────────────────────────────────────────────────────────────

_SEPARATOR = "─" * 54


class HumanTerminal:
    """实现 AiriConnection 兼容接口，将消息输出到终端，输入从 stdin 读取。"""

    def __init__(self, on_special=None):
        self._connected = False
        self._input_queue: queue.Queue = queue.Queue()
        self._input_thread: Optional[threading.Thread] = None
        self._running = False
        self._on_special = on_special  # (cmd: str) -> bool
        self._log_file: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── AiriConnection 兼容接口 ────────────────────────────────

    def connect(self):
        self._running = True
        self._input_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._input_thread.start()
        self._connected = True

    def send_text(self, text: str):
        self._write(f"\n{text}")

    def send_context(self, context: dict):
        ctx_text = context.get("text", "")
        if ctx_text:
            self._write(f"\n{_SEPARATOR}\n[Context] {ctx_text}\n{_SEPARATOR}")

    def send_notify(
        self,
        message: str,
        kind: str = "ping",
        urgency: str = "immediate",
        headline: Optional[str] = None,
        destinations: Optional[list] = None,
    ):
        self._write(f"\n[Event] {message}")

    def wait_for_response(self, timeout: float = 60.0) -> Optional[str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            try:
                line = self._input_queue.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            if line is None:
                return None
            line = line.strip()
            if not line:
                continue
            # 特殊命令以 : 开头
            if line.startswith(":"):
                handled = self._handle_special(line)
                if handled:
                    continue  # 拦截，继续等待
                # 未处理 → 作为普通输入返回
                return line
            # 普通输入
            return line
        return None  # 超时

    def drain_responses(self):
        """清空队列中所有待处理的输入。"""
        while True:
            try:
                self._input_queue.get_nowait()
            except queue.Empty:
                break
        return []

    # ── 内部实现 ────────────────────────────────────────────────

    def _read_loop(self):
        """后台线程：持续从 stdin 读取，放入队列。"""
        while self._running:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                self._input_queue.put(None)
                break
            except Exception:
                time.sleep(0.1)
                continue
            self._input_queue.put(line)

    def _handle_special(self, line: str) -> bool:
        """处理特殊命令，返回 True 表示已拦截。"""
        cmd = line[1:].strip()  # 去掉 :
        if not cmd:
            return True  # 空 : 忽略

        # ── :chat <消息> ──
        if cmd.startswith("chat ") or cmd == "chat":
            msg = cmd[5:].strip() if cmd.startswith("chat ") else ""
            if self._on_special:
                return self._on_special("chat", msg)
            return True

        # ── :forfeit ──
        if cmd == "forfeit":
            self._input_queue.put("forfeit")
            return True

        # ── 其他委托给 TestHumanBridge ──
        if self._on_special:
            parts = cmd.split(maxsplit=1)
            action = parts[0]
            arg = parts[1] if len(parts) > 1 else ""
            return self._on_special(action, arg)

        return False  # 未被识别，作为普通输入返回

    def _write(self, text: str):
        """写入终端，可选写入日志。"""
        print(text)
        if self._log_file:
            try:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
            except Exception:
                pass

    def enable_logging(self, path: str):
        self._log_file = path
        self._write(f"  [日志] 正在记录到 {path}")


# ──────────────────────────────────────────────────────────────────
#  TestHumanBridge — 继承 BotBridge，替换 self.airi
# ──────────────────────────────────────────────────────────────────

class TestHumanBridge(BotBridge):
    """人类测试桥：复用 BotBridge 全部逻辑，只替换 AIRI 为终端。"""

    def __init__(self, config: dict, *, enable_logging: bool = False):
        # 先初始化父类（会创建 self.airi = AiriConnection）
        super().__init__(config)
        # 然后替换
        self.airi = HumanTerminal(on_special=self._handle_special_cmd)
        if enable_logging:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = f"test_log_{ts}.txt"
            self.airi.enable_logging(log_path)
            print(f"\n  [日志] 正在记录到 {log_path}")

    # ── 重写角色设定：不需要等待 AIRI 确认 ──

    def _send_role_setup(self):
        setup_text = (
            f"{_SEPARATOR}\n"
            "  起闯战争 — 人类测试模式\n"
            f"{_SEPARATOR}\n"
            "\n"
            "你正在以人类身份接入游戏，取代 AIRI 的位置。\n"
            "所有 bot_bridge 发送给 AIRI 的消息将显示在终端中。\n"
            "\n"
            "【特殊命令】（以 : 开头）\n"
            "  :chat <消息>   — 发送公屏聊天\n"
            "  :status        — 查看最近事件\n"
            "  :events        — 查看全部事件历史\n"
            "  :context       — 查看最近推送给 AIRI 的上下文\n"
            "  :forfeit       — 快捷放弃行动\n"
            "  :quit          — 退出测试\n"
            "\n"
            "【行动格式】\n"
            "  分层枚举模式下：直接输入数字或指令名\n"
            "  传统 prompt 下：ACTION: <完整指令>\n"
            f"{_SEPARATOR}\n"
        )
        self.airi.send_text(setup_text)

    # ── 特殊命令处理 ──

    def _handle_special_cmd(self, action: str, arg: str) -> bool:
        """处理 : 开头的特殊命令，返回 True 表示已拦截。"""
        if action == "chat":
            if arg:
                self.game_client.send_sync({
                    "type": "CHAT_SEND",
                    "sender": self.bot_name,
                    "content": arg,
                    "channel": "public",
                })
                self.airi.send_text(f"  [你]: {arg}")
            else:
                self.airi.send_text("  [用法] :chat <消息>")
            return True

        if action == "status":
            events = self.game_events[-10:] if self.game_events else []
            if events:
                lines = ["\n  【近期事件】"]
                for i, ev in enumerate(reversed(events), 1):
                    lines.append(f"    {i}. {ev}")
                self.airi.send_text("\n".join(lines))
            else:
                self.airi.send_text("  (暂无事件)")
            return True

        if action == "events":
            events = self.game_events
            if events:
                lines = [f"\n  【全部事件（{len(events)}条）】"]
                for i, ev in enumerate(events, 1):
                    lines.append(f"    {i}. {ev}")
                self.airi.send_text("\n".join(lines))
            else:
                self.airi.send_text("  (暂无事件)")
            return True

        if action == "context":
            ctx = self._last_game_state_text
            if ctx:
                self.airi.send_text(f"\n  【最近上下文】\n{ctx}")
            else:
                self.airi.send_text("  (暂无上下文)")
            return True

        if action == "quit":
            self.airi.send_text("\n  正在退出...")
            self.airi._running = False
            sys.exit(0)

        # 未识别
        self.airi.send_text(
            f"  [未知命令] :{action}\n"
            "  可用: :chat :status :events :context :forfeit :quit"
        )
        return True

    # ── 启动 ──

    def start(self):
        # 覆盖父类 start，因为父类调用 self.airi.connect() 会尝试 WebSocket
        log.info("启动人类测试桥...")
        self.airi.connect()
        self._send_role_setup()

        host = self.config.get("game_server_host", "127.0.0.1")
        port = self.config.get("game_server_port", 9527)
        log.info(f"正在连接游戏服务器: {host}:{port}")
        self.game_client.connect(self.bot_name)
        log.info("游戏服务器连接成功")

        self._register_handlers()
        self._main_loop()


# ──────────────────────────────────────────────────────────────────
#  入口
# ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="人类测试桥 - 起闯战争")
    parser.add_argument(
        "--config", type=str, default="config/airi_bridge_config.json",
        help="配置文件路径",
    )
    parser.add_argument("--name", type=str, default=None, help="Bot 名称")
    parser.add_argument("--debug", action="store_true", help="启用调试日志")
    parser.add_argument("--log", action="store_true", default=None,
                       help="记录测试日志")
    parser.add_argument("--no-log", action="store_true", default=None,
                       help="不记录测试日志")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # 加载配置
    try:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        log.error(f"配置文件不存在: {args.config}")
        sys.exit(1)

    if args.name:
        config["bot_name"] = args.name

    # 询问是否记录日志
    enable_log = args.log
    if enable_log is None and args.no_log is None:
        answer = input("是否记录测试日志？(y/n): ").strip().lower()
        enable_log = answer in ("y", "yes", "是")

    print(f"\n  ═══════════════════════════════════════")
    print(f"    起闯战争 — 人类测试桥")
    print(f"  ═══════════════════════════════════════")
    print(f"  游戏服务器: {config.get('game_server_host', '127.0.0.1')}:{config.get('game_server_port', 9527)}")
    print(f"  Bot 名称:   {config.get('bot_name', 'TestHuman')}")
    print(f"  测试日志:   {'开启' if enable_log else '关闭'}")
    print()

    bridge = TestHumanBridge(config, enable_logging=enable_log)
    try:
        bridge.start()
    except ConnectionError as e:
        log.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
