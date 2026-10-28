"""GA无天赋完整调度：R0～R4、标准槽、公演、追击与额外槽。"""

from __future__ import annotations

import time
from typing import Protocol, TYPE_CHECKING

from engine.ga.actions import GrantKind
from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    ActionRequest, BarrierPolicy, ContractError, DamageAttribute, DamageKind, DeathPolicy,
    EffectKind, EffectSpec, HitPolicy, IsolationPolicy, Performance, RoundPhase, TargetScope,
    ChoiceWindow, TerminalSnapshot, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.decisions import notify_action_result
from engine.ga.gameplay import ActionOption, build_options, execute_action, validate_request
from engine.ga.state import (
    ActionGrant, emit, finish_if_needed, next_id, observe, run_hooks, touch, trace,
)
from engine.ga.status import expire_r4, heal

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any
    from engine.game_state import GameState


class Controller(Protocol):
    """所有控制者只读同一观察和合法候选，返回同一种ActionRequest。"""

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        """T0可明确跳过；T1必须返回合法候选。"""
        ...

    def wants_public(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...]) -> bool:
        """申请当前边界可兑现的公演，不在R0排FIFO。"""
        ...

    def reroll_initiative(self, view: Mapping[str, Any], value: int) -> bool:
        """在夺台排序前决定是否花PP采用新骰。"""
        ...

    def escape_petrify(self, view: Mapping[str, Any], attempt: int) -> bool:
        """槽开始的挣脱窗口不占T0，每次仍须支付SP。"""
        ...

    def respond(
        self, view: Mapping[str, Any], window: ChoiceWindow, context: Mapping[str, Any],
    ) -> str | None:
        """秘密承诺、接受及复合根内部选择，缺答保持显式失败。"""
        ...

    def choose_afterlife(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
    ) -> ActionRequest | None:
        """往世窗口可明确跳过。"""
        ...


EXTRA_SOURCES = {"t4_or_yue": 0, "poem_earthfire": 1}


def queue_extra_turn(state: GameState, actor_id: str, source_id: str) -> bool:
    """来源白名单/每轮一次/不递归；多个未开始候选只留最高优先。"""
    grant = state.current_grant
    if source_id not in EXTRA_SOURCES:
        raise ContractError("extra.source", "unregistered complete extra-turn source")
    if state.game_over or grant is None or grant.kind is not GrantKind.STANDARD or (
        actor_id in state.ga_extra_used or not state.players[actor_id].alive
    ):
        return False
    previous = state.ga_pending_extras.get(actor_id)
    if previous is None or EXTRA_SOURCES[source_id] < EXTRA_SOURCES[previous]:
        state.ga_pending_extras[actor_id] = source_id
    return True


def queue_followup(state: GameState, actor_id: str, source_id: str) -> bool:
    """单步追击与反应均不得再派生追击，额度跨来源共用。"""
    grant = state.current_grant
    if state.game_over or grant is None or grant.kind in (
        GrantKind.STEP_FOLLOWUP, GrantKind.REACTION
    ) or actor_id in state.ga_followup_used or not state.players[actor_id].alive:
        return False
    state.ga_pending_followups.setdefault(actor_id, source_id)
    return True


def _public_options(state: GameState, grant: ActionGrant) -> tuple[ActionOption, ...]:
    """执行前重查SP、硬控、存活和实际演出候选。"""
    player = state.players[grant.actor_id]
    if state.ga_public_used or grant.started or not player.alive or (
        state.ga_public_blocked.get(grant.actor_id) == state.current_round
    ):
        return ()
    control = player.statuses.get("control")
    if control and control.name in ("ACTION_SUPPRESSED", "PETRIFIED"):
        return ()
    options = build_options(state, grant, TurnStage.T1)
    return tuple(o for o in options if o.request.performance is Performance.PUBLIC and (
        not control or control.name != "SHOCKED" or not o.hostile and o.request.verb.value in (
            "move", "interact"
        )
    ))


def _choose_stage(state: GameState, controller: Controller, stage: TurnStage) -> None:
    """T0一次、T1一次；非法控制器返回直接报错且不扣费。"""
    state.current_stage = stage
    while not state.game_over:
        options = build_options(state, state.current_grant, stage)
        if stage is TurnStage.T1 and state.current_grant.must_public:
            public = tuple(o for o in options if o.request.performance is Performance.PUBLIC)
            if public:
                options = public
        actions = tuple(o.request for o in options)
        if not actions:
            if stage is TurnStage.T1 and state.current_grant.kind is GrantKind.STANDARD:
                raise ContractError("turn", "standard slot has no legal primary action")
            return
        request = controller.choose(observe(state, state.current_grant.actor_id, options=options),
                                    actions, stage)
        if request is None and stage is TurnStage.T0 and state.players[
            state.current_grant.actor_id
        ].awake:
            return
        if request not in actions:
            raise ContractError("controller", "selection was not one of the supplied legal actions")
        plan = validate_request(state, request)
        receipt = execute_action(state, plan)
        if getattr(controller, "on_action_result", None) is not None:
            notify_action_result(controller, observe(state, request.actor_id), receipt)
        if plan.phase_claim is not None:
            return


def _start_control(state: GameState, controller: Controller, grant: ActionGrant) -> bool:
    """结构检查先于普通硬控，挣脱不占T0，失神只封一次适用T0。"""
    player = state.players[grant.actor_id]
    if not player.alive:
        return False
    if grant.kind is GrantKind.STANDARD:
        player.moved = False
        grant.hook_on_cooldown = bool(player.hook_cooldown)
        player.hook_cooldown = max(0, player.hook_cooldown - 1)
    control = player.statuses.get("control")
    if control and control.name == "ACTION_SUPPRESSED":
        del player.statuses["control"]
        return False
    if control and control.name == "PETRIFIED":
        cfg = state.ga_config
        for attempt in range(cfg.get("status", "petrify_escape_attempts")):
            price = cfg.get("status", "petrify_escape_sp_cost")
            if player.sp < price or not controller.escape_petrify(
                observe(state, grant.actor_id), attempt
            ):
                break
            player.sp -= price
            touch(state)
            if state.ga_rng.chance(f"{grant.grant_id}/escape/{attempt}",
                                   cfg.get("status", "petrify_escape_probability"),
                                   actor_id=grant.actor_id):
                del player.statuses["control"]
                break
        else:
            return False
        if "control" in player.statuses:
            return False
    grant.shocked = bool(control and control.name == "SHOCKED")
    if grant.shocked:
        del player.statuses["control"]
    if grant.kind in (GrantKind.STANDARD, GrantKind.EXTRA_TURN) and (
        "DISORIENTED" in player.statuses
    ):
        grant.t0_disabled = True
        player.hidden = False
        del player.statuses["DISORIENTED"]
    return True


def _play_grant(
    state: GameState, controllers: Mapping[str, Controller], grant: ActionGrant,
) -> None:
    """不可被公演切开的完整槽；追击属于当前T2内收尾。"""
    state.current_grant = grant
    grant.started = True
    if grant.kind in (GrantKind.STANDARD, GrantKind.EXTRA_TURN):
        # 惧怖在下一完整回合开始转为该回合的移动禁令，跳槽也消费这一次。
        grant.movement_blocked = state.players[grant.actor_id].statuses.pop("FEAR", None) is not None
    touch(state)
    trace(state, "slot_start", actor=grant.actor_id, kind=grant.kind.value,
          grant_id=grant.grant_id, source_id=grant.source_id)
    run_hooks(state, "slot_start")
    if state.game_over:
        return
    controller = controllers[grant.actor_id]
    if not all(gate(state, grant) for gate in tuple(state.ga_slot_gates)) or not (
        _start_control(state, controller, grant)
    ):
        if state.players[grant.actor_id].alive:
            emit(state, EventKind.TURN_FORFEITED, actor_id=grant.actor_id,
                 payload={"controlled": True})
        grant.finished = True
        trace(state, "slot_end", actor=grant.actor_id, kind=grant.kind.value, controlled=True)
        return
    if grant.kind in (GrantKind.STANDARD, GrantKind.EXTRA_TURN):
        if not grant.t0_disabled:
            run_hooks(state, "T0")
        if not grant.t0_disabled and build_options(state, grant, TurnStage.T0):
            for callback in tuple(state.ga_t0_opportunities):
                callback(state, grant)
        _choose_stage(state, controller, TurnStage.T0)
    if not state.game_over:
        _choose_stage(state, controller, TurnStage.T1)
    player = state.players[grant.actor_id]
    if not state.game_over and player.surgery == "overclock" and grant.moved_this_slot and (
        TurnStage.T0 not in grant.occupied and not grant.t0_disabled and
        grant.kind in (GrantKind.STANDARD, GrantKind.EXTRA_TURN)
    ):
        _choose_stage(state, controller, TurnStage.T0)
    if not state.game_over and grant.kind in (GrantKind.STANDARD, GrantKind.EXTRA_TURN):
        state.current_stage = TurnStage.T2
        trace(state, "T2", actor=grant.actor_id, kind=grant.kind.value)
        run_hooks(state, "T2")
        for actor in tuple(state.ga_pending_followups):
            source = state.ga_pending_followups.pop(actor)
            if actor in state.ga_followup_used or not state.players[actor].alive:
                continue
            state.ga_followup_used.add(actor)
            followup = ActionGrant(next_id(state, "followup"), actor,
                                   GrantKind.STEP_FOLLOWUP, source)
            followup.movement_blocked = actor == grant.actor_id and grant.movement_blocked
            _play_grant(state, controllers, followup)
            if state.game_over:
                break
        state.current_grant = grant
    grant.finished = True
    if not state.game_over:
        run_hooks(state, "slot_end")
    trace(state, "slot_end", actor=grant.actor_id, kind=grant.kind.value)
    touch(state)


def _round_start(state: GameState, controllers: Mapping[str, Controller]) -> list[str]:
    """首R0刻度为1，以后单一推进点；每实际轮只建一组标准槽。"""
    cfg = state.ga_config
    state.current_round += 1
    state.current_phase = RoundPhase.R0
    state.ga_previous_world_tick = state.world_tick
    run_hooks(state, "before_clock")
    if state.game_over:
        return []
    if state.current_round == 1:
        state.world_tick = cfg.get("clock", "initial_tick")
    elif not state.ga_clock_paused:
        state.world_tick += cfg.get("clock", "reactor_step" if state.ga_clock_accelerated else
                                    "normal_step")
    run_hooks(state, "clock_advanced")
    state.ga_public_used = False
    state.ga_extra_used.clear()
    state.ga_followup_used.clear()
    state.ga_pending_extras.clear()
    state.ga_pending_followups.clear()
    state.ga_impact_penalties.clear()
    for player in state.players.values():
        player.engaged = False
    trace(state, "R0")
    touch(state)
    run_hooks(state, "R0")
    state.current_phase = RoundPhase.R1
    trace(state, "R1")
    values = {}
    for actor in sorted(state.players):
        player = state.players[actor]
        if not player.alive:
            continue
        value = state.ga_rng.die(f"{state.current_round}/{actor}/initiative",
                                 cfg.get("start", "initiative_sides"), actor_id=actor)
        if player.pp >= cfg.get("pp", "initiative_reroll_cost") and controllers[
            actor
        ].reroll_initiative(observe(state, actor), value):
            player.pp -= cfg.get("pp", "initiative_reroll_cost")
            value = state.ga_rng.die(f"{state.current_round}/{actor}/reroll",
                                     cfg.get("start", "initiative_sides"), actor_id=actor)
            touch(state)
        if 0 < player.hp <= cfg.get("status", "severe_hp_threshold"):
            value += cfg.get("status", "severe_initiative_penalty")
        if player.surgery == "overclock":
            value += cfg.get("hospital", "overclock_initiative_bonus")
        value += player.initiative_bonus
        values[actor] = value
    state.ga_initiative = values
    state.ga_order_round = state.current_round
    state.ga_round_order = tuple(sorted(values, key=lambda actor: (-values[actor], actor)))
    state.ga_standard_grants = {
        actor: ActionGrant(f"round/{state.current_round}/standard/{actor}", actor,
                           GrantKind.STANDARD)
        for actor in state.ga_round_order
    }
    initial = []
    for actor in state.ga_round_order:
        grant = state.ga_standard_grants[actor]
        options = _public_options(state, grant)
        if options and controllers[actor].wants_public(
            observe(state, actor, options=options, grant=grant), tuple(o.request for o in options)
        ):
            initial.append(actor)
    run_hooks(state, "R1")
    return initial


def _system_damage(state: GameState, source: str, amount: int) -> None:
    """环境是同批全目标直达伤害，不能因遍历顺序先发王冠。"""
    targets = tuple(sorted(key for key, p in state.players.items() if p.alive and p.home_locked))
    if not targets or not amount:
        return
    batch = next_id(state, source)
    effect = EffectSpec(batch, source, None, batch, None, None, None, targets,
                        EffectKind.DAMAGE, DamageKind.DIRECT, amount, DamageAttribute.UNATTRIBUTED,
                        TargetScope.GLOBAL_AREA, HitPolicy.CERTAIN, BarrierPolicy.RESPECT,
                        IsolationPolicy.RESPECT, DeathPolicy.NORMAL, {"hostile": False})
    resolve_batch(state, (effect,))


def _r4(state: GameState) -> None:
    """五个固定阶段各一次；终局成立后不再回复或tick。"""
    cfg = state.ga_config
    state.current_phase = RoundPhase.R4
    for boundary in ("police", "environment", "dot_maintenance", "recovery", "expiry"):
        if state.game_over:
            return
        trace(state, f"R4.{boundary}")
        run_hooks(state, f"R4.{boundary}")
        if state.game_over:
            return
        if boundary == "environment" and not state.ga_clock_paused:
            damage = max(0, state.world_tick - cfg.get("clock", "apocalypse_start") + 1)
            damage *= cfg.get("clock", "apocalypse_damage_per_tick")
            if state.ga_clock_accelerated:
                damage *= cfg.get("clock", "reactor_damage_multiplier")
            _system_damage(state, "environment", damage)
        elif boundary == "dot_maintenance":
            for index in range(cfg.get("status", "burn_cap")):
                effects = []
                batch = next_id(state, "burn-batch")
                from engine.ga.extensions import units
                entities = units(state)
                for actor in sorted(entities):
                    player = entities[actor]
                    if not player.alive or len(player.burns) <= index:
                        continue
                    entry = player.burns[index]
                    if entry.timer.on_r4(state.current_round) == entry.timer:
                        continue
                    effects.append(EffectSpec(
                        f"{batch}/{actor}/{index}", "burn", None, batch,
                        entry.actor_id, entry.score_owner_id, entry.crime_owner_id, (actor,),
                        EffectKind.DAMAGE, DamageKind.DIRECT,
                        cfg.get("status", "burn_damage_per_stack"), DamageAttribute.MAGIC,
                        TargetScope.GLOBAL_AREA, HitPolicy.CERTAIN, BarrierPolicy.RESPECT,
                        IsolationPolicy.RESPECT, DeathPolicy.NORMAL, {"hostile": False},
                    ))
                if effects:
                    resolve_batch(state, tuple(effects))
                    if state.game_over:
                        return
        elif boundary == "recovery":
            for player in state.players.values():
                if player.alive and player.surgery == "life" and not player.engaged:
                    heal(player, cfg.get("hospital", "life_idle_heal"))
            touch(state)
        elif boundary == "expiry":
            expire_r4(state)
            for location, timer in tuple(state.ga_smoke.items()):
                updated = timer.on_r4(state.current_round)
                if updated.remaining:
                    state.ga_smoke[location] = updated
                else:
                    del state.ga_smoke[location]
    finish_if_needed(state, round_limit=state.current_round >= cfg.get("clock", "max_rounds"))


def run_round(state: GameState, controllers: Mapping[str, Controller]) -> None:
    """运行完整实际轮；标准槽只消费一次，公演优先于未开始额外槽。"""
    if state.game_over:
        return
    if set(controllers) != set(state.players):
        raise ContractError("controllers", "requires exactly one controller per participant")
    state.ga_controllers = dict(controllers)
    initial = _round_start(state, controllers)
    if state.game_over:
        return
    state.current_phase = RoundPhase.R2
    trace(state, "R2")
    run_hooks(state, "R2")
    if state.game_over:
        return
    state.current_phase = RoundPhase.R3
    trace(state, "R3")
    first_boundary = True
    while not state.game_over:
        selected = None
        contenders = [*initial, *(a for a in state.ga_round_order if a not in initial)] if (
            first_boundary
        ) else list(state.ga_round_order)
        first_boundary = False
        for actor in contenders:
            grant = state.ga_standard_grants[actor]
            options = _public_options(state, grant)
            willing = actor in initial or (options and controllers[actor].wants_public(
                observe(state, actor, options=options, grant=grant),
                tuple(o.request for o in options)
            ))
            if options and willing:
                selected = grant
                selected.must_public = True
                break
        if selected is None and state.ga_pending_extras:
            actor = min(state.ga_pending_extras, key=lambda a: state.ga_round_order.index(a))
            source = state.ga_pending_extras.pop(actor)
            if actor not in state.ga_extra_used and state.players[actor].alive:
                state.ga_extra_used.add(actor)
                selected = ActionGrant(next_id(state, "extra"), actor, GrantKind.EXTRA_TURN, source)
        if selected is None:
            selected = next((state.ga_standard_grants[a] for a in state.ga_round_order
                             if not state.ga_standard_grants[a].started), None)
        if selected is None:
            break
        _play_grant(state, controllers, selected)
    state.current_grant = None
    state.current_stage = None
    if not state.game_over:
        _r4(state)


def run_game(
    state: GameState, controllers: Mapping[str, Controller], *,
    timeout_seconds: float | None = None,
) -> TerminalSnapshot:
    """有界完整对局；超时是运行失败，不能伪装成40轮正常结算。"""
    if state.ga_running:
        raise ContractError("runtime", "reentrant game loop")
    state.ga_running = True
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    try:
        while not state.game_over:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("GA runtime exceeded its declared wall-clock budget")
            run_round(state, controllers)
        return state.terminal_snapshot
    finally:
        state.ga_running = False
