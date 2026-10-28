"""GA唯一防暴编队：公开案底、合法通缉、共用攻击额度与有限盾墙。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, TYPE_CHECKING

from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    ActionPlan, ActionRequest, ActionVerb, DamageAttribute, DamageKind, DefenseKind, DefenseSpec,
    EffectKind, HitOutcome, HitPolicy, Performance, TargetScope, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.extensions import IncomingModifier, unit, units
from engine.ga.gameplay import ActionOption, _attack_effect, _visible
from engine.ga.state import GaPlayer, LOCATIONS, emit, next_id, touch, trace
from engine.ga.talents.base import ability, packet

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ResolutionRecord
    from engine.ga.events import DomainEvent
    from engine.ga.state import ActionGrant


@dataclass
class PoliceState:
    """编制与盾墙真实资源，切姿态不重置。"""

    officer_ids: tuple[str, ...]
    wall: int
    captain: str | None = None
    guard_id: str | None = None
    applicants: list[str] = field(default_factory=list)
    wanted: list[str] = field(default_factory=list)
    location: str = "police"
    stance: str = "mobile"
    attack_upgrade: int = 0
    wall_upgrade: int = 0
    attack_round: int = 0
    immobile_round: int = 0
    stopped: set[str] = field(default_factory=set)
    case_serial: int = 0


class Police:
    """编队是一个公共系统，特警不是三个独立行动槽。"""

    def register(self, state: GameState) -> None:
        """满编只初始化一次；实例和案例归正式GameState。"""
        cfg = state.ga_config
        ids = tuple(f"npc/police/{i}" for i in range(cfg.get("police", "roster_cap")))
        hp = cfg.get("police", "officer_hp")
        for key in ids:
            state.ga_units[key] = GaPlayer(key, hp, hp, 0, 0, 0, "police", awake=True,
                                          home_locked=True, is_player=False, unit_kind="police")
        state.ga_police = PoliceState(ids, len(ids) * cfg.get("police", "wall_per_officer"))
        state.ga_case_filters = []
        state.ga_action_providers.append(self.options)
        state.ga_option_filters.append(self.filter_options)
        state.ga_before_action.append(self.before_action)
        state.ga_after_action.append(self.after_action)
        state.ga_batch_listeners.append(self.on_batch)
        state.ga_event_listeners.append(self.on_events)
        state.ga_incoming_modifiers.append(self.incoming)
        state.ga_location_listeners.append(self.location_changed)
        state.ga_hooks.setdefault("R2", []).append(self.r2)
        state.ga_hooks.setdefault("R4.police", []).append(self.r4)

    def live(self, state: GameState) -> tuple[str, ...]:
        """编制人数只读真实存活NPC。"""
        return tuple(key for key in state.ga_police.officer_ids if state.ga_units[key].alive)

    def available(self, state: GameState) -> bool:
        """停机/毁局不删案底，不复活阵亡者。"""
        return any(not state.ga_units[k].submerged_sources for k in self.live(state)) and (
            "police" not in state.destroyed_locations) and not (
            state.ga_police.stopped or state.ga_clock_paused
        )

    def location(self, state: GameState) -> str:
        """随身编队的唯一位置来自队长或驻地。"""
        captain = state.ga_police.captain
        if state.ga_police.location in state.destroyed_locations:
            state.ga_police.location = next(loc for loc in LOCATIONS if loc not in
                                            state.destroyed_locations)
        return state.players[captain].location if captain and state.players[captain].alive else (
            state.ga_police.location
        )

    def sync(self, state: GameState) -> None:
        """同步物理位置/盾墙副本，不补充一丝已损耐久。"""
        police = state.ga_police
        if police.captain and not state.players[police.captain].alive:
            police.captain, police.stance, police.location = None, "mobile", "police"
        if police.guard_id and not state.players[police.guard_id].alive:
            police.guard_id = None
        protected = police.captain or police.guard_id
        cap = len(self.live(state)) * (state.ga_config.get("police", "wall_per_officer") +
                                      police.wall_upgrade * state.ga_config.get(
                                          "police", "wall_upgrade_per_officer"))
        if protected:
            old = state.players[protected].defenses.get("police/wall")
            if old:
                police.wall = min(police.wall, old.durability)
        police.wall = min(police.wall, cap)
        for player in state.players.values():
            player.defenses.pop("police/wall", None)
        for key in police.officer_ids:
            npc = state.ga_units[key]
            npc.location = self.location(state)
            npc.owner_id = police.captain
        if protected and police.stance == "wall" and police.wall and self.available(state):
            state.players[protected].defenses["police/wall"] = DefenseSpec(
                "police/wall", DefenseKind.FINITE_BARRIER, police.wall, max(1, cap),
                frozenset({DamageKind.NORMAL, DamageKind.DIRECT, DamageKind.PURE_WEAR}),
                frozenset({TargetScope.LOCAL_SINGLE, TargetScope.RANGED_SINGLE}),
            )

    def mark_wanted(self, state: GameState, offender: str) -> bool:
        """只对已登记的合法案件挂通缉；停机仍可记录。"""
        if offender in state.ga_police.wanted or not any(
            case["actor_id"] == offender for case in state.ga_crimes
        ):
            return False
        state.ga_police.wanted.append(offender)
        emit(state, EventKind.STATE_CHANGED, actor_id=offender, effective=True,
             payload={"police": "wanted"})
        touch(state)
        return True

    def eligible(self, state: GameState, target: str) -> bool:
        """执法射击复核通缉、存活、可见、隔离；步枪支持跨建筑。"""
        if not self.available(state) or target not in state.ga_police.wanted:
            return False
        player = state.players.get(target)
        officer = state.ga_units[self.live(state)[0]]
        return bool(player and player.alive and player.home_locked and not player.hidden and
                    player.isolation_id == officer.isolation_id)

    def fire(
        self, state: GameState, target: str | None = None, *, owner: str | None = None,
    ) -> bool:
        """协同/即时/R4共用一次普通攻击；无辜命令只减编不射击。"""
        police = state.ga_police
        self.sync(state)
        if target is not None and target not in police.wanted and self.available(state):
            live = self.live(state)
            for key in live[:state.ga_config.get("police", "innocent_command_roster_loss")]:
                state.ga_units[key].hp = 0
            police.stance = "mobile"
            self.sync(state)
            trace(state, "police_backlash", target=target)
            touch(state)
            return False
        if police.attack_round == state.current_round:
            return False
        candidates = (target,) if target is not None else tuple(police.wanted)
        selected = next((key for key in candidates if self.eligible(state, key)), None)
        if selected is None:
            return False
        police.attack_round = state.current_round
        live = self.live(state)
        shooters = [key for key in live if "control" not in state.ga_units[key].statuses and
                    not state.ga_units[key].submerged_sources]
        for key in live:
            control = state.ga_units[key].statuses.get("control")
            if control and control.name in ("SHOCKED", "ACTION_SUPPRESSED"):
                del state.ga_units[key].statuses["control"]
        if not shooters:
            return False
        actor = shooters[0]
        request = ability_dummy(state, actor, selected)
        cfg = state.ga_config
        amount = (cfg.get("police", "gauss_volley_base" if police.attack_upgrade else
                          "volley_base") + len(self.live(state)) *
                  cfg.get("police", "volley_per_officer"))
        effect = packet(state, request, amount, source="police/volley", certain=False,
                        attribute=DamageAttribute.TECH if police.attack_upgrade else
                        DamageAttribute.NORMAL, scope=TargetScope.RANGED_SINGLE)
        effect = replace(effect, score_owner_id=owner or police.captain, crime_owner_id=None)
        trace(state, "police_target", target=selected, amount=amount)
        resolve_batch(state, (effect,))
        return True

    def upgrade(self, state: GameState, kind: str) -> bool:
        """升级真实编队，满级不再给免费维修。"""
        if not self.available(state) and kind != "reinforce":
            return False
        police = state.ga_police
        cfg = state.ga_config
        if kind == "attack" and police.attack_upgrade < cfg.get("police", "attack_upgrade_cap"):
            police.attack_upgrade += 1
        elif kind == "wall" and police.wall_upgrade < cfg.get("police", "wall_upgrade_cap"):
            police.wall_upgrade += 1
            police.wall += len(self.live(state)) * cfg.get("police", "wall_upgrade_per_officer")
            if police.captain or police.guard_id:
                state.players[police.captain or police.guard_id].defenses.pop("police/wall", None)
        elif kind == "reinforce" and "police" not in state.destroyed_locations and not (
            police.stopped or state.ga_clock_paused
        ):
            missing = [key for key in police.officer_ids if not state.ga_units[key].alive]
            if not missing:
                return False
            for key in missing[:cfg.get("police", "reinforce_count")]:
                state.ga_units[key].hp = cfg.get("police", "officer_hp")
                state.ga_death_cleaned.discard(key)
        else:
            return False
        self.sync(state)
        touch(state)
        return True

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """公共服务与NPC普通攻击也走同一候选集。"""
        player = state.players[grant.actor_id]
        police = state.ga_police
        result = []
        if not player.awake or not player.home_locked:
            return ()
        if stage is TurnStage.T0 and grant.actor_id in (police.captain, police.guard_id) and (
            self.available(state)
        ):
            for stance in ("mobile", "wall"):
                if stance != police.stance and (stance != "wall" or police.wall):
                    result.append(ability(grant, stage, "police_stance",
                                          parameters={"stance": stance}, after=self.execute))
        if stage is not TurnStage.T1:
            return tuple(result)
        if player.location == "police":
            if self.available(state) and not police.captain and grant.actor_id not in police.wanted:
                if grant.actor_id not in police.applicants:
                    result.append(ability(grant, stage, "police_apply", verb=ActionVerb.INTERACT,
                                          after=self.execute))
            for case in state.ga_crimes:
                offender = case["actor_id"]
                if offender not in police.wanted and offender != grant.actor_id and (
                    grant.actor_id == case.get("victim_id") or grant.actor_id in case.get(
                        "witness_ids", ())
                ):
                    result.append(ability(grant, stage, "police_report", verb=ActionVerb.INTERACT,
                                          targets=(offender,), after=self.execute))
            cap = len(self.live(state)) * (state.ga_config.get("police", "wall_per_officer") +
                                          police.wall_upgrade * state.ga_config.get(
                                              "police", "wall_upgrade_per_officer"))
            if self.available(state) and police.wall < cap:
                from engine.ga.contracts import CostEntry
                result.append(ability(grant, stage, "police_repair", verb=ActionVerb.INTERACT,
                                      costs=(CostEntry(grant.actor_id, "credits",
                                                       state.ga_config.get(
                                                           "police", "wall_repair_price")),),
                                      after=self.execute))
        if police.captain == grant.actor_id and self.available(state):
            for target in sorted(state.players):
                if target != grant.actor_id and state.players[target].alive:
                    result.append(ability(grant, stage, "police_command", targets=(target,),
                                          after=self.execute))
        for target, npc in state.ga_units.items():
            if not npc.alive or npc.owner_id == player.player_id or (
                npc.isolation_id != player.isolation_id
            ):
                continue
            for weapon in sorted(player.weapons & set(state.ga_config.get("weapons"))):
                if weapon in ("gun_ammo", "earthquake") or weapon in player.disabled_weapons:
                    continue
                if weapon == "hook" and (player.hook_cooldown or grant.hook_on_cooldown):
                    continue
                ranged = weapon in ("bow", "electromagnetic_rifle", "gauss_rifle", "hook")
                if npc.location != player.location and not ranged:
                    continue
                from engine.ga.contracts import CostEntry
                costs = ()
                if weapon in ("bow", "electromagnetic_rifle", "gauss_rifle") and not (
                    weapon == "bow" and "infinite" in player.modules
                ):
                    costs = (CostEntry(player.player_id, f"ammo:{weapon}", state.ga_config.get(
                        "weapons", weapon, "ammo_per_attack")),)
                candidate = ability(grant, stage, "npc_attack", verb=ActionVerb.ATTACK,
                                    targets=(target,), parameters={"weapon": weapon}, costs=costs,
                                    hostile=True, after=self.execute)
                effect = _attack_effect(state, grant, candidate.request, weapon)
                result.append(replace(candidate, effects=(effect,), weapon_use=True))
        return tuple(result)

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """盾墙当轮禁移动；范围攻击不能漏掉同地特警。"""
        police = state.ga_police
        result = []
        for option in options:
            if grant.actor_id in (police.captain, police.guard_id) and (
                police.immobile_round == state.current_round
            ) and (
                option.request.verb is ActionVerb.MOVE
            ):
                continue
            effects = []
            for effect in option.effects:
                if effect.scope in (TargetScope.LOCAL_AREA, TargetScope.GLOBAL_AREA):
                    location = state.players[grant.actor_id].location
                    extras = tuple(key for key in self.live(state) if
                                   key not in effect.target_ids and
                                   state.ga_units[key].location == location
                                   and state.ga_units[key].owner_id != grant.actor_id)
                    if extras:
                        effect = replace(effect, target_ids=(*effect.target_ids, *extras))
                effects.append(effect)
            result.append(replace(option, effects=tuple(effects)))
        return tuple(result)

    def execute(self, state: GameState, plan: ActionPlan) -> None:
        """公共操作只执行已通过共同预检的选择。"""
        request = plan.request
        police = state.ga_police
        if request.operation == "police_apply":
            police.applicants.append(request.actor_id)
        elif request.operation == "police_report":
            self.mark_wanted(state, request.target_ids[0])
        elif request.operation == "police_stance":
            police.stance = request.parameters["stance"]
            if police.stance == "wall":
                police.immobile_round = state.current_round
        elif request.operation == "police_repair":
            if police.captain or police.guard_id:
                state.players[police.captain or police.guard_id].defenses.pop("police/wall", None)
            police.wall += state.ga_config.get("police", "wall_repair_amount")
        elif request.operation == "police_command":
            self.fire(state, request.target_ids[0])
        self.sync(state)
        touch(state)

    def before_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """记录主动移动的起点以供受限跟随反应使用。"""
        if plan.request.verb is ActionVerb.MOVE:
            state.ga_action_meta[plan.request.root_id]["from_location"] = state.players[
                plan.request.actor_id
            ].location

    def after_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """随队长移动；合法普通攻击触发共用额度协同。"""
        self.sync(state)
        if plan.request.actor_id == state.ga_police.captain and state.ga_police.stance == "mobile":
            if plan.request.verb is ActionVerb.ATTACK and plan.request.target_ids and (
                plan.request.root_id not in state.ga_quiet_roots
            ) and self.eligible(state, plan.request.target_ids[0]):
                self.fire(state, plan.request.target_ids[0])

    def location_changed(self, state: GameState, actor_id: str) -> None:
        """移动、钩索和建筑驱逐都同步随身编队。"""
        if actor_id == state.ga_police.captain:
            self.sync(state)

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """只把真实玩家伤人写成案件；各角色的免罪过滤在这里统一应用。"""
        for record in records:
            effect = record.effect
            if effect.crime_owner_id in state.players and record.target_id in state.players and (
                effect.crime_owner_id != record.target_id and record.hp_loss > 0 and
                effect.root_id not in state.ga_quiet_roots
            ) and all(callback(state, record) for callback in state.ga_case_filters):
                self.case(state, effect.crime_owner_id, record.target_id, "player_damage")
            if record.target_id in (state.ga_police.captain, state.ga_police.guard_id) and (
                "SHOCKED" in record.applied_statuses or effect.source_id == "hook"
            ):
                state.ga_police.stance = "mobile"
        self.sync(state)

    def case(self, state: GameState, actor: str, victim: str | None, kind: str) -> None:
        """案件保存当时目击者，后来走到现场不能补造证据。"""
        location = state.players[victim or actor].location
        state.ga_police.case_serial += 1
        state.ga_crimes.append({"id": state.ga_police.case_serial, "actor_id": actor,
                                "victim_id": victim, "kind": kind, "round": state.current_round,
                                "witness_ids": [key for key, p in state.players.items() if
                                                p.alive and p.location == location]})

    def on_events(self, state: GameState, events: tuple[DomainEvent, ...]) -> None:
        """补全军基案件与闪光破阵，停机不清案底。"""
        for case in state.ga_crimes:
            if "id" not in case:
                state.ga_police.case_serial += 1
                case["id"] = state.ga_police.case_serial
                location = state.players[case["actor_id"]].location
                case["witness_ids"] = [key for key, p in state.players.items() if p.alive and
                                       p.location == location]
        for event in events:
            if event.kind is EventKind.STATUS_APPLIED and event.target_id in (
                state.ga_police.captain, state.ga_police.guard_id
            ):
                if set(event.payload.get("statuses", ())) & {"SHOCKED", "FLASHED"}:
                    state.ga_police.stance = "mobile"
                    self.sync(state)

    def incoming(self, state: GameState, effect: Any, target: GaPlayer) -> IncomingModifier:
        """普通来袭默认正面；明文背击不获正面盾墙拦截。"""
        bypass = target.player_id in (state.ga_police.captain, state.ga_police.guard_id) and (
            effect.parameters.get("attack_side", "front") == "back"
        )
        ignored = frozenset({"police/wall"}) if bypass else frozenset()
        return IncomingModifier(ignored_defenses=ignored)

    def r2(self, state: GameState) -> None:
        """仅就任与状态推进，绝不在R2齐射。"""
        police = state.ga_police
        if police.captain and not state.players[police.captain].alive:
            police.captain = None
            police.location = "police"
            police.stance = "mobile"
        if not police.captain and self.available(state):
            police.captain = next((key for key in state.ga_round_order if
                                   key in police.applicants and state.players[key].alive and
                                   key not in police.wanted), None)
            if police.captain:
                police.guard_id = None
        police.applicants.clear()
        self.sync(state)

    def r4(self, state: GameState) -> None:
        """R4唯一自动执法点。"""
        self.fire(state)


def ability_dummy(state: GameState, actor: str, target: str) -> ActionRequest:
    """系统攻击只有效果身份，不额外授予一个玩家行动槽。"""
    from engine.ga.contracts import ActionRequest
    key = next_id(state, "police-volley")
    return ActionRequest(actor, key, key, TurnStage.T1, ActionVerb.ATTACK,
                         "police_volley", (target,), {}, Performance.NONE)
