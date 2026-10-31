"""G4：受难火种、主动/濒死救世主、有限余烬生命与秘密迎战总池。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.actions import GrantKind
from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    ActionVerb, DamageKind, DeathOutcome, Performance, TargetScope, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.extensions import Replacement, ask, chapter, high_impact, reveal_choices, units
from engine.ga.gameplay import build_options, execute_action, validate_request
from engine.ga.state import ActionGrant, next_id, touch, trace
from engine.ga.talents.base import (
    Talent, ability, attack_choices, enemies, own_slot, packet, timer, weapon_option,
)

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec, ResolutionRecord
    from engine.ga.events import DomainEvent
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import GaPlayer


class G4(Talent):
    """火种只在人形积累；救世主退场不附赠攻击或完整额外行动。"""

    talent_id = "G4"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.form = "human"
        self.ember = 0
        self.ruin = 0
        self.body_hp = 0
        self.body_max_hp = 0
        self.form_timer: Any = None
        self.hostile_round = 0
        self.hostile_gain = 0
        self.ruin_roots: set[str] = set()
        self.splash_pending: str | None = None
        self.burden = False
        self.sun_roots: set[tuple[str, str]] = set()

    def public(self) -> Mapping[str, Any]:
        """余烬与本体分开展示，普通治疗不修余烬。"""
        return {"form": self.form, "ember": self.ember, "ruin": self.ruin,
                "body_hp": self.body_hp, "body_max_hp": self.body_max_hp,
                "form_ticks": self.form_timer.remaining if self.form_timer else 0}

    def register(self, state: GameState) -> None:
        """火种开局值来自集中参数。"""
        super().register(state)
        self.ember = state.ga_config.get("talents", "G4", "ember_initial")
        state.ga_hooks.setdefault("T0", []).append(self.t0_boundary)

    def t0_boundary(self, state: GameState) -> None:
        """存在T0阶段便支付负世代价，不能用空菜单冒充没有T0。"""
        self.burden_t0(state, state.current_grant)

    def burden_t0(self, state: GameState, grant: ActionGrant) -> None:
        """烈阳每个真实T0加一层不可普通净化的灼烧，不从硬控跳槽偷算T0。"""
        if grant.actor_id != self.owner_id or self.form != "savior" or not self.burden:
            return
        from engine.ga.status import add_burns
        player = state.players[self.owner_id]
        source = f"burden/{grant.grant_id}"
        add_burns(player, state.ga_config, state.current_round, state.ga_config.get(
            "poems", "burden_self_burn_gain"), source_id=source, actor_id=self.owner_id)
        player.burns = [replace(e, uncleansable=True) if e.timer.effect_id.startswith(source) else e
                        for e in player.burns]

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """人形夺火占T1；满火主动升格占T0且不发额外槽。"""
        if not own_slot(state, self.owner_id, grant):
            return ()
        cfg = state.ga_config.get("talents", "G4")
        if stage is TurnStage.T0 and self.form == "human" and self.ember >= cfg["active_threshold"]:
            return (ability(grant, stage, "g4_ascend", after=self.ascend),)
        if stage is not TurnStage.T1:
            return ()
        if self.form == "human":
            return tuple(weapon_option(state, grant, original, "g4_slash", Performance.INSTANT,
                                       bonus=cfg["slash_bonus"])
                         for original in attack_choices(state, grant, melee=True, local=True))
        targets = tuple(key for key in enemies(state, self.owner_id, local=False) if
                        key in state.players)
        if targets and self.ruin:
            return (ability(grant, stage, "g4_judgment", mode=Performance.PUBLIC,
                            targets=targets, hostile=True, after=self.judgment),)
        return ()

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """救世主普通武器+2，不把总池伤害再按人数加值。"""
        if grant.actor_id != self.owner_id or self.form != "savior":
            return options
        result = []
        for option in options:
            if option.request.operation in ("heal", "syringe") or (
                option.request.operation == "surgery" and
                option.request.parameters["kind"] == "life"
            ):
                continue
            if option.request.operation == "attack" or option.weapon_use and (
                option.request.performance is Performance.NONE
            ):
                option = replace(option, effects=tuple(replace(
                    e, amount=e.amount + state.ga_config.get("talents", "G4", "savior_attack_bonus")
                ) if e.damage_kind is DamageKind.NORMAL else e for e in option.effects))
            result.append(option)
        return tuple(result)

    def activate(self, state: GameState, *, amount: int, ruin: int, body_hp: int) -> None:
        """余烬是临时生命池，保存本体并禁止普通治疗。"""
        player = state.players[self.owner_id]
        self.body_hp, self.body_max_hp = body_hp, player.max_hp
        self.form = "savior"
        self.ember = 0
        self.ruin = ruin
        player.hp = player.max_hp = amount
        player.healable = False
        self.form_timer = timer(state, self.owner_id, "g4/savior", state.ga_config.get(
            "talents", "G4", "savior_ticks"))
        chapter(state, self.owner_id, 2)
        touch(state)

    def ascend(self, state: GameState, plan: ActionPlan) -> None:
        """主动升格SP置2，当前T1可公演，不产生第二个标准槽。"""
        cfg = state.ga_config.get("talents", "G4")
        hp = state.players[self.owner_id].hp
        self.activate(state, amount=self.ember * cfg["ember_hp_multiplier"],
                      ruin=cfg["full_initial_ruin"], body_hp=hp)
        state.players[self.owner_id].sp = state.ga_config.get("action", "sp_cap")
        high_impact(state, self.owner_id, plan.request.root_id)

    def exit(self, state: GameState, *, depleted: bool = False) -> None:
        """血条耗尽以1HP退回，其余出口保留原本体HP，不反击。"""
        player = state.players[self.owner_id]
        player.max_hp = self.body_max_hp
        player.hp = state.ga_config.get("talents", "G4", "exit_body_hp") if depleted else (
            min(self.body_max_hp, self.body_hp)
        )
        player.healable = True
        self.form, self.ember, self.ruin = "human", 0, 0
        self.form_timer = None
        self.splash_pending = None
        touch(state)

    def lethal(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> Replacement | None:
        """普通余烬耗尽与人形高火濒死都只兑现一个自身转折。"""
        if target.player_id != self.owner_id:
            return None
        cfg = state.ga_config.get("talents", "G4")
        if self.form == "savior":
            return Replacement(DeathOutcome.MORPHED, cfg["exit_body_hp"], "g4_exit", {})
        if self.ember >= cfg["lethal_threshold"]:
            return Replacement(DeathOutcome.MORPHED, self.ember * cfg["ember_hp_multiplier"],
                               "g4_partial", {"ember": self.ember})
        return None

    def commit_lethal(
        self, state: GameState, effect: EffectSpec, target: GaPlayer, result: Replacement,
    ) -> None:
        """不让本击过量继续追入新肉身。"""
        if result.reason == "g4_exit":
            self.exit(state, depleted=True)
        else:
            cfg = state.ga_config.get("talents", "G4")
            self.activate(state, amount=result.hp, ruin=max(cfg["partial_ruin_floor"],
                                                           result.data["ember"] // cfg[
                                                               "partial_ruin_divisor"]),
                          body_hp=cfg["lethal_body_hp"])

    def on_events(self, state: GameState, events: tuple[DomainEvent, ...]) -> None:
        """玩家死亡/建筑摧毁的共鸣不把NPC当玩家人头。"""
        if self.form != "human" or not state.players[self.owner_id].alive:
            return
        cfg = state.ga_config.get("talents", "G4")
        for event in events:
            death = event.kind is EventKind.ACTOR_DIED and event.target_id in state.players and (
                event.target_id != self.owner_id
            )
            destroyed = event.kind is EventKind.STATE_CHANGED and event.payload.get("kind") == (
                "location_destroyed"
            )
            if death or destroyed:
                self.ember = min(cfg["ember_cap"], self.ember + cfg["exit_ember_gain"])

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """受击火种、夺火和血棘渡亡毁伤各用自己的额度。"""
        cfg = state.ga_config.get("talents", "G4")
        if self.hostile_round != state.current_round:
            self.hostile_round, self.hostile_gain = state.current_round, 0
        for record in records:
            effect = record.effect
            key = effect.root_id, record.target_id
            if self.burden and self.form == "savior" and effect.actor_id == self.owner_id and (
                record.effective_hit and effect.source_id not in ("burden_fire", "burn") and
                record.target_id != self.owner_id and key not in self.sun_roots):
                self.sun_roots.add(key)
                from functools import partial
                state.ga_after_batch_queue.append(partial(self.sun, effect=effect,
                                                         target=record.target_id))
            if self.form == "human" and record.target_id == self.owner_id and (
                state.ga_record_context.get(record.resolution_id, {}).get(
                    "talent_form") == "human" and
                effect.actor_id is not None and effect.actor_id != self.owner_id and
                effect.source_id not in ("burn", "environment") and
                effect.root_id not in state.ga_quiet_roots and
                (record.hp_loss or any(c.broken for c in record.armor_changes))
            ):
                gain = min(cfg["hostile_gain"],
                           cfg["hostile_gain_cap_per_round"] - self.hostile_gain)
                self.hostile_gain += gain
                self.ember = min(cfg["ember_cap"], self.ember + gain)
            if effect.actor_id != self.owner_id:
                continue
            # 命中并造成有效伤害才拿火种/毁伤；第一章只要求有效伤害（§5.7，DEC-061）。
            hit = record.outcome.value == "hit" and record.effective_hit
            if self.form == "human" and effect.source_id == "g4_slash" and record.effective_hit:
                if hit:
                    self.ember = min(cfg["ember_cap"], self.ember + cfg["slash_ember_gain"])
                chapter(state, self.owner_id, 1)
            meta = state.ga_action_meta.get(effect.root_id, {})
            if self.form == "savior" and meta.get("normal_attack") and (
                hit and effect.root_id not in self.ruin_roots
            ):
                self.ruin_roots.add(effect.root_id)
                self.ruin = min(cfg["ruin_cap"], self.ruin + cfg["attack_ruin_gain"])
                self.splash_pending = record.target_id
            if effect.source_id in ("g4_counter", "g4_judgment_pool") and record.effective_hit:
                chapter(state, self.owner_id, 3)

    def judgment(self, state: GameState, plan: ActionPlan) -> None:
        """先秘密承诺，再受限普通响应，最后各分一次固定总池。"""
        cfg = state.ga_config.get("talents", "G4")
        damage = self.ruin
        self.ruin = 0
        order = tuple(sorted(plan.request.target_ids, key=lambda a: (
            -state.ga_initiative.get(a, 0), a)))
        before = set(state.ga_choice_windows)
        choices = {actor: ask(state, self.owner_id, actor, ("challenge", "decline"),
                               kind="judgment_commit", secret=True, context={
                                   "ruin": damage, "counter_pool": cfg["counter_pool_per_ruin"] *
                                   damage, "judgment_per_segment": cfg["judgment_pool_per_segment"],
                               }) for actor in order}
        reveal_choices(state, tuple(key for key in state.ga_choice_windows if key not in before))
        challengers = [actor for actor in order if choices[actor] == "challenge"]
        completed = 0
        for actor in challengers:
            if not state.players[actor].alive or self.form != "savior":
                continue
            self.challenge(state, actor, plan.request.root_id)
            completed += 1
            if self.form != "savior" or state.game_over:
                trace(state, "judgment_cancelled", actor=self.owner_id)
                return
        refusers = [a for a in order if choices[a] == "decline" and state.players[a].alive]
        judgment_amount = cfg["judgment_pool_per_segment"] * max(
            0, damage - completed * cfg["challenge_segment_reduction"])
        self.pool(state, plan, [a for a in challengers if state.players[a].alive],
                  cfg["counter_pool_per_ruin"] * damage, absolute=False,
                  final=not refusers or not judgment_amount)
        if not state.game_over:
            self.pool(state, plan, [a for a in refusers if state.players[a].alive], judgment_amount,
                      absolute=True, final=True)
        if not state.game_over and self.form == "savior":
            self.exit(state)

    def challenge(self, state: GameState, actor: str, parent_root: str) -> None:
        """临时解决距离，只允许一个实际普通武器攻击，不发T0/T2。"""
        original_grant, original_stage = state.current_grant, state.current_stage
        grant = ActionGrant(f"{parent_root}/response/{actor}", actor, GrantKind.REACTION)
        role = state.ga_roles.get(actor)
        raw = role.reaction_attacks(state, grant) if role else attack_choices(
            state, grant, chase=True)
        candidates = []
        for candidate in raw:
            if candidate.request.target_ids != (self.owner_id,):
                continue
            effects = tuple(replace(e, scope=TargetScope.RANGED_SINGLE,
                                    parameters={**dict(e.parameters), "ignore_range_penalty": True})
                            for e in candidate.effects)
            candidates.append(replace(candidate, effects=effects))
        state.ga_response_options[grant.grant_id] = tuple(candidates)
        state.current_grant, state.current_stage = grant, TurnStage.T1
        state.ga_quiet_roots.add(f"{grant.grant_id}/T1")
        try:
            options = build_options(state, grant, TurnStage.T1)
            if not options:
                return
            names = tuple(str(index) for index in range(len(options)))
            chosen = ask(state, self.owner_id, actor, names, kind="challenge_weapon",
                         context={"weapons": [o.request.to_dict() for o in options]})
            execute_action(state, validate_request(state, options[int(chosen)].request))
        finally:
            state.current_grant, state.current_stage = original_grant, original_stage
            state.ga_response_options.pop(grant.grant_id, None)

    def pool(
        self, state: GameState, plan: ActionPlan, targets: list[str], amount: int, *,
        absolute: bool,
        final: bool = False,
    ) -> None:
        """余数按先攻/ID，池守恒，不给每个人复制一次总池。"""
        if not targets or not amount:
            return
        if not absolute:
            for callback in tuple(state.ga_pool_bonus_hooks):
                amount = callback(state, self.owner_id, amount)
        share, remainder = divmod(amount, len(targets))
        effects = []
        suffix = "judgment" if absolute else "counter"
        for index, target in enumerate(targets):
            damage = share + (index < remainder)
            if not damage:
                continue
            effect = packet(state, plan.request, damage, targets=(target,),
                            source="g4_judgment_pool" if absolute else "g4_counter",
                            suffix=f"{suffix}/{index}", kind=DamageKind.DIRECT if absolute else
                            DamageKind.NORMAL, scope=TargetScope.GLOBAL_AREA, certain=True,
                            absolute=absolute)
            effects.append(replace(effect, batch_id=f"{plan.request.root_id}/{suffix}-pool"))
        if final:
            finalizers = state.ga_batch_finalizers.setdefault(effects[0].batch_id, [])
            finalizers.append(self.finish_judgment)
        resolve_batch(state, tuple(effects))

    def finish_judgment(self, state: GameState) -> None:
        """最终目标批次完成后、成绩定格前恢复人形。"""
        if self.form == "savior" and state.players[self.owner_id].alive:
            self.exit(state)

    def boundary(self, state: GameState, *, boundary: str) -> None:
        """烈阳余波是T2附加磨甲，不是追击或额外回合。"""
        if self.form != "savior" or not state.players[self.owner_id].alive:
            return
        if boundary == "T2" and state.current_grant.actor_id == self.owner_id and (
            self.splash_pending
        ):
            target = self.splash_pending
            self.splash_pending = None
            targets = tuple(key for key in enemies(state, self.owner_id) if key != target)
            if targets:
                request = ability(state.current_grant, TurnStage.T1, "g4_splash",
                                  targets=targets).request
                resolve_batch(state, (packet(state, request, state.ga_config.get(
                    "talents", "G4", "splash_wear"), kind=DamageKind.PURE_WEAR,
                    scope=TargetScope.LOCAL_AREA, certain=True),))
        elif boundary == "R4.expiry" and self.form_timer and not self.burden:
            self.form_timer = self.form_timer.on_r4(state.current_round)
            if not self.form_timer.remaining:
                self.exit(state)

    def sun(self, state: GameState, *, effect: EffectSpec, target: str) -> None:
        """一次根对同一目标只附一份烈阳，不递归复制反击或固定总池。"""
        if not units(state)[target].alive or self.form != "savior":
            return
        from engine.ga.contracts import (
            BarrierPolicy, DamageAttribute, DeathPolicy, EffectKind, HitPolicy,
        )
        fire = replace(effect, effect_id=f"{effect.effect_id}/sun", source_id="burden_fire",
            batch_id=f"{effect.batch_id}/sun/{target}", target_ids=(target,),
            damage_kind=DamageKind.NORMAL,
            amount=state.ga_config.get("poems", "burden_fire_damage"),
            kind=EffectKind.DAMAGE, death_policy=DeathPolicy.NORMAL,
            barrier_policy=BarrierPolicy.RESPECT,
            attribute=DamageAttribute.MAGIC, hit_policy=HitPolicy.CERTAIN, parameters={})
        resolve_batch(state, (fire,))

    def receive_poem(self, state: GameState, giver: str) -> None:
        """补充火种/毁伤可重复兑现，永久烈阳只装一次。"""
        if self.form == "human":
            self.ember = min(state.ga_config.get("talents", "G4", "ember_cap"), self.ember +
                             state.ga_config.get("poems", "burden_resource_gain"))
        else:
            self.ruin = min(state.ga_config.get("talents", "G4", "ruin_cap"), self.ruin +
                            state.ga_config.get("poems", "burden_resource_gain"))
        self.burden = True
