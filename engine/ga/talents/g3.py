"""G3投影魔术：真实魔力账、隔离剑丘、逐段连发与幻想崩坏。"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    ActionVerb, BarrierPolicy, CostEntry, DamageAttribute, DamageKind, DefenseKind, DefenseSpec,
    HitPolicy, Performance, TargetScope, TurnStage,
)
from engine.ga.extensions import IncomingModifier, ask, chapter, high_impact, units
from engine.ga.gameplay import _visible
from engine.ga.state import GaPlayer, LOCATIONS, next_id, touch, trace
from engine.ga.status import put_status
from engine.ga.talents.base import (
    Talent, ability, attack_choices, enemies, own_slot, packet, timer, weapon_option,
)

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec, ResolutionRecord
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant


COPYABLE = ("knife", "bow", "electromagnetic_rifle", "gauss_rifle")


class G3(Talent):
    """结界只挂隔离身份，全部战果/装备差量都不回滚。"""

    talent_id = "G3"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.realm: str | None = None
        self.anchor: str | None = None
        self.captured: dict[str, tuple[str, str | None]] = {}
        self.realm_timer: Any = None
        self.target: str | None = None
        self.seen: set[str] = set()
        self.styles: set[str] = set()
        self.guard = False
        self.channel: str | None = None
        self.pending_old: str | None = None
        self.copy: str | None = None
        self.spent: dict[str, int] = {}
        self.gale_targets: set[str] = set()
        self.ideal = False
        self.eternity = False
        self.eternity_active = False
        self.chain_counts: dict[str, int] = {}

    def public(self) -> Mapping[str, Any]:
        """公共资源由玩家账户公开，角色只展示结构身份与已见证兵装。"""
        return {"realm": self.realm, "anchor": self.anchor, "main_target": self.target,
                "seen": sorted(self.seen), "styles": sorted(self.styles), "ideal": self.ideal}

    def register(self, state: GameState) -> None:
        """普通魔力与临时魔力独立计费，普通先付。"""
        super().register(state)
        state.players[self.owner_id].resources.update(
            magic=state.ga_config.get("talents", "G3", "magic_initial"), temporary_magic=0)

    def available(self, state: GameState) -> int:
        """不是第二个魔力账本，只汇总两个正式账户。"""
        p = state.players[self.owner_id]
        return p.resources["magic"] + p.resources["temporary_magic"]

    def costs(self, state: GameState, amount: int) -> tuple[CostEntry, ...]:
        """报价保持纯函数，临时余额不足由共同预检拒绝。"""
        base = min(amount, state.players[self.owner_id].resources["magic"])
        return (CostEntry(self.owner_id, "resource:magic", base),
                CostEntry(self.owner_id, "resource:temporary_magic", amount - base))

    def pay(self, state: GameState, amount: int) -> bool:
        """根内下一段在启动前重查余额；前段不因后段付不起回滚。"""
        if self.available(state) < amount:
            return False
        p = state.players[self.owner_id]
        base = min(amount, p.resources["magic"])
        p.resources["magic"] -= base
        p.resources["temporary_magic"] -= amount - base
        touch(state)
        return True

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """被困者有真实锚点攻击/破界，施术者有投影和公演。"""
        actor = state.players[grant.actor_id]
        if self.realm and actor.isolation_id == self.realm and grant.actor_id != self.owner_id:
            if stage is TurnStage.T1 and self.anchor and state.ga_units[self.anchor].alive:
                result = [ability(grant, stage, "g3_break", targets=(self.anchor,),
                                  after=self.break_anchor)]
                for original in attack_choices(state, grant):
                    if original.request.target_ids != (self.anchor,):
                        continue
                    candidate = weapon_option(state, grant, original, "g3_anchor_attack",
                                              Performance.NONE)
                    result.append(replace(candidate, effects=tuple(replace(
                        e, hit_policy=HitPolicy.CERTAIN) for e in candidate.effects)))
                return tuple(result)
            return ()
        if not own_slot(state, self.owner_id, grant):
            return ()
        cfg = state.ga_config.get("talents", "G3")
        result = []
        if stage is TurnStage.T0:
            for kind, price in (("rho", cfg["rho_cost"]), ("guard", cfg["dual_guard_cost"])):
                result.append(ability(grant, stage, "g3_trace", parameters={"kind": kind},
                                      costs=self.costs(state, price), after=self.project))
            if not self.realm:
                result.extend(ability(grant, stage, "g3_trace", parameters={"kind": weapon},
                                      costs=self.costs(state, cfg["copy_cost"]), after=self.project)
                              for weapon in sorted(self.seen))
            else:
                for target in enemies(state, self.owner_id, local=False):
                    if target != self.target:
                        option = ability(grant, stage, "g3_target", targets=(target,),
                                         after=self.project)
                        result.append(replace(option, definition=replace(
                            option.definition, consumes_stage=False)))
            return tuple(result)
        if stage is not TurnStage.T1:
            return ()
        targets = enemies(state, self.owner_id, local=False)
        for target in targets:
            if not _visible(actor, units(state)[target], DamageAttribute.NORMAL):
                continue
            for mode in (Performance.NONE, Performance.INSTANT):
                price = cfg["spiral_cost" if mode is Performance.NONE else "enhanced_spiral_cost"]
                if self.realm and self.eternity_active:
                    price = state.ga_config.get("poems", "eternity_first_cost")
                candidate = ability(grant, stage, "g3_spiral", mode=mode, verb=ActionVerb.ATTACK,
                                    targets=(target,), costs=self.costs(state, price), hostile=True,
                                    after=self.chain)
                params = {"hit_bonus": cfg["spiral_hit_bonus"]}
                if mode is Performance.INSTANT:
                    params["defense_coefficient"] = cfg["enhanced_spiral_defense_coefficient"]
                result.append(replace(candidate, effects=(packet(
                    state, candidate.request, cfg["spiral_damage"] if mode is Performance.NONE else
                    cfg["enhanced_spiral_damage"], scope=TargetScope.RANGED_SINGLE,
                    bypass=mode is Performance.INSTANT, **params,
                ),)))
        for original in attack_choices(state, grant, melee=True, local=True):
            for mode in (Performance.NONE, Performance.INSTANT):
                candidate = weapon_option(state, grant, original, "g3_dual", mode,
                                          bonus=cfg["dual_attack_bonus"] + (
                                              cfg["instant_dual_bonus"] if mode is
                                              Performance.INSTANT else 0))
                result.append(replace(candidate, definition=replace(candidate.definition,
                                      costs=(*candidate.definition.costs, *self.costs(
                                          state, cfg["dual_attack_cost"]))), effects=tuple(replace(
                    e, parameters={**dict(e.parameters), "attached_wear": cfg[
                        "instant_dual_wear"] if mode is Performance.INSTANT else 0})
                    for e in candidate.effects)))
        pool = self.seen if self.realm else ({self.copy} if self.copy else set())
        if not self.realm and self.pending_old in COPYABLE:
            pool.add(self.pending_old)
        for weapon in sorted(pool):
            for target in targets:
                ranged = weapon != "knife"
                if not ranged and units(state)[target].location != actor.location:
                    continue
                option = ability(grant, stage, "g3_copy_attack", verb=ActionVerb.ATTACK,
                                 targets=(target,), parameters={"weapon": weapon}, hostile=True)
                amount = math.ceil(state.ga_config.get("weapons", weapon, "damage") * cfg[
                    "inside_copy_ratio" if self.realm else "outside_copy_ratio"])
                attribute = DamageAttribute.TECH if weapon.endswith("rifle") else (
                    DamageAttribute.NORMAL)
                if not _visible(actor, units(state)[target], attribute):
                    continue
                result.append(replace(option, effects=(packet(
                    state, option.request, amount, attribute=attribute,
                    scope=TargetScope.RANGED_SINGLE if ranged else TargetScope.LOCAL_SINGLE),)))
        if not self.realm:
            for target in enemies(state, self.owner_id):
                if units(state)[target].unit_kind != "police":
                    result.append(ability(grant, stage, "g3_domain", mode=Performance.PUBLIC,
                                          targets=(target,), after=self.open))
        elif self.ideal and self.target in units(state) and units(state)[self.target].alive:
            result.append(ability(grant, stage, "g3_collapse", targets=(self.target,),
                                  costs=self.costs(state, self.available(state)),
                                  after=self.collapse, hostile=True)) if self.available(state) >= (
                                      cfg["collapse_min_magic"]) else None
        return tuple(result)

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """结界内不向商店或外部编队伸手，锚点只接受明文入口。"""
        player = state.players[grant.actor_id]
        if self.realm and player.isolation_id == self.realm:
            options = tuple(o for o in options if o.request.verb not in (
                ActionVerb.MOVE, ActionVerb.INTERACT) and not (o.request.operation == "npc_attack"
                and self.anchor in o.request.target_ids) and o.request.parameters.get(
                    "location", player.location) == player.location)
        if grant.actor_id == self.owner_id and self.realm:
            return tuple(replace(o, effects=tuple(replace(
                e, parameters={**dict(e.parameters), "hit_bonus": min(1, e.parameters.get(
                    "hit_bonus", 0) + state.ga_config.get("talents", "G3", "array_hit_bonus"))}
            ) for e in o.effects)) for o in options)
        return options

    def project(self, state: GameState, plan: ActionPlan) -> None:
        """成功新投影后才登记待清理旧通道，不预检拆盾。"""
        if plan.request.operation == "g3_target":
            self.target = plan.request.target_ids[0]
            return
        kind = plan.request.parameters["kind"]
        cfg = state.ga_config.get("talents", "G3")
        if not self.realm:
            self.pending_old = self.channel if self.channel != kind else None
            self.channel = kind
        self.styles.add(kind)
        if kind == "rho":
            state.players[self.owner_id].defenses[self.rho_id] = DefenseSpec(
                self.rho_id, DefenseKind.BREAKING_HIT_BARRIER, cfg["rho_durability"],
                cfg["rho_durability"], frozenset({DamageKind.NORMAL, DamageKind.DIRECT}),
                frozenset({TargetScope.RANGED_SINGLE}))
            state.players[self.owner_id].barrier_timers[self.rho_id] = timer(
                state, self.owner_id, "rho", cfg["rho_ticks"])
        elif kind == "guard":
            self.guard = True
        else:
            self.copy = kind
        high_impact(state, self.owner_id, plan.request.root_id)

    @property
    def rho_id(self) -> str:
        """同源稳定装备ID不允许重复挂层。"""
        return f"g3/rho/{self.owner_id}"

    def open(self, state: GameState, plan: ActionPlan) -> None:
        """捕捉当前公开建筑，警员留在外侧；同批候选之后才建立隔离。"""
        player = state.players[self.owner_id]
        self.realm = next_id(state, f"realm/{self.owner_id}")
        self.captured = {key: (p.location, p.isolation_id) for key, p in units(state).items() if
                         p.alive and p.home_locked and p.location == player.location and
                         p.isolation_id == player.isolation_id and p.unit_kind != "police"}
        for key in self.captured:
            units(state)[key].isolation_id = self.realm
        self.target = plan.request.target_ids[0]
        cfg = state.ga_config.get("talents", "G3")
        player.resources["temporary_magic"] = min(cfg["temp_magic_cap"],
            player.resources["temporary_magic"] + cfg["public_temp_magic"])
        self.realm_timer = timer(state, self.owner_id, "realm", cfg["max_barrier_ticks"])
        self.anchor = f"{self.realm}/anchor"
        state.ga_units[self.anchor] = GaPlayer(
            self.anchor, cfg["anchor_durability"], cfg["anchor_durability"], 0, 0, 0,
            player.location, awake=True, home_locked=True, is_player=False,
            unit_kind="anchor", owner_id=self.owner_id, isolation_id=self.realm)
        self.styles = {*self.seen, "array"}
        self.ideal = False
        self.gale_targets.clear()
        self.eternity_active, self.eternity = self.eternity, False
        self.array(state)
        if self.eternity_active:
            put_status(units(state)[self.target], state.ga_config, state.current_round, "SHOCKED",
                       source_id=self.realm, actor_id=self.owner_id)
        high_impact(state, self.owner_id, plan.request.root_id)
        touch(state)

    def array(self, state: GameState) -> None:
        """每轮首次远程直接攻击完整拦截；不拿普通甲伪装剑阵。"""
        key = f"g3/array/{self.owner_id}"
        state.players[self.owner_id].defenses[key] = DefenseSpec(
            key, DefenseKind.BREAKING_HIT_BARRIER, 1, 1,
            frozenset({DamageKind.NORMAL, DamageKind.DIRECT}),
            frozenset({TargetScope.RANGED_SINGLE}))

    def close(self, state: GameState) -> None:
        """所有出口幂等归并，已扣生命/装备/费用永不回滚。"""
        if not self.realm:
            return
        realm = self.realm
        for key, (location, previous) in self.captured.items():
            p = units(state).get(key)
            if p and p.isolation_id == realm:
                p.isolation_id = previous
                p.location = location if location not in state.destroyed_locations else next(
                    loc for loc in LOCATIONS if loc not in state.destroyed_locations)
                for callback in tuple(state.ga_location_listeners):
                    callback(state, key)
        if self.anchor:
            state.ga_units[self.anchor].hp = 0
        p = state.players[self.owner_id]
        for key in (self.rho_id, f"g3/array/{self.owner_id}"):
            p.defenses.pop(key, None)
            p.barrier_timers.pop(key, None)
        p.resources["temporary_magic"] = 0
        self.guard = False
        self.channel = self.copy = self.pending_old = None
        self.realm = self.target = None
        self.eternity_active = False
        trace(state, "realm_closed", realm=realm)
        touch(state)

    def break_anchor(self, state: GameState, plan: ActionPlan) -> None:
        """专用破界稳定扣结构耐久，普通攻击仍支付真实弹药。"""
        resolve_batch(state, (packet(state, plan.request, state.ga_config.get(
            "talents", "G3", "break_action_power"), certain=True,
            attribute=DamageAttribute.UNATTRIBUTED, defense_coefficient=0),))

    def before_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """实际付款驱动猎风，免费首段不伪造耗魔。"""
        if plan.request.actor_id != self.owner_id:
            return
        self.spent[plan.request.root_id] = sum(c.amount for c in plan.costs if
                                              c.account.startswith("resource:"))
        if plan.request.operation == "g3_dual":
            self.styles.add("dual")

    def chain(self, state: GameState, plan: ActionPlan) -> None:
        """只在螺旋根内逐段重查；控制者明确停止/选目标，不自动花尽魔力。"""
        if not self.realm or state.game_over:
            return
        cfg = state.ga_config.get("talents", "G3")
        prices = cfg["poem_chain_costs"] if self.eternity_active else cfg["chain_costs"]
        for index, price in enumerate(prices[1:], 1):
            targets = tuple(key for key in enemies(state, self.owner_id, local=False) if
                            key != self.anchor)
            if not targets or self.available(state) < price:
                break
            selected = ask(state, self.owner_id, self.owner_id, ("stop", *targets),
                           kind="projection_chain", context={"segment": index + 1, "magic": price})
            if selected == "stop" or not self.pay(state, price):
                break
            self.spent[plan.request.root_id] += price
            resolve_batch(state, (packet(state, plan.request, cfg["spiral_damage"],
                targets=(selected,), suffix=f"spiral/{index}", source="g3_spiral",
                scope=TargetScope.RANGED_SINGLE, hit_bonus=cfg["spiral_hit_bonus"]),))
            if state.game_over or not self.realm:
                return
            chapter(state, self.owner_id, 2)
        if self.ideal and self.target in units(state) and units(state)[self.target].alive and (
            self.available(state) >= cfg["collapse_min_magic"]
        ):
            if ask(state, self.owner_id, self.owner_id, ("decline", "collapse"),
                   kind="projection_finish") == "collapse":
                self.pay(state, self.available(state))
                self.collapse(state, plan)

    def collapse(self, state: GameState, plan: ActionPlan) -> None:
        """终段仍是普通穿甲、双倍耐久计费；终局快照前拆除结界。"""
        if not self.realm or self.target not in units(state) or not units(state)[self.target].alive:
            return
        cfg = state.ga_config.get("talents", "G3")
        amount = cfg["collapse_base_damage"] + min(len(self.styles), cfg["collapse_style_cap"]) * (
            cfg["collapse_per_style"])
        effect = packet(state, plan.request, amount, targets=(self.target,), source="g3_collapse",
                        suffix="collapse", scope=TargetScope.RANGED_SINGLE,
                        defense_coefficient=cfg["collapse_defense_coefficient"],
                        wear_price=cfg["collapse_wear_price"])
        state.ga_batch_finalizers.setdefault(effect.batch_id, []).append(self.close)
        resolve_batch(state, (effect,))

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """守势遇到下一次攻击即耗，直达仍消耗但不享受减伤。"""
        return IncomingModifier(reduction=state.ga_config.get("talents", "G3",
                                "dual_guard_reduction") if target.player_id == self.owner_id and
                                self.guard and effect.root_id and effect.actor_id else 0)

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """见证、实战投影、连发猎风与结构坍塌都用完整已落地结果。"""
        player = state.players[self.owner_id]
        for record in records:
            effect = record.effect
            actor = units(state).get(effect.actor_id)
            if effect.source_id in COPYABLE and actor and actor.location == player.location and (
                actor.isolation_id == player.isolation_id and
                _visible(player, actor, effect.attribute)
            ):
                self.seen.add(effect.source_id)
            if record.target_id == self.owner_id:
                if self.guard and effect.root_id and effect.actor_id:
                    self.guard = False
                    chapter(state, self.owner_id, 1)
                if any(c.defense_id == self.rho_id and c.absorbed for c in record.barrier_changes):
                    chapter(state, self.owner_id, 1)
            if effect.actor_id == self.owner_id and effect.source_id == "g3_copy_attack" and (
                record.effective_hit
            ):
                chapter(state, self.owner_id, 1)
            if self.realm and effect.source_id == "g3_spiral" and effect.actor_id == self.owner_id:
                self.chain_counts[effect.root_id] = self.chain_counts.get(effect.root_id, 0) + 1
                if self.chain_counts[effect.root_id] >= 2 and (
                    record.effective_hit or record.barrier_changes):
                    chapter(state, self.owner_id, 2)
                if self.spent.get(effect.root_id, 0) >= state.ga_config.get(
                    "talents", "G3", "gale_threshold") and record.effective_hit and (
                        record.target_id in state.players and
                        record.target_id not in self.gale_targets
                ):
                    target = state.players[record.target_id]
                    target.sp = max(0, target.sp - state.ga_config.get(
                        "talents", "G3", "gale_sp_loss"))
                    state.ga_public_blocked[record.target_id] = state.current_round
                    self.gale_targets.add(record.target_id)
                    self.ideal = True
            if effect.source_id == "g3_collapse" and effect.actor_id == self.owner_id and (
                record.effective_hit
            ):
                chapter(state, self.owner_id, 3)
        if self.realm and (not player.alive or self.anchor and not state.ga_units[
            self.anchor].alive):
            self.close(state)

    def boundary(self, state: GameState, *, boundary: str) -> None:
        """普通魔力唯一R0恢复；未来R4付维持，终焉/破界/死亡均幂等退出。"""
        player = state.players[self.owner_id]
        cfg = state.ga_config.get("talents", "G3")
        if boundary == "R0" and player.alive:
            if self.realm:
                self.array(state)
            elif state.current_round > 1:
                bonus = state.ga_config.get("hospital", "furnace_g3_magic_bonus") if (
                    player.surgery == "furnace") else 0
                player.resources["magic"] = min(cfg["magic_cap"], player.resources["magic"] +
                                                 cfg["magic_recover_r0"] + bonus)
        if boundary == "slot_end" and state.current_grant.actor_id == self.owner_id:
            if self.pending_old == "rho":
                player.defenses.pop(self.rho_id, None)
            elif self.pending_old == "guard":
                self.guard = False
            elif self.pending_old in COPYABLE and self.channel not in COPYABLE:
                self.copy = None
            self.pending_old = None
        if boundary == "R4.expiry" and self.realm:
            updated = self.realm_timer.on_r4(state.current_round)
            if updated != self.realm_timer and not self.pay(state, cfg["upkeep_cost"]):
                self.close(state)
                return
            self.realm_timer = updated
            if not updated.remaining or state.world_tick >= state.ga_config.get(
                "clock", "apocalypse_start") or not player.alive:
                self.close(state)

    def receive_poem(self, state: GameState, giver: str) -> None:
        """普通上限外的回复只进入有限临时账户，不虚构连发耗魔。"""
        cfg = state.ga_config.get("talents", "G3")
        player = state.players[self.owner_id]
        amount = state.ga_config.get("poems", "eternity_magic")
        restored = min(amount, cfg["magic_cap"] - player.resources["magic"])
        player.resources["magic"] += restored
        player.resources["temporary_magic"] = min(cfg["temp_magic_cap"],
            player.resources["temporary_magic"] + amount - restored)
        self.eternity = True
        touch(state)
