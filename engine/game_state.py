"""全局游戏状态（Phase 3 完整版）"""

from models.markers import MarkerManager
from models.police import PoliceData
from models.virus import VirusSystem
from engine.response_window import ResponseWindowManager


class GameState:
    def __init__(self):
        # 玩家
        self.players = {}
        self.player_order = []

        # 轮次
        self.current_round = 0
        self.current_phase = "not_started"

        # AI 演示速度控制
        self.ai_delay = 0.0         # 每次行动后的延迟秒数（0=不延迟）
        self.pause_mode = False    # True=每次行动后等回车

        # 本轮行动权
        self.d4_results = {}
        self.d4_bonuses = {}
        self.round_winners = []

        # 标记系统
        self.markers = MarkerManager()

        # 警察系统
        self.police = PoliceData()
        self.police_engine = None   # 在 round_manager 初始化时注入

        # 病毒系统
        self.virus = VirusSystem()

        # 违法行为列表（可被天赋扩展）
        self.crime_types = {
            "伤害玩家",
            "无凭证商店",
            "无凭证手术",
        }
        # Phase 4: 朝阳好市民会添加更多
        self.active_barrier = None  # 神代3结界引用

        # 响应窗口
        self._response_window = ResponseWindowManager(self)

        # 事件日志
        self.event_log = []

        # 游戏状态
        self.game_over = False
        self.winner = None

    def add_player(self, player):
        self.players[player.player_id] = player
        self.player_order.append(player.player_id)
        self.markers.init_player(player.player_id)

    def get_player(self, player_id):
        return self.players.get(player_id)

    def alive_players(self):
        return [p for p in self.players.values() if p.is_alive()]

    def awake_alive_players(self):
        return [p for p in self.players.values()
                if p.is_alive() and p.is_awake]

    def players_at_location(self, location):
        return [p for p in self.players.values()
                if p.is_alive() and p.location == location]

    def check_victory(self):
        alive = self.alive_players()
        if len(alive) == 1:
            return alive[0].player_id
        if len(alive) == 0:
            return "nobody"
        return None

    def log_event(self, event_type, **kwargs):
        self.event_log.append({
            "round": self.current_round,
            "phase": self.current_phase,
            "type": event_type,
            **kwargs
        })

    @property
    def response_window(self):
        return self._response_window
