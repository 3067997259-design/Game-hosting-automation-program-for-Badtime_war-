"""GA常驻法则与短窗口：时钟、NPC停机、雨幕和公开资格。"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from engine.ga.contracts import DamageKind
from engine.ga.extensions import IncomingModifier, units

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import EffectSpec
    from engine.ga.events import DomainEvent
    from engine.ga.state import GaPlayer


class World:
    """只维护法则来源；玩家HP、NPC编制与实际轮仍归原实体。"""

    def __init__(self) -> None:
        self.rain: set[str] = set()
        self.reversers: set[str] = set()
        self.submerged: dict[tuple[str, str], int] = {}
        self.vulnerability: dict[str, tuple[int, int]] = {}

    def register(self, state: GameState) -> None:
        """时钟改变之前提供唯一法则接缝，不能在R4多推进刻度。"""
        state.ga_world = self
        state.ga_hooks.setdefault("before_clock", []).append(self.before_clock)
        state.ga_hooks.setdefault("R4.expiry", []).append(self.expire)
        state.ga_event_listeners.append(self.on_events)
        state.ga_effect_filters.append(self.effects)
        state.ga_attack_pool_hooks.append(self.pool_bonus)
        state.ga_incoming_modifiers.append(self.incoming)

    def sync(self, state: GameState) -> None:
        """消除已死来源；停止法则不复活NPC，不解除其他来源。"""
        self.rain.intersection_update(a for a, p in state.players.items() if p.alive)
        old = bool(self.reversers)
        self.reversers.intersection_update(a for a, p in state.players.items() if p.alive)
        if old and not self.reversers:
            state.ga_clock_paused = False
        if hasattr(state, "ga_police"):
            for name, active in (("rain", bool(self.rain)), ("reversal", bool(self.reversers))):
                if active:
                    state.ga_police.stopped.add(name)
                else:
                    state.ga_police.stopped.discard(name)
        for actor, npc in state.ga_units.items():
            for key in tuple(npc.submerged_sources):
                if key.startswith("world/"):
                    npc.submerged_sources.discard(key)
            if self.rain:
                npc.submerged_sources.add("world/rain")
            if self.reversers:
                npc.submerged_sources.add("world/reversal")
            for (provider, target), expires in self.submerged.items():
                if target == actor:
                    npc.submerged_sources.add(f"world/local/{provider}/{expires}")

    def before_clock(self, state: GameState) -> None:
        """有效倒流来源负责指针；多个来源不重复推进公共世界时钟。"""
        self.sync(state)
        if self.reversers:
            state.ga_clock_paused = True
            owner = sorted(self.reversers)[0]
            state.ga_roles[owner].reverse_step(state)

    def begin_reversal(self, state: GameState, owner: str) -> None:
        """仅在下一R0兑现升格，不吞掉升格轮的其他槽。"""
        self.reversers.add(owner)
        state.ga_clock_paused = True
        self.sync(state)

    def end_reversal(self, state: GameState, owner: str) -> None:
        """解除本来源，已发生炉心加速仍留在GameState。"""
        self.reversers.discard(owner)
        if not self.reversers:
            state.ga_clock_paused = False
        self.sync(state)

    def expire(self, state: GameState) -> None:
        """溺音的明文短窗口在建立轮R4关闭，不等待额外世界刻度。"""
        self.submerged = {key: expiry for key, expiry in self.submerged.items()
                           if expiry > state.current_round}
        self.vulnerability = {key: value for key, value in self.vulnerability.items()
                              if value[1] > state.current_round}
        self.sync(state)

    def on_events(self, state: GameState, events: tuple[DomainEvent, ...]) -> None:
        """真实退场及新NPC出现后立即同步法则。"""
        self.sync(state)

    def effects(self, state: GameState, effects: tuple[EffectSpec, ...]) -> tuple[EffectSpec, ...]:
        """雨只强化实际普通攻击，不放大固定总池、DOT或独立技能。"""
        self.sync(state)
        result = []
        for effect in effects:
            meta = state.ga_action_meta.get(effect.root_id, {})
            if self.rain and meta.get("normal_attack") and not effect.parameters.get(
                "fixed_pool") and (
                effect.damage_kind is DamageKind.NORMAL):
                effect = replace(effect, amount=effect.amount + state.ga_config.get(
                    "talents", "G2", "rain_attack_bonus"))
            result.append(effect)
        return tuple(result)

    def pool_bonus(self, state: GameState, actor: str, amount: int) -> int:
        """分弹丸的普通枪先在总池加一次雨幕，再标为已分配固定池。"""
        if self.rain:
            return amount + state.ga_config.get("talents", "G2", "rain_attack_bonus")
        return amount

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """短窗口易伤不复制普通状态；普通甲仍按新增减免付费。"""
        value = self.vulnerability.get(target.player_id, (0, 0))[0]
        return IncomingModifier(vulnerability=value)
