"""G0：专属AR、四遗物、可攻击无人机、十字炮火与有限呼吸窗口。"""

from __future__ import annotations

import math
from dataclasses import replace
from functools import partial
from itertools import combinations
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    ActionVerb, CostEntry, DamageAttribute, DamageKind, DeathOutcome, DeathPolicy,
    Performance, TargetScope, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.extensions import IncomingModifier, Replacement, chapter, high_impact, units
from engine.ga.gameplay import _visible
from engine.ga.state import GaPlayer, emit, finish_if_needed, touch
from engine.ga.status import heal, put_status
from engine.ga.talents.base import (
    Talent, ability, barrier, enemies, hp_cost, own_slot, packet, timer,
)

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec, ResolutionRecord
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant


RELICS = ("shield", "belt", "sight", "glasses")


class G0(Talent):
    """资源与资格均属于本局角色，不借旧十三人支援技表。"""

    talent_id = "G0"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.relics = set(RELICS)
        self.mounted: tuple[str, ...] = ()
        self.seen_relics: set[str] = set()
        self.drone_timer: Any = None
        self.breath_timer: Any = None
        self.breath_used = 0
        self.paid_hp_round = 0
        self.shield_residual = 0
        self.crossfire_used = False
        self.root_hp: dict[str, int] = {}
        self.crossfire_roots: set[str] = set()
        self.tomorrow = False
        self.promise = False

    @property
    def drone_id(self) -> str:
        """稳定NPC身份，重部署不创建无限存活分身。"""
        return f"npc/drone/{self.owner_id}"

    def public(self) -> Mapping[str, Any]:
        """公开遗物、无人机与呼吸余期。"""
        return {"relics": sorted(self.relics), "mounted": self.mounted,
                "breath_used": self.breath_used,
                "breath_ticks": self.breath_timer.remaining if self.breath_timer else 0,
                "drone_ticks": self.drone_timer.remaining if self.drone_timer else 0}

    def register(self, state: GameState) -> None:
        """所有位移都同步随身无人机，包含强制驱逐。"""
        super().register(state)
        state.ga_location_listeners.append(self.location_changed)

    def location_changed(self, state: GameState, actor_id: str) -> None:
        """只有拥有者的实际位移才搬运自己的无人机。"""
        if actor_id == self.owner_id and self.drone_alive(state):
            state.ga_units[self.drone_id].location = state.players[actor_id].location

    def reaction_attacks(self, state: GameState, grant: ActionGrant) -> tuple[ActionOption, ...]:
        """普通AR也是合法挑战武器，但不能部署或公演。"""
        from engine.ga.actions import GrantKind
        base = super().reaction_attacks(state, grant)
        return (*base, *(o for o in self.options(state, replace(grant, kind=GrantKind.STANDARD),
                                                TurnStage.T1) if o.request.operation == "g0_ar"))

    def drone_alive(self, state: GameState) -> bool:
        """无人机必须确实仍有HP和持续期。"""
        return bool(self.drone_timer and self.drone_timer.remaining and
                    self.drone_id in state.ga_units and state.ga_units[self.drone_id].alive and
                    not state.ga_units[self.drone_id].submerged_sources)

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """挂载/部署占T0；AR、公演与补给各占T1。"""
        if not own_slot(state, self.owner_id, grant):
            return ()
        cfg = state.ga_config.get("talents", "G0")
        player = state.players[self.owner_id]
        result = []
        discount = state.ga_config.get("hospital", "furnace_g0_hp_discount") if (
            player.surgery == "furnace"
        ) else 0
        if stage is TurnStage.T0:
            for count in range(cfg["relic_slots"] + 1):
                for mounted in combinations(sorted(self.relics), count):
                    if mounted != self.mounted:
                        result.append(ability(grant, stage, "g0_mount", parameters={
                            "relics": mounted}, after=self.mount))
            cost = hp_cost(state, self.owner_id, cfg["drone_hp_ratio"], discount=discount)
            if not self.drone_alive(state) and cost is not None:
                result.append(ability(grant, stage, "g0_drone", mode=Performance.INSTANT,
                                      costs=(CostEntry(self.owner_id, "hp", cost),),
                                      after=self.deploy))
            return tuple(result)
        if stage is not TurnStage.T1:
            return ()
        drone = self.drone_alive(state)
        attribute = DamageAttribute.MYSTERIOUS if drone else DamageAttribute.NORMAL
        for target in enemies(state, self.owner_id, local=False):
            if not _visible(player, units(state)[target], DamageAttribute.NORMAL):
                continue
            for mode in (() if "ar" in player.disabled_weapons else ("point", "auto")):
                candidate = ability(grant, stage, "g0_ar", verb=ActionVerb.ATTACK,
                                    targets=(target,), parameters={"fire_mode": mode},
                                    costs=(CostEntry(self.owner_id, "ammo:ar",
                                                     cfg[f"{mode}_ammo"]),),
                                    hostile=True)
                amount = cfg[f"{mode}_damage"] + (cfg["drone_attack_bonus"] if drone else 0)
                if "WEAK" in player.statuses:
                    amount += state.ga_config.get("status", "weakness")
                params: dict[str, Any] = {}
                if "sight" in self.mounted or grant.locked_target == target:
                    params.update(ignore_move_evasion=True, hit_bonus=state.ga_config.get(
                        "accuracy", "lock_bonus"), defense_coefficient=state.ga_config.get(
                            "accuracy", "lock_defense_coefficient"))
                if mode == "auto":
                    params["attached_wear"] = cfg["auto_attached_wear"]
                result.append(replace(candidate, effects=(packet(
                    state, candidate.request, max(0, amount), source="g0_ar", attribute=attribute,
                    scope=TargetScope.RANGED_SINGLE, **params,
                ),)))
        cost = 0 if self.promise else hp_cost(state, self.owner_id, cfg["crossfire_hp_ratio"],
                                             discount=discount)
        targets = enemies(state, self.owner_id)
        if drone and targets and cost is not None:
            choices = [(), *((key,) for key in self.mounted)]
            if self.promise:
                choices.extend(combinations(self.mounted, state.ga_config.get(
                    "poems", "tomorrow_overloads")))
            for overloaded in choices:
                overload = overloaded[0] if overloaded else "none"
                damage = cfg["crossfire_damage"] * (cfg["overload_damage_multiplier"] if (
                    "belt" in overloaded
                ) else 1)
                parameters = {"destroy_relic": overload}
                if self.promise:
                    parameters["promise_relics"] = tuple(overloaded)
                candidate = ability(grant, stage, "g0_crossfire", mode=Performance.PUBLIC,
                                    targets=targets, parameters=parameters,
                                    costs=(CostEntry(self.owner_id, "hp", cost),), hostile=True,
                                    after=self.crossfire_end)
                result.append(replace(candidate, effects=(packet(
                    state, candidate.request, damage, kind=DamageKind.DIRECT, certain=True,
                    scope=TargetScope.LOCAL_AREA, bypass="sight" in overloaded,
                    statuses=("SHOCKED",) if "shield" in overloaded else (),
                ),)))
        if player.location == "shop" and player.ammo.get("ar", 0) < cfg["ammo_cap"]:
            price = 0 if self.breath_timer else state.ga_config.get(
                "weapons", "bow", "refill_price")
            result.append(ability(grant, stage, "g0_refill", verb=ActionVerb.INTERACT,
                                  costs=(CostEntry(self.owner_id, "credits", price),),
                                  after=self.refill))
        return tuple(result)

    def mount(self, state: GameState, plan: ActionPlan) -> None:
        """同轮卸装不补屏障；四遗物的挂载经历是永久记录。"""
        self.mounted = tuple(plan.request.parameters["relics"])
        self.seen_relics.update(self.mounted)
        self.sync_shield(state)
        if len(self.mounted) >= state.ga_config.get("talents", "G0", "relic_slots"):
            chapter(state, self.owner_id, 1)
        self.check_final_chapter(state)

    def sync_shield(self, state: GameState) -> None:
        """离槽收起防护，重新挂载只恢复实际剩值。"""
        player = state.players[self.owner_id]
        key = f"g0/relic-shield/{self.owner_id}"
        current = player.defenses.get(key)
        if current:
            self.shield_residual = current.durability
        player.defenses.pop(key, None)
        if "shield" in self.mounted and self.shield_residual:
            barrier(state, self.owner_id, key, self.shield_residual)

    def deploy(self, state: GameState, plan: ActionPlan) -> None:
        """部署一个可被正常攻击的真实NPC。"""
        cfg = state.ga_config.get("talents", "G0")
        state.ga_units[self.drone_id] = GaPlayer(
            self.drone_id, cfg["drone_hp"], cfg["drone_hp"], 0, 0, 0,
            state.players[self.owner_id].location, awake=True, home_locked=True,
            is_player=False, unit_kind="drone", owner_id=self.owner_id,
        )
        state.ga_death_cleaned.discard(self.drone_id)
        self.drone_timer = timer(state, self.owner_id, "g0/drone", cfg["drone_ticks"])
        high_impact(state, self.owner_id, plan.request.root_id)

    def refill(self, state: GameState, plan: ActionPlan) -> None:
        """补给按箭矢1:3转化，免费窗口不突破子弹容量。"""
        cfg = state.ga_config.get("talents", "G0")
        player = state.players[self.owner_id]
        player.ammo["ar"] = min(cfg["ammo_cap"], player.ammo.get("ar", 0) +
                                state.ga_config.get("weapons", "bow", "refill_amount") *
                                cfg["arrow_conversion"])

    def crossfire_end(self, state: GameState, plan: ActionPlan) -> None:
        """无人机真实消失；绝唱只消耗选中的实物一次。"""
        if plan.request.root_id in self.crossfire_roots:
            return
        self.crossfire_roots.add(plan.request.root_id)
        player = state.players[self.owner_id]
        relic = plan.request.parameters["destroy_relic"]
        promised = "promise_relics" in plan.request.parameters
        self.crossfire_used = True
        self.drone_timer = None
        state.ga_units[self.drone_id].hp = 0
        relics = plan.request.parameters["promise_relics"] if promised else (
            (relic,) if relic != "none" else ())
        for relic in relics:
            self.seen_relics.add(relic)
            if not promised and plan.request.parameters.get("encore_waiver") != "destroy":
                self.relics.discard(relic)
                self.mounted = tuple(key for key in self.mounted if key != relic)
            if relic == "shield":
                barrier(state, self.owner_id, "g0/overload", state.ga_config.get(
                    "talents", "G0", "overload_barrier"), ticks=state.ga_config.get(
                        "status", "temporary_barrier_ticks"))
            elif relic == "glasses":
                player.hp = max(player.hp, min(player.max_hp, state.ga_config.get(
                    "talents", "G0", "overload_hp_floor")))
        if promised:
            self.promise = False
            if self.breath_timer:
                heal(player, state.ga_config.get("poems", "tomorrow_breath_heal"))
        self.sync_shield(state)
        self.check_final_chapter(state)
        touch(state)

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """明天消费减费只修正实际信用点报价，最低1，不返还旧账。"""
        if not self.tomorrow or grant.actor_id != self.owner_id or state.players[
            self.owner_id].location not in (
            "shop", "hospital", "military"):
            return options
        return tuple(replace(o, definition=replace(o.definition, costs=tuple(replace(c, amount=max(
            state.ga_config.get("poems", "tomorrow_min_price"), c.amount - state.ga_config.get(
                "poems", "tomorrow_discount"))) if c.account == "credits" and c.amount else c
                for c in o.definition.costs))) for o in options)

    def check_final_chapter(self, state: GameState) -> None:
        """四件均有经历且炮火已实际释放。"""
        if set(RELICS) <= self.seen_relics and self.crossfire_used:
            chapter(state, self.owner_id, 3)

    def receive_poem(self, state: GameState, giver: str) -> None:
        """永久打工/消费改造只设一次，下一炮火承诺重复刷新。"""
        self.tomorrow = True
        self.promise = True

    def before_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """只记真实技能HP支付，不把弹药或被打伤当烧血。"""
        if plan.request.actor_id == self.owner_id and any(c.account == "hp" and c.amount for c in
                                                        plan.costs):
            self.paid_hp_round = state.current_round
        if plan.request.actor_id == self.owner_id and plan.request.operation == "g0_crossfire":
            state.ga_batch_finalizers.setdefault(option.effects[0].batch_id, []).append(
                partial(self.crossfire_end, plan=plan))

    def after_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """起床替换弓；随身无人机同步；弹链溅射保持同一根行动。"""
        request = plan.request
        if request.actor_id != self.owner_id:
            return
        if request.operation == "work" and self.tomorrow:
            state.players[self.owner_id].credits += state.ga_config.get(
                "poems", "tomorrow_work_bonus")
        player = state.players[self.owner_id]
        cfg = state.ga_config.get("talents", "G0")
        if request.operation == "wake":
            player.weapons.discard("bow")
            player.ammo.pop("bow", None)
            player.weapons.add("ar")
            player.ammo["ar"] = cfg["initial_ammo"]
        if self.drone_alive(state):
            state.ga_units[self.drone_id].location = player.location
        if request.operation == "g0_ar" and request.parameters["fire_mode"] == "auto" and (
            "belt" in self.mounted
        ):
            primary = units(state)[request.target_ids[0]]
            targets = tuple(key for key in enemies(state, self.owner_id, local=False) if
                            key != primary.player_id and
                            units(state)[key].location == primary.location)
            if targets:
                chosen = targets[state.ga_rng.die(f"{request.root_id}/splash", len(targets),
                                                   actor_id=self.owner_id) - 1]
                attribute = DamageAttribute.MYSTERIOUS if self.drone_alive(state) else (
                    DamageAttribute.NORMAL)
                resolve_batch(state, (packet(state, request, cfg["splash_damage"],
                                             targets=(chosen,),
                                             source="g0_ar", suffix="splash", certain=True,
                                             scope=TargetScope.RANGED_SINGLE,
                                             attribute=attribute),))

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """呼吸只阻止外部普通HP损失，绝对致死来源仍可杀死。"""
        return IncomingModifier(immune=target.player_id == self.owner_id and
                                self.breath_timer is not None and
                                effect.death_policy is DeathPolicy.NORMAL)

    def on_events(self, state: GameState, events: tuple[Any, ...]) -> None:
        """攻击真实无人机为持有人登记焦点，不生成玩家伤害分或犯罪。"""
        for event in events:
            if event.kind in (EventKind.ACTION_ACTIVATED, EventKind.HP_DAMAGED) and (
                event.target_id == self.drone_id and event.actor_id != self.owner_id and
                event.effective and state.players[self.owner_id].alive
            ):
                high_impact(state, self.owner_id, event.root_id or event.batch_id)

    def lethal(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> Replacement | None:
        """每局一次、自身转折，优先于保险。"""
        if target.player_id == self.owner_id and self.breath_used < state.ga_config.get(
            "talents", "G0", "breath_uses"
        ):
            return Replacement(DeathOutcome.MORPHED, 1, "g0_breath", {})
        return None

    def commit_lethal(
        self, state: GameState, effect: EffectSpec, target: GaPlayer, result: Replacement,
    ) -> None:
        """同一击超额不追入呼吸后的肉身。"""
        self.breath_used += 1
        self.breath_timer = timer(state, self.owner_id, "g0/breath", state.ga_config.get(
            "talents", "G0", "breath_ticks"))
        target.statuses.pop("control", None)

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """吸血按同根累计实际敌方HP取floor，屏障和过量伤害不计。"""
        for record in records:
            if record.effect.actor_id != self.owner_id:
                continue
            if record.effect.source_id == "g0_crossfire":
                self.crossfire_used = True
            if record.effect.source_id == "g0_ar" and self.drone_alive(state) and (
                record.effective_hit
            ):
                chapter(state, self.owner_id, 2)
            if self.breath_timer and record.hp_loss and record.target_id != self.owner_id and (
                units(state)[record.target_id].owner_id != self.owner_id
            ):
                root = record.effect.root_id or record.effect.batch_id
                old = self.root_hp.get(root, 0)
                self.root_hp[root] = old + record.hp_loss
                ratio = state.ga_config.get("talents", "G0", "breath_lifesteal_ratio")
                heal(state.players[self.owner_id], math.floor(self.root_hp[root] * ratio) -
                     math.floor(old * ratio))
        self.sync_shield(state)
        self.check_final_chapter(state)

    def boundary(self, state: GameState, *, boundary: str) -> None:
        """未来R4裁决；失败是退场，不是给最后攻击者补一个人头。"""
        player = state.players[self.owner_id]
        cfg = state.ga_config.get("talents", "G0")
        if boundary == "R4.expiry" and self.drone_timer:
            self.drone_timer = self.drone_timer.on_r4(state.current_round)
            if not self.drone_timer.remaining:
                state.ga_units[self.drone_id].hp = 0
                self.drone_timer = None
        if not player.alive:
            self.breath_timer = None
            return
        if boundary == "R0" and "shield" in self.mounted:
            self.shield_residual = cfg["shield_barrier"]
            if any(role.talent_id == "G7" and state.players[key].location == player.location for
                   key, role in state.ga_roles.items()):
                self.shield_residual = cfg["synergy_barrier"]
            player.defenses.pop(f"g0/relic-shield/{self.owner_id}", None)
            self.sync_shield(state)
        if boundary == "R4.recovery" and "glasses" in self.mounted and (
            self.paid_hp_round == state.current_round
        ):
            heal(player, cfg["glasses_heal"])
        if boundary == "R4.expiry":
            if self.breath_timer:
                self.breath_timer = self.breath_timer.on_r4(state.current_round)
                if not self.breath_timer.remaining:
                    self.breath_timer = None
                    if player.hp < cfg["breath_success_hp"]:
                        from engine.ga.state import record_death
                        player.exited = True
                        record_death(state, self.owner_id, absolute=False)
                        emit(state, EventKind.ACTOR_EXITED, actor_id=self.owner_id,
                             effective=True, payload={"reason": "breath_failed"})
                        finish_if_needed(state)
