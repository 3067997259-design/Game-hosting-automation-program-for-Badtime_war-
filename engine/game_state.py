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
        from engine import experiments as _exp
        self.profile = _exp.current_profile()
        self.experiment_flags = frozenset(_exp.active())
        self.m9_enabled = "m9_rfc" in self.experiment_flags

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
        if _exp.is_enabled("m4_gear"):
            from engine.bow_modules import init_supply
            init_supply(self)
            self.arrow_piles = {}
            self.hook_taken = False  # 钩索神器全图唯一
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

        # profile → setup 的唯一入口：m9-rfc 即使无人选择天赋也必须拥有完整机制层。
        from engine.m9.gate import ensure_state_mechanisms
        ensure_state_mechanisms(self)

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
        self._drop_player_loot(player, loc)

    def drop_loot_on_retreat(self, player):
        """M9 退场遗留：不依赖白昼时钟，且与死亡掉落共享幂等信源。"""
        if not getattr(self, "m9_enabled", False):
            return
        if getattr(player, "_loot_dropped", False):
            return
        loc = getattr(player, "location", None)
        if loc is None:
            return
        self._drop_player_loot(player, loc)

    def drop_world_items_on_homecoming(self, player, location):
        """G5 归家/闭合的世界物品脱离。

        与死亡掉落不同，credits 属于共通身份而保留；箭、后天武器、
        护甲与物品仍留在离开地点。这是新肉身边界的运行时接线，
        不依赖 M5 白昼时钟。
        """
        if not getattr(self, "m9_enabled", False):
            return
        if getattr(player, "_loot_dropped", False) or location is None:
            return
        self._drop_player_loot(
            player, location, drop_credits=False,
            preserve_legacy_base_weapons=False)

    @staticmethod
    def _loot_entry(obj, kind, source_slot):
        """保留装备对象及原持有者槽位，供 G0 拾取时建立遗物身份。"""
        return {
            "name": getattr(obj, "name", str(obj)),
            "kind": kind,
            "source_slot": source_slot,
            "object": obj,
        }

    def _drop_player_loot(self, player, loc, *, drop_credits=True,
                          preserve_legacy_base_weapons=True):
        player._loot_dropped = True
        if not hasattr(self, "ground_loot"):
            self.ground_loot = {}
        m9_provenance = bool(getattr(self, "m9_enabled", False))
        empty_pile = {"credits": 0, "arrows": 0,
                      "items": [], "weapons": []}
        if m9_provenance:
            empty_pile["armor"] = []
        pile = self.ground_loot.setdefault(loc, empty_pile)
        if m9_provenance:
            pile.setdefault("armor", [])
        source_slot = getattr(player, "talent_slot_id", "") or ""
        # 钱包
        credits = getattr(player, "credits", 0)
        if drop_credits and credits > 0:
            pile["credits"] += credits
            player.credits = 0
        # 箭
        arrows = getattr(player, "arrows", 0)
        if arrows > 0:
            pile["arrows"] += arrows
            player.arrows = 0
        # 非起始装备（弓/拳击不掉，避免基础装备污染地面）
        protected_weapons = {"拳击", "弓"}
        if preserve_legacy_base_weapons:
            protected_weapons.add("小刀")
        for w in list(getattr(player, "weapons", [])):
            if w and w.name not in protected_weapons:
                if m9_provenance:
                    pile["weapons"].append(
                        self._loot_entry(w, "weapon", source_slot))
                    player.weapons.remove(w)
                else:
                    pile["weapons"].append(w.name)
        armor = getattr(player, "armor", None)
        if m9_provenance and armor is not None:
            for piece in list(getattr(armor, "outer", [])):
                if not getattr(piece, "is_broken", False):
                    pile["armor"].append(
                        self._loot_entry(piece, "armor", source_slot))
                armor.outer.remove(piece)
            for piece in list(getattr(armor, "inner", [])):
                if not getattr(piece, "is_broken", False):
                    pile["armor"].append(
                        self._loot_entry(piece, "armor", source_slot))
                armor.inner.remove(piece)
        # 可拾取物品（防毒面具/磨刀石等，钩索神器回归"未取走"由 hook_taken 另管）
        for it in list(getattr(player, "items", [])):
            nm = getattr(it, "name", "")
            if nm and nm != "钩索":
                if m9_provenance:
                    pile["items"].append(
                        self._loot_entry(it, "item", source_slot))
                    player.items.remove(it)
                else:
                    pile["items"].append(nm)

    def add_player(self, player):
        self.players[player.player_id] = player
        self.player_order.append(player.player_id)
        self.markers.init_player(player.player_id)
        m9 = getattr(self, "m9_system", None)
        if m9 is not None:
            m9.register_player(player.player_id)

    def get_player(self, player_id):
        player = self.players.get(player_id)
        if player is not None:
            return player
        return getattr(self, "m9_shadows", {}).get(player_id)

    def get_actor(self, actor_id):
        """按统一 id 查询玩家、影身、M9 警察或可指定的附属对象。"""
        actor = self.get_player(actor_id)
        if actor is not None:
            talent = getattr(actor, "talent", None)
            form = getattr(talent, "form", None)
            if (talent is not None
                    and getattr(talent, "is_retreated", lambda: False)()) or form in ("home", "past"):
                return None
            return actor

        police = getattr(self, "m9_police", None)
        if police is not None and hasattr(police, "get_unit"):
            unit_id = actor_id.removeprefix("police:")
            actor = police.get_unit(unit_id)
            if actor is not None:
                return actor

        for player in self.players.values():
            talent = getattr(player, "talent", None)
            if talent is None or not hasattr(talent, "get_auxiliary_actor"):
                continue
            actor = talent.get_auxiliary_actor(actor_id)
            if actor is not None:
                return actor
        return None

    def iter_actors(self):
        """稳定遍历有独立单位身份的 actor；不含无行动槽的 G0 无人机。"""
        yielded = set()
        for player_id in self.player_order:
            actor = self.players.get(player_id)
            talent = getattr(actor, "talent", None) if actor is not None else None
            form = getattr(talent, "form", None)
            if (actor is not None
                    and not (talent is not None
                             and getattr(talent, "is_retreated", lambda: False)())
                    and form not in ("home", "past")):
                yielded.add(player_id)
                yield actor
        for actor_id, actor in self.players.items():
            if actor_id not in yielded:
                talent = getattr(actor, "talent", None)
                form = getattr(talent, "form", None)
                if (talent is not None
                        and getattr(talent, "is_retreated", lambda: False)()):
                    continue
                if form in ("home", "past"):
                    continue
                yield actor
        for actor_id in sorted(getattr(self, "m9_shadows", {})):
            yield self.m9_shadows[actor_id]
        police = getattr(self, "m9_police", None)
        if police is not None and hasattr(police, "units"):
            for unit in sorted(police.units(), key=lambda item: item.unit_id):
                if unit.is_alive():
                    yield unit

    def iter_targetable_actors(self):
        """稳定遍历公共行动可指定的目标，附加无独立行动槽的召唤物。"""
        yield from self.iter_actors()
        auxiliary = []
        for player in self.players.values():
            talent = getattr(player, "talent", None)
            if talent is None or not hasattr(talent, "drone_actor"):
                continue
            actor = talent.drone_actor()
            if actor is not None and actor.is_alive():
                auxiliary.append(actor)
        yield from sorted(auxiliary, key=lambda item: item.player_id)

    def iter_action_actors(self, global_round=None):
        """返回本轮应取得标准槽的普通层 actor。"""
        round_num = self.current_round if global_round is None else global_round
        for player_id in self.player_order:
            player = self.players.get(player_id)
            if player is None or not player.is_alive():
                continue
            talent = getattr(player, "talent", None)
            if (talent is not None
                    and getattr(talent, "is_retreated", lambda: False)()):
                continue
            form = getattr(getattr(player, "talent", None), "form", None)
            if form in ("home", "past"):
                continue
            yield player
        for actor_id in sorted(getattr(self, "m9_shadows", {})):
            shadow = self.m9_shadows[actor_id]
            if not shadow.is_alive() or shadow.is_terminal_singer:
                continue
            if getattr(shadow, "created_round", round_num) >= round_num:
                continue
            yield shadow

    def attention_owner_id(self, actor_id):
        """影身与光身共享玩家级 SP/关注；其他 actor 使用自身 id。"""
        actor = self.get_actor(actor_id)
        return getattr(actor, "owner_pid", actor_id) if actor is not None else actor_id

    def register_chorus(self, unit):
        """注册 Chorus 单位到 players dict（不加入 player_order）。"""
        self.players[unit.player_id] = unit

    def unregister_chorus(self, unit_id):
        """从 players dict 移除 Chorus 单位。"""
        self.players.pop(unit_id, None)

    def alive_players(self):
        return [p for p in self.players.values()
                if p.is_alive()
                and not (getattr(p, "talent", None) is not None
                         and getattr(p.talent, "is_retreated", lambda: False)())]

    def awake_alive_players(self):
        # TODO: Use this in D4 phase and other places that need awake+alive filtering
        return [p for p in self.alive_players() if p.is_awake]

    def players_at_location(self, location):
        return [p for p in self.alive_players() if p.location == location]

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
        self._record_m9_attention(event_type, kwargs)
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

    def _record_m9_attention(self, event_type, payload):
        """把已进入结算的公开高影响事件映射为玩家级关注。"""
        m9 = getattr(self, "m9_system", None)
        if m9 is None:
            return
        # G1 卸甲免费 find（R0 自动、不占行动槽）不触发关注/SP
        initiator = payload.get("player") or payload.get("attacker")
        if isinstance(initiator, str):
            owner = self.players.get(self.attention_owner_id(initiator))
            if owner is not None and getattr(
                    owner, "_m9_suppress_attention", False):
                return
        role_fields = {
            "attack": ("target", "attacker"),
            "shoot": ("target", "attacker"),
            "opportunity_attack": ("target", "attacker"),
            "find": ("target", "player"),
            "lock": ("target", "player"),
            "hook": ("target", "player"),
        }.get(event_type)
        if role_fields is None:
            return
        seen = set()
        for index, field in enumerate(role_fields):
            actor_id = payload.get(field)
            if not isinstance(actor_id, str):
                continue
            owner_id = self.attention_owner_id(actor_id)
            if owner_id in seen:
                continue
            seen.add(owner_id)
            owner = self.players.get(owner_id)
            if owner is None or not owner.is_alive():
                continue
            form = getattr(getattr(owner, "talent", None), "form", None)
            if form in ("home", "past"):
                continue
            is_initiator = index == 1
            if is_initiator and m9.performance_actor_id == owner_id:
                continue
            m9.mark_attention(self.current_round, owner_id)

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
