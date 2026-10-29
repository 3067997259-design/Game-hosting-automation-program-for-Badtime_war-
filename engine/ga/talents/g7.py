"""G7融合、有限护体、Cost复合根、三形态EX与有预警的Terror。"""

from __future__ import annotations

from dataclasses import replace
from collections.abc import Callable
import math
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.actions import GrantKind, precheck_action
from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    ActionVerb, CostEntry, DamageAttribute, DamageKind, DeathOutcome, DefenseKind, DefenseSpec,
    Performance, TargetScope, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.extensions import (
    IncomingModifier, Replacement, ask, chapter, high_impact, relocate, units,
)
from engine.ga.gameplay import _context, _throw, _visible, execute_registered_step
from engine.ga.state import ActionGrant, LOCATIONS, emit, next_id, touch, trace
from engine.ga.status import heal, put_status
from engine.ga.talents.base import Talent, ability, barrier, enemies, own_slot, packet

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec, ResolutionRecord
    from engine.ga.events import DomainEvent
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import GaPlayer


FORMS = ("swimsuit", "archer", "shielder")
MEDICINES = ("epo", "chocolate", "adrenaline")
ATTRIBUTES = (DamageAttribute.NORMAL, DamageAttribute.TECH, DamageAttribute.MAGIC)


class G7(Talent):
    """盾/光环/弹匣/备用弹各有唯一实际资源，不靠换装或零费宏补满。"""

    talent_id = "G7"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.body: GaPlayer | None = None
        self.form = "swimsuit"
        self.old_form = "swimsuit"
        self.shield_fused = self.gun_fused = self.supplied = False
        self.pose = "passive"
        self.front: set[str] = set()
        self.magazine: list[DamageAttribute] = []
        self.reserve: list[DamageAttribute] = []
        self.supply = 0
        self.changes = 0
        self.changed_round = 0
        self.color: int | None = 0
        self.revive_ready = False
        self.revive_used = 0
        self.macro_round = 0
        self.nap_round = 0
        self.adrenaline_round = 0
        self.adrenaline_used = 0
        self.initiative_boost_round = 0
        self.paid_shots = 0
        self.shot_serial = 0
        self.shield_hit = False
        self.ex_hit = False
        self.terror_ready = False
        self.extra_hp = 0
        self.base_hp = 0
        self.base_max_hp = 0
        self.suspended: dict[str, DefenseSpec] = {}
        self.leave_delays: dict[str, str] = {}

    @property
    def shield_id(self) -> str:
        """姿态只改同一件装备，不生成另一个耐久池。"""
        return f"g7/horus/{self.owner_id}"

    @property
    def halo_id(self) -> str:
        """层数展示折算自同一个有限护体池。"""
        return f"g7/halo/{self.owner_id}"

    def public(self) -> Mapping[str, Any]:
        """弹匣类型是实际已装入弹药，不是免费生成的射击次数。"""
        return {"form": self.form, "pose": self.pose, "shield_fused": self.shield_fused,
                "gun_fused": self.gun_fused, "magazine": [a.value for a in self.magazine],
                "reserve": [a.value for a in self.reserve], "color": self.color,
                "extra_hp": self.extra_hp, "revive_used": self.revive_used,
                "changes": self.changes, "supply": self.supply}

    def register(self, state: GameState) -> None:
        """基础Cost与光环只初始化一次，不额外叠首轮R0回复。"""
        super().register(state)
        self.body = state.players[self.owner_id]
        cfg = state.ga_config.get("talents", "G7")
        self.body.resources["cost"] = cfg["cost_initial"]
        self.color = cfg["color_initial"]
        self.body.ammo.update(horus_loaded=0, horus_reserve=0)
        self.halo(state, cfg["halo_initial_layers"] * cfg["halo_per_layer"])
        state.ga_slot_gates.append(self.slot_gate)
        state.ga_move_gates.append(self.movement_gate)
        state.ga_location_listeners.append(self.location_changed)
        state.ga_case_filters.append(lambda s, r: not (
            self.form == "terror" and r.effect.crime_owner_id == self.owner_id))

    def halo(self, state: GameState, amount: int) -> None:
        """总护体值最多6，脱战每R4只回1，不把每层都回一遍。"""
        cfg = state.ga_config.get("talents", "G7")
        amount = max(0, min(amount, cfg["halo_layers"] * cfg["halo_per_layer"]))
        barrier(state, self.owner_id, self.halo_id, amount, kind=DefenseKind.HALO)
        if amount == cfg["halo_layers"] * cfg["halo_per_layer"]:
            self.revive_ready = True

    def halo_value(self) -> int:
        """读取唯一护体装备；破碎后确实为零。"""
        item = self.body.defenses.get(self.halo_id)
        return item.durability if item else 0

    def fuse(self, state: GameState) -> None:
        """融合消耗实物且整局仅一次，修好不重新配发。"""
        if self.form in ("terror", "doubt"):
            return
        cfg = state.ga_config.get("talents", "G7")
        kinds = self.body.armor_kinds
        shield = next((k for k in self.body.defenses if kinds.get(k) == "shield"), None)
        at = next((k for k in self.body.defenses if kinds.get(k) == "at_field"), None)
        if not self.shield_fused and shield and at:
            for key in (shield, at):
                self.body.defenses.pop(key)
                self.body.armor_kinds.pop(key, None)
                self.body.armor_affinities.pop(key, None)
            self.shield_fused = True
            self.body.defenses[self.shield_id] = DefenseSpec(
                self.shield_id, DefenseKind.ARMOR, cfg["horus_durability"], cfg["horus_durability"],
                frozenset({DamageKind.NORMAL, DamageKind.PURE_WEAR}), frozenset(TargetScope),
                {a: cfg[f"horus_{a.value}"] for a in ATTRIBUTES},
                wear_price=state.ga_config.get("combat", "normal_wear_price"))
            self.body.armor_kinds[self.shield_id] = "horus"
            self.body.armor_affinities[self.shield_id] = DamageAttribute.NORMAL
            chapter(state, self.owner_id, 1)
        if not self.gun_fused and {"electromagnetic_rifle", "gauss_rifle"} <= self.body.weapons:
            self.body.weapons.difference_update({"electromagnetic_rifle", "gauss_rifle"})
            self.body.weapons.add("horus")
            self.gun_fused = True
            self.magazine = [DamageAttribute.NORMAL] * cfg["initial_loaded_ammo"]
            self.body.ammo["horus_loaded"] = len(self.magazine)
            chapter(state, self.owner_id, 1)
        if self.shield_fused and self.gun_fused and not self.supplied:
            self.supplied = True
            self.supply = cfg["free_supply_uses"]

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """宏外准备仍占唯一T0；Terror没有药物/公演/偷跑额外攻击。"""
        if not own_slot(state, self.owner_id, grant):
            return ()
        cfg = state.ga_config.get("talents", "G7")
        if self.form == "terror":
            targets = tuple(t for t in enemies(state, self.owner_id, local=False) if
                            t in state.players)
            if stage is TurnStage.T1 and self.terror_ready and targets and (
                "horus" not in self.body.disabled_weapons):
                candidate = ability(grant, stage, "g7_terror", verb=ActionVerb.ATTACK,
                                    targets=targets, hostile=True, after=self.terror_end)
                return (replace(candidate, effects=(packet(state, candidate.request,
                    cfg["terror_damage"], kind=DamageKind.DIRECT, absolute=True,
                    scope=TargetScope.GLOBAL_AREA, certain=True),)),)
            return ()
        if self.form == "doubt":
            return ()
        result = []
        if stage is TurnStage.T0:
            if self.changes < cfg["change_form_uses"] and self.changed_round != state.current_round:
                result.extend(ability(grant, stage, "g7_change", parameters={"form": f},
                                      after=self.perform) for f in FORMS if f != self.form)
            if self.body.hp < self.body.max_hp:
                result.append(ability(grant, stage, "g7_nap", mode=Performance.INSTANT,
                                      parameters={"kind": "heal"}, after=self.perform))
            shield = self.body.defenses.get(self.shield_id)
            if shield and 0 < shield.durability < shield.max_durability:
                result.append(ability(grant, stage, "g7_nap", mode=Performance.INSTANT,
                                      parameters={"kind": "shield"}, after=self.perform))
            if self.halo_value() < cfg["halo_layers"] * cfg["halo_per_layer"]:
                result.append(ability(grant, stage, "g7_nap", mode=Performance.INSTANT,
                                      parameters={"kind": "halo"}, after=self.perform))
            if self.body.inventory.get("adrenaline", 0) and self.adrenaline_used < cfg[
                "adrenaline_uses"]:
                result.append(ability(grant, stage, "g7_medicine",
                    parameters={"kind": "adrenaline"},
                    costs=(CostEntry(self.owner_id, "item:adrenaline", 1),), after=self.perform))
            if self.color is not None and self.color >= cfg["color_voluntary_threshold"]:
                result.append(ability(grant, stage, "g7_doubt", after=self.perform))
            if self.supply:
                for kind in (*MEDICINES, "fragment", "shock", "flash", "smoke", "molotov", "ammo"):
                    if kind != "ammo" and self.body.inventory.get(kind, 0) >= cfg[
                        "medicine_hold_cap"]:
                        continue
                    if kind == "ammo" and len(self.reserve) >= cfg["reserve_ammo_cap"]:
                        continue
                    candidate = ability(grant, stage, "g7_supply", parameters={"kind": kind,
                        "remaining": self.supply}, after=self.perform)
                    result.append(replace(candidate, definition=replace(candidate.definition,
                                                                       consumes_stage=False)))
            return tuple(result)
        if stage is not TurnStage.T1:
            return ()
        if self.supplied and self.body.resources["cost"] >= 1:
            result.append(ability(grant, stage, "g7_macro", after=self.macro))
        if self.gun_fused and self.magazine and "horus" not in self.body.disabled_weapons:
            for target in enemies(state, self.owner_id, local=False):
                if not _visible(self.body, units(state)[target], self.magazine[0]):
                    continue
                result.append(replace(ability(grant, stage, "g7_shoot", verb=ActionVerb.ATTACK,
                    targets=(target,), costs=(CostEntry(self.owner_id, "ammo:horus_loaded", 1),),
                    hostile=True, after=self.perform), ordinary_attack=True))
            for location in LOCATIONS if self.form == "shielder" else (self.body.location,):
                if location in state.destroyed_locations:
                    continue
                targets = tuple(t for t in enemies(state, self.owner_id, local=False) if
                                units(state)[t].location == location)
                for target in targets:
                    result.append(ability(grant, stage, "g7_ex", mode=Performance.PUBLIC,
                        targets=(target,), parameters={"location": location,
                                                       "ammo": len(self.magazine)},
                        costs=(CostEntry(self.owner_id, "ammo:horus_loaded", len(self.magazine)),),
                        hostile=True, after=self.ex))
        if self.shield_fused:
            shield = self.body.defenses.get(self.shield_id)
            if shield and shield.durability < shield.max_durability:
                for key in self.body.defenses:
                    if self.body.armor_kinds.get(key) in ("shield", "at_field"):
                        result.append(ability(grant, stage, "g7_repair",
                                              parameters={"component": key},
                                              after=self.perform))
        if self.body.location == "hospital":
            for drug in MEDICINES:
                if self.body.inventory.get(drug, 0) < cfg["medicine_hold_cap"]:
                    result.append(ability(grant, stage, "g7_buy_medicine", verb=ActionVerb.INTERACT,
                        parameters={"kind": drug}, costs=(CostEntry(self.owner_id, "credits",
                                                                 cfg[f"{drug}_price"]),),
                        after=self.perform))
        if self.gun_fused and self.body.location == "military" and len(self.reserve) < cfg[
            "reserve_ammo_cap"]:
            result.extend(ability(grant, stage, "g7_ammo", verb=ActionVerb.INTERACT,
                parameters={"attribute": a.value}, costs=(CostEntry(self.owner_id, "credits",
                cfg["ammo_refill_price"]),), after=self.perform) for a in ATTRIBUTES)
        return tuple(result)

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """起床时自选初始形态；Terror只留本命攻击/移动；架盾不能靠普通move溜走。"""
        if grant.actor_id != self.owner_id:
            return options
        if stage is TurnStage.T0 and not self.body.awake:
            result = []
            for option in options:
                if option.request.operation != "wake":
                    result.append(option)
                    continue
                definition = replace(option.definition, required_parameters=(
                    *option.definition.required_parameters, "form"))
                for form in FORMS:
                    request = replace(option.request,
                                      parameters={**option.request.parameters, "form": form})
                    result.append(replace(option, request=request, definition=definition))
            return tuple(result)
        if self.form == "terror":
            return tuple(o for o in options if o.request.operation in ("g7_terror", "move"))
        if self.form == "doubt":
            return tuple(o for o in options if o.request.operation == "forfeit")
        if self.pose == "active":
            return tuple(o for o in options if o.request.verb is not ActionVerb.MOVE or
                         o.request.operation == "g7_ex")
        return options

    def set_pose(self, state: GameState, pose: str, targets: tuple[str, ...] = ()) -> None:
        """切姿态沿用残盾，主动破盾当击承接，背击不获正面保护。"""
        shield = self.body.defenses.get(self.shield_id)
        self.pose = pose
        if pose != "active":
            self.leave_delays.clear()
        if targets:
            self.front = set(targets)
        if not shield:
            return
        cfg = state.ga_config.get("talents", "G7")
        if pose == "active" and shield.durability:
            self.body.defenses[self.shield_id] = replace(shield,
                kind=DefenseKind.BREAKING_HIT_BARRIER, accepted_damage_kinds=frozenset({
                    DamageKind.NORMAL, DamageKind.DIRECT, DamageKind.PURE_WEAR}),
                attribute_defense={}, wear_price=None)
        else:
            self.body.defenses[self.shield_id] = replace(shield, kind=DefenseKind.ARMOR,
                accepted_damage_kinds=frozenset({DamageKind.NORMAL, DamageKind.PURE_WEAR}),
                wear_price=state.ga_config.get("combat", "normal_wear_price"),
                attribute_defense={a: cfg[f"horus_{a.value}"] + (
                    cfg["hold_defense_bonus"] if pose == "hold" else 0) for a in ATTRIBUTES})

    def perform(self, state: GameState, plan: ActionPlan) -> None:
        """所有射击和药物先由共同预检支付，再改变真实库存。"""
        request = plan.request
        op = request.operation
        cfg = state.ga_config.get("talents", "G7")
        if op.startswith("g7_macro_") and self.shield_fused and self.gun_fused:
            chapter(state, self.owner_id, 2)
        if op == "g7_change":
            self.form = request.parameters["form"]
            self.changes += 1
            self.changed_round = state.current_round
            self.paid_shots = 0
        elif op == "g7_nap":
            kind = request.parameters["kind"]
            if kind == "heal":
                heal(self.body, cfg["nap_low_hp_heal"] if self.body.hp <= self.body.max_hp * cfg[
                    "nap_low_hp_ratio"] else cfg["nap_heal"])
                self.nap_round = state.current_round
            else:
                if kind == "shield":
                    shield = self.body.defenses[self.shield_id]
                    self.body.defenses[self.shield_id] = replace(shield, durability=min(
                        shield.max_durability, shield.durability + cfg["nap_repair"]))
                else:
                    self.halo(state, self.halo_value() + cfg["nap_halo_layers"] * cfg[
                        "halo_per_layer"])
                self.magazine.extend([DamageAttribute.NORMAL] * min(cfg["nap_loaded_ammo"],
                    cfg["magazine_cap"] - len(self.magazine)))
                self.body.ammo["horus_loaded"] = len(self.magazine)
            high_impact(state, self.owner_id, request.root_id)
        elif op in ("g7_medicine", "g7_macro_medicine"):
            kind = request.parameters["kind"]
            if kind == "epo":
                cap = cfg["adrenaline_cost_cap"] if self.adrenaline_round == (
                    state.current_round) else cfg["cost_cap"]
                self.body.resources["cost"] = min(cap + cfg["epo_cost_bonus"],
                    self.body.resources["cost"] + cfg["epo_cost_bonus"])
            elif kind == "chocolate":
                self.halo(state, self.halo_value() + cfg["chocolate_layers"] * cfg[
                    "halo_per_layer"])
            else:
                self.adrenaline_used += 1
                self.adrenaline_round = state.current_round + 1
        elif op == "g7_doubt":
            self.old_form, self.form = self.form, "doubt"
        elif op == "g7_supply":
            self.supply -= 1
            kind = request.parameters["kind"]
            if kind == "ammo":
                self.reserve.extend([DamageAttribute.NORMAL] * min(cfg["free_supply_ammo"],
                    cfg["reserve_ammo_cap"] - len(self.reserve)))
                self.body.ammo["horus_reserve"] = len(self.reserve)
            else:
                self.body.inventory[kind] = self.body.inventory.get(kind, 0) + 1
        elif op == "g7_buy_medicine":
            key = request.parameters["kind"]
            self.body.inventory[key] = self.body.inventory.get(key, 0) + 1
        elif op == "g7_ammo":
            self.reserve.extend([DamageAttribute(request.parameters["attribute"])] * min(
                cfg["ammo_refill_amount"], cfg["reserve_ammo_cap"] - len(self.reserve)))
            self.body.ammo["horus_reserve"] = len(self.reserve)
        elif op == "g7_repair":
            key = request.parameters["component"]
            self.body.defenses.pop(key)
            self.body.armor_kinds.pop(key, None)
            self.body.armor_affinities.pop(key, None)
            shield = self.body.defenses[self.shield_id]
            self.body.defenses[self.shield_id] = replace(shield, durability=min(
                shield.max_durability, shield.durability + cfg["component_repair"]))
            self.set_pose(state, "passive")
        elif op in ("g7_shoot", "g7_macro_shoot"):
            attribute = self.magazine.pop(0)
            self.shoot(state, plan, request.target_ids[0], attribute=attribute)
        elif op == "g7_macro_reload":
            amount = request.parameters["amount"]
            self.magazine.extend(self.reserve[:amount])
            del self.reserve[:amount]
            self.body.ammo["horus_loaded"] = len(self.magazine)
        elif op in ("g7_macro_shield", "g7_macro_hold"):
            pose = "active" if op == "g7_macro_shield" else "hold"
            self.set_pose(state, pose, request.target_ids)
        elif op == "g7_macro_turn":
            self.front = set(request.target_ids)
        elif op == "g7_macro_eject":
            self.magazine.clear()
            self.body.ammo["horus_loaded"] = 0
        elif op == "g7_macro_dash":
            relocate(state, self.owner_id, request.parameters["location"], active=True)
            if self.form == "shielder" and request.target_ids:
                resolve_batch(state, (packet(state, request, cfg["dash_damage"], certain=True,
                    statuses=("SHOCKED",)),))
        elif op == "g7_macro_search":
            self.body.revealed_to.add(request.target_ids[0])
            units(state)[request.target_ids[0]].revealed_to.add(self.owner_id)
        elif op == "g7_macro_lock":
            self.body.locked_targets.add(request.target_ids[0])
        elif op == "g7_macro_throw":
            _throw(state, request)
        touch(state)

    def macro_options(
        self, state: GameState, grant: ActionGrant, *, epo_used: bool,
        settings: set[tuple[str, tuple[str, ...]]],
    ) -> tuple[ActionOption, ...]:
        """每个子步骤重新枚举和预检，零费步骤必须有真实库存或一次设置差量。"""
        cfg = state.ga_config.get("talents", "G7")
        result = []

        def add(
            name: str, *, targets: tuple[str, ...] = (), costs: tuple[CostEntry, ...] = (),
            verb: ActionVerb = ActionVerb.SPECIAL, **params: Any,
        ) -> None:
            price_name = {"shield": "deploy_shield", "hold": "hold_shield", "medicine": "medicine",
                          "eject": "eject", "turn": "turn", "shoot": "shoot", "dash": "dash",
                          "reload": "reload", "search": "search", "lock": "lock",
                          "throw": "throw"}[name]
            candidate = ability(grant, TurnStage.T1, f"g7_macro_{name}", targets=targets,
                parameters=params, costs=(*costs, CostEntry(self.owner_id, "resource:cost",
                cfg["macro_costs"][price_name])), verb=verb, after=self.perform,
                hostile=name in ("shoot", "throw"))
            if name in ("turn", "eject") and (name, targets) in settings:
                return
            context = _context(state, grant, TurnStage.T1)
            plan = precheck_action(candidate.request, context, candidate.definition,
                                   state.ga_config)
            if plan.accepted:
                result.append(candidate)

        targets = enemies(state, self.owner_id, local=False)
        if self.magazine and "horus" not in self.body.disabled_weapons:
            for target in targets:
                if not _visible(self.body, units(state)[target], self.magazine[0]):
                    continue
                add("shoot", targets=(target,), verb=ActionVerb.ATTACK,
                    costs=(CostEntry(self.owner_id, "ammo:horus_loaded", cfg["ammo_per_shot"]),))
        shield = self.body.defenses.get(self.shield_id)
        if shield and shield.durability:
            if self.pose != "hold":
                add("hold")
            for target in targets:
                if self.pose != "active" or target not in self.front:
                    add("shield", targets=(target,))
                if self.pose == "active" and {target} != self.front:
                    add("turn", targets=(target,))
        if self.reserve and len(self.magazine) < cfg["magazine_cap"]:
            amount = min(len(self.reserve), cfg["magazine_cap"] - len(self.magazine))
            add("reload", costs=(CostEntry(self.owner_id, "ammo:horus_reserve", amount),),
                amount=amount)
        if self.magazine:
            add("eject")
        for medicine in MEDICINES:
            if not self.body.inventory.get(medicine, 0):
                continue
            if medicine == "epo" and epo_used or medicine == "adrenaline" and (
                self.adrenaline_used >= cfg["adrenaline_uses"]):
                continue
            cap = cfg["adrenaline_cost_cap"] if self.adrenaline_round == (
                state.current_round) else cfg["cost_cap"]
            if medicine == "epo" and self.body.resources["cost"] >= cap + cfg["epo_cost_bonus"]:
                continue
            if medicine == "chocolate" and self.halo_value() == cfg["halo_layers"] * cfg[
                "halo_per_layer"]:
                continue
            add("medicine", costs=(CostEntry(self.owner_id, f"item:{medicine}", 1),), kind=medicine)
        if self.pose != "active":
            for loc in LOCATIONS:
                if loc == self.body.location or loc in state.destroyed_locations or (
                    self.body.isolation_id):
                    continue
                victims = tuple(t for t in targets if units(state)[t].location == loc and
                                _visible(self.body, units(state)[t], DamageAttribute.NORMAL))
                add("dash", verb=ActionVerb.MOVE, location=loc,
                    targets=(victims[:1] if self.form == "shielder" else ()))
        for target in targets:
            enemy = units(state)[target]
            if enemy.location == self.body.location and enemy.hidden and (
                self.owner_id not in enemy.revealed_to):
                add("search", targets=(target,))
            if target not in self.body.locked_targets and _visible(
                self.body, enemy, DamageAttribute.NORMAL):
                add("lock", targets=(target,))
        for item in ("fragment", "shock", "flash", "smoke", "molotov"):
            if not self.body.inventory.get(item, 0):
                continue
            for location in LOCATIONS:
                if location not in state.destroyed_locations and not self.body.isolation_id:
                    add("throw", verb=ActionVerb.ATTACK,
                        costs=(CostEntry(self.owner_id, f"item:{item}", 1),),
                        kind=item, location=location)
        return tuple(result)

    def macro(self, state: GameState, plan: ActionPlan) -> None:
        """整个宏只有一个标准根和T2，内部支付失败只停止后续步骤。"""
        self.macro_round = state.current_round
        settings: set[tuple[str, tuple[str, ...]]] = set()
        epo_used = False
        index = 0
        while not state.game_over and self.body.alive and self.form in FORMS:
            grant = ActionGrant(f"{plan.request.root_id}/step/{index}", self.owner_id,
                                GrantKind.ROOT_STEP, root_id=plan.request.root_id)
            options = self.macro_options(state, grant, epo_used=epo_used, settings=settings)
            if not options:
                break
            selected = ask(state, self.owner_id, self.owner_id, ("cancel", *(str(i) for i in range(
                len(options)))), kind="hoshino_step",
                context={"steps": [o.request.to_dict() for o in options]})
            if selected == "cancel":
                break
            option = options[int(selected)]
            execute_registered_step(state, option)
            if state.game_over:
                return
            name = option.request.operation.removeprefix("g7_macro_")
            settings.add((name, option.request.target_ids))
            epo_used |= name == "medicine" and option.request.parameters["kind"] == "epo"
            index += 1
        if not state.game_over and index and self.shield_fused and self.gun_fused:
            chapter(state, self.owner_id, 2)
        elif not state.game_over and not index:
            emit(state, EventKind.TURN_FORFEITED, root_id=plan.request.root_id,
                 actor_id=self.owner_id, payload={"controlled": False, "empty_macro": True})

    def shoot(
        self, state: GameState, plan: ActionPlan, target: str, *, attribute: DamageAttribute,
        free: bool = False,
    ) -> None:
        """一次普通枪总池4或免费枪总池3，分弹前固定加值只入一次。"""
        cfg = state.ga_config.get("talents", "G7")
        amount = cfg["free_shot_pool" if free else "shot_pool"]
        for callback in (*state.ga_attack_pool_hooks, *state.ga_pool_bonus_hooks):
            amount = callback(state, self.owner_id, amount)
        self.shot_serial += 1
        for index in range(cfg["pellets"]):
            if state.game_over or not self.body.alive or not units(state)[target].alive:
                break
            selected = target
            if not free and index and units(state)[target].location == self.body.location:
                alternatives = tuple(t for t in enemies(state, self.owner_id) if t != target)
                if alternatives:
                    selected = alternatives[state.ga_rng.die("g7/scatter", len(alternatives),
                                                              actor_id=self.owner_id)-1]
            damage = amount // cfg["pellets"] + (index < amount % cfg["pellets"])
            resolve_batch(state, (packet(state, plan.request, damage, targets=(selected,),
                source="g7_free_shot" if free else "g7_shot",
                suffix=f"shot/{self.shot_serial}/{index}", attribute=attribute,
                scope=TargetScope.RANGED_SINGLE, fixed_pool=True,
                attached_wear=cfg["free_shot_wear"] if free and index == 0 else 0),))
        if not free:
            self.paid_shots += 1
            if not state.game_over and self.form == "archer" and self.paid_shots % cfg[
                "free_shot_every"] == 0 and (
                units(state)[target].alive):
                self.shoot(state, plan, target, attribute=attribute, free=True)

    def ex(self, state: GameState, plan: ActionPlan) -> None:
        """三EX按实际弹数结算，点射/爆炸与四连喷是同根独立段。"""
        cfg = state.ga_config.get("talents", "G7")
        count = plan.request.parameters["ammo"]
        primary = plan.request.target_ids[0]
        attributes, self.magazine = self.magazine, []
        actual_attributes = tuple(a for a in ATTRIBUTES if a in attributes)
        enemy = units(state)[primary]
        counters = {DamageAttribute.NORMAL: DamageAttribute.MAGIC,
                    DamageAttribute.TECH: DamageAttribute.NORMAL,
                    DamageAttribute.MAGIC: DamageAttribute.TECH}
        def quality(attribute: DamageAttribute) -> tuple[int, int]:
            """先比较主目标普通甲减免，再优先克制磨甲，稳定属性次序断平。"""
            armors = [(d.attribute_defense.get(attribute, 0), enemy.armor_affinities.get(k))
                      for k, d in enemy.defenses.items() if d.kind is DefenseKind.ARMOR and
                      d.durability]
            defense = max((d for d, _ in armors), default=0)
            counter = any(affinity is counters[attribute] for _, affinity in armors)
            return -defense, int(counter)
        attribute = max(actual_attributes, key=quality)
        self.paid_shots = 0
        if self.form == "shielder":
            self.set_pose(state, "passive")
            relocate(state, self.owner_id, plan.request.parameters["location"], active=True)
            shield = self.body.defenses.get(self.shield_id)
            if shield:
                repair = cfg["shielder_ex_repair_base"] + count // cfg["shielder_ex_repair_divisor"]
                self.body.defenses[self.shield_id] = replace(shield, durability=min(
                    shield.max_durability, shield.durability + repair))
            barrier(state, self.owner_id, "g7/ex-barrier", math.ceil(
                count / cfg["shielder_ex_barrier_divisor"]))
        targets = enemies(state, self.owner_id)
        if self.form in ("swimsuit", "shielder"):
            shield = self.body.defenses.get(self.shield_id)
            if shield and shield.durability:
                self.set_pose(state, "active", targets)
            if self.form == "swimsuit":
                amount = cfg["swimsuit_ex_base"] + count // cfg["swimsuit_ex_ammo_divisor"]
            else:
                amount = cfg["shielder_ex_damage_base"] + count // cfg["shielder_ex_damage_divisor"]
            effects = []
            for target in targets:
                status = "ACTION_SUPPRESSED" if self.form == "swimsuit" and target == primary and (
                    count >=
                    cfg["swimsuit_ex_suppress_ammo"]) else "SHOCKED"
                effect = packet(state, plan.request, amount, targets=(target,),
                    source="g7_ex", suffix=f"ex/{target}", attribute=attribute, certain=True,
                    scope=TargetScope.LOCAL_AREA, statuses=(status,))
                effects.append(replace(effect, batch_id=f"{plan.request.root_id}/ex-area"))
            if effects:
                resolve_batch(state, tuple(effects))
        else:
            for index in range(cfg["archer_ex_pellets"]):
                targets = enemies(state, self.owner_id)
                if state.game_over or not targets:
                    break
                resolve_batch(state, (packet(state, plan.request,
                    cfg["archer_ex_distribution"][index], targets=targets, source="g7_ex",
                    suffix=f"ex/spray/{index}", attribute=attribute,
                    scope=TargetScope.LOCAL_AREA, certain=True, statuses=("VULNERABLE",),
                    wear_price=cfg["archer_ex_wear_price"]),))
            if not state.game_over and units(state)[primary].alive:
                amount = cfg["archer_ex_point_base"] + count // cfg["archer_ex_point_divisor"]
                records = resolve_batch(state, (packet(state, plan.request, amount,
                    targets=(primary,), source="g7_ex", suffix="ex/point",
                    scope=TargetScope.RANGED_SINGLE, bypass=True, attribute=attribute,
                    defense_coefficient=cfg["archer_ex_defense_coefficient"]),))
                if not state.game_over and any(r.effective_hit for r in records):
                    targets = enemies(state, self.owner_id)
                    if targets:
                        resolve_batch(state, (packet(state, plan.request, math.ceil(count / cfg[
                            "archer_ex_splash_divisor"]), targets=targets,
                            source="g7_ex", suffix="ex/blast",
                            attribute=attribute, scope=TargetScope.LOCAL_AREA, certain=True),))
        touch(state)

    def slot_gate(self, state: GameState, grant: ActionGrant) -> bool:
        """标准槽的结构代价先于普通压制；额外槽不能代缴或抢首击。"""
        if grant.actor_id != self.owner_id:
            return True
        if self.form == "doubt":
            if grant.kind is GrantKind.STANDARD:
                self.enter_terror(state)
            return False
        if self.form == "terror" and not self.terror_ready:
            if grant.kind is not GrantKind.STANDARD:
                return False
            self.terror_ready = True
        return True

    def movement_gate(self, state: GameState, actor: str, destination: str) -> bool:
        """正面敌人的普通离开多付一个实际槽；强制位移与专属冲刺不计普通离开。"""
        grant = state.current_grant
        root = grant.root_id or f"{grant.grant_id}/T1" if grant else ""
        meta = state.ga_action_meta.get(root, {})
        if not grant or meta.get("operation") != "move" or self.pose != "active" or (
            actor not in self.front or
            units(state)[actor].location != self.body.location or not self.body.alive):
            return True
        shield = self.body.defenses.get(self.shield_id)
        if not shield or not shield.durability:
            return True
        previous = self.leave_delays.get(actor)
        if previous and previous != grant.grant_id:
            return True
        self.leave_delays[actor] = grant.grant_id
        emit(state, EventKind.STATE_CHANGED, root_id=meta.get("root_id"), actor_id=actor,
             payload={"kind": "shield_delayed_departure", "destination": destination})
        return False

    def location_changed(self, state: GameState, actor: str) -> None:
        """实际强制搬离会破驻地架盾，不能把旧正面带着瞬移。"""
        if actor == self.owner_id and self.pose == "active":
            self.set_pose(state, "passive")

    def enter_terror(self, state: GameState) -> None:
        """实物只计一次；有限额外生命耗尽必须死亡，不能拿本体代付。"""
        cfg = state.ga_config.get("talents", "G7")
        shield = self.body.defenses.get(self.shield_id)
        other = sum(d.kind is DefenseKind.ARMOR for k, d in self.body.defenses.items() if
                    k != self.shield_id)
        self.extra_hp = max(cfg["terror_hp_floor"], min(cfg["terror_hp_cap"],
            other * cfg["terror_hp_per_armor"] + math.ceil((shield.durability if shield else 0) /
            cfg["terror_horus_divisor"]) + self.halo_value() * cfg["terror_halo_multiplier"]))
        self.base_hp, self.base_max_hp = self.body.hp, self.body.max_hp
        self.suspended = dict(self.body.defenses)
        self.body.defenses.clear()
        self.body.hp += self.extra_hp
        self.body.max_hp += self.extra_hp
        self.body.healable = False
        self.body.statuses.clear()
        self.form = "terror"
        self.pose = "passive"
        self.terror_ready = False
        state.ga_police.stopped.add(f"terror/{self.owner_id}")
        self.body.resources["cost"] = 0
        touch(state)

    def terror_end(self, state: GameState, plan: ActionPlan) -> None:
        """先全体真实结算；已经全歼获胜则不再追收自耗。"""
        if not state.game_over:
            self.pay_terror(state, state.ga_config.get("talents", "G7", "terror_attack_hp_cost"))

    def pay_terror(self, state: GameState, amount: int) -> None:
        """只扣剩余额外生命；耗尽执行普通死亡门，不变成HP借贷。"""
        actual = min(amount, self.extra_hp)
        self.extra_hp -= actual
        self.body.hp -= actual
        if not self.extra_hp and self.body.alive:
            grant = ActionGrant(next_id(state, "terror/depletion"), self.owner_id,
                                GrantKind.REACTION)
            request = ability(grant, TurnStage.T1, "g7_terror_depleted",
                              targets=(self.owner_id,)).request
            state.ga_quiet_roots.add(request.root_id)
            resolve_batch(state, (packet(state, request, self.body.hp, kind=DamageKind.DIRECT,
                certain=True, bypass=True, hostile=False, scope=TargetScope.GLOBAL_AREA),))

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """主动防御只承接声明正面，普通光环不共享破盾整击特权。"""
        ignore = target.player_id == self.owner_id and self.pose == "active" and (
            effect.actor_id not in self.front or effect.parameters.get("attack_side") == "back")
        ignored = frozenset({self.shield_id}) if ignore else frozenset()
        return IncomingModifier(ignored_defenses=ignored)

    def lethal(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> Replacement | None:
        """续行是一局一次自身转折，Terror不享用。"""
        if target.player_id == self.owner_id and self.form != "terror" and self.revive_ready and (
            self.revive_used < state.ga_config.get("talents", "G7", "revive_uses")):
            return Replacement(DeathOutcome.MORPHED, state.ga_config.get(
                "talents", "G7", "revive_hp"),
                               "g7_continuation", {})
        return None

    def commit_lethal(
        self, state: GameState, effect: EffectSpec, target: GaPlayer, result: Replacement,
    ) -> None:
        """光环清空，之后再次回满也不刷新次数。"""
        self.revive_used += 1
        self.halo(state, 0)

    def after_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """起床选定初始形态并领该形态的收益，只有一次；每步完成后融合仍不生成额外主行动。"""
        if plan.request.actor_id != self.owner_id:
            return
        if plan.request.operation == "wake":
            from engine.ga.state import add_armor
            self.form = self.old_form = plan.request.parameters["form"]
            if self.form == "swimsuit":
                add_armor(state, self.owner_id, "shield")
            elif self.form == "shielder":
                self.halo(state, self.halo_value() + state.ga_config.get(
                    "talents", "G7", "halo_per_layer"))
        self.fuse(state)
        if self.form == "terror" and plan.request.verb is ActionVerb.MOVE and (
            self.body.location !=
            state.ga_action_meta[plan.request.root_id].get("from_location")):
            self.pay_terror(state, state.ga_config.get("talents", "G7", "terror_move_hp_cost"))
        elif plan.request.verb is ActionVerb.ATTACK and plan.request.operation not in (
            "g7_shoot", "g7_macro_shoot"):
            self.paid_shots = 0

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """实际主动承伤与EX命中完成第三章；色彩非致死伤害触发强制预警。"""
        cfg = state.ga_config.get("talents", "G7")
        for record in records:
            if record.target_id == self.owner_id:
                if self.pose == "active" and any(c.defense_id == self.shield_id and c.absorbed
                                                 for c in record.barrier_changes):
                    self.shield_hit = True
                shield = self.body.defenses.get(self.shield_id)
                if self.pose == "active" and shield and not shield.durability:
                    self.set_pose(state, "passive")
                if self.pose == "active" and set(record.applied_statuses) & {"SHOCKED", "FLASHED"}:
                    self.set_pose(state, "passive")
                if self.form == "terror":
                    self.extra_hp = max(0, self.extra_hp - record.hp_loss)
                    if not self.extra_hp and self.body.alive and record.effect.source_id != (
                        "g7_terror_depleted"):
                        state.ga_batch_finalizers.setdefault(record.effect.batch_id, []).append(
                            partial_terror_death(self))
                elif self.color is not None and self.color >= cfg["color_forced_threshold"] and (
                    record.hp_loss and self.body.alive and record.death_outcome is DeathOutcome.NONE
                    and self.form != "doubt"):
                    for key, armor in tuple(self.body.defenses.items()):
                        if armor.kind is DefenseKind.ARMOR and not armor.durability:
                            self.body.defenses[key] = replace(armor,
                                                              durability=armor.max_durability)
                    self.old_form, self.form = self.form, "doubt"
            if record.effect.actor_id == self.owner_id and record.effect.source_id == (
                "g7_ex") and record.effective_hit:
                self.ex_hit = True
                if "VULNERABLE" in record.applied_statuses:
                    player = units(state)[record.target_id]
                    entry = player.statuses["VULNERABLE"]
                    player.statuses["VULNERABLE"] = replace(entry, timer=replace(
                        entry.timer, remaining=cfg["archer_ex_ticks"]))
        if self.shield_hit and self.ex_hit:
            chapter(state, self.owner_id, 3)

    def on_events(self, state: GameState, events: tuple[DomainEvent, ...]) -> None:
        """撤退与普通死亡各是一次出局事实，NPC头不积色彩。"""
        if not self.body.alive:
            state.ga_police.stopped.discard(f"terror/{self.owner_id}")
        cfg = state.ga_config.get("talents", "G7")
        for event in events:
            if event.kind is EventKind.STATUS_APPLIED and event.target_id == self.owner_id and (
                set(event.payload.get("statuses", ())) & {"SHOCKED", "FLASHED"}):
                self.set_pose(state, "passive")
            if self.color is None:
                continue
            if event.kind is EventKind.ACTOR_DIED and event.target_id in state.players and (
                event.target_id != self.owner_id):
                self.color += cfg["color_exit_gain"] + (cfg["color_kill_gain"] if
                    event.score_owner_id == self.owner_id else 0)
            elif event.kind is EventKind.ACTOR_EXITED and event.actor_id in state.players and (
                event.actor_id != self.owner_id):
                self.color += cfg["color_exit_gain"]

    def boundary(self, state: GameState, *, boundary: str) -> None:
        """Cost回满/一次疲劳/肾上腺素覆盖与有限盾维持各一次。"""
        if not self.body.alive or self.form == "terror":
            return
        cfg = state.ga_config.get("talents", "G7")
        if boundary == "R0":
            if self.initiative_boost_round and self.initiative_boost_round < state.current_round:
                self.body.initiative_bonus -= cfg["adrenaline_initiative"]
                self.initiative_boost_round = 0
            if self.adrenaline_round == state.current_round:
                self.body.resources["cost"] = cfg["adrenaline_cost_cap"]
                self.halo(state, cfg["halo_layers"] * cfg["halo_per_layer"])
                self.body.initiative_bonus += cfg["adrenaline_initiative"]
                self.initiative_boost_round = state.current_round
            else:
                tired = self.macro_round == state.current_round - 1 and self.nap_round != (
                    state.current_round - 1)
                self.body.resources["cost"] = cfg["cost_cap"] - (cfg["macro_fatigue"] if
                                                               tired else 0)
        elif boundary == "R4.recovery" and not self.body.engaged:
            self.halo(state, self.halo_value() + cfg["halo_idle_recovery"])
        elif boundary == "R4.dot_maintenance" and self.pose == "active":
            price = cfg["swimsuit_upkeep"] if self.form == "swimsuit" else cfg["shield_upkeep"]
            if self.body.resources["cost"] < price:
                self.set_pose(state, "passive")
            else:
                self.body.resources["cost"] -= price

    def receive_poem(self, state: GameState, giver: str) -> None:
        """守夜人只转换一次剩余额外生命；重复不新造装备/融合/资源。"""
        if self.color is None:
            return
        self.color = None
        state.ga_police.stopped.discard(f"terror/{self.owner_id}")
        if self.form == "terror":
            repair = min(state.ga_config.get("poems", "watchman_hp_repair_cost_cap"),
                         self.extra_hp) if self.shield_fused else 0
            permanent = max(0, self.extra_hp - repair)
            self.body.max_hp = self.base_max_hp + permanent
            self.body.hp = min(self.body.max_hp, self.base_hp + permanent)
            self.body.defenses = dict(self.suspended)
            if self.shield_fused and self.shield_id in self.body.defenses:
                shield = self.body.defenses[self.shield_id]
                self.body.defenses[self.shield_id] = replace(shield, durability=max(
                    shield.durability, state.ga_config.get(
                        "poems", "watchman_repaired_durability")))
            self.extra_hp = 0
            self.body.healable = True
        if self.form in ("terror", "doubt"):
            self.form = self.old_form
        self.set_pose(state, "passive")
        cfg = state.ga_config.get("talents", "G7")
        self.halo(state, cfg["halo_initial_layers"] * cfg["halo_per_layer"])
        self.body.resources["cost"] = cfg["cost_cap"]
        touch(state)


def partial_terror_death(role: G7) -> Callable[[GameState], None]:
    """批末结构死亡优先于终局快照，回调不再次扣已损生命。"""
    return lambda state: role.pay_terror(state, 0)
