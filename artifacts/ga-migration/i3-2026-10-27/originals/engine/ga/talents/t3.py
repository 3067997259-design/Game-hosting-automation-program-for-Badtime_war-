"""T3：玉璋有限屏障、纯磨甲震波与原地天星石化。"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from engine.ga.combat import resolve_batch
from engine.ga.contracts import DamageAttribute, DamageKind, Performance, TargetScope, TurnStage
from engine.ga.extensions import chapter, high_impact, units
from engine.ga.talents.base import Talent, ability, barrier, enemies, own_slot, packet

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, ResolutionRecord
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant


class T3(Talent):
    """玉璋不是破碎整击特权盾；天星不是直达伤害。"""

    talent_id = "T3"

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """准备型即演占T0，公演天星占T1。"""
        if not own_slot(state, self.owner_id, grant):
            return ()
        if stage is TurnStage.T0:
            return (ability(grant, stage, "t3_jade", mode=Performance.INSTANT,
                            after=self.jade),)
        targets = enemies(state, self.owner_id)
        if stage is TurnStage.T1 and targets:
            candidate = ability(grant, stage, "t3_starfall", mode=Performance.PUBLIC,
                                targets=targets, hostile=True)
            cfg = state.ga_config.get("talents", "T3")
            return (replace(candidate, effects=(packet(
                state, candidate.request, cfg["starfall_damage"],
                attribute=DamageAttribute.UNATTRIBUTED,
                certain=True, scope=TargetScope.LOCAL_AREA,
                defense_coefficient=cfg["defense_coefficient"],
                statuses=("PETRIFIED",),
            ),)),)
        return ()

    def jade(self, state: GameState, plan: ActionPlan) -> None:
        """随机敌人无甲时只磨甲无效，自己的屏障仍正常建立。"""
        cfg = state.ga_config.get("talents", "T3")
        barrier(state, self.owner_id, f"t3/jade/{self.owner_id}", cfg["barrier"],
                ticks=cfg["barrier_ticks"])
        high_impact(state, self.owner_id, plan.request.root_id)
        targets = enemies(state, self.owner_id)
        if targets:
            index = state.ga_rng.die(f"{plan.request.root_id}/jade-target", len(targets),
                                     actor_id=self.owner_id) - 1
            resolve_batch(state, (packet(state, plan.request, cfg["shockwave_wear"],
                                         targets=(targets[index],), kind=DamageKind.PURE_WEAR,
                                         source="t3_jade", suffix="wear"),))

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """承伤/石化/后续破甲三条真实战斗路径。"""
        for record in records:
            if any(c.defense_id == f"t3/jade/{self.owner_id}" and c.absorbed > 0 for c in
                   record.barrier_changes):
                chapter(state, self.owner_id, 1)
            if record.effect.actor_id != self.owner_id:
                continue
            if record.effect.source_id == "t3_starfall" and "PETRIFIED" in record.applied_statuses:
                if units(state)[record.target_id].alive:
                    chapter(state, self.owner_id, 2)
            previous = state.ga_record_context.get(record.resolution_id, {})
            if previous.get("control") == "PETRIFIED" and previous.get("control_actor") == (
                self.owner_id
            ) and (any(c.broken for c in record.armor_changes) or record.death_outcome.value in (
                "dead", "absolute_dead"
            )):
                chapter(state, self.owner_id, 3)
