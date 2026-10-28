"""GA往世层：未来押注固定池、真实付费微光与一次性Encore。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.actions import GrantKind
from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    ActionVerb, CostEntry, DamageAttribute, DamageKind, Performance, RoundPhase,
    TargetScope, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.extensions import IncomingModifier, ask, units
from engine.ga.gameplay import ActionOption, execute_action, legal_actions, validate_request
from engine.ga.state import ActionGrant, emit, next_id, observe, touch
from engine.ga.status import heal
from engine.ga.talents.base import (
    ability, attack_choices, barrier, packet, timer, weapon_option,
)

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec, ResolutionRecord
    from engine.ga.events import DomainEvent
    from engine.ga.state import GaPlayer


MARKS = ("ranger", "stars", "yin_yang", "eternity", "firefly", "spotlight", "tomorrow")


@dataclass
class AfterlifeState:
    """只保存真实额度/押注/借用标记；PP钱包仍在玩家实体。"""

    bets: dict[str, str] = field(default_factory=dict)
    bet_round: dict[str, int] = field(default_factory=dict)
    glow_round: dict[str, int] = field(default_factory=dict)
    buff_round: dict[str, int] = field(default_factory=dict)
    encore_used: set[str] = field(default_factory=set)
    marks: dict[str, list[str]] = field(default_factory=dict)
    borrowed: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    paid_pools: set[str] = field(default_factory=set)
    break_rounds: set[tuple[str, int]] = field(default_factory=set)
    remainder_cursor: int = 0
    revelation: dict[str, tuple[int, int]] = field(default_factory=dict)
    gaze: set[str] = field(default_factory=set)
    temporary_immunity: dict[str, int] = field(default_factory=dict)
    spotlight_roots: set[str] = field(default_factory=set)


class Afterlife:
    """所有往世操作仍提交ActionRequest，由共同验证器支付。"""

    def register(self, state: GameState) -> None:
        """安装三个按钮与有限标记，不注册二十六张旧魂援表。"""
        state.ga_afterlife = AfterlifeState()
        state.ga_action_providers.append(self.options)
        state.ga_option_filters.append(self.filter_options)
        state.ga_before_action.append(self.before_action)
        state.ga_event_listeners.append(self.on_events)
        state.ga_batch_listeners.append(self.on_batch)
        state.ga_incoming_modifiers.append(self.incoming)
        state.ga_effect_filters.append(self.effects)
        state.ga_pool_bonus_hooks.append(self.pool_bonus)
        state.ga_before_terminal.append(self.terminal)
        state.ga_hooks.setdefault("R0", []).append(self.r0)
        state.ga_rng.filters.append(lambda value, kind, key, bound, actor:
                                    self.reroll(state, value, kind, key, bound, actor))

    def ghost(self, state: GameState, actor: str) -> bool:
        """绝对死者和撤退者都没有往世权限。"""
        player = state.players[actor]
        return not player.alive and not player.absolute_dead and not player.exited

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """R0往世按钮与生者待用的借用核心有共同候选语义。"""
        actor = grant.actor_id
        ledger = state.ga_afterlife
        if state.players[actor].alive:
            return self.borrowed_options(state, grant, stage)
        if grant.kind is not GrantKind.AFTERLIFE or stage is not TurnStage.T0 or not (
            self.ghost(state, actor) and state.current_phase is RoundPhase.R0
        ):
            return ()
        result = []
        live = tuple(key for key, p in state.players.items() if p.alive and p.home_locked)
        for target in live:
            if ledger.bet_round.get(actor) != state.current_round:
                result.append(self.free(ability(grant, stage, "afterlife_bet", targets=(target,),
                                                after=self.execute)))
            if ledger.glow_round.get(actor) != state.current_round and ledger.buff_round.get(
                target
            ) != state.current_round:
                player = state.players[target]
                for kind in ("barrier", "heal", "revelation", "gaze"):
                    if kind == "heal" and (not player.healable or player.hp >= player.max_hp):
                        continue
                    if kind == "gaze" and not player.hidden and target in ledger.gaze:
                        continue
                    for payer in (actor, target):
                        candidate = ability(
                            grant, stage, "afterlife_glow", targets=(target,),
                            parameters={"kind": kind, "payer": payer},
                            costs=(CostEntry(payer, "pp", state.ga_config.get(
                                "afterlife", "microglow_cost")),), after=self.execute,
                        )
                        if payer != actor:
                            candidate = replace(candidate, confirm=self.confirm_aid)
                        result.append(self.free(candidate))
            if actor not in ledger.encore_used and actor in state.ga_roles:
                slot = state.ga_roles[actor].talent_id
                kinds = MARKS if slot.startswith("G") else (slot,)
                for kind in kinds:
                    if kind in MARKS and kind in ledger.marks.get(target, ()):
                        continue
                    candidate = ability(grant, stage, "afterlife_encore", targets=(target,),
                                        parameters={"kind": kind}, after=self.execute)
                    result.append(self.free(candidate))
        return tuple(result)

    def free(self, option: ActionOption) -> ActionOption:
        """往世按钮不占生者T0，也不凭空获得生者T1。"""
        return replace(option, definition=replace(option.definition, consumes_stage=False))

    def confirm_aid(self, state: GameState, plan: ActionPlan) -> bool:
        """生者明确同意后才支付，拒绝不转账或铸造PP。"""
        request = plan.request
        return ask(state, request.actor_id, request.parameters["payer"], ("accept", "decline"),
                   kind="microglow_aid", context={"provider": request.actor_id,
                                                 "microglow": request.parameters["kind"],
                                                 "cost": state.ga_config.get(
                                                     "afterlife", "microglow_cost")}) == "accept"

    def execute(self, state: GameState, plan: ActionPlan) -> None:
        """实际执行才消费微光/受益额度；Encore赠出后整局资格耗尽。"""
        request = plan.request
        actor, target = request.actor_id, request.target_ids[0]
        ledger = state.ga_afterlife
        if request.operation == "afterlife_bet":
            ledger.bets[actor] = target
            ledger.bet_round[actor] = state.current_round
        elif request.operation == "afterlife_glow":
            kind = request.parameters["kind"]
            ledger.glow_round[actor] = state.current_round
            ledger.buff_round[target] = state.current_round
            player = state.players[target]
            cfg = state.ga_config.get("afterlife")
            if kind == "barrier":
                barrier(state, target, f"afterlife/glow/{target}", cfg["blessing_barrier"],
                        ticks=state.ga_config.get("status", "temporary_barrier_ticks"))
            elif kind == "heal":
                heal(player, cfg["blessing_heal"])
            elif kind == "revelation":
                player.initiative_bonus += cfg["revelation_initiative"]
                player.sp = min(state.ga_config.get("action", "sp_cap"),
                                player.sp + cfg["revelation_sp"])
                ledger.revelation[target] = state.current_round, cfg["revelation_initiative"]
            else:
                player.hidden = False
                player.revealed_to.clear()
                ledger.gaze.add(target)
            emit(state, EventKind.STATE_CHANGED, actor_id=actor, target_id=target, effective=True,
                 payload={"microglow": kind, "payer": request.parameters["payer"]})
        else:
            ledger.encore_used.add(actor)
            kind = request.parameters["kind"]
            if kind in MARKS:
                ledger.marks.setdefault(target, []).append(kind)
            else:
                ledger.borrowed.setdefault(target, []).append({"id": next_id(state, "encore"),
                                                               "talent": kind, "provider": actor})
        touch(state)

    def r0(self, state: GameState) -> None:
        """先关闭上轮增益，再向现有普通死者提供有限R0窗口。"""
        ledger = state.ga_afterlife
        for actor, (created, amount) in tuple(ledger.revelation.items()):
            if created < state.current_round:
                state.players[actor].initiative_bonus -= amount
                del ledger.revelation[actor]
        for actor, created in tuple(ledger.temporary_immunity.items()):
            if created < state.current_round:
                state.players[actor].status_immunities = frozenset()
                del ledger.temporary_immunity[actor]
        previous = state.current_grant, state.current_stage
        try:
            for actor in sorted(state.players):
                if not self.ghost(state, actor):
                    continue
                state.current_grant = ActionGrant(f"afterlife/{state.current_round}/{actor}", actor,
                                                  GrantKind.AFTERLIFE)
                state.current_stage = TurnStage.T0
                while not state.game_over:
                    actions = legal_actions(state)
                    controller = state.ga_controllers.get(actor)
                    chooser = getattr(controller, "choose_afterlife", None)
                    request = None
                    if chooser and actions:
                        request = chooser(observe(state, actor), actions)
                    if request is None:
                        break
                    if request not in actions:
                        from engine.ga.contracts import ContractError
                        raise ContractError("afterlife", "selection not in supplied candidates")
                    execute_action(state, validate_request(state, request))
        finally:
            state.current_grant, state.current_stage = previous

    def distribute(self, state: GameState, key: str, actor: str, pool: int) -> None:
        """固定总池守恒，余数按死者ID轮转，未押中不发。"""
        ledger = state.ga_afterlife
        if key in ledger.paid_pools:
            return
        ledger.paid_pools.add(key)
        receivers = sorted(dead for dead, selected in ledger.bets.items() if selected == actor and
                           self.ghost(state, dead))
        if not receivers:
            return
        share, remainder = divmod(pool, len(receivers))
        for dead in receivers:
            state.players[dead].pp += share
        for index in range(remainder):
            dead = receivers[(ledger.remainder_cursor + index) % len(receivers)]
            state.players[dead].pp += 1
        ledger.remainder_cursor = (ledger.remainder_cursor + remainder) % len(receivers)
        touch(state)

    def on_events(self, state: GameState, events: tuple[DomainEvent, ...]) -> None:
        """只分未来战果池，不发旧首杀/复仇/存活附奖。"""
        ledger = state.ga_afterlife
        for event in events:
            actor = event.score_owner_id
            if actor not in state.players or event.target_id not in state.players:
                continue
            if event.target_id == actor:
                continue
            if event.kind is EventKind.ACTOR_DIED:
                self.distribute(state, event.event_id, actor, state.ga_config.get(
                    "afterlife", "bet_kill_pool"))
            elif event.kind is EventKind.ARMOR_BROKEN and (actor, state.current_round) not in (
                ledger.break_rounds
            ):
                ledger.break_rounds.add((actor, state.current_round))
                self.distribute(state, event.event_id, actor, state.ga_config.get(
                    "afterlife", "bet_break_pool"))

    def terminal(self, state: GameState, survivors: tuple[str, ...]) -> None:
        """合格王冠固定池在快照前分配，不给多人存活/同时全灭发冠。"""
        if len(survivors) == 1:
            actor = survivors[0]
            player = state.players[actor]
            if player.kills >= state.ga_config.get("scoring", "crown_min_kills") or len(
                player.chapters
            ) >= state.ga_config.get("scoring", "crown_min_chapters"):
                self.distribute(state, f"crown/{actor}", actor, state.ga_config.get(
                    "afterlife", "bet_crown_pool"))

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """一次性整数费用减免在预检报价，只有实际发动才耗标记。"""
        marks = state.ga_afterlife.marks.get(grant.actor_id, ())
        if not state.players[grant.actor_id].alive or not marks:
            return options
        result = []
        for option in options:
            costs = list(option.definition.costs)
            tags = []
            for mark, discount, allowed in (
                ("eternity", state.ga_config.get("encore", "eternity_discount"),
                 lambda c: c.account != "sp"),
                ("firefly", state.ga_config.get("encore", "firefly_reduction"),
                 lambda c: c.account == "hp"),
            ):
                if mark in marks:
                    index = next((i for i, c in enumerate(costs) if c.amount and allowed(c)), None)
                    if index is not None:
                        amount = max(0, costs[index].amount - discount)
                        costs[index] = replace(costs[index], amount=amount)
                        tags.append(mark)
            if "tomorrow" in marks:
                variants = tuple(name for name, valid in (
                    ("hp", any(c.account == "hp" and c.amount for c in costs)),
                    ("destroy", option.request.parameters.get("destroy_relic") not in (
                        None, "none")),
                ) if valid) or (None,)
            else:
                variants = (None,)
            for waiver in variants:
                selected = tuple(replace(c, amount=0) if waiver == "hp" and c.account == "hp" else c
                                 for c in costs)
                params = dict(option.request.parameters)
                active = [*tags, *( ("tomorrow",) if waiver else () )]
                if active:
                    params["encore_tags"] = tuple(active)
                if waiver:
                    params["encore_waiver"] = waiver
                result.append(replace(option, request=replace(option.request, parameters=params),
                                      definition=replace(option.definition, costs=selected,
                                                         required_parameters=tuple(params))))
        return tuple(result)

    def before_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """合法费用减免与借用只使用一次，挡下也不返还。"""
        ledger = state.ga_afterlife
        actor = plan.request.actor_id
        for tag in plan.request.parameters.get("encore_tags", ()):
            if tag in ledger.marks.get(actor, ()):
                ledger.marks[actor].remove(tag)
        borrowed = plan.request.parameters.get("borrow_id")
        if borrowed:
            ledger.borrowed[actor] = [e for e in ledger.borrowed.get(actor, ())
                                      if e["id"] != borrowed]
        if plan.request.operation == "encore_chase":
            from engine.ga.extensions import relocate
            relocate(state, actor, units(state)[plan.request.target_ids[0]].location)
            ledger.marks[actor].remove("ranger")

    def effects(self, state: GameState, effects: tuple[EffectSpec, ...]) -> tuple[EffectSpec, ...]:
        """追光只加普通攻击方产出；独立固定总池在均分前调用pool_bonus。"""
        result = []
        for effect in effects:
            owner = effect.score_owner_id
            marks = state.ga_afterlife.marks.get(owner, ())
            if "spotlight" in marks and effect.damage_kind is DamageKind.NORMAL and effect.amount:
                effect = replace(effect, amount=effect.amount + state.ga_config.get(
                    "encore", "spotlight_attack_bonus"))
            result.append(effect)
        return tuple(result)

    def pool_bonus(self, state: GameState, actor: str, amount: int) -> int:
        """总池固定加值仅入池一次，再交给统一均分。"""
        marks = state.ga_afterlife.marks.get(actor, [])
        if "spotlight" in marks:
            marks.remove("spotlight")
            return amount + state.ga_config.get("encore", "spotlight_attack_bonus")
        return amount

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """凝视消耗于下一次实际受击，飞萤只减自损或持续伤害。"""
        ledger = state.ga_afterlife
        reduction = state.ga_config.get("encore", "firefly_reduction") if "firefly" in (
            ledger.marks.get(target.player_id, ())
        ) and (effect.source_id == "burn" or effect.actor_id == target.player_id or (
            effect.source_id == "g1_entropy"
        )) else 0
        return IncomingModifier(
            immune=ledger.temporary_immunity.get(target.player_id) == state.current_round,
            vulnerability=state.ga_config.get("afterlife", "gaze_next_hit_bonus") if (
                target.player_id in ledger.gaze
            ) else 0, hp_reduction=reduction,
        )

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """已真正进入本体的效果才消费相应标记，避免纯预检吃资格。"""
        ledger = state.ga_afterlife
        for record in records:
            effect = record.effect
            target = record.target_id
            if record.remaining_damage and record.outcome.value in ("hit", "graze"):
                if effect.damage_kind is DamageKind.NORMAL:
                    ledger.gaze.discard(target)
                marks = ledger.marks.get(target, [])
                if "firefly" in marks and (effect.source_id in ("burn", "g1_entropy") or
                                            effect.actor_id == target):
                    marks.remove("firefly")
            owner = effect.score_owner_id
            marks = ledger.marks.get(owner, [])
            if "spotlight" in marks and effect.damage_kind is DamageKind.NORMAL and effect.amount:
                marks.remove("spotlight")
                actor = units(state).get(effect.actor_id)
                if actor and not actor.is_player and actor.owner_id == owner and actor.alive:
                    heal(actor, state.ga_config.get("encore", "spotlight_proxy_heal"))
            if "stars" in marks and record.effective_hit and owner in state.players:
                targets = tuple(key for key, p in units(state).items() if
                                p.alive and p.home_locked and key != owner and
                                p.owner_id != owner and
                                p.isolation_id == state.players[owner].isolation_id)
                ordered = tuple(key for key in targets if key != target) or targets
                if ordered:
                    marks.remove("stars")
                    grant = ActionGrant(next_id(state, "encore-stars"), owner, GrantKind.REACTION)
                    request = ability(grant, TurnStage.T1, "encore_stars",
                                      targets=(ordered[0],)).request
                    bounce = packet(state, request, state.ga_config.get("encore", "stars_damage"),
                                    attribute=DamageAttribute.UNATTRIBUTED,
                                    scope=TargetScope.RANGED_SINGLE, certain=True,
                                    defense_coefficient=0)
                    if effect.root_id:
                        bounce = replace(bounce, root_id=effect.root_id)
                    state.ga_after_batch_queue.append(partial(resolve_batch, effects=(bounce,)))

    def reroll(
        self, state: GameState, value: int | float, kind: str, key: str, bound: int | float,
        actor: str | None,
    ) -> int | float:
        """阴阳明确掷第二次并交本人二选一，不递归重掷。"""
        marks = state.ga_afterlife.marks.get(actor, [])
        if "yin_yang" not in marks or actor not in state.players or not state.players[actor].alive:
            return value
        marks.remove("yin_yang")
        second = state.ga_rng.reroll(key, kind, bound)
        selected = ask(state, actor, actor, ("first", "second"), kind="encore_reroll",
                       context={"first": value, "second": second, "draw_kind": kind})
        return value if selected == "first" else second

    def borrowed_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """借原初即演核心，不借被动、整套形态、保险或完整再动。"""
        if grant.kind not in (GrantKind.STANDARD, GrantKind.EXTRA_TURN):
            return ()
        result = []
        actor = grant.actor_id
        ledger = state.ga_afterlife
        for entry in ledger.borrowed.get(actor, ()):
            slot = entry["talent"]
            if stage is TurnStage.T1 and slot in ("T1", "T2"):
                for original in attack_choices(state, grant, melee=slot == "T1", local=True):
                    candidate = weapon_option(state, grant, original, f"encore_{slot}",
                                              Performance.INSTANT,
                                              multiplier=state.ga_config.get("talents", "T1",
                                                                              "core_multiplier") if
                                              slot == "T1" else 1,
                                              bonus=state.ga_config.get("talents", "T2",
                                                                         "performance_bonus") if
                                              slot == "T2" else 0,
                                              wear_price=state.ga_config.get("talents", "T1",
                                                                              "wear_price") if
                                              slot == "T1" else 1)
                    result.append(self.borrow_tag(candidate, entry["id"]))
            elif slot == "T3" and stage is TurnStage.T0:
                result.append(self.borrow_tag(ability(grant, stage, "encore_T3",
                                                      mode=Performance.INSTANT,
                                                      after=self.borrow_execute), entry["id"]))
            elif slot in ("T4", "T6", "T7") and stage is TurnStage.T1:
                targets = tuple(key for key, p in state.players.items() if
                                p.alive and p.home_locked)
                if slot == "T4":
                    targets = tuple(key for key in targets if key != actor)
                for target in targets:
                    if slot == "T6":
                        from engine.ga.police import Police
                        p = state.ga_police
                        if "police" in state.destroyed_locations or p.stopped or (
                            state.ga_clock_paused
                        ):
                            continue
                        available = Police().available(state)
                        kinds = [name for name, ok in (
                            ("attack", available and not p.attack_upgrade),
                            ("wall", available and not p.wall_upgrade),
                            ("reinforce", len(Police().live(state)) < state.ga_config.get(
                                "police", "roster_cap")),
                        ) if ok]
                        for kind in kinds:
                            candidate = ability(grant, stage, "encore_T6",
                                                mode=Performance.INSTANT, parameters={"kind": kind},
                                                after=self.borrow_execute)
                            result.append(self.borrow_tag(candidate, entry["id"]))
                        break
                    player = state.players[target]
                    if slot == "T7" and not (player.burns or player.statuses or player.healable and
                                             player.hp < player.max_hp):
                        continue
                    candidate = ability(grant, stage, f"encore_{slot}",
                                        mode=Performance.INSTANT, targets=(target,),
                                        after=self.borrow_execute)
                    result.append(self.borrow_tag(candidate, entry["id"]))
        if stage is TurnStage.T1 and "ranger" in ledger.marks.get(actor, ()):
            for original in attack_choices(state, grant, melee=True, chase=True):
                target = units(state)[original.request.target_ids[0]]
                if target.location != state.players[actor].location:
                    candidate = replace(original, request=replace(original.request,
                                                                  operation="encore_chase"),
                                        definition=replace(original.definition,
                                                           operation="encore_chase"))
                    result.append(candidate)
        return tuple(result)

    def borrow_tag(self, option: ActionOption, key: str) -> ActionOption:
        """借用身份是精确参数，不能由控制器伪装成第二份资格。"""
        params = {**dict(option.request.parameters), "borrow_id": key}
        return replace(option, request=replace(option.request, parameters=params),
                       definition=replace(option.definition, required_parameters=tuple(params)))

    def borrow_execute(self, state: GameState, plan: ActionPlan) -> None:
        """只复用明文简化核心，不注册借来的角色被动或章节。"""
        request = plan.request
        actor = request.actor_id
        if request.operation == "encore_T3":
            from engine.ga.talents.t3 import T3
            T3(actor).jade(state, plan)
        elif request.operation == "encore_T4":
            from engine.ga.talents.t4 import T4, HEXAGRAMS
            from engine.ga.extensions import reveal_choices
            target = request.target_ids[0]
            before = set(state.ga_choice_windows)
            a = ask(state, actor, actor, ("rock", "paper", "scissors"), kind="encore_hexagram",
                    secret=True)
            b = ask(state, actor, target, ("rock", "paper", "scissors"), kind="encore_hexagram",
                    secret=True)
            pending = tuple(key for key in state.ga_choice_windows if key not in before)
            reveal_choices(state, pending)
            result = HEXAGRAMS[tuple(sorted((a, b)))]
            if result == "extra":
                allowed = tuple(name for name in HEXAGRAMS.values() if name != "extra")
                index = state.ga_rng.die("encore/non-extra", len(allowed), actor_id=actor) - 1
                result = allowed[index]
            T4(actor).apply(state, plan, target, result)
            if result == "immune":
                state.ga_afterlife.temporary_immunity[actor] = state.current_round
        elif request.operation == "encore_T6":
            from engine.ga.police import Police
            if Police().upgrade(state, request.parameters["kind"]):
                emit(state, EventKind.STATE_CHANGED, actor_id=actor, effective=True,
                     payload={"police_upgrade": request.parameters["kind"]})
        else:
            player = state.players[request.target_ids[0]]
            heal(player, state.ga_config.get("talents", "T7", "prayer_heal"))
            player.statuses.clear()
            player.burns.clear()
