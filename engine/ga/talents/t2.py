"""T2剪刀手：未杀免罪、同名修甲、原地/追近演出与有限暗影步。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.contracts import DeathOutcome, Performance, TurnStage
from engine.ga.extensions import ask, chapter, units
from engine.ga.talents.base import Talent, attack_choices, own_slot, weapon_option
from engine.ga.state import touch

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, ResolutionRecord
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant


class T2(Talent):
    """反应只移动与锁定，不制造T0/T2或额外槽。"""

    talent_id = "T2"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.free_shadow = False
        self.armor_hits = 0
        self.shadow_uses = 0
        self.shadow_lock: str | None = None
        self.performance_rounds: set[int] = set()

    def public(self) -> Mapping[str, Any]:
        """有限资格可见。"""
        return {"shadow_uses": self.shadow_uses, "armor_hits": self.armor_hits,
                "performance_rounds": sorted(self.performance_rounds)}

    def register(self, state: GameState) -> None:
        """免罪只豁免本人的非致死玩家伤害。"""
        super().register(state)
        state.ga_case_filters.append(self.case_allowed)
        state.players[self.owner_id].retain_stealth_attacks = True

    def case_allowed(self, state: GameState, record: ResolutionRecord) -> bool:
        """致死仍写合法杀戮案底。"""
        return record.effect.crime_owner_id != self.owner_id or record.death_outcome in (
            DeathOutcome.DEAD, DeathOutcome.ABSOLUTE_DEAD
        )

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """公演可以追到远处目标，普通即演仍要求原地。"""
        if stage is not TurnStage.T1 or not own_slot(state, self.owner_id, grant):
            return ()
        result = []
        for original in attack_choices(state, grant, chase=True):
            local = units(state)[original.request.target_ids[0]].location == state.players[
                self.owner_id
            ].location
            for mode in (Performance.INSTANT, Performance.PUBLIC):
                if mode is Performance.INSTANT and not local:
                    continue
                result.append(weapon_option(state, grant, original, "t2_strike", mode,
                                            bonus=state.ga_config.get("talents", "T2",
                                                                      "performance_bonus")))
        return tuple(result)

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """暗影步锁定只作用于相应目标，不借出额外小动作。"""
        if grant.actor_id != self.owner_id or self.shadow_lock is None:
            return options
        result = []
        for option in options:
            effects = tuple(replace(e, parameters={
                **dict(e.parameters), "ignore_move_evasion": True,
                "hit_bonus": e.parameters.get("hit_bonus", 0) + state.ga_config.get(
                    "accuracy", "lock_bonus"),
                "defense_coefficient": min(e.parameters.get("defense_coefficient", 1),
                                            state.ga_config.get("accuracy",
                                                                "lock_defense_coefficient")),
            }) if self.shadow_lock in e.target_ids else e for e in option.effects)
            result.append(replace(option, effects=effects))
        return tuple(result)

    def before_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """公演追近属于同根，不另发move行动或暗影反应。"""
        request = plan.request
        if request.actor_id == self.owner_id and request.operation == "t2_strike" and (
            request.performance is Performance.PUBLIC
        ):
            state.players[self.owner_id].location = units(state)[request.target_ids[0]].location

    def after_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """目标主动离开时，明确选择是否支付一次有限跟随。"""
        request = plan.request
        player = state.players[self.owner_id]
        if request.actor_id == self.owner_id:
            if request.verb.value == "attack":
                self.shadow_lock = None
            return
        meta = state.ga_action_meta[request.root_id]
        cfg = state.ga_config.get("talents", "T2")
        if request.verb.value != "move" or not player.alive or self.shadow_uses >= cfg[
            "shadow_step_uses"
        ] or not self.free_shadow and player.sp < cfg["shadow_step_sp_cost"] or (
            meta.get("from_location") != player.location):
            return
        if state.players[request.actor_id].location == meta.get("from_location"):
            return
        if state.players[request.actor_id].location == player.location:
            return
        if ask(state, self.owner_id, self.owner_id, ("follow", "decline"), kind="shadow_step",
               context={"target": request.actor_id}) == "follow":
            if not self.free_shadow:
                player.sp -= cfg["shadow_step_sp_cost"]
            self.free_shadow = False
            from engine.ga.extensions import relocate
            relocate(state, self.owner_id, state.players[request.actor_id].location)
            self.shadow_uses += 1
            self.shadow_lock = request.actor_id
            chapter(state, self.owner_id, 2)
            touch(state)

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """修甲只认实际计费命中，不修破碎甲，不用擦伤次数代替。"""
        player = state.players[self.owner_id]
        cfg = state.ga_config.get("talents", "T2")
        for record in records:
            if record.effect.actor_id != self.owner_id:
                continue
            meta = state.ga_action_meta.get(record.effect.root_id, {})
            if record.effective_hit and meta.get("hidden"):
                chapter(state, self.owner_id, 1)
            if record.effective_hit and record.effect.source_id == "t2_strike":
                self.performance_rounds.add(state.current_round)
                if len(self.performance_rounds) >= 2:
                    chapter(state, self.owner_id, 3)
            if record.armor_changes and any(c.ordinary_wear for c in record.armor_changes):
                self.armor_hits += 1
                if self.armor_hits % cfg["armor_hits_per_repair"] == 0:
                    kind = state.ga_record_context.get(record.resolution_id, {}).get(
                        "armor_kinds", {}).get(record.armor_changes[0].defense_id)
                    for key, defense in player.defenses.items():
                        if kind is not None and player.armor_kinds.get(key) == kind and 0 < (
                            defense.durability
                        ) < defense.max_durability:
                            player.defenses[key] = replace(defense, durability=min(
                                defense.max_durability, defense.durability + cfg["repair_amount"]))
                            break
        if player.kills:
            player.retain_stealth_attacks = False
            player.hidden = False

    def receive_poem(self, state: GameState, giver: str) -> None:
        """地火的再动仍走公共白名单，暗影步只赠一次免费支付。"""
        from engine.ga.runtime import queue_extra_turn
        self.shadow_uses = 0
        self.free_shadow = True
        state.players[self.owner_id].hidden = True
        queue_extra_turn(state, self.owner_id, "poem_earthfire")
