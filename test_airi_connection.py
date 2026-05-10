"""
AIRI 连接测试工具
═════════════════
单独测试与 AIRI 的 WebSocket 连接，不需要启动游戏服务器。

用法：
  python test_airi_connection.py
  python test_airi_connection.py ws://localhost:6121/ws --token your_token
  python test_airi_connection.py wss://localhost:6121/ws --token your_token

也可以通过环境变量 AIRI_AUTH_TOKEN 提供认证 token。
"""
import argparse
import asyncio
import json
import os
import sys
import uuid


async def test(url: str, auth_token: str):
    try:
        import websockets
    except ImportError:
        print("  [错误] 需要安装 websockets: pip install websockets")
        sys.exit(1)

    print(f"  正在连接 AIRI: {url}")

    connect_kwargs = {}
    if url.startswith("wss://"):
        import ssl as _ssl
        ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl.CERT_NONE
        connect_kwargs["ssl"] = ssl_ctx
        print("  [WSS] 已启用 SSL（跳过证书验证）")

    try:
        async with websockets.connect(url, **connect_kwargs) as ws:
            print("  ✓ WebSocket 连接成功！")

            identity = {
                "id": "test-instance",
                "kind": "plugin",
                "plugin": {"id": "connection-test"},
            }

            # 认证（如果提供了 token）
            if auth_token:
                auth_msg = {
                    "type": "module:authenticate",
                    "data": {"token": auth_token},
                    "metadata": {
                        "source": identity,
                        "event": {"id": str(uuid.uuid4())},
                    },
                }
                await ws.send(json.dumps(auth_msg))
                print("  ✓ 认证消息已发送")
            else:
                print("  [提示] 未提供 token，跳过认证步骤")

            # 注册模块
            announce = {
                "type": "module:announce",
                "data": {
                    "name": "Connection Test",
                    "identity": identity,
                },
                "metadata": {
                    "source": identity,
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
                    "source": identity,
                    "event": {"id": str(uuid.uuid4())},
                },
            }
            await ws.send(json.dumps(test_msg))
            print("  ✓ 测试消息已发送，等待 AIRI 回复...")

            # 等待回复（最多 30 秒）
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        print(f"  ← 收到非 JSON 消息: {str(raw)[:200]}")
                        continue

                    # SuperJSON 解包
                    if isinstance(msg, dict) and "json" in msg and isinstance(msg["json"], dict):
                        msg = msg["json"]

                    msg_type = msg.get("type", "") if isinstance(msg, dict) else ""
                    data = msg.get("data", {}) if isinstance(msg, dict) else {}

                    # 打印完整 type 与 data，方便调试
                    try:
                        data_repr = json.dumps(data, ensure_ascii=False)[:500]
                    except Exception:
                        data_repr = str(data)[:500]
                    print(f"  ← 收到消息: type={msg_type} data={data_repr}")

                    if msg_type in (
                        "output:gen-ai:chat:message",
                        "output:gen-ai:chat:complete",
                    ):
                        # 优先从 message.content 提取实际回复
                        message_obj = data.get("message", {}) if isinstance(data, dict) else {}
                        text = message_obj.get("content", "") if isinstance(message_obj, dict) else ""
                        if not text and isinstance(data, dict):
                            # 降级：尝试 data.text（兼容旧版本）
                            text = data.get("text", "")
                        if text:
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


def main():
    parser = argparse.ArgumentParser(
        description="测试与 AIRI WebSocket 服务器的连接",
    )
    parser.add_argument(
        "url",
        nargs="?",
        default="ws://localhost:6121/ws",
        help="AIRI WebSocket URL（默认 ws://localhost:6121/ws）",
    )
    parser.add_argument(
        "--token",
        default=None,
        help=(
            "AIRI 认证 token（优先级：命令行 > AIRI_AUTH_TOKEN 环境变量"
            " > config/airi_config.json）"
        ),
    )
    args = parser.parse_args()

    # token 优先级：命令行参数 > 环境变量 > 配置文件
    token = args.token
    if not token:
        token = os.environ.get("AIRI_AUTH_TOKEN", "")
    if not token:
        try:
            with open(
                "config/airi_config.json", "r", encoding="utf-8",
            ) as f:
                config = json.load(f)
                token = config.get("airi_auth_token", "")
            if token:
                print("  [提示] 从 config/airi_config.json 读取 token")
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"  [警告] 读取 config/airi_config.json 失败: {e}")

    asyncio.run(test(args.url, token))


if __name__ == "__main__":
    main()
