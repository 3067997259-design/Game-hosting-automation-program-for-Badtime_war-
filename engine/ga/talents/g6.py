"""G6欢愉：根级笑点、单段当身、真实多付款者与有限礼盒响应。"""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    ActionVerb, ChoiceState, ChoiceWindow, CostEntry, DamageAttribute, DamageKind, HitOutcome,
    Performance, TargetScope, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.extensions import IncomingModifier, ask, chapter, high_impact, relocate, units
from engine.ga.gameplay import _apply_builtin, _attack_effect, _location_options, _visible
from engine.ga.state import LOCATIONS, add_armor, emit, next_id, touch
from engine.ga.status import heal, put_status
from engine.ga.talents.base import Talent, ability, enemies, own_slot, packet, timer

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec, ResolutionRecord
    from engine.ga.events import DomainEvent
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant, GaPlayer


# 礼盒 5 号面只禁用、不报废的神器与融合武器：G0 的 AR、G7 的荷鲁斯之眼。
ARTIFACT_WEAPONS = frozenset({"ar", "horus"})


class G6(Talent):
    """盒子、笑点与假币都有真实额度；玩偶不是普通外甲。"""

    talent_id = "G6"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.body: GaPlayer | None = None
        self.armed = False
        self.counter_slot: str | None = None
        self.joy_timer: Any = None
        self.laugh_roots: set[tuple[str, str]] = set()
        self.gain_round = 0
        self.gain_amount = 0
        self.last_victim: tuple[str, int] | None = None
        self.victims: dict[int, set[str]] = {}
        self.boxes_opened = 0

    def public(self) -> Mapping[str, Any]:
        """只展示实际假币和有限读心窗口。"""
        return {"laugh": self.body.resources["laugh"] if self.body else 0,
                "armed": self.armed, "boxes_opened": self.boxes_opened,
                "joy_ticks": self.joy_timer.remaining if self.joy_timer else 0}

    def register(self, state: GameState) -> None:
        """共享持盒表保证每人至多一盒，重复塞盒不刷新年龄。"""
        super().register(state)
        self.body = state.players[self.owner_id]
        self.body.resources["laugh"] = state.ga_config.get("talents", "G6", "laugh_initial")
        if not hasattr(state, "ga_gifts"):
            state.ga_gifts = {}
        state.ga_t0_opportunities.append(self.opportunity)

    def opportunity(self, state: GameState, grant: ActionGrant) -> None:
        """真正可选T0才记录机会；压制/失神/结构跳槽都不会抵达这里。"""
        entry = state.ga_gifts.get(grant.actor_id)
        if entry and entry[0] == self.owner_id:
            window = entry[1].offer_t0(state.current_round, selectable=True)
            state.ga_gifts[grant.actor_id] = self.owner_id, window
            state.ga_choice_windows[window.window_id] = window

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """自己拆是持有者自己的T0，代拆是G6自己的T0。"""
        actor = grant.actor_id
        result = []
        entry = state.ga_gifts.get(actor)
        if stage is TurnStage.T0 and entry and entry[0] == self.owner_id:
            result.append(ability(grant, stage, "g6_open_box", verb=ActionVerb.INTERACT,
                                  targets=(actor,), after=self.perform))
        if not own_slot(state, self.owner_id, grant):
            return tuple(result)
        cfg = state.ga_config.get("talents", "G6")
        if stage is TurnStage.T0:
            result.append(ability(grant, stage, "g6_counter", costs=(CostEntry(
                actor, "resource:laugh", cfg["counter_laugh_cost"]),), after=self.perform))
            eligible = tuple(target for target, (owner, window) in state.ga_gifts.items() if
                             owner == actor and window.first_opportunity_round is not None and
                             state.players[target].alive and state.players[target].isolation_id ==
                             state.players[actor].isolation_id)
            for target in eligible:
                for face in (4, 5, 6):
                    result.append(ability(grant, stage, "g6_remote_box", targets=(target,),
                                          parameters={"face": face}, after=self.perform))
            if self.joy_timer and eligible:
                result.append(ability(grant, stage, "g6_open_all", targets=eligible,
                                      after=self.perform))
            return tuple(result)
        if stage is not TurnStage.T1:
            return tuple(result)
        targets = tuple(target for target in enemies(state, actor, local=False) if
                        target in state.players)
        if targets:
            result.append(ability(grant, stage, "g6_gifts", mode=Performance.PUBLIC,
                                  targets=targets, after=self.perform))
        for victim in targets:
            if victim in self.victims.get(state.current_round - 1, set()):
                continue
            result.extend(self.card_options(state, grant, victim))
            x = state.players[victim]
            for target in enemies(state, actor, local=False):
                enemy = units(state)[target]
                if target == victim or enemy.location != x.location:
                    continue
                for weapon in ("fist", "knife", "bow", "electromagnetic_rifle", "gauss_rifle"):
                    for source in ("own", "borrow"):
                        user = self.body if source == "own" else x
                        if weapon not in user.weapons or weapon in user.disabled_weapons:
                            continue
                        costs = ()
                        if source == "own" and weapon in (
                            "bow", "electromagnetic_rifle", "gauss_rifle"):
                            if not (weapon == "bow" and "infinite" in user.modules):
                                costs = (CostEntry(actor, f"ammo:{weapon}", state.ga_config.get(
                                    "weapons", weapon, "ammo_per_attack")),)
                        candidate = ability(grant, stage, "g6_borrow", mode=Performance.INSTANT,
                            verb=ActionVerb.ATTACK, targets=(target,), parameters={"victim": victim,
                            "weapon": weapon, "source": source}, costs=costs, hostile=True,
                            after=self.perform)
                        effect = _attack_effect(state, grant, candidate.request, weapon) if (
                            source == "own") else packet(state, candidate.request,
                            state.ga_config.get("weapons", weapon, "damage"), attribute=(
                                DamageAttribute.TECH if weapon.endswith("rifle") else
                                DamageAttribute.NORMAL), scope=TargetScope.RANGED_SINGLE)
                        effect = replace(effect, source_id="g6_borrow", crime_owner_id=victim,
                            scope=TargetScope.RANGED_SINGLE, parameters={**dict(effect.parameters),
                            "ignore_range_penalty": True})
                        if not _visible(x, enemy, effect.attribute):
                            continue
                        result.append(replace(candidate, effects=(effect,),
                                              weapon_use=source == "own"))
        return tuple(result)

    def card_options(
        self, state: GameState, grant: ActionGrant, victim: str,
    ) -> tuple[ActionOption, ...]:
        """借用同一地点菜单的纯枚举，虚拟位置不写回GameState或复制钱包。"""
        result = []
        proxy = replace(self.body, location=state.players[victim].location)

        def add(
            operation: str, verb: ActionVerb, *, costs: tuple[CostEntry, ...] = (),
            targets: tuple[str, ...] = (), **parameters: Any,
        ) -> ActionOption:
            price = sum(c.amount for c in costs if c.account == "credits")
            theft = min(price, state.players[victim].credits,
                        state.ga_config.get("talents", "G6", "card_theft_cap"))
            fake = min(price - theft, self.body.resources["laugh"])
            costs = (CostEntry(victim, "credits", theft),
                     CostEntry(self.owner_id, "resource:laugh", fake),
                     CostEntry(self.owner_id, "credits", price - theft - fake))
            candidate = ability(grant, TurnStage.T1, "g6_card", mode=Performance.INSTANT,
                verb=ActionVerb.INTERACT, parameters={"victim": victim, "menu_operation": operation,
                                                     **parameters}, costs=costs, after=self.perform)
            if price:
                result.append(candidate)
            return candidate

        _location_options(state, proxy, add, lambda account, amount: (
            CostEntry(self.owner_id, account, amount),))
        return tuple(result)

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """假币只能抵实际信用消费，不能创造负债或重复抵已经拆分的卡账。"""
        if grant.actor_id != self.owner_id:
            return options
        result = []
        for option in options:
            if option.request.verb is ActionVerb.INTERACT and option.request.operation != "g6_card":
                remaining = self.body.resources["laugh"]
                costs = []
                for cost in option.definition.costs:
                    discount = min(remaining, cost.amount) if cost.account == "credits" else 0
                    remaining -= discount
                    costs.append(replace(cost, amount=cost.amount - discount))
                used = self.body.resources["laugh"] - remaining
                if used:
                    costs.append(CostEntry(self.owner_id, "resource:laugh", used))
                option = replace(option, definition=replace(option.definition, costs=tuple(costs)))
            result.append(option)
        return tuple(result)

    def perform(self, state: GameState, plan: ActionPlan) -> None:
        """赠盒不覆盖旧盒，拆盒先完成真实响应再处理有限奖惩。"""
        request = plan.request
        actor = request.actor_id
        if request.operation == "g6_counter":
            self.armed = True
            self.counter_slot = request.grant_id
        elif request.operation == "g6_gifts":
            for target in request.target_ids:
                if target not in state.ga_gifts:
                    window = ChoiceWindow(next_id(state, "gift"), self.owner_id, target,
                        ("open", "keep"), state.ga_config.get(
                            "talents", "G6", "gift_fallback_ticks"),
                        allow_remote_resolution=True, requires_t0_opportunity=True)
                    state.ga_gifts[target] = self.owner_id, window
                    state.ga_choice_windows[window.window_id] = window
            high_impact(state, actor, request.root_id)
        elif request.operation in ("g6_open_box", "g6_remote_box", "g6_open_all"):
            if request.operation == "g6_open_box":
                owner, window = state.ga_gifts[actor]
                state.ga_gifts[actor] = owner, window.offer_t0(state.current_round, selectable=True)
            for target in request.target_ids:
                if state.game_over:
                    break
                face = request.parameters.get("face")
                if request.operation == "g6_open_all":
                    face = int(ask(state, actor, actor, ("4", "5", "6"), kind="gift_face",
                                   context={"target": target}))
                if face is None:
                    face = state.ga_rng.die(f"{request.root_id}/gift", state.ga_config.get(
                        "talents", "G6", "gift_faces"), actor_id=actor)
                self.open_box(state, target, face, request.root_id, responder=actor)
        elif request.operation == "g6_card":
            params = {k: v for k, v in request.parameters.items() if k not in (
                "victim", "menu_operation")}
            nested = replace(request, operation=request.parameters["menu_operation"],
                             parameters=params)
            _apply_builtin(state, nested)
            self.last_victim = request.parameters["victim"], state.current_round
            self.victims.setdefault(state.current_round, set()).add(request.parameters["victim"])
            chapter(state, self.owner_id, 2)
            high_impact(state, actor, request.root_id)
        else:
            self.last_victim = request.parameters["victim"], state.current_round
            self.victims.setdefault(state.current_round, set()).add(request.parameters["victim"])
        touch(state)

    def open_box(
        self, state: GameState, target: str, face: int, root: str, *, responder: str | None,
    ) -> None:
        """拆盒事件发生一次；奖励选择由持有者作答，代拆不得替他选收益。"""
        entry = state.ga_gifts.pop(target)
        window = entry[1]
        if window.state is ChoiceState.PENDING:
            window = window.resolve("open", actor_id=responder or self.owner_id)
        state.ga_choice_windows[window.window_id] = window
        self.boxes_opened += 1
        emit(state, EventKind.STATE_CHANGED, root_id=root, actor_id=self.owner_id, target_id=target,
             effective=True, payload={"kind": "gift_opened", "face": face})
        if self.boxes_opened >= 3:
            chapter(state, self.owner_id, 3)
        player = state.players[target]
        cfg = state.ga_config.get("talents", "G6")
        from engine.ga.state import ActionGrant
        from engine.ga.actions import GrantKind
        grant = ActionGrant(root, self.owner_id, GrantKind.REACTION, root_id=root)
        request = ability(grant, TurnStage.T1,
                          "g6_gift_effect", targets=(target,)).request
        if face == 1:
            player.credits += cfg["gift_credits"]
            heal(player, cfg["gift_heal"])
        elif face == 2:
            choices = tuple(f"armor:{a}" for a in state.ga_config.get("armor")) + tuple(
                f"weapon:{w}" for w in ("knife", "bow", "electromagnetic_rifle", "gauss_rifle"))
            selected = ask(state, self.owner_id, target, choices, kind="gift_equipment")
            category, name = selected.split(":", 1)
            if category == "armor":
                add_armor(state, target, name)
            else:
                player.weapons.add(name)
        elif face == 3:
            location = ask(state, self.owner_id, target, tuple(loc for loc in LOCATIONS if
                           loc not in state.destroyed_locations), kind="gift_door")
            relocate(state, target, location)
        elif face == 4:
            actual = min(player.credits, cfg["gift_theft_cap"])
            player.credits -= actual
            self.body.credits += actual
            resolve_batch(state, (packet(state, request, cfg["gift_damage"], kind=DamageKind.DIRECT,
                                        suffix=f"gift/{target}/4",
                                        scope=TargetScope.RANGED_SINGLE, certain=True),))
        elif face == 5:
            armors = tuple(key for key, d in player.defenses.items() if d.kind.value == "armor" and
                           d.durability)
            if armors:
                key = ask(state, self.owner_id, target, armors, kind="gift_broken_armor")
                resolve_batch(state, (packet(state, request, player.defenses[key].durability,
                    kind=DamageKind.PURE_WEAR, certain=True, bypass=True, armor_id=key,
                    suffix=f"gift/{target}/5", scope=TargetScope.RANGED_SINGLE),))
            else:
                weapons = tuple(sorted(player.weapons - {"fist", "knife", "bow"}))
                if weapons:
                    key = ask(state, self.owner_id, target, weapons, kind="gift_broken_weapon")
                    if key in ARTIFACT_WEAPONS:
                        player.disabled_weapons[key] = timer(state, target, "gift/disabled",
                                                            cfg["gift_artifact_disable_ticks"])
                    else:
                        player.weapons.discard(key)
        else:
            choices = tuple(loc for loc in LOCATIONS if loc not in state.destroyed_locations and
                            loc != player.location)
            if choices:
                index = state.ga_rng.die(f"{root}/gift-door", len(choices)) - 1
                relocate(state, target, choices[index])
            changed = put_status(player, state.ga_config, state.current_round, "SHOCKED",
                                 source_id=root, actor_id=self.owner_id)
            if changed:
                emit(state, EventKind.STATUS_APPLIED, root_id=root, actor_id=self.owner_id,
                     target_id=target, effective=True, payload={"statuses": ("SHOCKED",)})
            if target in (state.ga_police.captain, state.ga_police.guard_id):
                state.ga_police.stance = "mobile"
        touch(state)

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """只吞首段合法单体攻击，不吞AOE/DOT/环境或明文直接死亡。"""
        return IncomingModifier(substituted=self.armed and target.player_id == self.owner_id and
            effect.actor_id is not None and effect.actor_id != self.owner_id and effect.scope in (
                TargetScope.LOCAL_SINGLE, TargetScope.RANGED_SINGLE) and effect.damage_kind in (
                    DamageKind.NORMAL, DamageKind.DIRECT))

    def counter(self, state: GameState, target: str, root: str, amount: int) -> None:
        """反噬追真实施术者，不追案底替身；独立反应不发T0/T2。"""
        if target not in units(state) or not units(state)[target].alive or not self.body.alive or (
            units(state)[target].isolation_id != self.body.isolation_id):
            return
        from engine.ga.actions import GrantKind
        from engine.ga.state import ActionGrant
        grant = ActionGrant(root + "/counter", self.owner_id, GrantKind.REACTION, root_id=root)
        request = ability(grant,
                          TurnStage.T1, "g6_riposte", targets=(target,)).request
        resolve_batch(state, (packet(state, request, amount, kind=DamageKind.DIRECT, certain=True,
                                    suffix=f"counter/{self.owner_id}/{target}",
                                    scope=TargetScope.RANGED_SINGLE, statuses=("SHOCKED",)),))

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """当身确认后立刻耗玩偶；后段不能重复兑现。"""
        for record in records:
            if self.armed and record.target_id == self.owner_id and (
                record.outcome is HitOutcome.SUBSTITUTED):
                self.armed = False
                amount = state.ga_config.get("talents", "G6", "poem_counter_damage" if
                                             self.joy_timer else "counter_damage")
                high_impact(state, self.owner_id, record.effect.root_id)
                chapter(state, self.owner_id, 2)
                state.ga_after_batch_queue.append(partial(self.counter,
                                                         target=record.effect.actor_id,
                                                         root=record.effect.root_id, amount=amount))
            if record.effect.actor_id == self.owner_id and record.effect.source_id == (
                "g6_borrow") and (
                record.effective_hit):
                chapter(state, self.owner_id, 2)

    def on_events(self, state: GameState, events: tuple[DomainEvent, ...]) -> None:
        """只对真实场面领根级笑点，自己主动放弃和硬控跳槽均不返成本。"""
        if not self.body.alive:
            return
        if self.gain_round != state.current_round:
            self.gain_round, self.gain_amount = state.current_round, 0
        cfg = state.ga_config.get("talents", "G6")
        for event in events:
            eligible = event.kind in (EventKind.EVADED, EventKind.ARMOR_BROKEN) or (
                event.kind is EventKind.TURN_FORFEITED and event.actor_id != self.owner_id and
                not event.payload.get("controlled")) or event.payload.get("kind") == "gift_opened"
            if eligible and event.root_scope not in self.laugh_roots:
                self.laugh_roots.add(event.root_scope)
                gained = min(cfg["laugh_per_root"], cfg["laugh_per_round"] - self.gain_amount,
                             cfg["laugh_cap"] - self.body.resources["laugh"])
                self.body.resources["laugh"] += gained
                self.gain_amount += gained
                if gained:
                    chapter(state, self.owner_id, 1)

    def boundary(self, state: GameState, *, boundary: str) -> None:
        """读空至下个自身槽；礼盒年龄只从真实响应机会之后推进。"""
        if boundary == "slot_start" and state.current_grant.actor_id == self.owner_id and (
            self.armed and state.current_grant.grant_id != self.counter_slot):
            self.armed = False
            if self.joy_timer and self.body.alive:
                targets = enemies(state, self.owner_id)
                if not targets:
                    for loc in LOCATIONS:
                        targets = tuple(t for t in enemies(state, self.owner_id, local=False) if
                                        units(state)[t].location == loc)
                        if targets:
                            break
                if targets:
                    index = state.ga_rng.die("joy/missed", len(targets),
                                             actor_id=self.owner_id) - 1
                    target = targets[index]
                    self.counter(state, target, next_id(state, "joy/miss"), state.ga_config.get(
                        "poems", "joy_missed_counter_damage"))
        if boundary == "R4.expiry":
            if self.joy_timer:
                self.joy_timer = self.joy_timer.on_r4(state.current_round)
                if not self.joy_timer.remaining:
                    self.joy_timer = None
            for target, (owner, window) in tuple(state.ga_gifts.items()):
                if owner != self.owner_id:
                    continue
                if not state.players[target].alive:
                    state.ga_gifts.pop(target)
                    continue
                updated = window.on_r4(state.current_round)
                state.ga_gifts[target] = owner, updated
                state.ga_choice_windows[window.window_id] = updated
                if updated.state is ChoiceState.EXPIRED:
                    face = 3 + state.ga_rng.die("gift/fallback", 3)
                    self.open_box(state, target, face, next_id(state, "gift/fallback"),
                                  responder=None)
                    if state.game_over:
                        return

    def receive_poem(self, state: GameState, giver: str) -> None:
        """置满与被动轮额度分开，重复只刷新四未来R4标记。"""
        self.body.resources["laugh"] = state.ga_config.get("poems", "joy_laugh_set")
        self.joy_timer = timer(state, self.owner_id, "joy", state.ga_config.get(
            "poems", "joy_ticks"))
