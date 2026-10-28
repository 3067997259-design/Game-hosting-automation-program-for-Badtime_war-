"""G2一体双标：共享肉身、键位裁决、世末之雨与永久炉心加速。"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.actions import GrantKind
from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    ActionVerb, DamageAttribute, DeathOutcome, Performance, TargetScope, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.extensions import (
    IncomingModifier, Replacement, chapter, high_impact, relocate, units,
)
from engine.ga.state import emit, finish_if_needed, record_death, touch
from engine.ga.status import heal, put_status
from engine.ga.talents.base import Talent, ability, barrier, enemies, own_slot, packet

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant, GaPlayer


class G2(Talent):
    """残影只是坐标，从不复制HP、背包、计分或行动授予。"""

    talent_id = "G2"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.control = "light"
        self.positions: dict[str, str] = {}
        self.loneliness = 2
        self.white = 0
        self.black = 0
        self.form = "dual"
        self.fused = False
        self.forced_move: str | None = None
        self.stay_once = False
        self.reactor_round = 0
        self.beats_seen: set[tuple[int, int, int]] = set()

    def public(self) -> Mapping[str, Any]:
        """两标坐标公开；只有主控坐标参与共同战斗实体。"""
        return {"form": self.form, "control": self.control, "positions": self.positions,
                "loneliness": self.loneliness, "white": self.white, "black": self.black}

    def register(self, state: GameState) -> None:
        """按实际时钟节拍记键，不另建回合，也不重复首R0初始化。"""
        super().register(state)
        self.loneliness = state.ga_config.get("talents", "G2", "loneliness_initial")
        self.positions = dict.fromkeys(("light", "shadow"), state.players[self.owner_id].location)
        state.ga_hooks.setdefault("clock_advanced", []).append(self.beat)
        state.ga_location_listeners.append(self.location_changed)
        state.ga_slot_gates.append(self.slot_gate)

    def beat(self, state: GameState) -> None:
        """双倍速跨两格记两键；时钟暂停一键不记。"""
        if self.form != "dual" or not state.players[self.owner_id].alive:
            return
        old, new = state.ga_previous_world_tick, state.world_tick
        key = state.current_round, old, new
        if key in self.beats_seen:
            return
        self.beats_seen.add(key)
        if self.loneliness <= state.ga_config.get("talents", "G2", "white_key_max_loneliness"):
            self.white += max(0, new - old)
        else:
            self.black += max(0, new - old)
        if self.white + self.black >= 6:
            chapter(state, self.owner_id, 2)

    def location_changed(self, state: GameState, actor: str) -> None:
        """首次出门归并私宅残影；废墟中的残影并回主控，不生成新单位。"""
        if actor != self.owner_id or self.form != "dual":
            return
        player = state.players[actor]
        self.positions[self.control] = player.location
        for key, location in tuple(self.positions.items()):
            if location in state.destroyed_locations or location.startswith("home:") and (
                player.home_locked
            ):
                self.positions[key] = player.location

    def slot_gate(self, state: GameState, grant: ActionGrant) -> bool:
        """炉心失去操控权，救回后自然恢复正常授予。"""
        return grant.actor_id != self.owner_id or self.form != "reactor"

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """双SP走强化即演，绝不报名全局公演。"""
        if not own_slot(state, self.owner_id, grant) or self.form == "reactor":
            return ()
        result = []
        if self.form == "dual":
            if stage is TurnStage.T0:
                other = "shadow" if self.control == "light" else "light"
                setup = ability(grant, stage, "g2_control", parameters={"control": other},
                                after=self.perform)
                if not state.players[self.owner_id].isolation_id or self.positions[other] == (
                    state.players[self.owner_id].location):
                    result.append(replace(setup, definition=replace(setup.definition,
                                                                   consumes_stage=False)))
            if stage is (TurnStage.T0 if self.control == "light" else TurnStage.T1):
                result.extend(ability(grant, stage, "g2_a" if self.control == "light" else "g2_b",
                                      mode=mode, after=self.perform)
                              for mode in (Performance.INSTANT, Performance.MAJOR_INSTANT))
        elif self.form == "rain" and stage is TurnStage.T1:
            targets = tuple(key for key in enemies(state, self.owner_id, local=False) if
                            key in state.players)
            if targets:
                candidate = ability(grant, stage, "g2_rain", mode=Performance.INSTANT,
                                    targets=targets, hostile=True)
                result.append(replace(candidate, effects=(packet(
                    state, candidate.request, state.ga_config.get("talents", "G2", "rain_damage"),
                    attribute=DamageAttribute.MAGIC, scope=TargetScope.GLOBAL_AREA, certain=True,
                ),)))
                result.extend(ability(grant, stage, "g2_audience", mode=Performance.MAJOR_INSTANT,
                                      targets=(target,), after=self.perform) for target in targets)
        return tuple(result)

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """光驻足后的强制离开只约束本槽；影加值不放大独立技能。"""
        if grant.actor_id != self.owner_id:
            return options
        result = []
        for option in options:
            if option.request.performance is Performance.PUBLIC:
                continue
            if self.forced_move == grant.grant_id and stage is TurnStage.T1 and (
                option.request.verb is not ActionVerb.MOVE
            ):
                continue
            if self.form == "echo" or self.form == "dual" and self.control == "shadow":
                if option.request.operation == "attack" or option.weapon_use:
                    option = replace(option, effects=tuple(replace(
                        e, amount=e.amount + state.ga_config.get("talents", "G2",
                                                                 "shadow_attack_bonus")
                    ) for e in option.effects))
            result.append(option)
        return tuple(result)

    def perform(self, state: GameState, plan: ActionPlan) -> None:
        """瞬移、真实治疗/屏障、短溺音和点名分别有明确权限。"""
        request = plan.request
        player = state.players[self.owner_id]
        cfg = state.ga_config.get("talents", "G2")
        enhanced = request.performance is Performance.MAJOR_INSTANT
        prefix = "enhanced_" if enhanced else ""
        if request.operation == "g2_control":
            self.control = request.parameters["control"]
            relocate(state, self.owner_id, self.positions[self.control])
            return
        if request.operation == "g2_a":
            relocate(state, self.owner_id, self.positions["shadow"])
            heal(player, cfg[f"a_{prefix}heal"])
            barrier(state, self.owner_id, f"g2/a/{self.owner_id}", cfg[f"a_{prefix}barrier"],
                    ticks=cfg["a_barrier_ticks"])
            self.loneliness = max(cfg["loneliness_min"], self.loneliness - cfg[
                f"a_{prefix}loneliness_reduction"])
            self.white += cfg["enhanced_performance_keys" if enhanced else "performance_keys"]
            if self.stay_once:
                self.stay_once = False
            else:
                self.forced_move = request.grant_id
        elif request.operation == "g2_b":
            self.loneliness = min(cfg["loneliness_cap"], self.loneliness + cfg[
                f"b_{prefix}loneliness_gain"])
            self.black += cfg["enhanced_performance_keys" if enhanced else "performance_keys"]
            for target in enemies(state, self.owner_id):
                if target in state.players:
                    if not state.ga_poems.can_harm(state, self.owner_id, target):
                        continue
                    state.ga_world.vulnerability[target] = cfg[f"b_{prefix}vulnerability"], (
                        state.current_round)
                    put_status(state.players[target], state.ga_config, state.current_round,
                               "DISORIENTED", source_id=request.root_id, actor_id=self.owner_id)
                else:
                    state.ga_world.submerged[self.owner_id, target] = state.current_round
            state.ga_world.sync(state)
        elif request.operation == "g2_audience":
            target = request.target_ids[0]
            if state.players[target].location != player.location:
                relocate(state, target, player.location)
            else:
                put_status(state.players[target], state.ga_config, state.current_round,
                           "ACTION_SUPPRESSED", source_id=request.root_id, actor_id=self.owner_id)
        high_impact(state, self.owner_id, request.root_id)
        chapter(state, self.owner_id, 1)
        if self.white + self.black >= 6:
            chapter(state, self.owner_id, 2)
        touch(state)

    def fuse(self, state: GameState, *, lethal: bool = False) -> None:
        """自身转折先建立正血量，再执行一次雨/炉心判决。"""
        if self.fused:
            return
        self.fused = True
        player = state.players[self.owner_id]
        cfg = state.ga_config.get("talents", "G2")
        if lethal:
            player.hp = cfg["lethal_fusion_hp"]
        self.positions = {"merged": player.location}
        if self.white >= self.black:
            self.form = "rain"
            state.ga_world.rain.add(self.owner_id)
            state.ga_world.sync(state)
        else:
            self.form = "reactor"
            for victim in state.players.values():
                if not victim.alive:
                    continue
                victim.hp = math.ceil(victim.hp * cfg["reactor_halving_ratio"])
                victim.max_hp = math.ceil(victim.max_hp * cfg["reactor_halving_ratio"])
                for key, defense in tuple(victim.defenses.items()):
                    if defense.kind.value == "armor":
                        victim.defenses[key] = replace(defense, durability=math.ceil(
                            defense.durability * cfg["reactor_halving_ratio"]))
            player.targetable = player.healable = False
            self.reactor_round = state.current_round
            state.ga_clock_accelerated = True
        chapter(state, self.owner_id, 3)
        touch(state)

    def after_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """光主控的真实打工收入加一，不从非法/失败菜单领取工资。"""
        if plan.request.actor_id == self.owner_id and plan.request.operation == "work" and (
            self.form == "dual" and self.control == "light"):
            state.players[self.owner_id].credits += state.ga_config.get(
                "talents", "G2", "light_work_bonus")

    def lethal(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> Replacement | None:
        """黄昏前让位保险，绝对致死由核心跳过所有自身替代。"""
        if target.player_id == self.owner_id and not self.fused and state.world_tick >= (
            state.ga_config.get("clock", "dusk_start")
        ):
            return Replacement(DeathOutcome.MORPHED, state.ga_config.get(
                "talents", "G2", "lethal_fusion_hp"), "g2_fusion", {})
        return None

    def commit_lethal(
        self, state: GameState, effect: EffectSpec, target: GaPlayer, result: Replacement,
    ) -> None:
        """普通致死只融合一次，不再向保险索取第二次替代。"""
        self.fuse(state, lethal=True)

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """追光救回后的雨幕是普通固定减伤，不能解除炉心法则。"""
        return IncomingModifier(reduction=state.ga_config.get("poems", "spotlight_reduction") if
                                target.player_id == self.owner_id and self.form == "echo" else 0)

    def boundary(self, state: GameState, *, boundary: str) -> None:
        """黄昏首个自身槽裁决；炉心未来R4自耗，雨死即停。"""
        player = state.players[self.owner_id]
        if boundary == "slot_start" and state.current_grant.actor_id == self.owner_id and (
            player.alive and not self.fused and state.world_tick >= state.ga_config.get(
                "clock", "dusk_start")
        ):
            self.fuse(state)
        if boundary == "R4.dot_maintenance" and self.form == "reactor" and player.alive and (
            state.current_round > self.reactor_round
        ):
            player.hp = max(0, player.hp - state.ga_config.get("talents", "G2", "reactor_hp_loss"))
            if not player.hp:
                record_death(state, self.owner_id, absolute=False)
                player.exited = True
                emit(state, EventKind.ACTOR_EXITED, actor_id=self.owner_id,
                     payload={"reason": "reactor_burnout"})
                finish_if_needed(state)
        if boundary == "R4.expiry":
            self.location_changed(state, self.owner_id)

    def receive_poem(self, state: GameState, giver: str) -> None:
        """追光不把已发生的炉心腰斩或永久倍速倒转。"""
        player = state.players[self.owner_id]
        if self.form == "reactor":
            self.form = "echo"
            player.targetable = player.healable = True
        elif self.form == "dual":
            self.loneliness = 0
            self.white += state.ga_config.get("poems", "spotlight_white_keys")
            self.stay_once = True
            if self.white + self.black >= 6:
                chapter(state, self.owner_id, 2)
        touch(state)
