"""
断线检测与心跳管理
══════════════════
- 客户端每 5 秒发送 heartbeat
- 服务器 15 秒未收到则判定断线
- 断线时根据 DisconnectPolicy 执行策略
"""

import threading
import time
from typing import Any, Callable, Optional


class DisconnectMonitor:
    """后台线程监控客户端心跳，检测断线。"""

    def __init__(
        self,
        server: Any,
        lobby: Any,
        check_interval: float = 5.0,
        heartbeat_timeout: float = 15.0,
    ):
        self.server = server
        self.lobby = lobby
        self.check_interval = check_interval
        self.heartbeat_timeout = heartbeat_timeout
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        while self._running:
            time.sleep(self.check_interval)
            now = time.time()
            for client_id in list(self.server._last_heartbeat.keys()):
                last = self.server._last_heartbeat.get(client_id, now)
                if now - last > self.heartbeat_timeout:
                    if self.server.is_connected(client_id):
                        print(f"  [Monitor] 客户端 {client_id} 心跳超时")
                        self.lobby.handle_disconnect(client_id)
