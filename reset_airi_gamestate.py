"""
AIRI 上下文清空工具
══════════════════
一键连接 AIRI 并清除游戏状态上下文。

用法：
  python reset_airi_gamestate.py
  python reset_airi_gamestate.py ws://localhost:6121/ws --token your_token

模块 ID 默认从 config/airi_bridge_config.json 读取（与 bot_bridge 一致），
也可通过 --module-id 覆盖。这确保了 source identity 匹配，context 桶一致。
"""
import argparse
import asyncio
import json
import os
import sys
import uuid


def _load_module_id(override: str | None = None) -> str:
    """读取 module_id，优先级：命令行 > airi_bridge_config.json > 默认值"""
    if override:
        return override
    for path in ("config/airi_bridge_config.json", "config/airi_config.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            mid = cfg.get("module_id", "")
            if mid:
                print(f"  [提示] 从 {path} 读取 module_id: {mid}")
                return mid
        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"  [警告] 读取 {path} 失败: {e}")
    fallback = "badtime-war-bridge"
    print(f"  [提示] 使用默认 module_id: {fallback}")
    return fallback


def _load_token(override: str | None = None) -> str:
    """读取 auth_token，优先级：命令行 > 环境变量 > config/airi_config.json"""
    if override:
        return override
    token = os.environ.get("AIRI_AUTH_TOKEN", "")
    if token:
        return token
    for path in ("config/airi_config.json", "config/airi_bridge_config.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            token = cfg.get("airi_auth_token", "") or cfg.get("auth_token", "")
            if token:
                print(f"  [提示] 从 {path} 读取 token")
                return token
        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"  [警告] 读取 {path} 失败: {e}")
    return ""


async def reset_context(
    url: str, auth_token: str, context_id: str, module_id: str,
):
    try:
        import websockets
    except ImportError:
        print("  [错误] 需要安装 websockets: pip install websockets")
        sys.exit(1)

    # 与 bot_bridge 使用完全相同的 source identity，确保命中同一个 context 桶
    source_identity = {
        "id": f"{module_id}-instance",
        "kind": "plugin",
        "plugin": {"id": module_id},
    }
    print(f"  source identity: {source_identity['id']}")

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

            # 认证（如果提供了 token）
            if auth_token:
                auth_msg = {
                    "type": "module:authenticate",
                    "data": {"token": auth_token},
                    "metadata": {
                        "source": source_identity,
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
                    "name": "Context Reset Tool",
                    "identity": source_identity,
                },
                "metadata": {
                    "source": source_identity,
                    "event": {"id": str(uuid.uuid4())},
                },
            }
            await ws.send(json.dumps(announce))
            print("  ✓ 模块注册消息已发送")

            # 发送清空上下文消息
            clear_msg = {
                "type": "context:update",
                "data": {
                    "id": str(uuid.uuid4()),
                    "contextId": context_id,
                    "strategy": "replace-self",
                    "text": "(上下文已清空)",
                    "lane": "game-state",
                },
                "metadata": {
                    "source": source_identity,
                    "event": {"id": str(uuid.uuid4())},
                },
            }
            await ws.send(json.dumps(clear_msg))
            print(f"  ✓ 清空上下文消息已发送 (contextId: {context_id})")

            # 等待确认（最多 5 秒）
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        print(f"  ← 收到非 JSON 消息: {str(raw)[:200]}")
                        continue

                    if isinstance(msg, dict) and "json" in msg and isinstance(msg["json"], dict):
                        msg = msg["json"]

                    msg_type = msg.get("type", "") if isinstance(msg, dict) else ""

                    print(f"  ← 收到消息: type={msg_type}")

                    if msg_type == "context:ack":
                        print("  ✓ AIRI 确认收到上下文更新")
                        break
                    elif msg_type in ("output:gen-ai:chat:message", "output:gen-ai:chat:complete"):
                        continue

            except asyncio.TimeoutError:
                print("  ⚠ 5秒内未收到确认消息（但消息可能已成功发送）")

            print("\n  上下文清空完成！")

    except ConnectionRefusedError:
        print(f"  ✗ 连接被拒绝。请确认 AIRI 正在运行且 WebSocket 地址正确: {url}")
    except Exception as e:
        print(f"  ✗ 连接异常: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="连接 AIRI 并清除游戏状态上下文",
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
        help="AIRI 认证 token（优先级：命令行 > AIRI_AUTH_TOKEN 环境变量 > config）",
    )
    parser.add_argument(
        "--context-id",
        default="game-state",
        help="要清空的 contextId（默认 game-state）",
    )
    parser.add_argument(
        "--module-id",
        default=None,
        help="模块 ID（默认从 config/airi_bridge_config.json 读取，需与 bot_bridge 一致）",
    )
    args = parser.parse_args()

    module_id = _load_module_id(args.module_id)
    token = _load_token(args.token)

    asyncio.run(reset_context(args.url, token, args.context_id, module_id))


if __name__ == "__main__":
    main()