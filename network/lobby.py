"""
LobbyManager —— 房间与玩家槽位管理
════════════════════════════════════
管理大厅状态：玩家加入、AI 设置、断线策略、游戏启动。
"""

import random
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from models.player import Player
from engine.game_state import GameState
from engine.round_manager import RoundManager
from controllers.human import HumanController
from controllers.network_controller import NetworkController
from controllers.forfeit_controller import ForfeitController
from controllers.ai_basic import BasicAIController
from engine.game_setup import (
    TALENT_TABLE, AI_TALENT_PREFERENCE, AI_PERSONALITIES,
    AI_NAME_POOL, TALENT_DECAY_FACTOR, _ai_pick_talent,
    AI_DISABLED_TALENTS, _talent_selection,
)
from network.protocol import MessageType


class SlotType(str, Enum):
    HUMAN_LOCAL = "human_local"
    HUMAN_REMOTE = "human_remote"
    BASIC_AI = "basic_ai"
    RL_AI = "rl_ai"
    EMPTY = "empty"


class DisconnectPolicy(str, Enum):
    WAIT_RECONNECT = "wait_reconnect"
    AI_TAKEOVER = "ai_takeover"


class RoomState(str, Enum):
    WAITING = "waiting"
    IN_GAME = "in_game"
    FINISHED = "finished"


@dataclass
class PlayerSlot:
    slot_id: int
    slot_type: SlotType = SlotType.EMPTY
    client_id: Optional[str] = None
    player_name: Optional[str] = None
    is_connected: bool = False
    disconnect_policy: DisconnectPolicy = DisconnectPolicy.WAIT_RECONNECT
    personality: Optional[str] = None  # AI 用
    rl_model_path: Optional[str] = None  # RL AI 用

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "slot_type": self.slot_type.value,
            "player_name": self.player_name,
            "is_connected": self.is_connected,
            "disconnect_policy": self.disconnect_policy.value,
            "personality": self.personality,
        }


class LobbyManager:
    def __init__(self, total_players: int, host_plays: bool, server: Any):
        self.total_players = max(2, min(6, total_players))
        self.host_plays = host_plays
        self.server = server
        self.state = RoomState.WAITING

        self.slots: List[PlayerSlot] = []
        for i in range(self.total_players):
            slot = PlayerSlot(slot_id=i + 1)
            self.slots.append(slot)

        # 如果房主参与游戏，slot 1 为 HUMAN_LOCAL
        if host_plays:
            self.slots[0].slot_type = SlotType.HUMAN_LOCAL
            self.slots[0].player_name = "房主"
            self.slots[0].is_connected = True

        # 游戏运行时的引用
        self.game_state: Optional[GameState] = None
        self.round_manager: Optional[RoundManager] = None

        # slot_id → Player 映射
        self._slot_players: Dict[int, Player] = {}

    # ──────────────────────────────────────────
    #  玩家加入
    # ──────────────────────────────────────────

    def on_player_join(self, client_id: str, player_name: str) -> Optional[PlayerSlot]:
        if self.state != RoomState.WAITING:
            return None
        for slot in self.slots:
            if slot.slot_type == SlotType.EMPTY:
                slot.slot_type = SlotType.HUMAN_REMOTE
                slot.client_id = client_id
                slot.player_name = player_name
                slot.is_connected = True
                self._broadcast_lobby_update()
                return slot
        return None  # 没有空位

    def on_player_leave(self, client_id: str):
        for slot in self.slots:
            if slot.client_id == client_id:
                if self.state == RoomState.WAITING:
                    # 大厅阶段：直接释放槽位
                    slot.slot_type = SlotType.EMPTY
                    slot.client_id = None
                    slot.player_name = None
                    slot.is_connected = False
                else:
                    # 游戏中：标记断线
                    slot.is_connected = False
                self._broadcast_lobby_update()
                return slot
        return None

    # ──────────────────────────────────────────
    #  AI 设置
    # ──────────────────────────────────────────

    def set_slot_ai(
        self, slot_id: int, ai_type: str = "basic", personality: str = "balanced",
        rl_model_path: Optional[str] = None,
    ) -> bool:
        slot = self._get_slot(slot_id)
        if slot is None:
            return False
        if slot.slot_type not in (SlotType.EMPTY, SlotType.BASIC_AI, SlotType.RL_AI):
            return False

        if ai_type == "rl":
            slot.slot_type = SlotType.RL_AI
            slot.rl_model_path = rl_model_path
        else:
            slot.slot_type = SlotType.BASIC_AI
        slot.personality = personality
        slot.player_name = f"AI-{personality}"
        slot.is_connected = True
        self._broadcast_lobby_update()
        return True

    def set_disconnect_policy(self, slot_id: int, policy: DisconnectPolicy):
        slot = self._get_slot(slot_id)
        if slot:
            slot.disconnect_policy = policy
            self._broadcast_lobby_update()

    # ──────────────────────────────────────────
    #  游戏启动
    # ──────────────────────────────────────────

    def can_start(self) -> bool:
        return all(s.slot_type != SlotType.EMPTY for s in self.slots)

    def start_game(self) -> GameState:
        if not self.can_start():
            raise RuntimeError("还有空位未填满")

        self.state = RoomState.IN_GAME
        game_state = GameState()
        ai_players_info = []

        used_ai_names = set()
        available_ai_names = list(AI_NAME_POOL)
        random.shuffle(available_ai_names)

        for slot in self.slots:
            pid = f"p{slot.slot_id}"
            name = slot.player_name or f"玩家{slot.slot_id}"

            if slot.slot_type == SlotType.HUMAN_LOCAL:
                controller = HumanController()
            elif slot.slot_type == SlotType.HUMAN_REMOTE:
                controller = NetworkController(slot.client_id, self.server)
            elif slot.slot_type == SlotType.BASIC_AI:
                personality = slot.personality or random.choice(AI_PERSONALITIES)
                controller = BasicAIController(personality=personality)
                ai_players_info.append((pid, name, personality))
            elif slot.slot_type == SlotType.RL_AI:
                controller = self._create_rl_controller(slot)
                if controller is None:
                    personality = slot.personality or "balanced"
                    controller = BasicAIController(personality=personality)
                    ai_players_info.append((pid, name, personality))
            else:
                controller = ForfeitController()

            player = Player(pid, name, controller=controller)
            game_state.add_player(player)
            self._slot_players[slot.slot_id] = player

        # 随机化玩家顺序
        random.shuffle(game_state.player_order)

        # 设置最大轮数
        player_count = len(game_state.player_order)
        game_state.max_rounds = GameState.compute_default_max_rounds(player_count)

        self.game_state = game_state
        return game_state

    def _create_rl_controller(self, slot: PlayerSlot):
        try:
            from network.rl_detect import detect_rl_availability
            rl_info = detect_rl_availability()
            if not rl_info["available"]:
                return None
            from rl.diagnose_single_game import load_controller
            from rl.obs_builder import N_STACK
            model_path = slot.rl_model_path or (
                rl_info["models"][0] if rl_info["models"] else None
            )
            if model_path is None:
                return None
            return load_controller(model_path, N_STACK)
        except Exception:
            return None

    # ──────────────────────────────────────────
    #  断线处理
    # ──────────────────────────────────────────

    def handle_disconnect(self, client_id: str):
        for slot in self.slots:
            if slot.client_id == client_id and slot.slot_type == SlotType.HUMAN_REMOTE:
                slot.is_connected = False
                player = self._slot_players.get(slot.slot_id)
                if player is None:
                    return

                if slot.disconnect_policy == DisconnectPolicy.WAIT_RECONNECT:
                    player.controller = ForfeitController()
                    self.server.broadcast_sync({
                        "type": MessageType.DISCONNECT_NOTICE,
                        "player_name": slot.player_name,
                        "action": "waiting_reconnect",
                    })
                elif slot.disconnect_policy == DisconnectPolicy.AI_TAKEOVER:
                    self._ai_takeover(slot, player)
                    self.server.broadcast_sync({
                        "type": MessageType.DISCONNECT_NOTICE,
                        "player_name": slot.player_name,
                        "action": "ai_takeover",
                    })
                return

    def handle_reconnect(self, client_id: str, player_name: str) -> bool:
        for slot in self.slots:
            if (slot.player_name == player_name
                    and slot.slot_type == SlotType.HUMAN_REMOTE
                    and not slot.is_connected):
                slot.client_id = client_id
                slot.is_connected = True
                player = self._slot_players.get(slot.slot_id)
                if player:
                    player.controller = NetworkController(client_id, self.server)
                # 向重连客户端发送当前房间状态（使其跳过等待游戏开始的循环）
                self.server.send_to_sync(client_id, {
                    "type": MessageType.LOBBY_UPDATE,
                    "room_state": self.state.value,
                    "slots": [s.to_dict() for s in self.slots],
                })
                self.server.broadcast_sync({
                    "type": MessageType.DISCONNECT_NOTICE,
                    "player_name": player_name,
                    "action": "reconnected",
                })
                return True
        return False

    def _ai_takeover(self, slot: PlayerSlot, player: Player):
        try:
            from network.rl_detect import detect_rl_availability
            rl_info = detect_rl_availability()
            if rl_info["available"] and rl_info["models"]:
                from rl.diagnose_single_game import load_controller
                from rl.obs_builder import N_STACK
                ctrl = load_controller(rl_info["models"][0], N_STACK)
                if ctrl is not None:
                    player.controller = ctrl
                    if hasattr(ctrl, 'set_player_ref') and self.game_state:
                        ctrl.set_player_ref(player, self.game_state)
                    return
        except Exception:
            pass
        personality = slot.personality or "random"
        if personality == "random":
            personality = random.choice(AI_PERSONALITIES)
        player.controller = BasicAIController(personality=personality)

    # ──────────────────────────────────────────
    #  辅助
    # ──────────────────────────────────────────

    def _get_slot(self, slot_id: int) -> Optional[PlayerSlot]:
        for slot in self.slots:
            if slot.slot_id == slot_id:
                return slot
        return None

    def _broadcast_lobby_update(self):
        msg = {
            "type": MessageType.LOBBY_UPDATE,
            "room_state": self.state.value,
            "slots": [s.to_dict() for s in self.slots],
        }
        try:
            self.server.broadcast_sync(msg)
        except Exception:
            pass

    def get_lobby_info(self) -> Dict[str, Any]:
        return {
            "total_players": self.total_players,
            "room_state": self.state.value,
            "host_plays": self.host_plays,
            "slots": [s.to_dict() for s in self.slots],
        }
