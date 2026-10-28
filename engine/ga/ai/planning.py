"""无天赋语义路线、真实资源预算与回执驱动的跨轮目标。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from math import ceil
from typing import Any

from engine.ga.config import GaConfig
from engine.ga.contracts import ActionRequest, TurnStage, freeze_json, thaw_json
from engine.ga.decisions import GoalIntent
from engine.ga.gameplay import ARMOR_SHOPS, MODULE_SHOPS


@dataclass(frozen=True)
class Step:
    """只保存语义，不把已过期的grant/root带到下一行动槽。"""

    operation: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    target_ids: tuple[str, ...] = ()
    stage: TurnStage = TurnStage.T1

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", freeze_json(self.parameters))

    def matches(self, request: ActionRequest) -> bool:
        """精确匹配当前菜单里的语义请求，不生成猜测命令。"""
        return (request.operation == self.operation and request.stage == self.stage and
                request.target_ids == self.target_ids and
                thaw_json(request.parameters) == thaw_json(self.parameters))


@dataclass(frozen=True)
class Budget:
    """分别保留资源量纲；信用点、PP与生命不能互相冒充。"""

    credits: int = 0
    sp: int = 0
    pp: int = 0
    hp: int = 0
    ammo: Mapping[str, int] = field(default_factory=dict)
    items: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        values = (self.credits, self.sp, self.pp, self.hp,
                  *self.ammo.values(), *self.items.values())
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("budget requires nonnegative integers")
        object.__setattr__(self, "ammo", freeze_json(self.ammo))
        object.__setattr__(self, "items", freeze_json(self.items))

    def affordable(self, player: Mapping[str, Any]) -> bool:
        """生命支付须留下1点，其他账户不能透支。"""
        return all(player[key] >= getattr(self, key) for key in ("credits", "sp", "pp")) and (
            player["hp"] > self.hp and
            all(player["ammo"].get(key, 0) >= value for key, value in self.ammo.items()) and
            all(player["inventory"].get(key, 0) >= value for key, value in self.items.items()))


@dataclass(frozen=True)
class Route:
    """完整剩余路线；成本、耗时与目的供仲裁比较，本层不自行决定收益。"""

    goal_id: str
    family: str
    steps: tuple[Step, ...]
    objective: str
    costs: Budget = field(default_factory=Budget)
    reserves: Budget = field(default_factory=Budget)
    t1_turns: int = 0
    target_id: str | None = None
    item: str | None = None
    facts: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", freeze_json(self.facts))

    def first_action(self, actions: tuple[ActionRequest, ...]) -> ActionRequest | None:
        """仅返回首步对应的真实候选；阻塞时不会跳过购买或筹款前置。"""
        return next((action for action in actions if self.steps and
                     self.steps[0].matches(action)), None)


def _funded_route(
    view: Mapping[str, Any], config: GaConfig, *, objective: str, family: str,
    destination: str, price: int, finish: tuple[Step, ...], item: str | None = None,
    target_id: str | None = None, facts: Mapping[str, Any] | None = None,
    all_wallet: bool = False,
) -> Route | None:
    """筹款只能去商店；计算完整移动/打工/消费链，不凭空增加钱包。"""
    actor = view["players"][view["actor_id"]]
    destroyed = view["world"]["destroyed_locations"]
    if destination in destroyed:
        return None
    income = config.get("economy", "work_income")
    shortage = max(0, price - actor["credits"])
    work_count = ceil(shortage / income) if shortage else 0
    if work_count and "shop" in destroyed:
        return None
    steps: list[Step] = []
    location = actor["location"]
    if work_count:
        if location != "shop":
            steps.append(Step("move", {"location": "shop"}))
        steps.extend(Step("work") for _ in range(work_count))
        location = "shop"
    if location != destination:
        steps.append(Step("move", {"location": destination}))
    steps.extend(finish)
    actual_price = actor["credits"] + work_count * income if all_wallet else price
    t1_turns = sum(step.stage is TurnStage.T1 for step in steps)
    return Route(objective, family, tuple(steps), objective, Budget(credits=actual_price),
                 Budget(credits=min(actor["credits"], actual_price)), t1_turns, target_id, item,
                 {"destination": destination, "credit_income": work_count * income,
                  "work_turns": work_count, "wallet_after": actor["credits"] +
                  work_count * income - actual_price, **(facts or {})})


def propose_routes(view: Mapping[str, Any], config: GaConfig) -> tuple[Route, ...]:
    """从公开事实构造有终点的筹款、装备、补给和警务路线；不排序强迫采购。"""
    actor_id = view["actor_id"]
    player = view["players"][actor_id]
    if not player["alive"] or not player["awake"] or view.get("terminal") is not None:
        return ()
    result: list[Route] = []

    def add(**kwargs: Any) -> None:
        route = _funded_route(view, config, **kwargs)
        if route is not None:
            result.append(route)

    for weapon in ("bow", "electromagnetic_rifle", "gauss_rifle"):
        if weapon not in player["weapons"] or weapon == "bow" and "infinite" in player["modules"]:
            continue
        cap = config.get("start", "arrow_cap") if weapon == "bow" else config.get(
            "weapons", weapon, "ammo_cap")
        current = player["ammo"].get(weapon, 0)
        if current >= cap:
            continue
        supply = "bow" if weapon == "bow" else "gun_ammo"
        add(objective=f"ammo:{weapon}", family="supply", destination=(
            "shop" if weapon == "bow" else "military"), item=weapon,
            price=config.get("weapons", supply, "refill_price"),
            finish=(Step("refill", {"weapon": weapon}),), facts={
                "expected_gain": min(cap - current, config.get("weapons", supply, "refill_amount")),
                "baseline_ammo": current})
    if player["hp"] < player["max_hp"] or player["burns"]:
        if player["hospital_heals"] < config.get("hospital", "heal_uses_per_game"):
            add(objective="heal", family="recover", destination="hospital",
                price=config.get("hospital", "heal_price"), finish=(Step("heal"),), facts={
                    "expected_gain": min(player["max_hp"] - player["hp"],
                                         config.get("hospital", "heal_amount")),
                    "baseline_heals": player["hospital_heals"], "cleanses_burns": bool(
                        player["burns"])})
    if player["surgery"] is None:
        add(objective="surgery:life", family="develop", destination="hospital", item="life",
            price=config.get("hospital", "surgery_min_price"),
            finish=(Step("surgery", {"kind": "life"}),), all_wallet=True, facts={
                "expected_gain": config.get("hospital", "life_current_hp_bonus"),
                "max_hp_gain": config.get("hospital", "life_max_hp_bonus")})
    for kind, location in ARMOR_SHOPS.items():
        if kind not in player["armor_kinds"].values():
            add(objective=f"armor:{kind}", family="develop", destination=location, item=kind,
                price=config.get("armor", kind, "price"),
                finish=(Step("buy_armor", {"kind": kind}),), facts={
                    "expected_gain": config.get("armor", kind, "durability")})
    for key, defense in player["defense_details"].items():
        if defense["kind"] == "armor" and 0 < defense["durability"] < defense["max_durability"]:
            add(objective=f"repair:{key}", family="recover", destination="shop", item=key,
                price=config.get("economy", "repair_price"),
                finish=(Step("repair", {"defense_id": key}),), facts={
                    "baseline_durability": defense["durability"], "expected_gain": min(
                        defense["max_durability"] - defense["durability"],
                        config.get("economy", "repair_amount"))})
    if "bow" in player["weapons"]:
        for kind, location in MODULE_SHOPS.items():
            counts = (1,) if kind == "infinite" else range(1, config.get("modules", "slots") + 1)
            for count in counts:
                modules = (kind,) * count
                if player["modules"].count(kind) >= count:
                    continue
                owned = player["inventory"].get(f"module:{kind}", 0) + player["modules"].count(kind)
                buy = max(0, count - owned)
                if buy > view["module_stock"][kind]:
                    continue
                attribute = "magic" if kind == "fire" else (
                    "tech" if kind == "pierce" else "normal")
                finish = tuple(Step("buy_module", {"kind": kind}) for _ in range(buy)) + (
                    Step("modules", {"modules": modules, "attribute": attribute},
                         stage=TurnStage.T0),)
                add(objective=f"module:{kind}:{count}", family="develop", item=kind,
                    destination=location if buy else player["location"],
                    price=buy * config.get("modules", kind, "price"), finish=finish,
                    facts={"modules": modules, "buy_count": buy, "expected_gain": count})
    for weapon, location in (("knife", "shop"), ("magic_barrage", "magic"),
                             ("earthquake", "magic"), ("gauss_rifle", "military")):
        if weapon not in player["weapons"]:
            add(objective=f"weapon:{weapon}", family="develop", destination=location, item=weapon,
                price=config.get("weapons", weapon, "price"),
                finish=(Step("buy_weapon", {"weapon": weapon}),), facts={
                    "expected_gain": config.get("weapons", weapon, "damage")})
    police = view.get("police")
    if police and not police["stopped"] and not view["world"]["clock_paused"] and any(
        view["units"][key]["alive"] and not view["units"][key]["submerged_sources"]
        for key in police["officer_ids"]
    ):
        if not police["captain"] and actor_id not in police["wanted"]:
            waiting = actor_id in police["applicants"]
            add(objective="captain", family="police", destination=(
                player["location"] if waiting else "police"), price=0,
                finish=() if waiting else (Step("police_apply"),), facts={"queued": waiting})
        reported = set()
        for case in view["crimes"]:
            offender = case["actor_id"]
            if offender not in reported and offender not in police["wanted"] and (
                offender != actor_id and view["players"][offender]["alive"] and
                (case.get("victim_id") == actor_id or actor_id in case.get("witness_ids", ()))
            ):
                reported.add(offender)
                add(objective=f"report:{offender}", family="police", destination="police",
                    price=0, target_id=offender,
                    finish=(Step("police_report", target_ids=(offender,)),))
    for location, piles in view.get("ground_loot", {}).items():
        if any(_loot_available(pile, player, config) for pile in piles):
            add(objective=f"loot:{location}", family="loot", destination=location, price=0,
                finish=(Step("loot"),), facts={"expected_gain": sum(
                    pile.get("credits", 0) for pile in piles)})
    result.extend(_counter_routes(view, config))
    return tuple(result)


def _counter_routes(view: Mapping[str, Any], config: GaConfig) -> tuple[Route, ...]:
    """仅为实际警务压力提供两条采购到投掷路线，不加入优先返回规则。"""
    actor = view["actor_id"]
    player = view["players"][actor]
    police = view.get("police")
    if not police or police["stopped"] or view["world"]["clock_paused"] or (
        "police" in view["world"]["destroyed_locations"]
    ):
        return ()
    live = [view["units"][key] for key in police["officer_ids"]
            if view["units"][key]["alive"] and not view["units"][key]["submerged_sources"]]
    if not live:
        return ()
    protected = police["captain"] or police["guard_id"]
    if protected == actor:
        return ()
    foreign = protected is not None and protected != actor and (
        view["players"][protected]["alive"])
    risk = actor in police["wanted"] or foreign
    if not risk:
        return ()
    location = live[0]["location"]
    targets = tuple(sorted([key for key, body in view["players"].items()
        if key != actor and body["alive"] and body["home_locked"] and
        body["location"] == location and body["isolation_id"] == player["isolation_id"]] +
        [key for key, body in view["units"].items() if body["alive"] and
         body["owner_id"] != actor and body["location"] == location and
         body["isolation_id"] == player["isolation_id"]]))
    if not targets:
        return ()
    routes = []
    for kind in ("fragment", "molotov"):
        damage = config.get("consumables", f"{kind}_damage")
        per_throw = config.get("consumables", "per_throw")
        capacity = config.get("consumables", "hold_cap") // per_throw
        if damage <= 0 or capacity == 0:
            continue
        needed = ceil(max(body["hp"] for body in live) / damage)
        owned = player["inventory"].get(kind, 0)
        throws = min(needed, capacity)
        if owned >= per_throw and (player["location"] == location or
                                   "military" in view["world"]["destroyed_locations"]):
            throws = min(throws, owned // per_throw)
        buy = max(0, throws * per_throw - owned)
        objective = f"counter:{kind}:{location}"
        supply = "military" if buy else player["location"]
        route = _funded_route(view, config, objective=objective, family="counter",
            destination=supply, price=buy * config.get("consumables", "price"), item=kind,
            finish=tuple(Step("buy_item", {"kind": kind}) for _ in range(buy)))
        if route is None or location in view["world"]["destroyed_locations"]:
            continue
        tail = (() if supply == location else (Step("move", {"location": location}),)) + tuple(
            Step("throw", {"kind": kind}, target_ids=targets) for _ in range(throws))
        steps = route.steps + tail
        routes.append(replace(route, steps=steps,
            costs=replace(route.costs, items={kind: throws * per_throw}),
            reserves=replace(route.reserves, items={kind: min(owned, throws * per_throw)}),
            t1_turns=sum(step.stage is TurnStage.T1 for step in steps), facts={
                **route.facts, "destination": location, "supply_location": "military",
                "buy_count": buy, "remaining_throws": throws, "counter_targets": targets,
                "capacity_limited": throws < needed, "pre_throw": True,
                "prospective_police_risk": actor not in police["wanted"]}))
    return tuple(routes)


def _loot_available(pile: Mapping[str, Any], player: Mapping[str, Any], config: GaConfig) -> bool:
    """已满容量且没有新实物的地面堆不值得规划一次空拾取。"""
    if pile.get("credits") or pile.get("defenses") or pile.get("modules") or (
        set(pile.get("weapons", ())) - set(player["weapons"])
    ):
        return True
    for name, amount in pile.get("ammo", {}).items():
        cap = config.get("start", "arrow_cap") if name == "bow" else config.get(
            "weapons", name, "ammo_cap")
        if amount and player["ammo"].get(name, 0) < cap:
            return True
    for name, amount in pile.get("inventory", {}).items():
        cap = config.get("hospital", "syringe_hold_cap") if name == "syringe" else (
            config.get("consumables", "hold_cap") if name in (
                "fragment", "shock", "flash", "smoke", "molotov") else amount +
            player["inventory"].get(name, 0))
        if amount and player["inventory"].get(name, 0) < cap:
            return True
    return False


def _completed(view: Mapping[str, Any], route: Route) -> bool:
    """完成依据当前事实，不能因为曾提出操作就宣布取得收益。"""
    player = view["players"][view["actor_id"]]
    objective = route.objective
    if objective.startswith("module:"):
        return tuple(player["modules"]) == tuple(route.facts["modules"])
    if objective.startswith("weapon:"):
        return route.item in player["weapons"]
    if objective.startswith("armor:"):
        return route.item in player["armor_kinds"].values()
    if objective.startswith("surgery:"):
        return player["surgery"] == route.item
    if objective.startswith("ammo:"):
        return player["ammo"].get(route.item, 0) > route.facts["baseline_ammo"]
    if objective == "heal":
        return player["hospital_heals"] > route.facts["baseline_heals"] or (
            player["hp"] == player["max_hp"] and not player["burns"])
    if objective.startswith("repair:"):
        defense = player["defense_details"].get(route.item)
        return bool(defense and defense["durability"] > route.facts["baseline_durability"])
    if objective == "captain":
        return view["police"]["captain"] == view["actor_id"]
    if objective.startswith("report:"):
        return route.target_id in view["police"]["wanted"]
    if objective.startswith("counter:"):
        police = view["police"]
        return bool(police["stopped"] or view["world"]["clock_paused"] or not any(
            view["units"][key]["alive"] for key in police["officer_ids"]))
    return False


class GoalStack:
    """单一活跃承诺加暂停意图；只有活跃目标预留资源，不允许重复占款。"""

    def __init__(self) -> None:
        self.active: Route | None = None
        self.paused: Route | None = None
        self._events: list[GoalIntent] = []
        self._receipts: set[str] = set()
        self.completed_steps = 0

    @property
    def reserved(self) -> Budget:
        """暂停目标不继续占用另一条路线已经需要的资源。"""
        return self.active.reserves if self.active else Budget()

    def _event(self, route: Route, status: str, reason: str) -> None:
        """只记录真实生命周期变化；不是每次查询都加一条进度。"""
        self._events.append(GoalIntent(route.goal_id, status, reason, {
            "objective": route.objective, "remaining_t1": route.t1_turns,
            "reserve_credits": route.reserves.credits}))

    def commit(self, route: Route) -> None:
        """仲裁选中后才承诺；同一目标重新报价不改变其身份或累计进度。"""
        if self.active and self.active.goal_id == route.goal_id:
            self.active = route
            return
        resumed = self.paused is not None and self.paused.goal_id == route.goal_id
        if resumed:
            self.paused = None
        if self.active:
            self.interrupt("better_route_selected")
        self.active = route
        self._event(route, "active", "replanned_and_resumed" if resumed else "route_selected")

    def interrupt(self, reason: str) -> None:
        """暂停只保留意图，不保留原价格或继续冻结预算。"""
        if self.active is None:
            return
        if self.paused and self.paused.goal_id != self.active.goal_id:
            self._event(self.paused, "abandoned", "replaced_suspended_route")
        self.paused = self.active
        self._event(self.active, "interrupted", reason)
        self.active = None

    def reconcile(
        self, view: Mapping[str, Any], routes: tuple[Route, ...], outcome: Any = None,
    ) -> None:
        """依实际回执/新观察核对进度，再替换剩余路线；重复观察没有进度副作用。"""
        available = {route.goal_id: route for route in routes}
        if outcome is not None:
            request = outcome.request if hasattr(outcome, "request") else outcome["request"]
            if isinstance(request, Mapping):
                request = ActionRequest.from_dict(request)
            cancelled = outcome.cancelled if hasattr(outcome, "cancelled") else outcome.get(
                "cancelled", False)
            key = str(request.to_dict())
            if key not in self._receipts:
                self._receipts.add(key)
                if self.active and self.active.steps and self.active.steps[0].matches(request):
                    if cancelled:
                        self.interrupt("execution_cancelled")
                    else:
                        self.completed_steps += 1
                        if len(self.active.steps) == 1 and self.active.objective != "captain":
                            self._event(self.active, "completed", "execution_confirmed")
                            self.active = None
        for attribute in ("active", "paused"):
            route = getattr(self, attribute)
            if route is None:
                continue
            if _completed(view, route):
                reason = ("counter_threat_gone" if route.family == "counter" else
                          "objective_observed")
                self._event(route, "completed", reason)
                setattr(self, attribute, None)
            elif not view["players"][view["actor_id"]]["alive"] or route.goal_id not in available:
                self._event(route, "abandoned", "route_no_longer_feasible")
                setattr(self, attribute, None)
            else:
                setattr(self, attribute, available[route.goal_id])

    def intents(self) -> tuple[GoalIntent, ...]:
        """诊断输出近期真实状态转换和当前承诺。"""
        events = self._events[-8:]
        if self.active:
            events = [*events, GoalIntent(self.active.goal_id, "active", "remaining_route", {
                "remaining_t1": self.active.t1_turns,
                "reserve_credits": self.reserved.credits})]
        return tuple(events)
