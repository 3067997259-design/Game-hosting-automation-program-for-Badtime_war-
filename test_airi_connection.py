"""
AIRI 连接测试工具
═════════════════
单独测试与 AIRI 的 WebSocket 连接，不需要启动游戏服务器。

用法：
  python test_airi_connection.py
  python test_airi_connection.py ws://localhost:6121/ws
  python test_airi_connection.py wss://localhost:6121/ws
"""
import asyncio
import json
import sys
import uuid


async def test():
    try:
        import websockets
    except ImportError:
        print("  [错误] 需要安装 websockets: pip install websockets")
        sys.exit(1)

    url = "ws://localhost:6121/ws"
    if len(sys.argv) > 1:
        url = sys.argv[1]

    print(f"  正在连接 AIRI: {url}")

    connect_kwargs = {}
    if url.startswith("wss://"):
        import ssl as _ssl
        ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl.CERT_NONE
        connect_kwargs["ssl"] = ssl_ctx

    try:
        async with websockets.connect(url, **connect_kwargs) as ws:
            print("  ✓ WebSocket 连接成功！")

            # 发送模块注册
            instance_id = f"connection-test-{uuid.uuid4().hex[:8]}"
            announce = {
                "type": "module:announce",
                "data": {
                    "name": "Connection Test",
                    "identity": {
                        "id": instance_id,
                        "kind": "plugin",
                        "plugin": {
                            "id": "connection-test",
                        },
                    },
                },
                "metadata": {
                    "source": {
                        "id": instance_id,
                        "kind": "plugin",
                        "plugin": {
                            "id": "connection-test",
                        },
                    },
                    "event": {"id": str(uuid.uuid4())},
                },
            }
            await ws.send(json.dumps(announce))
            print("  ✓ 模块注册消息已发送")

            # 发送测试消息
            test_msg = {
                "type": "input:text",
                "data": {"text": "你好，这是一条测试消息。请回复任意内容。"},
                "metadata": {
                    "source": {
                        "id": instance_id,
                        "kind": "plugin",
                        "plugin": {
                            "id": "connection-test",
                        },
                    },
                    "event": {"id": str(uuid.uuid4())},
                },
            }
            await ws.send(json.dumps(test_msg))
            print("  ✓ 测试消息已发送，等待 AIRI 回复...")

            # 等待回复（最多 30 秒）
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    msg = json.loads(raw)
                    msg_type = msg.get("type", "")
                    print(f"  ← 收到消息: type={msg_type}")

                    if msg_type in (
                        "output:gen-ai:chat:message",
                        "output:gen-ai:chat:complete",
                    ):
                        text = msg.get("data", {}).get("text", "")
                        print(f"  ✓ AIRI 回复: {text[:200]}")
                        if msg_type == "output:gen-ai:chat:complete":
                            break

            except asyncio.TimeoutError:
                print("  ⚠ 30秒内未收到 AIRI 回复（可能需要检查 AIRI 是否正在运行）")

            print("\n  测试完成！")

    except ConnectionRefusedError:
        print(f"  ✗ 连接被拒绝。请确认 AIRI 正在运行且 WebSocket 地址正确: {url}")
    except Exception as e:
        print(f"  ✗ 连接异常: {e}")


if __name__ == "__main__":
    asyncio.run(test())
