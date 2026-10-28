"""GA控制器公开事实投影；不包含秘密承诺、随机流或控制器内部状态。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, TYPE_CHECKING

from engine.ga.contracts import ContractError, freeze_json
from engine.ga.scoring import crown_eligible, score_components

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant, GaPlayer


OBSERVATION_SCHEMA = "ga.observation.v2"


def public_value(value: Any) -> Any:
    """只转换显式选取的公开值对象，不对GameState或角色实例做反射导出。"""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: public_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {public_value(key): public_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(public_value(item) for item in value)
    if isinstance(value, (tuple, list)):
        return [public_value(item) for item in value]
    return value


def _body(player: GaPlayer) -> dict[str, Any]:
    """玩家与NPC共享已公开的战斗状态，旧耐久摘要继续保留。"""
    return {
        "hp": player.hp, "max_hp": player.max_hp, "location": player.location,
        "alive": player.alive, "awake": player.awake, "hidden": player.hidden,
        "absolute_dead": player.absolute_dead, "exited": player.exited,
        "home_locked": player.home_locked, "moved": player.moved, "engaged": player.engaged,
        "healable": player.healable, "targetable": player.targetable,
        "isolation_id": player.isolation_id, "flat_reduction": player.flat_reduction,
        "weapons": sorted(player.weapons), "ammo": player.ammo, "inventory": player.inventory,
        "modules": player.modules, "bow_attribute": player.bow_attribute.value,
        "defenses": {key: item.durability for key, item in player.defenses.items()},
        "defense_details": public_value(player.defenses),
        "armor_affinities": public_value(player.armor_affinities),
        "armor_kinds": player.armor_kinds, "barrier_timers": public_value(player.barrier_timers),
        "statuses": public_value(player.statuses), "burns": public_value(player.burns),
        "hidden_timer": public_value(player.hidden_timer),
        "disabled_weapons": public_value(player.disabled_weapons),
        "immune_kinds": public_value(player.immune_kinds),
        "status_immunities": sorted(player.status_immunities),
        "locked_targets": sorted(player.locked_targets), "revealed_to": sorted(player.revealed_to),
        "submerged_sources": sorted(player.submerged_sources),
        "surgery": player.surgery, "hospital_heals": player.hospital_heals,
        "syringe_purchases": player.syringe_purchases, "furnace_uses": player.furnace_uses,
        "sharpened": player.sharpened, "charged": player.charged,
        "hook_cooldown": player.hook_cooldown, "initiative_bonus": player.initiative_bonus,
        "retain_stealth_attacks": player.retain_stealth_attacks,
    }


def build_observation(
    state: GameState, actor_id: str, *, options: tuple[ActionOption, ...] = (),
    grant: ActionGrant | None = None,
) -> Mapping[str, Any]:
    """复制公开事实和本次候选说明；所有估计与预测由策略层另行报告。"""
    from engine.ga.gameplay import describe_options

    if actor_id not in state.players:
        raise ContractError("observation.actor", "unknown participant")
    cfg = state.ga_config
    players = {}
    for key, player in state.players.items():
        role = state.ga_roles.get(key)
        players[key] = {
            **_body(player), "credits": player.credits, "sp": player.sp, "pp": player.pp,
            "resources": player.resources, "talent": role.talent_id if role else None,
            "talent_state": role.public() if role else {},
            "score": {"kills": player.kills, "hp_damage": player.hp_damage,
                      "armor_breaks": player.armor_breaks, "chapters": sorted(player.chapters),
                      "components": score_components(player, cfg),
                      "crown_eligible": crown_eligible(player, cfg)},
        }
    police = getattr(state, "ga_police", None)
    afterlife = getattr(state, "ga_afterlife", None)
    world = getattr(state, "ga_world", None)
    current_order = state.current_round > 0 and state.ga_order_round == state.current_round
    current = grant or state.current_grant
    if current is not None and current.finished:
        current = None
    return freeze_json({
        "schema": OBSERVATION_SCHEMA, "ruleset": cfg.get("_meta", "ruleset"),
        "parameter_fingerprint": cfg.fingerprint, "revision": state.ga_revision,
        "actor_id": actor_id, "round": state.current_round, "world_tick": state.world_tick,
        "phase": state.current_phase.value, "public_used": state.ga_public_used,
        "closure_display": state.ga_closure_display, "players": players,
        "units": {key: {**_body(p), "kind": p.unit_kind, "owner_id": p.owner_id}
                  for key, p in state.ga_units.items()},
        "schedule": {
            "order_ready": current_order,
            "stage": options[0].request.stage.value if options else (
                state.current_stage.value if current and state.current_stage else None),
            "initiative": state.ga_initiative if current_order else {},
            "round_order": state.ga_round_order if current_order else (),
            "grant": public_value(current),
            "standard_grants": public_value(state.ga_standard_grants) if current_order else {},
            "extra_used": sorted(state.ga_extra_used),
            "followup_used": sorted(state.ga_followup_used),
            "pending_extras": state.ga_pending_extras,
            "pending_followups": state.ga_pending_followups,
            "public_blocked": state.ga_public_blocked,
        },
        "world": {"clock_paused": state.ga_clock_paused,
                  "clock_accelerated": state.ga_clock_accelerated,
                  "max_rounds": cfg.get("clock", "max_rounds"),
                  "destroyed_locations": sorted(state.destroyed_locations),
                  "smoke": public_value(state.ga_smoke),
                  "rain_sources": sorted(world.rain) if world else [],
                  "reversal_sources": sorted(world.reversers) if world else []},
        "police": public_value(police), "crimes": state.ga_crimes,
        "ground_loot": public_value(state.ground_loot),
        "reward_allowance": {
            actor: {rule: max(0, cap - state.ga_ledger.awarded_in_round(
                rule, actor, state.current_round)) for rule, cap in (
                    ("armor_break_pp", cfg.get("pp", "armor_break_rewards_per_round")),
                    ("attention", cfg.get("action", "attention_per_round")),
                )} for actor in state.players
        },
        "insurance": state.ga_insurance, "module_stock": state.module_stock,
        "afterlife": None if afterlife is None else {
            "bets": afterlife.bets, "bet_round": afterlife.bet_round,
            "glow_round": afterlife.glow_round, "buff_round": afterlife.buff_round,
            "encore_used": sorted(afterlife.encore_used), "marks": afterlife.marks,
            "borrowed": afterlife.borrowed,
            "gaze": sorted(afterlife.gaze), "remainder_cursor": afterlife.remainder_cursor,
            "break_rounds": public_value(afterlife.break_rounds),
        },
        "terminal": state.terminal_snapshot.to_dict() if state.terminal_snapshot else None,
        "score_winners": state.score_winners,
        "candidates": describe_options(state, current, options) if options else (),
    })
