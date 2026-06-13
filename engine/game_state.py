"""全局游戏状态（Phase 3 完整版）"""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.game_logger import GameLogger

from models.markers import MarkerManager
from models.police import PoliceData
from models.virus import VirusSystem
from engine.response_window import ResponseWindowManager
from typing import Optional

class GameState:
    def __init__(self):
        # 玩家
        self.players = {}
        self.player_order = []

        # 轮次
        self.current_round = 0
        self.current_phase = "not_started"
        self.max_rounds: Optional[int] = None   # 新增：None = 无限制

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
            "释放病毒",
        }
        # Phase 4: 朝阳好市民会添加更多
        self.active_barrier = None  # 神代3结界引用
        self.ish_bosheth = None    # G2 ish-bosheth 舞台结界实例

        # 响应窗口
        self._response_window = ResponseWindowManager(self)

        # 事件日志
        self.event_log = []

        # 每局确定性边界：重置跨局漂移的模块级计数器（M0 种子复现要求）
        from models.chorus import reset_chorus_counter
        reset_chorus_counter()

        # M4 弓模块全图供应池 + 箭堆（experiment: m4_gear）
        from engine import experiments as _exp
        if _exp.is_enabled("m4_gear"):
            from engine.bow_modules import init_supply
            init_supply(self)
            self.arrow_piles = {}
            self.hook_taken = False  # 钩锁神器全图唯一
            # M5 击杀掉落：地面物品（location → {credits, arrows, items, weapons}）
            self.ground_loot = {}

        # M6 评分制：谁对谁造成过伤害（反合谋三闸用）+ per-game 喝彩去重
        if _exp.is_enabled("m6_scoring"):
            self.damage_relations = {}      # victim_id → set(attacker_id)
            self._applause_events_used = set()  # per-game 喝彩事件去重
            self.final_scores = {}          # 终局揭晓的玩家终分

        # 游戏状态
        self.game_over = False
        self.winner: Optional[str] = None
        self.logger: Optional[GameLogger] = None  # 游戏日志记录器

    def record_combat_damage(self, attacker, victim, amount):
        """M6：回写战果分累计伤害 + 伤害关系（反合谋用）。仅玩家→玩家有效伤害。"""
        from engine import experiments
        if not experiments.is_enabled("m6_scoring"):
            return
        if amount <= 0 or attacker is None or victim is None:
            return
        if getattr(attacker, "is_chorus", False) or not hasattr(victim, "player_id"):
            return
        if not hasattr(attacker, "player_id"):
            return  # 警察等非玩家攻击者不计战果
        attacker.damage_dealt = getattr(attacker, "damage_dealt", 0) + int(amount)
        rel = self.damage_relations.setdefault(victim.player_id, set())
        rel.add(attacker.player_id)

    def drop_loot_on_death(self, player):
        """M5 击杀掉落（v2.0 §3/§6.4）：死者的 credits/箭/可掉落装备/模块掉到
        所在地点。白昼起阶段才启用。幂等——掉落后清空死者携带（轮末扫描重复
        调用无害）。死者 location 此时仍在（markers 只清标记）。"""
        from engine import experiments, world_clock
        if not experiments.is_enabled("m5_clock"):
            return
        if getattr(player, "_loot_dropped", False):
            return  # 幂等：轮末扫描重复调用只掉落一次
        if not world_clock.active_value(self, "kill_drop", default=False):
            return
        loc = getattr(player, "location", None)
        if loc is None:
            return
        player._loot_dropped = True
        pile = self.ground_loot.setdefault(
            loc, {"credits": 0, "arrows": 0, "items": [], "weapons": []})
        # 钱包
        credits = getattr(player, "credits", 0)
        if credits > 0:
            pile["credits"] += credits
            player.credits = 0
        # 箭
        arrows = getattr(player, "arrows", 0)
        if arrows > 0:
            pile["arrows"] += arrows
            player.arrows = 0
        # 非起始装备（弓/拳击不掉，避免基础装备污染地面）
        for w in list(getattr(player, "weapons", [])):
            if w and w.name not in ("拳击", "弓", "小刀"):
                pile["weapons"].append(w.name)
        # 可拾取物品（防毒面具/磨刀石等，钩锁神器回归"未取走"由 hook_taken 另管）
        for it in list(getattr(player, "items", [])):
            nm = getattr(it, "name", "")
            if nm and nm != "钩锁":
                pile["items"].append(nm)

    def add_player(self, player):
        self.players[player.player_id] = player
        self.player_order.append(player.player_id)
        self.markers.init_player(player.player_id)

    def get_player(self, player_id):
        return self.players.get(player_id)

    def register_chorus(self, unit):
        """注册 Chorus 单位到 players dict（不加入 player_order）。"""
        self.players[unit.player_id] = unit

    def unregister_chorus(self, unit_id):
        """从 players dict 移除 Chorus 单位。"""
        self.players.pop(unit_id, None)

    def alive_players(self):
        return [p for p in self.players.values() if p.is_alive()]

    def awake_alive_players(self):
        # TODO: Use this in D4 phase and other places that need awake+alive filtering
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

    @staticmethod
    def compute_default_max_rounds(player_count: int) -> int:
        """动态计算默认最大轮数：每人 50 轮"""
        return player_count * 50

    def is_max_rounds_reached(self) -> bool:
        """检查是否达到最大轮数限制"""
        return self.max_rounds is not None and self.current_round >= self.max_rounds

    def log_event(self, event_type, **kwargs):
        event = {
            "round": self.current_round,
            "phase": self.current_phase,
            "type": event_type,
            **kwargs
        }
        self.event_log.append(event)
        # M3 行动隐匿：事件 actor 对某观察者隐匿则不推送（event_log 本体
        # 始终存全量——golden 回放与日志不受广播过滤影响）
        actor = None
        from engine import experiments as _exp
        if _exp.is_enabled("m3_accuracy"):
            actor_id = kwargs.get("player") or kwargs.get("attacker")
            if isinstance(actor_id, str):
                actor = self.get_player(actor_id)
        # 广播事件到所有玩家控制器
        for pid in self.player_order:
            p = self.get_player(pid)
            if p and hasattr(p, 'controller') and p.controller:
                if actor is not None and pid != actor.player_id:
                    from engine.visibility import can_see
                    if not can_see(p, actor, self):
                        continue  # 行动隐匿：该观察者看不到此行动
                try:
                    p.controller.on_event(event)
                except Exception:
                    pass  # 不让控制器错误影响游戏流程

    def is_terror_alive(self):
        """场上是否存在存活的 Terror"""
        for pid in self.player_order:
            p = self.get_player(pid)
            if (p and p.is_alive() and p.talent
                    and hasattr(p.talent, 'is_terror') and p.talent.is_terror):
                return True
        return False

    @property
    def response_window(self):
        return self._response_window
