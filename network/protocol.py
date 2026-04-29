"""
帧协议 —— TCP 长度前缀 + UTF-8 JSON
══════════════════════════════════════
4 字节大端长度前缀 + UTF-8 JSON payload
"""

import asyncio
import json
import struct
import socket
from enum import Enum
from typing import Dict, Any, Optional

HEADER_FMT = "!I"  # 4 字节 big-endian unsigned int
HEADER_SIZE = struct.calcsize(HEADER_FMT)
MAX_MSG_SIZE = 16 * 1024 * 1024  # 16 MB


class MessageType(str, Enum):
    # Server → Client
    REQUEST_COMMAND = "request_command"
    REQUEST_CHOOSE = "request_choose"
    REQUEST_CHOOSE_MULTI = "request_choose_multi"
    REQUEST_CONFIRM = "request_confirm"
    GAME_EVENT = "game_event"
    CHAT_MESSAGE = "chat_message"
    LOBBY_UPDATE = "lobby_update"
    DISCONNECT_NOTICE = "disconnect_notice"
    GAME_STATE_SNAPSHOT = "game_state_snapshot"
    HEARTBEAT_ACK = "heartbeat_ack"

    # Client → Server
    COMMAND_RESPONSE = "command_response"
    CHOOSE_RESPONSE = "choose_response"
    CHOOSE_MULTI_RESPONSE = "choose_multi_response"
    CONFIRM_RESPONSE = "confirm_response"
    CHAT_SEND = "chat_send"
    LOBBY_JOIN = "lobby_join"
    LOBBY_READY = "lobby_ready"
    HEARTBEAT = "heartbeat"
    RECONNECT = "reconnect"


def _encode(msg_dict: Dict[str, Any]) -> bytes:
    payload = json.dumps(msg_dict, ensure_ascii=False).encode("utf-8")
    return struct.pack(HEADER_FMT, len(payload)) + payload


async def send_message(writer: asyncio.StreamWriter, msg_dict: Dict[str, Any]) -> None:
    writer.write(_encode(msg_dict))
    await writer.drain()


async def recv_message(reader: asyncio.StreamReader) -> Optional[Dict[str, Any]]:
    header = await reader.readexactly(HEADER_SIZE)
    length = struct.unpack(HEADER_FMT, header)[0]
    if length > MAX_MSG_SIZE:
        raise ValueError(f"消息过大: {length} bytes")
    payload = await reader.readexactly(length)
    return json.loads(payload.decode("utf-8"))


def send_message_sync(sock: socket.socket, msg_dict: Dict[str, Any]) -> None:
    data = _encode(msg_dict)
    sock.sendall(data)


def recv_message_sync(sock: socket.socket) -> Optional[Dict[str, Any]]:
    header = _recv_exactly(sock, HEADER_SIZE)
    if header is None:
        return None
    length = struct.unpack(HEADER_FMT, header)[0]
    if length > MAX_MSG_SIZE:
        raise ValueError(f"消息过大: {length} bytes")
    payload = _recv_exactly(sock, length)
    if payload is None:
        return None
    return json.loads(payload.decode("utf-8"))


def _recv_exactly(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)
