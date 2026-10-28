"""T6朝阳好市民：热线/真实编队整备与无需警务的个人章节。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.ga.contracts import Performance, TurnStage
from engine.ga.extensions import ask, chapter, high_impact
from engine.ga.police import Police
from engine.ga.talents.base import Talent, ability, own_slot
from engine.ga.state import touch

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, ResolutionRecord
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant


class T6(Talent):
    """没有新增伤害演出；停机时靠普通武器照样能完成三章。"""

    talent_id = "T6"

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """热线只对真实案件，整备不凭空创建第二支编队。"""
        if not own_slot(state, self.owner_id, grant):
            return ()
        police = Police()
        if stage is TurnStage.T0:
            return tuple(ability(grant, stage, "t6_hotline", targets=(offender,),
                                 after=self.hotline)
                         for offender in sorted({c["actor_id"] for c in state.ga_crimes}) if
                         offender != self.owner_id and offender not in state.ga_police.wanted and
                         state.players[offender].alive)
        if stage is not TurnStage.T1:
            return ()
        p = state.ga_police
        if "police" in state.destroyed_locations or p.stopped or state.ga_clock_paused:
            return ()
        kinds = []
        if police.available(state) and not p.attack_upgrade:
            kinds.append("attack")
        if police.available(state) and not p.wall_upgrade:
            kinds.append("wall")
        if len(police.live(state)) < state.ga_config.get("police", "roster_cap"):
            kinds.append("reinforce")
        return tuple(ability(grant, stage, "t6_supply", mode=mode,
                             parameters={"kind": kind, "deployment": deploy}, after=self.supply)
                     for kind in kinds for mode in (Performance.INSTANT, Performance.PUBLIC)
                     for deploy in (("none", "volley", "wall") if mode is Performance.PUBLIC and
                                    p.captain is None else ("none",)))

    def hotline(self, state: GameState, plan: ActionPlan) -> None:
        """警务停机仍能依法登记通缉，不能把无辜人变成合法目标。"""
        if Police().mark_wanted(state, plan.request.target_ids[0]):
            high_impact(state, self.owner_id, plan.request.root_id, plan.request.target_ids[0])
            chapter(state, self.owner_id, 1)

    def supply(self, state: GameState, plan: ActionPlan) -> None:
        """即时与R4齐射共用额度，升级不反复修满盾墙。"""
        police = Police()
        if police.upgrade(state, plan.request.parameters["kind"]):
            high_impact(state, self.owner_id, plan.request.root_id)
            chapter(state, self.owner_id, 2)
        deployment = plan.request.parameters["deployment"]
        if deployment != "none" and not state.ga_police.captain:
            state.ga_police.location = state.players[self.owner_id].location
            for key in police.live(state):
                state.ga_units[key].location = state.ga_police.location
            if deployment == "volley":
                police.fire(state, owner=self.owner_id)
            else:
                state.ga_police.guard_id = self.owner_id
                state.ga_police.stance = "wall"
                state.ga_police.immobile_round = state.current_round
                police.sync(state)
        touch(state)

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """个人攻击/破甲/击杀与执法击杀都走真实账本。"""
        for record in records:
            effect = record.effect
            if effect.actor_id == self.owner_id and record.effective_hit:
                chapter(state, self.owner_id, 1)
            if effect.actor_id == self.owner_id and any(c.broken for c in record.armor_changes):
                chapter(state, self.owner_id, 2)
            if effect.score_owner_id == self.owner_id and record.target_id in state.players and (
                record.death_outcome.value in ("dead", "absolute_dead")
            ):
                chapter(state, self.owner_id, 3)

    def receive_poem(self, state: GameState, giver: str) -> None:
        """律法重建同一编队，不能冲掉雨/倒流/Terror的独立停机来源。"""
        police = state.ga_police
        state.destroyed_locations.discard("police")
        police.captain = None
        police.guard_id = self.owner_id
        police.location = state.players[self.owner_id].location
        for player in state.players.values():
            player.defenses.pop("police/wall", None)
        cfg = state.ga_config.get("police")
        for key in police.officer_ids:
            npc = state.ga_units[key]
            npc.hp = npc.max_hp = state.ga_config.get("poems", "law_officer_hp")
            npc.absolute_dead = npc.exited = False
            npc.statuses.clear()
            npc.burns.clear()
            npc.location = police.location
            state.ga_death_cleaned.discard(key)
        police.attack_upgrade = cfg["attack_upgrade_cap"]
        police.wall_upgrade = cfg["wall_upgrade_cap"]
        police.wall = len(police.officer_ids) * (cfg["wall_per_officer"] +
                                                cfg["wall_upgrade_per_officer"])
        state.ga_world.sync(state)
        selected = ask(state, giver, self.owner_id, ("volley", "wall"), kind="poem_law")
        if selected == "volley":
            police.stance = "mobile"
            Police().fire(state, owner=self.owner_id)
        else:
            police.stance = "wall"
            police.immobile_round = state.current_round
        Police().sync(state)
        touch(state)
