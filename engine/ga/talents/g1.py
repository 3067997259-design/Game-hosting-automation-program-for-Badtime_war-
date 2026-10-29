"""G1：卸甲/SAM/完全燃烧、有限炽愿、失熵与繁育绝唱。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    ActionVerb, DamageKind, DeathOutcome, Performance, TargetScope, TurnStage,
)
from engine.ga.extensions import IncomingModifier, Replacement, ask, chapter, high_impact, units
from engine.ga.runtime import queue_followup
from engine.ga.state import LOCATIONS, finish_if_needed, next_id, touch
from engine.ga.status import DEBUFFS, cleanse_burns, heal
from engine.ga.talents.base import Talent, ability, barrier, own_slot, packet, timer

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec, ResolutionRecord
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant, GaPlayer


class G1(Talent):
    """失熵不是敌对伤害；形态期限按实际未来R4运行。"""

    talent_id = "G1"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.form = "unarmored"
        self.entropy = 0
        self.overload = 0
        self.full_opened = False
        self.propagation_used = 0
        self.form_timer: Any = None
        self.structure_round = 0
        self.round_form = "unarmored"
        self.attacked_round = 0
        self.ardent_serial = 0
        self.rescued = False
        self.firefly_safe = False

    def public(self) -> Mapping[str, Any]:
        """公开形态、失熵和有限资格。"""
        return {"form": self.form, "entropy": self.entropy, "overload": self.overload,
                "full_opened": self.full_opened, "propagation_used": self.propagation_used,
                "form_ticks": self.form_timer.remaining if self.form_timer else 0}

    def register(self, state: GameState) -> None:
        """首轮资源读取N0，不额外执行一次R0回复。"""
        super().register(state)
        self.entropy = state.ga_config.get("talents", "G1", "entropy_initial")
        self.gain_ardent(state, state.ga_config.get("talents", "G1", "ardent_initial"))

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """着装/卸甲占T0，完全燃烧占公演T1，超新星以move为根。"""
        if self.rescued:
            return ()
        follow_move = (grant.actor_id == self.owner_id and self.form == "propagation" and
                       grant.kind.value == "step_followup" and state.players[self.owner_id].alive)
        if not own_slot(state, self.owner_id, grant) and not follow_move:
            return ()
        result = []
        if stage is TurnStage.T0:
            if self.form == "unarmored":
                result.append(ability(grant, stage, "g1_sam", mode=Performance.INSTANT,
                                      after=self.change_form))
            elif self.form == "sam":
                result.append(ability(grant, stage, "g1_unarmor", after=self.change_form))
        if stage is TurnStage.T1:
            if self.form == "sam":
                result.append(ability(grant, stage, "g1_full", mode=Performance.PUBLIC,
                                      after=self.change_form))
            if self.form != "unarmored" and (self.overload or self.form == "propagation"):
                for location in LOCATIONS:
                    if location in state.destroyed_locations:
                        continue
                    player = state.players[self.owner_id]
                    targets = tuple(key for key, p in units(state).items() if p.alive and
                                    p.home_locked and key != self.owner_id and
                                    p.location == location and
                                    p.isolation_id == player.isolation_id)
                    if not targets and location == player.location:
                        continue
                    candidate = ability(grant, stage, "g1_supernova", verb=ActionVerb.MOVE,
                                        targets=targets, parameters={"location": location},
                                        hostile=bool(targets), after=self.supernova_end)
                    if targets:
                        cfg = state.ga_config.get("talents", "G1")
                        name = "propagation" if self.form == "propagation" else self.form
                        result.append(replace(candidate, effects=(packet(
                            state, candidate.request, cfg[f"supernova_{name}_damage"],
                            scope=TargetScope.GLOBAL_AREA, certain=True,
                            defense_coefficient=cfg["supernova_defense_coefficient"],
                            burn_stacks=cfg[f"supernova_{name}_burn"],
                        ),)))
                    else:
                        result.append(candidate)
        return tuple(result)

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """普通攻击加值只加一次；已含形态增益的超新星不重复加成。"""
        if grant.actor_id != self.owner_id:
            return options
        cfg = state.ga_config.get("talents", "G1")
        result = []
        for option in options:
            if self.form != "unarmored" and (option.request.verb is ActionVerb.INTERACT or
                                             option.request.operation == "search"):
                continue
            if self.form == "propagation" and self.structure_round != state.current_round and (
                option.request.operation == "move"
            ):
                continue
            if self.form == "propagation" and self.structure_round == state.current_round and (
                option.request.operation == "g1_supernova"
            ):
                continue
            costs = option.definition.costs
            if self.form == "unarmored" and option.request.verb is ActionVerb.INTERACT and (
                option.request.operation != "surgery"
            ):
                costs = tuple(replace(c, amount=max(cfg["minimum_interact_price"], c.amount - cfg[
                    "interact_discount"])) if c.account == "credits" and c.amount else c
                              for c in costs)
            effects = []
            for effect in option.effects:
                if effect.damage_kind is not DamageKind.NORMAL:
                    effects.append(effect)
                    continue
                form_bonus = cfg[f"{self.form}_attack"] if self.form != "unarmored" else (
                    0 if self.rescued else cfg["unarmored_attack"])
                if effect.source_id == "g1_supernova":
                    form_bonus = state.ga_config.get("status", "weakness") if (
                        "WEAK" in state.players[self.owner_id].statuses
                    ) else 0
                for target in effect.target_ids:
                    enemy = units(state)[target]
                    bonus = cfg["break_bonus"] if self.form != "unarmored" and (
                        enemy.burns or "control" in enemy.statuses or "WEAK" in enemy.statuses
                    ) else 0
                    params = dict(effect.parameters)
                    if self.form == "unarmored" and not self.rescued:
                        params["hit_bonus"] = params.get("hit_bonus", 0) - cfg[
                            "unarmored_hit_penalty"]
                    effects.append(replace(effect, effect_id=f"{effect.effect_id}/{target}",
                                           target_ids=(target,), amount=max(0, effect.amount +
                                                                           form_bonus + bonus),
                                           parameters=params))
            result.append(replace(option, definition=replace(option.definition, costs=costs),
                                  effects=tuple(effects)))
        return tuple(result)

    def change_form(self, state: GameState, plan: ActionPlan) -> None:
        """不允许完全燃烧主动卸甲或续杯；初始化不多发一次R0回复。"""
        cfg = state.ga_config.get("talents", "G1")
        operation = plan.request.operation
        if operation == "g1_sam":
            self.form = "sam"
            self.overload = min(cfg["overload_cap"], self.overload + cfg["overload_gain"])
            chapter(state, self.owner_id, 1)
        elif operation == "g1_unarmor":
            self.form = "unarmored"
        else:
            self.form = "full"
            self.full_opened = True
            self.form_timer = timer(state, self.owner_id, "g1/full", cfg["full_ticks"])
        ranks = {"unarmored": 0, "sam": 1, "full": 2, "propagation": 3}
        if ranks[self.form] > ranks[self.round_form]:
            self.round_form = self.form
        high_impact(state, self.owner_id, plan.request.root_id)
        touch(state)

    def before_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """超新星先移动再打落点；只有结构段允许摧毁建筑。"""
        request = plan.request
        if request.actor_id != self.owner_id:
            return
        if request.verb is ActionVerb.ATTACK or request.operation == "g1_supernova":
            self.attacked_round = state.current_round
        if request.operation == "g1_supernova":
            from engine.ga.extensions import relocate
            relocate(state, self.owner_id, request.parameters["location"], active=True)
            if self.form == "propagation":
                self.structure_round = state.current_round
                state.ga_action_meta[request.root_id]["destroy_location"] = request.parameters[
                    "location"]
            else:
                self.overload -= 1

    def supernova_end(self, state: GameState, plan: ActionPlan) -> None:
        """无目标的结构移动仍需在落点执行合法缩圈。"""
        self.destroy_pending(state, plan.request.root_id)

    def destroy_pending(self, state: GameState, root: str) -> None:
        """主批结算后只摧毁一次，最后一栋安全建筑保留。"""
        from engine.ga.extensions import destroy_location
        meta = state.ga_action_meta.get(root, {})
        location = meta.pop("destroy_location", None)
        if location:
            destroy_location(state, location)

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """着甲免疫终焉，卸甲半伤；普通防护不降低直达伤害。"""
        if target.player_id != self.owner_id:
            return IncomingModifier()
        cfg = state.ga_config.get("talents", "G1")
        if effect.source_id == "environment":
            ratio = cfg["unarmored_apocalypse_ratio"] if self.form == "unarmored" else cfg[
                "armored_apocalypse_ratio"]
            return IncomingModifier(immune=ratio == 0, hp_ratio=ratio)
        reduction = cfg["armored_reduction"] if self.form != "unarmored" else 0
        return IncomingModifier(reduction=reduction,
                                vulnerability=cfg["unarmored_incoming"] if self.form ==
                                "unarmored" and not self.rescued else 0)

    def lethal(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> Replacement | None:
        """曾开启完全燃烧且当前着甲，才有一次繁育自身转折。"""
        cfg = state.ga_config.get("talents", "G1")
        if target.player_id == self.owner_id and self.full_opened and (
            self.form in ("sam", "full") and
            self.propagation_used < cfg["propagation_uses"]
        ):
            return Replacement(DeathOutcome.MORPHED, min(target.max_hp, cfg["propagation_hp"]),
                               "g1_propagation", {})
        return None

    def commit_lethal(
        self, state: GameState, effect: EffectSpec, target: GaPlayer, result: Replacement,
    ) -> None:
        """繁育不叠保险；普通致死第二次不再繁育。"""
        self.form = "propagation"
        self.propagation_used += 1
        self.form_timer = timer(state, self.owner_id, "g1/propagation", state.ga_config.get(
            "talents", "G1", "propagation_ticks"))
        # 飞萤之火：清空并免疫全部减益状态（§3.7），含灼烧。
        target.status_immunities = DEBUFFS
        target.statuses.clear()
        cleanse_burns(target)
        target.initiative_bonus += state.ga_config.get("talents", "G1", "propagation_initiative")

    def ardent(self, state: GameState) -> tuple[str, ...]:
        """每层炽愿都是会耗尽的有限护体池。"""
        return tuple(key for key, d in state.players[self.owner_id].defenses.items() if
                     key.startswith(f"g1/ardent/{self.owner_id}/") and d.durability)

    def gain_ardent(self, state: GameState, amount: int) -> None:
        """满层不刷新残层，不生成可重复免费恢复的吸收值。"""
        from engine.ga.contracts import DefenseKind
        cfg = state.ga_config.get("talents", "G1")
        for _ in range(min(amount, cfg["ardent_cap"] - len(self.ardent(state)))):
            self.ardent_serial += 1
            barrier(state, self.owner_id, f"g1/ardent/{self.owner_id}/{self.ardent_serial}",
                    cfg["ardent_absorption"], kind=DefenseKind.HALO)

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """有效攻击、超新星命中与击杀均由实际结果驱动。"""
        cfg = state.ga_config.get("talents", "G1")
        for record in records:
            if record.effect.actor_id != self.owner_id:
                continue
            if self.form == "full" and record.effective_hit:
                chapter(state, self.owner_id, 2)
            if record.death_outcome.value in ("dead", "absolute_dead"):
                self.overload = min(cfg["overload_cap"], self.overload + cfg["overload_gain"])
                if self.form == "propagation" and state.current_grant and (
                    state.current_grant.actor_id == self.owner_id
                ):
                    state.ga_propagation_followups.add(self.owner_id)
            if record.effect.source_id == "g1_supernova":
                if record.outcome.value == "hit" and record.remaining_damage:
                    self.gain_ardent(state, cfg["ardent_hit_gain"])
                if record.death_outcome.value in ("dead", "absolute_dead"):
                    self.gain_ardent(state, cfg["ardent_kill_gain"])
        for root in {r.effect.root_id for r in records if r.effect.actor_id == self.owner_id and
                     r.effect.source_id == "g1_supernova"}:
            self.destroy_pending(state, root)

    def entropy_settlement(self, state: GameState) -> None:
        """炽愿→自选未碎普通甲→HP；不触发关注、犯罪或敌对受伤被动。"""
        player = state.players[self.owner_id]
        if not player.alive:
            return
        ardent = self.ardent(state)
        if ardent:
            player.defenses.pop(ardent[0])
            return
        choices = tuple(key for key, d in player.defenses.items() if d.kind.value == "armor" and
                        d.durability > 0)
        if choices:
            key = ask(state, self.owner_id, self.owner_id, choices, kind="entropy_armor")
            player.defenses[key] = replace(player.defenses[key], durability=0)
        else:
            from engine.ga.state import ActionGrant
            from engine.ga.actions import GrantKind
            grant = ActionGrant(next_id(state, "entropy"), self.owner_id, GrantKind.REACTION)
            request = ability(grant, TurnStage.T1, "g1_entropy").request
            state.ga_quiet_roots.add(request.root_id)
            effect = packet(state, request, state.ga_config.get("talents", "G1", "entropy_hp_loss"),
                            targets=(self.owner_id,), kind=DamageKind.DIRECT, certain=True)
            resolve_batch(state, (replace(effect, actor_id=None, score_owner_id=None,
                                          crime_owner_id=None, parameters={"hostile": False}),))

    def boundary(self, state: GameState, *, boundary: str) -> None:
        """行动开始档决定本轮失熵；世界时钟暂停也照常消费时限。"""
        player = state.players[self.owner_id]
        cfg = state.ga_config.get("talents", "G1")
        if not player.alive:
            return
        if boundary == "R0":
            self.round_form = self.form
            if self.form == "full":
                heal(player, cfg["full_heal"])
        elif boundary == "slot_start" and state.current_grant.actor_id == self.owner_id:
            if state.current_grant.kind.value == "standard":
                ranks = {"unarmored": 0, "sam": 1, "full": 2, "propagation": 3}
                if ranks[self.form] > ranks[self.round_form]:
                    self.round_form = self.form
        elif boundary == "T2" and state.current_grant.actor_id == self.owner_id:
            if self.form == "full" or self.owner_id in state.ga_propagation_followups:
                queue_followup(state, self.owner_id, "g1_burning")
                state.ga_propagation_followups.discard(self.owner_id)
        elif boundary == "R4.dot_maintenance":
            if not self.rescued and not self.firefly_safe and (
                state.world_tick < state.ga_config.get(
                "clock", "apocalypse_start") and (
                self.form != "propagation"
            )):
                name = self.round_form if self.round_form in (
                    "unarmored", "sam", "full") else "full"
                gain = cfg[f"entropy_{name}_gain"]
                gain -= state.ga_config.get("hospital", "furnace_g1_entropy_discount") if (
                    player.surgery == "furnace"
                ) else 0
                self.entropy = min(cfg["entropy_cap"], self.entropy + max(0, gain))
                if self.attacked_round != state.current_round:
                    self.entropy = max(0, self.entropy - cfg["entropy_rest_reduction"])
                if self.entropy >= cfg["entropy_threshold"]:
                    self.entropy -= cfg["entropy_reset_amount"]
                    self.entropy_settlement(state)
        elif boundary == "R4.expiry" and self.form_timer:
            self.form_timer = self.form_timer.on_r4(state.current_round)
            if not self.form_timer.remaining:
                self.form_timer = None
                if finish_if_needed(state):
                    return
                if self.form == "propagation":
                    from engine.ga.state import ActionGrant
                    from engine.ga.actions import GrantKind
                    grant = ActionGrant(next_id(state, "propagation-expiry"), self.owner_id,
                                        GrantKind.REACTION)
                    request = ability(grant, TurnStage.T1,
                                      "g1_propagation_expiry").request
                    resolve_batch(state, (packet(state, request, 0, targets=(self.owner_id,),
                                                 kind=DamageKind.EXECUTION, certain=True),))
                else:
                    self.form = "unarmored"
                    if not self.firefly_safe:
                        self.entropy_settlement(state)
                    self.firefly_safe = False
                    if player.alive:
                        chapter(state, self.owner_id, 3)
        touch(state)

    def terminal(self, state: GameState, survivors: tuple[str, ...]) -> None:
        """繁育先赢再计时；不因到期维护把已经赢的角色杀掉。"""
        if survivors == (self.owner_id,) and self.form == "propagation":
            chapter(state, self.owner_id, 3)

    def receive_poem(self, state: GameState, giver: str) -> None:
        """繁育救回是永久去机甲；普通赠诗只开启零失熵的有限安全窗口。"""
        player = state.players[self.owner_id]
        if self.form == "propagation":
            self.rescued = True
            self.form = "unarmored"
            self.form_timer = None
            self.entropy = self.overload = 0
            player.status_immunities = frozenset()
            player.initiative_bonus -= state.ga_config.get(
                "talents", "G1", "propagation_initiative")
            player.hp = min(player.max_hp, state.ga_config.get("poems", "firefly_rescue_hp"))
            chapter(state, self.owner_id, 3)
        elif not self.rescued:
            self.form = "full"
            self.full_opened = True
            self.firefly_safe = True
            self.form_timer = timer(state, self.owner_id, "firefly/safe", state.ga_config.get(
                "poems", "firefly_ticks"))
        touch(state)
