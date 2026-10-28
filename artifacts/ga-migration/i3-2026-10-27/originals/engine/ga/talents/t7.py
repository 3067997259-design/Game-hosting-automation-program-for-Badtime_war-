"""T7：全局唯一保险、后置死亡门与落幕后仍可发动的祝祷。"""

from __future__ import annotations

import math
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.contracts import DeathOutcome, Performance, TurnStage
from engine.ga.extensions import DeathGate, Replacement, chapter, high_impact
from engine.ga.state import LOCATIONS, touch
from engine.ga.status import heal
from engine.ga.talents.base import Talent, ability, own_slot

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec, ResolutionRecord
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant, GaPlayer


class T7(Talent):
    """保险不撤销既成伤害，不维修装备，不刷新资格或轮额度。"""

    talent_id = "T7"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.used = 0
        self.capacity = 1

    def public(self) -> Mapping[str, Any]:
        """兑现次数是永久消耗，不由死亡或复活初始化。"""
        return {"insurance_used": self.used, "insurance_capacity": self.capacity}

    def register(self, state: GameState) -> None:
        """保险门排在所有自身转折之后，持有人死亡不注销。"""
        super().register(state)
        self.capacity = state.ga_config.get("talents", "T7", "insurance_uses")
        state.ga_death_gates.append(DeathGate(100, self.insurance_preview, self.insurance_commit))

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """保单占T1；有保单或已落幕时开放一SP祝祷。"""
        if stage is not TurnStage.T1 or not own_slot(state, self.owner_id, grant):
            return ()
        targets = [key for key, p in state.players.items() if p.alive and p.home_locked]
        result = []
        if state.ga_insurance is None and self.used < self.capacity:
            result.extend(ability(grant, stage, "t7_insure", mode=mode, targets=(target,),
                                  after=self.insure) for target in targets for mode in
                          (Performance.INSTANT, Performance.PUBLIC))
        if state.ga_insurance is not None or self.used:
            for target in targets:
                player = state.players[target]
                if player.burns or player.statuses or player.healable and player.hp < player.max_hp:
                    result.append(ability(grant, stage, "t7_prayer", mode=Performance.INSTANT,
                                          targets=(target,), after=self.prayer))
        return tuple(result)

    def insure(self, state: GameState, plan: ActionPlan) -> None:
        """只挂保单，不提前消费死亡替代次数。"""
        state.ga_insurance = {"owner": self.owner_id, "target": plan.request.target_ids[0]}
        high_impact(state, self.owner_id, plan.request.root_id, plan.request.target_ids[0])
        chapter(state, self.owner_id, 1)
        touch(state)

    def prayer(self, state: GameState, plan: ActionPlan) -> None:
        """净化只清普通状态，不清案底或不可逆形态。"""
        player = state.players[plan.request.target_ids[0]]
        cleaned = bool(player.statuses or player.burns)
        healed = heal(player, state.ga_config.get("talents", "T7", "prayer_heal"))
        player.statuses.clear()
        player.burns.clear()
        if cleaned or healed:
            high_impact(state, self.owner_id, plan.request.root_id, plan.request.target_ids[0])
            chapter(state, self.owner_id, 2)
        touch(state)

    def insurance_preview(
        self, state: GameState, effect: EffectSpec, target: GaPlayer,
    ) -> Replacement | None:
        """不检查施术者是否仍活着；自身转折成功时不会轮到本门。"""
        policy = state.ga_insurance
        if not policy or policy["owner"] != self.owner_id or policy["target"] != target.player_id:
            return None
        if self.used >= self.capacity:
            return None
        hp = math.ceil(target.max_hp * state.ga_config.get("talents", "T7", "revive_hp_ratio"))
        return Replacement(DeathOutcome.RESURRECTED, hp, "insurance", {})

    def insurance_commit(
        self, state: GameState, effect: EffectSpec, target: GaPlayer, result: Replacement,
    ) -> None:
        """原地优先，医院其次，固定建筑再次；只兑现一个正HP替代。"""
        if target.location not in LOCATIONS or target.location in state.destroyed_locations:
            target.location = "hospital" if "hospital" not in state.destroyed_locations else next(
                loc for loc in LOCATIONS if loc not in state.destroyed_locations
            )
        target.sp = state.ga_config.get("talents", "T7", "revive_sp")
        target.statuses.clear()
        target.burns.clear()
        self.used += 1
        state.ga_insurance = None
        chapter(state, self.owner_id, 3)
        touch(state)

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """绝对死亡不赔付；既成保单不挂在已退场实体上。"""
        if state.ga_insurance and any(r.target_id == state.ga_insurance["target"] and
                                      r.death_outcome is DeathOutcome.ABSOLUTE_DEAD
                                      for r in records):
            state.ga_insurance = None

    def on_events(self, state: GameState, events: tuple[Any, ...]) -> None:
        """目标体面退场时关闭失去承接对象的保单，不虚构兑现。"""
        from engine.ga.events import EventKind
        policy = state.ga_insurance
        if policy and any(e.kind is EventKind.ACTOR_EXITED and e.actor_id == policy["target"]
                          for e in events):
            state.ga_insurance = None
