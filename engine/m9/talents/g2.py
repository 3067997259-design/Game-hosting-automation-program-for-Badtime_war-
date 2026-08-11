"""M9 G2 光影双身天赋（profile: m9-rfc，G2 合同 v0.3）。

- ShadowActor：影身代理 actor（位置/HP/装备/状态/标准槽），owner=G2；
  玩家级资源（SP/PP/credits/评分）单一，归 player_id=G2；
- 创建：T0 即演（1 SP，占光身标准槽）或公演（2 SP，需公演位）；
  shadow_creation_eligible 开局 true；终曲承诺后永久 false（不可逆转）；
- 消散：影身 HP→0 → SHADOW_DISSIPATED（非玩家死亡：无 T7/无往世层/无击杀），
  至多 return_item_count 件合法实物归还光身，其余在影身地点掉落；
- 终曲承诺（公演 2 SP + 公演位）：资格永久锁定 → 影身转 TERMINAL_SINGER
  （无槽、不能行动、完整 unit actor）→ 建立终曲区域（歌者所在地）：
  全员易伤 / 伤害共享（shared_post_mitigation，总量守恒）/ 一次压制 /
  概率移动偏转（后两者机制类实现，引擎挂接随剧本阶段）；
- 听众 tick：R4 区域有 ≥1 非 G2 存活单位 → witnessed_ticks+1，
  首次达 terminal_witness_ticks → arc_count+1 + PP。
数值一律读 `m9_talents_extended.g2.*`（[待风洞]）。
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional

from engine.balance import get as bget
from engine.m9.talents.stub import M9TalentStub


def _g2(key: str, default):
    return bget("m9_talents_extended", "g2", key, default=default)


def shadow_actor_id(g2_pid: str) -> str:
    return f"G2:shadow@{g2_pid}"


def is_shadow_id(actor_id: str) -> bool:
    return isinstance(actor_id, str) and actor_id.startswith("G2:shadow@")


class ShadowActor:
    """影身代理 actor：完整单位（位置/HP/装备/状态），非第二玩家。"""

    def __init__(self, owner_pid: str, location: str, hp: float,
                 controller: Any) -> None:
        self.actor_id = shadow_actor_id(owner_pid)
        self.player_id = self.actor_id
        self.owner_pid = owner_pid
        self.location = location
        self.hp = hp
        self.max_hp = hp
        self.is_awake = True
        self.is_chorus = False
        self.weapons: List[Any] = []
        self.armor = None
        self.held_items: List[Any] = []      # 跨地点持有实物
        self.last_action_type = ""
        self.acted_this_round = False
        self.talent = None                   # 影身不使用玩家天赋
        self._m9_shadow_actor = True
        self.controller = controller         # 桥接到 G2 控制器
        self.is_terminal_singer = False

    def is_alive(self) -> bool:
        return self.hp > 0

    def get_weapon(self, name: str) -> Optional[Any]:
        for w in self.weapons:
            if w and getattr(w, "name", "") == name:
                return w
        return None


class TerminalArea:
    """终曲区域（合同 §八）：歌者所在地的共享效果状态。"""

    def __init__(self, g2_pid: str, location: str) -> None:
        self.g2_pid = g2_pid
        self.location = location
        self.suppression_uses = int(_g2("terminal_suppression_uses", 1))
        self.witnessed_ticks = 0
        self.arc_granted = False

    def vulnerability(self) -> int:
        return int(_g2("terminal_vulnerability", 1))

    def damage_share_ratio(self) -> float:
        return float(_g2("terminal_damage_share_ratio", 1.0))

    def move_redirect_chance(self) -> float:
        return float(_g2("terminal_move_redirect_chance", 0.5))

    def suppression_used(self) -> bool:
        return self.suppression_uses <= 0


class Hologram9(M9TalentStub):
    """M9 G2（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。"""

    name = "神代天赋-请一直注视着我"

    def __init__(self, player_id: str, game_state: Any) -> None:
        self.player_id = player_id
        self.state = game_state
        self.shadow_creation_eligible = True
        self.current_shadow_id: Optional[str] = None
        self.terminal_area: Optional[TerminalArea] = None

    def on_round_start(self, *a, **k):
        return None

    def get_t0_option(self, player: Any) -> Optional[dict]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        sp = m9.get_sp(self.player_id)
        shadow = self._shadow()
        options = []
        if self.shadow_creation_eligible and shadow is None and sp >= 1:
            options.append("创建影身（即演 1 SP）")
        if self.shadow_creation_eligible and shadow is None and sp >= 2:
            options.append("创建影身（公演 2 SP）")
        if shadow is not None and not shadow.is_terminal_singer and sp >= 2:
            options.append("世末终曲承诺（公演 2 SP，永久锁死再造资格）")
        if not options:
            return None
        return {"name": "光影双身", "description": "；".join(options),
                "m9_kind": "g2_dualbodies"}

    def execute_t0(self, player: Any):
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return "❌ M9 天赋未启用", False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return "❌ M9 机制未挂载", False
        round_num = getattr(self.state, "current_round", 1)
        ctrl = getattr(player, "controller", None)
        shadow = self._shadow()
        if shadow is None and self.shadow_creation_eligible:
            try:
                want = ctrl.choose("创建影身：", ["创建影身（即演 1 SP）",
                                                 "创建影身（公演 2 SP）"])
            except Exception:
                want = "创建影身（即演 1 SP）"
            if "即演" in want:
                if m9.dispatch_improvise(self.player_id, round_num) is None:
                    return "❌ SP 不足，创建取消", False
            else:
                if not self._ensure_public_seat(player, m9, round_num):
                    return "❌ SP/公演位不足", False
            self._create_shadow(player)
            return f"🌫️ {player.name} 创建影身（{_g2('shadow_hp', 8)} HP）！", True
        if shadow is not None and not shadow.is_terminal_singer:
            if m9.get_sp(self.player_id) < 2:
                return "❌ SP 不足，终曲承诺取消", False
            if not self._ensure_public_seat(player, m9, round_num):
                return "❌ SP/公演位不足", False
            self._commit_terminal(player, shadow)
            return (f"🎵 {player.name} 世末终曲承诺！影身转为终曲歌者，"
                    f"再造资格永久锁定。"), True
        return "❌ 条件不满足", False

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        if m9.assign_public_slot(round_num) != player.player_id:
            if not m9.register_performance(player.player_id, round_num):
                return False
        return m9.dispatch_public(player.player_id, round_num) is not None

    # ── 影身生命周期 ──

    def _create_shadow(self, player: Any) -> ShadowActor:
        m9 = getattr(self.state, "m9_system", None)
        shadows = getattr(self.state, "m9_shadows", {})
        actor = ShadowActor(
            self.player_id,
            getattr(player, "location", "home"),
            float(_g2("shadow_hp", 8)),
            getattr(player, "controller", None),
        )
        shadows[actor.actor_id] = actor
        self.current_shadow_id = actor.actor_id
        self.state.log_event("SHADOW_CREATED", player=self.player_id,
                             actor=actor.actor_id)
        return actor

    def _shadow(self) -> Optional[ShadowActor]:
        shadows = getattr(self.state, "m9_shadows", {})
        if self.current_shadow_id:
            return shadows.get(self.current_shadow_id)
        return None

    def dissipate(self, actor: ShadowActor, reason: str = "hp_zero") -> None:
        """消散：至多 return_item_count 件合法实物归还光身，其余掉落；
        非玩家死亡（无 T7/往世层/击杀）。"""
        shadows = getattr(self.state, "m9_shadows", {})
        shadows.pop(actor.actor_id, None)
        if self.current_shadow_id == actor.actor_id:
            self.current_shadow_id = None
        owner = self.state.get_player(self.player_id)
        if owner is not None and owner.is_alive():
            limit = int(_g2("return_item_count", 1))
            for item in actor.held_items[:limit]:
                owner.items.append(item)
        self.state.log_event("SHADOW_DISSIPATED", player=self.player_id,
                             reason=reason)

    def m9_on_lethal(self, target: Any, attacker: Any,
                     source_kind: Optional[str]) -> Optional[str]:
        """影身致死 → 消散（非玩家死亡）；终曲歌者同理（区域随之结束）。"""
        if getattr(target, "_m9_shadow_actor", False):
            self.dissipate(target)
            if self.terminal_area is not None \
                    and target.is_terminal_singer:
                self._end_terminal_area()
            return "g2_shadow_dissipated"
        return None

    # ── 终曲 ──

    def _commit_terminal(self, player: Any, shadow: ShadowActor) -> None:
        """终曲承诺：先永久锁死资格，再转歌者并建立区域（不可逆）。"""
        self.shadow_creation_eligible = False
        shadow.is_terminal_singer = True
        self.terminal_area = TerminalArea(self.player_id,
                                          shadow.location)
        self.state.log_event("TERMINAL_SONG_COMMITTED", player=self.player_id,
                             actor=shadow.actor_id)

    def _end_terminal_area(self) -> None:
        self.terminal_area = None
        self.state.log_event("TERMINAL_SONG_ENDED", player=self.player_id)

    def on_round_end(self, round_num: int) -> None:
        """听众 tick：区域有 ≥1 非 G2 存活单位 → +1；首次达标 → arc+1 + PP。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return None
        area = self.terminal_area
        shadow = self._shadow()
        if area is None or shadow is None:
            return None
        area.location = shadow.location  # 区域跟随歌者
        has_listener = False
        for pid in self.state.player_order:
            if pid == self.player_id:
                continue
            p = self.state.get_player(pid)
            if (p and p.is_alive()
                    and getattr(p, "location", None) == area.location):
                has_listener = True
                break
        if has_listener:
            area.witnessed_ticks += 1
            if (not area.arc_granted
                    and area.witnessed_ticks >= int(_g2("terminal_witness_ticks", 3))):
                area.arc_granted = True
                pp = getattr(self.state, "m9_pp", None)
                if pp is not None:
                    pp.earn(self.player_id, int(pp and 1))
                self.state.log_event("g2_last_song_heard",
                                     player=self.player_id)
        return None

    # ── 终曲区域效果挂载（combat 层调用）──

    def area_for_target(self, target: Any) -> Optional[TerminalArea]:
        """目标是否在任一终曲区域内。"""
        if self.terminal_area is None:
            return None
        loc = getattr(target, "location", None)
        if loc != self.terminal_area.location:
            return None
        return self.terminal_area

    def suppress_grant(self, actor_id: str, m9: Any) -> bool:
        """一次压制：对区域内非 G2 actor 的已授实际 grant 消费（预检先于消费）。"""
        area = self.terminal_area
        shadow = self._shadow()
        if area is None or shadow is None:
            return False
        if area.suppression_used():
            return False
        if actor_id == self.player_id:
            return False
        actor = self.state.get_player(actor_id)
        if actor is None or getattr(actor, "location", None) != area.location:
            return False  # 目标必须仍在区域
        area.suppression_uses -= 1
        return True


def shadow_actor_for(game_state: Any, actor_id: str) -> Optional[ShadowActor]:
    """R3 队列解析：G2:shadow@xxx → 影身 actor。"""
    if not is_shadow_id(actor_id):
        return None
    shadows = getattr(game_state, "m9_shadows", {})
    return shadows.get(actor_id)


def terminal_area_for(game_state: Any, location: str) -> Optional[TerminalArea]:
    """指定地点的终曲区域（若存在）。"""
    for pid in getattr(game_state, "player_order", []):
        p = game_state.get_player(pid)
        if p is None or p.talent is None:
            continue
        talent = p.talent
        if hasattr(talent, "terminal_area") and talent.terminal_area is not None:
            area = talent.terminal_area
            shadow = talent._shadow() if hasattr(talent, "_shadow") else None
            if shadow is not None and shadow.location == location:
                return area
    return None


def terminal_move_redirect(game_state: Any, player: Any, destination: str,
                           rng: Any = None) -> Optional[str]:
    """概率移动偏转（合同 G2 §8.4）：区域内 actor 根 move 离开歌者位置时，
    以 terminal_move_redirect_chance 把目的地改回歌者位置（槽消费、无成功离开）。
    强制位移/钩索/传送不适用（仅根 move 由引擎挂点调用本函数）。"""
    from engine.balance import get as bget
    rng = rng or random.random
    area = terminal_area_for(game_state, getattr(player, "location", None))
    if area is None:
        return None
    shadow = None
    for pid in getattr(game_state, "player_order", []):
        p = game_state.get_player(pid)
        if p and p.talent and hasattr(p.talent, "_shadow"):
            s = p.talent._shadow()
            if s is not None and s.is_terminal_singer:
                shadow = s
                break
    if shadow is None or destination == shadow.location:
        return None
    chance = float(bget("m9_talents_extended", "g2",
                        "terminal_move_redirect_chance", default=0.5))
    if rng() < chance:
        game_state.log_event("MOVE_REDIRECTED", player=player.player_id,
                             to=shadow.location)
        return shadow.location
    return None
