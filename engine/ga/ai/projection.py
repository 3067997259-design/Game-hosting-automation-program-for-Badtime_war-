"""只读公开事实的假设投影；不接触GameState、随机流或运行钩子。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from engine.ga.ai.planning import Route
from engine.ga.config import GaConfig
from engine.ga.contracts import (
    ActionRequest, DamageAttribute, DamageKind, DefenseKind, DefenseSpec, TargetScope, thaw_json,
)
from engine.ga.observation import public_value
from engine.ga.state import ARMOR_AFFINITIES


def _copy(view: Mapping[str, Any], assumption: str) -> dict[str, Any]:
    """复制玩家可见值；假设说明明确不包含路上对手行动和世界tick。"""
    projected = thaw_json(view)
    projected["projection"] = {"assumptions": [assumption,
        "does_not_simulate_opponents_world_ticks_or_runtime_hooks"], "unsupported": []}
    return projected


def _armor(player: dict[str, Any], kind: str, config: GaConfig) -> None:
    """与正式护甲采用相同值对象和参数，投影ID不消费引擎ID。"""
    cfg = config.get("armor", kind)
    key = f"projection/armor/{kind}/{len(player['defense_details'])}"
    defense = DefenseSpec(key, DefenseKind.ARMOR, cfg["durability"], cfg["durability"],
                          frozenset({DamageKind.NORMAL, DamageKind.PURE_WEAR}),
                          frozenset(TargetScope), {attribute: cfg[attribute.value] for attribute in
                          (DamageAttribute.NORMAL, DamageAttribute.TECH, DamageAttribute.MAGIC)},
                          config.get("combat", "normal_wear_price"))
    player["defense_details"][key] = public_value(defense)
    player["defenses"][key] = defense.durability
    player["armor_affinities"][key] = ARMOR_AFFINITIES[kind].value
    player["armor_kinds"][key] = kind


def _heal(player: dict[str, Any], amount: int) -> None:
    """生命上限和不可治疗资格仍遵守公开状态。"""
    if player["alive"] and player["healable"]:
        player["hp"] = min(player["max_hp"], player["hp"] + amount)


def _modules(player: dict[str, Any], modules: tuple[str, ...], attribute: str) -> None:
    """旧模块回库存，再取出目标装配；投影也不能复制物品。"""
    inventory = player["inventory"]
    for name in player["modules"]:
        key = f"module:{name}"
        inventory[key] = inventory.get(key, 0) + 1
    for name in modules:
        key = f"module:{name}"
        if inventory.get(key, 0) < 1:
            raise ValueError("projection cannot install unowned modules")
        inventory[key] -= 1
    player["modules"] = list(modules)
    player["bow_attribute"] = attribute


def _ammo_cap(config: GaConfig, weapon: str) -> int:
    """弓与枪弹分别读取正式容量。"""
    return config.get("start", "arrow_cap") if weapon == "bow" else config.get(
        "weapons", weapon, "ammo_cap")


def _loot(projected: dict[str, Any], player: dict[str, Any], config: GaConfig) -> None:
    """假设抵达时公开掉落仍在；按真实容量留下不能拾取的剩余。"""
    location = player["location"]
    piles = projected["ground_loot"].get(location, [])
    for pile in piles:
        player["credits"] += pile.get("credits", 0)
        pile["credits"] = 0
        player["weapons"] = sorted(set(player["weapons"]) | set(pile.get("weapons", ())))
        pile["weapons"] = []
        for key, defense in pile.get("defenses", {}).items():
            player["defense_details"][key] = defense
            player["defenses"][key] = defense["durability"]
        pile["defenses"] = {}
        player["armor_affinities"].update(pile.get("affinities", {}))
        player["armor_kinds"].update(pile.get("armor_kinds", {}))
        pile["affinities"], pile["armor_kinds"] = {}, {}
        inventory = pile.setdefault("inventory", {})
        for name in pile.get("modules", ()):
            key = f"module:{name}"
            inventory[key] = inventory.get(key, 0) + 1
        pile["modules"] = []
        for weapon, amount in pile.get("ammo", {}).items():
            take = min(amount, max(0, _ammo_cap(config, weapon) - player["ammo"].get(weapon, 0)))
            player["ammo"][weapon] = player["ammo"].get(weapon, 0) + take
            pile["ammo"][weapon] -= take
        for item, amount in inventory.items():
            cap = config.get("hospital", "syringe_hold_cap") if item == "syringe" else (
                config.get("consumables", "hold_cap") if item in (
                    "fragment", "shock", "flash", "smoke", "molotov") else
                player["inventory"].get(item, 0) + amount)
            take = min(amount, max(0, cap - player["inventory"].get(item, 0)))
            player["inventory"][item] = player["inventory"].get(item, 0) + take
            inventory[item] -= take
    projected["ground_loot"][location] = [pile for pile in piles if pile["credits"] or
        any(pile.get("ammo", {}).values()) or any(pile["inventory"].values()) or pile["weapons"] or
        pile["defenses"]]


def project_route(view: Mapping[str, Any], route: Route, config: GaConfig) -> dict[str, Any]:
    """假设整条路线成功完成后的公开状态；收益与耗时由上层另行评估。"""
    assumption = ("assumes_procurement_and_arrival_before_counter_throws" if route.family == (
        "counter") else "assumes_full_route_completion_with_current_supply_available")
    projected = _copy(view, assumption)
    projected["projection"]["route_id"] = route.goal_id
    projected["projection"]["t1_turns"] = route.t1_turns
    player = projected["players"][projected["actor_id"]]
    player["credits"] += route.facts.get("credit_income", 0) - route.costs.credits
    if player["credits"] < 0:
        raise ValueError("route projection exceeds funded wallet")
    player["location"] = route.facts.get("destination", player["location"])
    player["home_locked"] = not player["location"].startswith("home:")
    if route.t1_turns:
        player["moved"] = False
        player["locked_targets"] = []
        grant = projected["schedule"].get("grant")
        if grant:
            grant["locked_target"] = None
    objective = route.objective
    if objective.startswith("armor:"):
        _armor(player, route.item, config)
    elif objective.startswith("repair:"):
        defense = player["defense_details"][route.item]
        defense["durability"] = min(defense["max_durability"], defense["durability"] +
                                    config.get("economy", "repair_amount"))
        player["defenses"][route.item] = defense["durability"]
    elif objective.startswith("ammo:"):
        weapon = route.item
        supply = "bow" if weapon == "bow" else "gun_ammo"
        player["ammo"][weapon] = min(_ammo_cap(config, weapon), player["ammo"].get(weapon, 0) +
                                     config.get("weapons", supply, "refill_amount"))
    elif objective.startswith("weapon:"):
        player["weapons"] = sorted(set(player["weapons"]) | {route.item})
        if route.item in ("electromagnetic_rifle", "gauss_rifle"):
            player["ammo"][route.item] = config.get("weapons", route.item, "initial_ammo")
    elif objective == "heal":
        _heal(player, config.get("hospital", "heal_amount"))
        player["burns"] = [burn for burn in player["burns"] if burn["uncleansable"]]
        player["hospital_heals"] += 1
    elif objective == "surgery:life":
        player["surgery"] = "life"
        player["max_hp"] += config.get("hospital", "life_max_hp_bonus")
        player["hp"] += config.get("hospital", "life_current_hp_bonus")
    elif objective.startswith("module:"):
        kind = route.item
        bought = route.facts["buy_count"]
        if bought > projected["module_stock"][kind]:
            raise ValueError("route projection exceeds current module stock")
        projected["module_stock"][kind] -= bought
        key = f"module:{kind}"
        player["inventory"][key] = player["inventory"].get(key, 0) + bought
        attribute = "magic" if kind == "fire" else "tech" if kind == "pierce" else "normal"
        _modules(player, tuple(route.facts["modules"]), attribute)
    elif objective == "captain":
        police = projected["police"]
        police["captain"] = projected["actor_id"]
        police["applicants"] = []
        projected["projection"]["assumptions"].append("assumes_application_wins_at_next_R2")
        for key in police["officer_ids"]:
            projected["units"][key]["owner_id"] = projected["actor_id"]
            projected["units"][key]["location"] = player["location"]
    elif objective.startswith("report:"):
        projected["police"]["wanted"] = list(dict.fromkeys([
            *projected["police"]["wanted"], route.target_id]))
    elif objective.startswith("loot:"):
        _loot(projected, player, config)
    elif objective.startswith("counter:"):
        kind = route.item
        player["inventory"][kind] = player["inventory"].get(kind, 0) + route.facts["buy_count"]
        projected["projection"]["assumptions"].append(
            "counter_route_arrives_with_item_before_throw_no_damage_or_ammo_consumed")
        projected["projection"]["pre_throw"] = True
        projected["projection"]["remaining_throws"] = route.facts["remaining_throws"]
    else:
        raise NotImplementedError(f"unsupported route projection: {objective}")
    return projected


def project_preparation(
    view: Mapping[str, Any], request: ActionRequest, config: GaConfig,
) -> dict[str, Any]:
    """投影已给定合法准备动作的基础效果；未知操作明确拒绝。"""
    projected = _copy(view, "assumes_selected_action_executes_without_interruption")
    if request.actor_id != projected["actor_id"]:
        raise ValueError("preparation actor differs from observer")
    player = projected["players"][request.actor_id]
    operation, params = request.operation, request.parameters
    if operation == "wake":
        player["awake"] = True
        player["weapons"] = sorted(set(player["weapons"]) | {"bow"})
        player["ammo"]["bow"] = config.get("start", "arrows")
        if params["choice"] == "knife":
            player["weapons"] = sorted(set(player["weapons"]) | {"knife"})
        else:
            _armor(player, "shield", config)
    elif operation == "lock":
        target = request.target_ids[0]
        player["locked_targets"] = sorted(set(player["locked_targets"]) | {target})
        grant = projected["schedule"].get("grant")
        if grant is None:
            raise ValueError("lock projection requires current grant")
        grant["locked_target"] = target
    elif operation == "modules":
        _modules(player, tuple(params["modules"]), params["attribute"])
    elif operation in ("syringe", "sharpen"):
        item = "syringe" if operation == "syringe" else "whetstone"
        if player["inventory"].get(item, 0) < 1:
            raise ValueError("preparation projection cannot spend missing item")
        player["inventory"][item] -= 1
        if operation == "syringe":
            _heal(player, config.get("hospital", "syringe_heal"))
        else:
            player["sharpened"] = True
    elif operation == "move":
        player["location"] = params["location"]
        player["home_locked"], player["moved"] = True, True
    elif operation == "charge":
        player["charged"] = True
    elif operation == "hide":
        player["hidden"], player["revealed_to"] = True, []
    elif operation == "police_stance":
        police = projected["police"]
        police["stance"] = params["stance"]
        player["defenses"].pop("police/wall", None)
        player["defense_details"].pop("police/wall", None)
        if params["stance"] == "wall" and police["wall"]:
            police["immobile_round"] = projected["round"]
            live = sum(projected["units"][key]["alive"] for key in police["officer_ids"])
            capacity = live * (config.get("police", "wall_per_officer") +
                              police["wall_upgrade"] * config.get(
                                  "police", "wall_upgrade_per_officer"))
            defense = DefenseSpec("police/wall", DefenseKind.FINITE_BARRIER,
                                  min(capacity, police["wall"]), max(1, capacity),
                                  frozenset({DamageKind.NORMAL, DamageKind.DIRECT,
                                             DamageKind.PURE_WEAR}),
                                  frozenset({TargetScope.LOCAL_SINGLE, TargetScope.RANGED_SINGLE}))
            player["defenses"]["police/wall"] = defense.durability
            player["defense_details"]["police/wall"] = public_value(defense)
    elif operation != "forfeit":
        raise NotImplementedError(f"unsupported preparation projection: {operation}")
    return projected
