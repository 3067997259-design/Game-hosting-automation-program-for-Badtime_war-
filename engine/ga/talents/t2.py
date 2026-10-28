"""T2慈爱的怪盗：免费取物、惧怖、有限跟随与指定实物偷窃。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.acquisition import take_options
from engine.ga.contracts import (
    ActionVerb, BarrierPolicy, DamageAttribute, DamageKind, DeathOutcome, DeathPolicy,
    EffectKind, EffectSpec, HitPolicy, IsolationPolicy, Performance, TargetScope, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.extensions import ask, chapter, high_impact, relocate
from engine.ga.possessions import transferable_items, transfer_item
from engine.ga.talents.base import Talent, ability, own_slot
from engine.ga.state import emit, touch

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, ResolutionRecord
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant


class T2(Talent):
    """演出不再借普通攻击；如影随形不制造T0/T2或额外行动槽。"""

    talent_id = "T2"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.free_shadow = False
        self.armor_hits = 0
        self.shadow_uses = 0
        self.shadow_lock: str | None = None
        self.performance_rounds: set[int] = set()
        self.starting_cloak_received = False

    def public(self) -> Mapping[str, Any]:
        """公开有限次数、跨回合锁定和已完成的演出轮次。"""
        return {"shadow_uses": self.shadow_uses, "free_shadow": self.free_shadow,
                "shadow_lock": self.shadow_lock, "armor_hits": self.armor_hits,
                "performance_rounds": sorted(self.performance_rounds),
                "starting_cloak_received": self.starting_cloak_received}

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
        """即演从当前地点取商品；公演只给同地其他玩家施加惧怖。"""
        if stage is not TurnStage.T1 or not own_slot(state, self.owner_id, grant):
            return ()
        result = list(take_options(state, grant, "t2_take"))
        player = state.players[self.owner_id]
        targets = tuple(key for key, target in state.players.items() if
                        key != self.owner_id and target.alive and target.home_locked and
                        target.location == player.location and
                        target.isolation_id == player.isolation_id)
        if targets:
            candidate = ability(grant, stage, "t2_fear", mode=Performance.PUBLIC,
                                targets=targets, hostile=True)
            root = candidate.request.root_id
            effect = EffectSpec(
                f"{root}/fear", "t2_fear", root, f"{root}/fear", self.owner_id,
                self.owner_id, self.owner_id, targets, EffectKind.STATUS, DamageKind.NONE, 0,
                DamageAttribute.UNATTRIBUTED, TargetScope.LOCAL_AREA, HitPolicy.CERTAIN,
                BarrierPolicy.RESPECT, IsolationPolicy.RESPECT, DeathPolicy.NORMAL,
                {"statuses": ("FEAR",)},
            )
            result.append(replace(candidate, effects=(effect,)))
        return tuple(result)

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """跟随锁定留到真正攻击该目标；与本回合普通lock不叠加。"""
        if grant.actor_id != self.owner_id or self.shadow_lock is None:
            return options
        result = []
        for option in options:
            if option.request.verb is not ActionVerb.ATTACK:
                result.append(option)
                continue
            effects = tuple(replace(e, parameters={
                **dict(e.parameters), "ignore_move_evasion": True,
                "hit_bonus": e.parameters.get("hit_bonus", 0) + state.ga_config.get(
                    "accuracy", "lock_bonus"),
                "defense_coefficient": min(e.parameters.get("defense_coefficient", 1),
                                            state.ga_config.get("accuracy",
                                                                "lock_defense_coefficient")),
            }) if e.target_ids == (self.shadow_lock,) and e.kind is EffectKind.DAMAGE and
                grant.locked_target != self.shadow_lock else e for e in option.effects)
            result.append(replace(option, effects=effects))
        return tuple(result)

    def record_performance(self, state: GameState) -> None:
        """同轮多次生效只记一轮，跨两个真实轮次完成第三章。"""
        self.performance_rounds.add(state.current_round)
        if len(self.performance_rounds) >= state.ga_config.get(
            "talents", "T2", "chapter_performance_rounds"
        ):
            chapter(state, self.owner_id, 3)

    def after_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """开局赠品、演出记录和跟随选择都在实际动作成功后兑现。"""
        request = plan.request
        player = state.players[self.owner_id]
        if request.actor_id == self.owner_id:
            if request.operation == "wake" and not self.starting_cloak_received:
                player.inventory["cloak"] = max(player.inventory.get("cloak", 0), 1)
                self.starting_cloak_received = True
                touch(state)
            elif request.operation == "t2_take":
                self.record_performance(state)
            if request.verb is ActionVerb.ATTACK and self.shadow_lock in request.target_ids:
                self.shadow_lock = None
            return
        meta = state.ga_action_meta[request.root_id]
        cfg = state.ga_config.get("talents", "T2")
        target = state.players[request.actor_id]
        if request.verb is not ActionVerb.MOVE or not player.alive or not player.awake or (
            not target.alive or self.shadow_uses >= cfg["shadow_step_uses"] or
            not self.free_shadow and player.sp < cfg["shadow_step_sp_cost"] or
            meta.get("from_location") != player.location or
            target.location == player.location or target.isolation_id != player.isolation_id or
            player.isolation_id is not None
        ):
            return
        items = transferable_items(state, request.actor_id, self.owner_id)
        choice = ask(state, self.owner_id, self.owner_id,
                     ("lock", *(f"steal:{item}" for item in items), "decline"), kind="shadow_step",
                     context={"target": request.actor_id, "destination": target.location,
                              "sp_cost": 0 if self.free_shadow else cfg["shadow_step_sp_cost"],
                              "remaining_uses": cfg["shadow_step_uses"] - self.shadow_uses})
        if choice == "decline":
            return
        if not self.free_shadow:
            player.sp -= cfg["shadow_step_sp_cost"]
        self.free_shadow = False
        relocate(state, self.owner_id, target.location)
        self.shadow_uses += 1
        if choice == "lock":
            self.shadow_lock = request.actor_id
            target.revealed_to.add(self.owner_id)
        else:
            transfer_item(state, request.actor_id, self.owner_id, choice.removeprefix("steal:"))
        chapter(state, self.owner_id, 2)
        emit(state, EventKind.STATE_CHANGED, actor_id=self.owner_id, target_id=request.actor_id,
             root_id=request.root_id, effective=True,
             payload={"kind": "shadow_step", "choice": choice})
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
            if record.applied_statuses and record.effect.source_id == "t2_fear":
                self.record_performance(state)
                high_impact(state, self.owner_id, record.effect.root_id, record.target_id)
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
            if player.kills and record.death_outcome in (
                DeathOutcome.DEAD, DeathOutcome.ABSOLUTE_DEAD
            ):
                player.retain_stealth_attacks = False
                player.hidden = False

    def receive_poem(self, state: GameState, giver: str) -> None:
        """地火的再动仍走公共白名单，暗影步只赠一次免费支付。"""
        from engine.ga.runtime import queue_extra_turn
        self.shadow_uses = 0
        self.free_shadow = True
        state.players[self.owner_id].hidden = True
        queue_extra_turn(state, self.owner_id, "poem_earthfire")
