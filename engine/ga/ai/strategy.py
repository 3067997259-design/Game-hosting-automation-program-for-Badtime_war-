"""统一价值仲裁与有界响应情景；预测值不是胜率或训练Q标签。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
from typing import Any

from engine.ga.ai.forecast import (
    AttackEstimate, estimate_attack, estimate_option, estimate_police_response,
    estimate_police_volley,
)
from engine.ga.ai.minds import ScoreMind, Situation
from engine.ga.ai.planning import Route
from engine.ga.ai.police_mind import estimate_area_counter, estimate_counter
from engine.ga.ai.projection import project_preparation, project_route
from engine.ga.config import GaConfig
from engine.ga.contracts import ActionRequest, TurnStage, thaw_json


@dataclass
class SearchBudget:
    """一个节点是一次候选/路线或一次对手响应投影，局内人数始终有界。"""

    limit: int
    used: int = 0

    def take(self) -> bool:
        """耗尽后保留已完成估值，不因墙钟或机器负载选择不同动作。"""
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


@dataclass
class Proposal:
    """真实菜单动作绑定解释和可选长程目标。"""

    index: int
    value: float
    reason: str
    estimates: dict[str, Any] = field(default_factory=dict)
    route: Route | None = None


def best_attack(
    view: Mapping[str, Any], actor: str, config: GaConfig, *, locked: bool = True,
) -> tuple[str | None, str | None, AttackEstimate]:
    """对手也是各自争分，不能默认把全部敌人当作协同集火的一个玩家。"""
    best: tuple[str | None, str | None, AttackEstimate] = (None, None, AttackEstimate())
    best_value = -1.0
    if view["players"][actor]["statuses"].get("control", {}).get("name") in (
        "SHOCKED", "PETRIFIED", "ACTION_SUPPRESSED"
    ):
        return best
    for target, body in view["players"].items():
        if target == actor or not body["alive"]:
            continue
        for weapon in sorted(view["players"][actor]["weapons"]):
            if weapon not in config.get("weapons") or weapon == "gun_ammo":
                continue
            grant = view["schedule"].get("grant") or {}
            current_lock = grant.get("actor_id") == actor and grant.get("locked_target") == target
            quote = estimate_attack(view, actor, target, weapon, config, locked=(
                locked or current_lock))
            if not quote.available:
                continue
            progress = max(0.0, quote.expected_hp - body["hp"] * quote.kill_probability) / max(
                1, body["hp"])
            value = quote.score_gain + quote.deferred_hp / config.get(
                "scoring", "damage_divisor") + progress * config.get(
                    "scoring", "kill_weight")
            if value > best_value:
                best, best_value = (target, weapon, quote), value
    return best


def _score_refresh(body: dict[str, Any], config: GaConfig) -> None:
    """预测工作值按正式分项重算，不改变输入快照或真实钱包。"""
    score = body["score"]
    parts = {"kills": score["kills"] * config.get("scoring", "kill_weight"),
             "damage": score["hp_damage"] // config.get("scoring", "damage_divisor"),
             "armor_breaks": score["armor_breaks"] * config.get("scoring", "armor_break_weight"),
             "chapters": len(score["chapters"]) * config.get("scoring", "chapter_weight"),
             "pp": body["pp"] * config.get("scoring", "pp_weight"), "crown": 0}
    score["components"] = {**parts, "total": sum(parts.values())}


def project_hit(
    view: Mapping[str, Any], actor: str, target: str, quote: AttackEstimate, config: GaConfig,
) -> dict[str, Any]:
    """短程情景用向下取整的期望伤害投影；不宣称逐随机分支精确模拟。"""
    result = thaw_json(view)
    source = result["players"].get(actor)
    body = result["players"].get(target, result.get("units", {}).get(target))
    loss = min(body["hp"], math.floor(quote.expected_hp))
    if quote.kill_probability == 1:
        loss = body["hp"]
    body["hp"] -= loss
    body["engaged"] = True
    if source is not None:
        source["engaged"] = True
        if target in result["players"]:
            source["score"]["hp_damage"] += loss
    if body["hp"] <= 0:
        body["alive"] = False
        if source is not None and target in result["players"]:
            source["score"]["kills"] += 1
            source["pp"] += config.get("pp", "kill_gain")
    if source is not None:
        _score_refresh(source, config)
    return result


class Strategy:
    """所有提案使用相同计分单位；风格只影响接近方案的风险/投入时间偏好。"""

    def __init__(self, config: GaConfig, style: str = "balanced") -> None:
        if style not in ("balanced", "aggressive", "patient"):
            raise ValueError("unsupported GA BasicAI style")
        self.config = config
        self.style = style
        self._cache: dict[tuple[Any, ...], tuple[Mapping[str, Any], Any]] = {}

    def start_decision(self) -> None:
        """只复用同次不可变输入；不把旧轮预测或过程地址写入诊断。"""
        self._cache.clear()

    def pressure(self, view: Mapping[str, Any], actor: str) -> tuple[float, float]:
        """分别报告合理争分响应和单敌最危险攻击，不把集火上界当概率。"""
        key = ("pressure", id(view), actor)
        if key in self._cache:
            return self._cache[key][1]
        expected, maximum = 0.0, 0.0
        for enemy, player in view["players"].items():
            if enemy == actor or not player["alive"] or not player["awake"]:
                continue
            target, _, quote = best_attack(view, enemy, self.config)
            if target == actor:
                expected += quote.expected_hp
            for weapon in player["weapons"]:
                if weapon in self.config.get("weapons") and weapon != "gun_ammo":
                    attack = estimate_attack(view, enemy, actor, weapon, self.config, locked=True)
                    if attack.available:
                        maximum = max(maximum, attack.max_hp)
        target, _, volley = estimate_police_response(view, self.config)
        if target == actor:
            expected += volley.expected_hp
            maximum += volley.max_hp
        result = expected, maximum
        self._cache[key] = view, result
        return result

    def opportunity(
        self, view: Mapping[str, Any], actor: str, *, may_prepare: bool = True,
    ) -> float:
        """一个额外可用攻击槽的得分机会，含尚未完成的击杀进度。"""
        key = ("opportunity", id(view), actor, may_prepare)
        if key in self._cache:
            return self._cache[key][1]
        future = thaw_json(view)
        # 仅长程机会估值假设对手将完成强制开局；当前攻击仍用真实候选说明。
        if may_prepare:
            for player_id, body in future["players"].items():
                if player_id != actor and body["alive"] and not body["home_locked"]:
                    body["awake"], body["home_locked"] = True, True
                    body["location"] = "shop"
        target, _, quote = best_attack(future, actor, self.config, locked=may_prepare)
        if target is None:
            self._cache[key] = view, 0.0
            return 0.0
        target_hp = future["players"][target]["hp"]
        progress_hp = max(0.0, quote.expected_hp - target_hp * quote.kill_probability)
        value = quote.score_gain + quote.deferred_hp / self.config.get(
            "scoring", "damage_divisor") + progress_hp / max(1, target_hp) * self.config.get(
                "scoring", "kill_weight")
        self._cache[key] = view, value
        return value

    def attack_window(self, view: Mapping[str, Any], actor: str, slots: int) -> float:
        """有限静态靶场情景：消耗弹药和目标生命，末击收益不能重复兑现。"""
        key = ("attack_window", id(view), actor, slots)
        if key in self._cache:
            return self._cache[key][1]
        projected = thaw_json(view)
        for player_id, body in projected["players"].items():
            if player_id != actor and body["alive"] and not body["home_locked"]:
                body["awake"], body["home_locked"] = True, True
                body["location"] = "shop"
        value = 0.0
        for _ in range(slots):
            target, weapon, quote = best_attack(projected, actor, self.config)
            if target is None:
                break
            value += quote.score_gain
            # 灼烧仍只是未兑现机会；不把同一目标无限次挂火加到总池上。
            value += min(quote.deferred_hp, projected["players"][target]["hp"] -
                         quote.expected_hp) / self.config.get("scoring", "damage_divisor")
            projected = project_hit(projected, actor, target, quote, self.config)
            player = projected["players"][actor]
            if weapon in ("bow", "electromagnetic_rifle", "gauss_rifle") and not (
                weapon == "bow" and "infinite" in player["modules"]
            ):
                player["ammo"][weapon] -= self.config.get("weapons", weapon, "ammo_per_attack")
            player["charged"] = False
        self._cache[key] = view, value
        return value

    def counter_return(
        self, view: Mapping[str, Any], projected: Mapping[str, Any], horizon: int,
    ) -> tuple[float, dict[str, Any]]:
        """警队控制只按将来减少的实际齐射估值，NPC本身没有玩家战果分。"""
        actor, cfg = view["actor_id"], self.config
        original, after = thaw_json(view), thaw_json(projected)
        police = original.get("police")
        if not police:
            return 0.0, {}
        prospective = actor not in police["wanted"] and police["captain"] not in (None, actor)
        if prospective:
            original["police"]["wanted"].append(actor)
            after["police"]["wanted"].append(actor)
        original["police"]["attack_round"] = original["round"] - 1
        after["police"]["attack_round"] = after["round"] - 1
        before_quote = estimate_police_volley(original, actor, cfg)
        after_quote = estimate_police_volley(after, actor, cfg)
        saved = (before_quote.expected_hp if before_quote.available else 0.0) - (
            after_quote.expected_hp if after_quote.available else 0.0)
        pressure, _ = self.pressure(view, actor)
        rate = self.opportunity(view, actor)
        prevented = max(0, horizon) * saved
        value = prevented / max(cfg.get("weapons", "bow", "damage"), pressure) * rate
        return value, {"police_hp_saved_per_renewed_volley": saved,
                       "conditional_future_report": prospective,
                       "police_effective_horizon": horizon,
                       "estimated_police_control_value": value}

    def immediate(
        self, view: Mapping[str, Any], action: ActionRequest, descriptor: Mapping[str, Any],
        situation: Situation, index: int,
    ) -> Proposal:
        """即时战术均产生提案，不在治疗/战斗等阶段提前返回。"""
        actor, cfg = situation.actor_id, self.config
        player = view["players"][actor]
        op = action.operation
        estimate: dict[str, Any] = {"scope": "short_horizon_estimate"}
        value, reason = -float(situation.remaining_slots), "no_independent_gain"
        if descriptor.get("declared_effects"):
            quote = estimate_option(view, descriptor, cfg)
            if quote.available:
                scoring_quote = quote
                if any(target in view["units"] for target in action.target_ids):
                    scoring_effects = [{**effect, "target_ids": [target for target in
                                        effect["target_ids"] if target in view["players"]]}
                                       for effect in descriptor["declared_effects"]]
                    scoring_effects = [effect for effect in scoring_effects if effect["target_ids"]]
                    scoring_quote = estimate_option(view, {**descriptor,
                        "declared_effects": scoring_effects}, cfg) if scoring_effects else (
                            AttackEstimate())
                target_hp = sum(view["players"][target]["hp"] for target in action.target_ids
                                if target in view["players"])
                progress = max(0.0, scoring_quote.expected_hp - target_hp *
                               scoring_quote.kill_probability) / max(1, target_hp) if target_hp else 0.0
                value = quote.score_gain + (scoring_quote.deferred_hp / cfg.get(
                    "scoring", "damage_divisor") if target_hp else 0.0) + min(
                        1, progress) * cfg.get("scoring", "kill_weight")
                reason = "attack_score_and_kill_progress"
                estimate.update(immediate_score=quote.score_gain, expected_hp=quote.expected_hp,
                                deferred_hp=quote.deferred_hp,
                                kill_probability=quote.kill_probability,
                                prediction_notes=list(quote.notes))
                if action.target_ids and all(t in view["units"] for t in action.target_ids):
                    projected = view
                    for target in action.target_ids:
                        projected = project_hit(projected, actor, target, quote, cfg)
                    before, _ = self.pressure(view, actor)
                    after, _ = self.pressure(projected, actor)
                    value += (before - after) / max(1, player["hp"]) * cfg.get(
                        "scoring", "kill_weight")
                    reason = "remove_npc_threat_not_player_kill_score"
                if len(action.target_ids) == 1 and quote.kill_probability > 0:
                    margin = ScoreMind.terminal_margin(view, actor, action.target_ids[0],
                                                       quote.score_gain, cfg, breaks=math.floor(
                                                           quote.expected_breaks))
                    if margin is not None:
                        # 确定收局赢/输压过非终局的一点短期收益。
                        value += quote.kill_probability * (cfg.get("scoring", "kill_weight") +
                                  cfg.get("scoring", "crown")) * (1 if margin >= 0 else -1)
                        estimate["terminal_margin"] = margin
        elif op in ("syringe", "modules", "lock", "sharpen", "wake", "move", "charge", "hide"):
            projected = project_preparation(view, action, cfg)
            before = self.opportunity(view, actor, may_prepare=False)
            after = self.opportunity(projected, actor, may_prepare=False)
            value, reason = after - before, "prepare_next_attack"
            if op == "wake":
                # 自带弓仍有弹药时，盾保留更多有效行动机会。
                value += cfg.get("armor", "shield", "durability") / cfg.get(
                    "weapons", "bow", "damage") if action.parameters["choice"] == "shield" else 0
            if op == "syringe":
                hp_gain = projected["players"][actor]["hp"] - player["hp"]
                pressure, _ = self.pressure(view, actor)
                value += hp_gain / max(cfg.get("weapons", "fist", "damage"), pressure) * before
            if op == "move":
                old_pressure, _ = self.pressure(view, actor)
                new_pressure, _ = self.pressure(projected, actor)
                value += (old_pressure - new_pressure) / max(1, player["hp"]) * cfg.get(
                    "scoring", "kill_weight")
                value -= before  # 整个T1的输出机会确实被移动花掉。
            estimate.update(next_attack_before=before, next_attack_after=after)
        elif op == "police_command":
            targets = action.target_ids
            police = view.get("police") or {}
            if targets and targets[0] in police.get("wanted", ()):
                volley = estimate_police_volley(view, targets[0], cfg)
                value = volley.score_gain
                reason = "wanted_target_volley"
        elif op == "police_stance":
            projected = project_preparation(view, action, cfg)
            before, _ = self.pressure(view, actor)
            after, _ = self.pressure(projected, actor)
            value = max(0, before - after) / max(1, player["hp"]) * cfg.get(
                "scoring", "kill_weight")
            reason = "police_posture_reduces_next_attack"
        elif op == "forfeit":
            value, reason = 0.0, "wait_has_no_immediate_reward"
            if situation.score_lead < 0:
                value -= abs(situation.score_lead) / max(1, situation.remaining_slots)
        counter = estimate_counter(view, action, cfg)
        if counter.available:
            control, details = self.counter_return(
                view, counter.projected, min(3, situation.remaining_slots))
            value = max(value, counter.direct_damage.score_gain) + control
            estimate.update(details, counter_notes=list(counter.notes))
            if control > 0:
                reason = "score_and_real_police_pressure_reduction"
        return Proposal(index, value, reason, estimate)

    def route(
        self, view: Mapping[str, Any], route: Route, situation: Situation, index: int,
    ) -> Proposal:
        """路线按剩余成本重新报价；完整投资回报须覆盖输出机会成本和途中风险。"""
        actor, cfg = situation.actor_id, self.config
        player = view["players"][actor]
        steps = route.t1_turns
        estimate: dict[str, Any] = {"route": route.objective, "remaining_t1": steps,
                                    "credits_cost": route.costs.credits,
                                    "reserved_credits": route.reserves.credits,
                                    "scope": "conditional_route_projection"}
        if steps >= situation.remaining_slots:
            return Proposal(index, -float(steps), "investment_after_deadline", estimate, route)
        projected = project_route(view, route, cfg)
        if route.family == "counter":
            before_counter = projected
            direct = 0.0
            executed = 0
            for _ in range(route.facts["remaining_throws"]):
                counter = estimate_area_counter(projected, actor, route.item, cfg)
                if not counter.available:
                    break
                direct += counter.direct_damage.score_gain
                projected = counter.projected
                executed += 1
            horizon = min(3, situation.remaining_slots - steps)
            control, details = self.counter_return(before_counter, projected, horizon)
            rate = self.opportunity(view, actor)
            pressure, _ = self.pressure(view, actor)
            exposure = min(1.0, steps * pressure / max(1, player["hp"]))
            value = rate + (direct + control - steps * rate) / max(1, steps)
            value -= exposure * (cfg.get("scoring", "kill_weight") + cfg.get("pp", "kill_gain"))
            estimate.update(details, estimated_direct_score=direct,
                            projected_throws=executed, route_risk=exposure,
                            opportunity_cost=steps * rate)
            return Proposal(index, value, "funded_counter_route_with_exposure", estimate, route)
        baseline = view
        forced_move = int(not player["home_locked"])
        if forced_move:
            baseline = thaw_json(view)
            baseline["players"][actor]["location"] = route.facts["destination"]
            baseline["players"][actor]["home_locked"] = True
        prior = self.opportunity(baseline, actor)
        future = self.opportunity(projected, actor)
        pressure, worst = self.pressure(view, actor)
        hp = player["hp"]
        # 只预测少数有效输出槽，避免给长程积累无限乐观的未来回报。
        window = min(4, situation.remaining_slots - steps)
        gain = max(0.0, self.attack_window(projected, actor, window) -
                   self.attack_window(baseline, actor, window))
        p = projected["players"][actor]
        new_pressure, _ = self.pressure(projected, actor)
        hp_gain = p["hp"] - hp
        hp_saved = max(0, pressure - new_pressure) * window
        useful_hp = max(0.0, hp_gain + hp_saved)
        extra_slots = useful_hp / max(cfg.get("weapons", "bow", "damage"), pressure,
                                     situation.environment_next)
        gain += min(window, extra_slots) * max(prior, future)
        if route.objective == "captain":
            police = view["police"]
            count = sum(view["units"][key]["alive"] for key in police["officer_ids"])
            # 有待执法目标才计输出；满编盾墙本身按减少受击的窗口计保命价值。
            wanted = len([key for key in police["wanted"] if view["players"][key]["alive"]])
            gain += min(window, wanted) * cfg.get("scoring", "kill_weight")
            gain += min(window, count * cfg.get("police", "wall_per_officer") / max(
                cfg.get("weapons", "bow", "damage"), pressure)) * max(prior, 1.0)
            gain /= max(1, len(police["applicants"]) + 1)
        if route.objective.startswith("report:"):
            police = view["police"]
            if police["captain"] == actor:
                gain += cfg.get("scoring", "kill_weight")
            elif route.target_id in situation.enemies:
                gain += max(0, pressure) / cfg.get("scoring", "damage_divisor")
        if route.family == "loot":
            # 现金只按替代未来打工的行动价值估算，不直接变成终分。
            income = max(0, p["credits"] - player["credits"])
            gain += min(2, income / cfg.get("economy", "work_income")) * max(prior, future)
        opportunity_cost = max(0, steps - forced_move) * prior
        exposed_steps = max(0, steps - int(route.family == "recover" and steps == 1))
        projected_loss = exposed_steps * (pressure + situation.environment_next)
        risk = min(1.0, projected_loss / max(1, hp))
        if steps > 1 and worst >= hp:
            risk = 1.0
        value = prior + (gain - opportunity_cost) / max(1, steps)
        value -= risk * (cfg.get("scoring", "kill_weight") + cfg.get("pp", "kill_gain"))
        if route.family == "recover" and steps == 1:
            value += min(1.0, pressure / max(1, hp)) * cfg.get("scoring", "kill_weight")
        if self.style == "patient" and steps > 1:
            value += gain / max(1, window) / 8
        if self.style == "aggressive":
            value -= steps / max(1, window)
        estimate.update(estimated_gain=gain, opportunity_cost=opportunity_cost,
                        route_risk=risk, best_attack_after=future)
        return Proposal(index, value, "route_return_after_cost_and_risk", estimate, route)

    def response_scenario(
        self, view: Mapping[str, Any], action: ActionRequest, descriptor: Mapping[str, Any],
        proposal: Proposal, mode: str,
    ) -> float:
        """展开当前动作后的独立争分/保命响应；每次仅计算到下一己方槽。"""
        actor, cfg = view["actor_id"], self.config
        projected = thaw_json(view)
        counter = estimate_counter(view, action, cfg)
        if counter.available:
            projected = counter.projected
        elif action.operation in ("attack", "npc_attack") and len(action.target_ids) == 1:
            quote = estimate_option(view, descriptor, cfg)
            projected = project_hit(projected, actor, action.target_ids[0], quote, cfg)
        elif action.operation == "police_command" and action.target_ids:
            target = action.target_ids[0]
            quote = estimate_police_volley(projected, target, cfg)
            if quote.available:
                projected = project_hit(projected, actor, target, quote, cfg)
                projected["police"]["attack_round"] = projected["round"]
        elif action.operation in ("move", "charge", "hide"):
            projected = project_preparation(view, action, cfg)
        elif proposal.route and proposal.route.t1_turns <= 1:
            projected = project_route(view, proposal.route, cfg)
        initial_hp = projected["players"][actor]["hp"]
        alive = sum(p["alive"] for p in projected["players"].values())
        if alive <= 1:
            return 0.0
        order = list(view["schedule"]["round_order"])
        if actor in order:
            pivot = order.index(actor)
            order = order[pivot + 1:] + order[:pivot]
        else:
            order = sorted(view["players"])
        for enemy in order:
            if enemy == actor or not projected["players"][enemy]["alive"]:
                continue
            player = projected["players"][enemy]
            if mode == "recover" and player["location"] == "hospital" and (
                player["hp"] < player["max_hp"] and player["credits"] >= cfg.get(
                    "hospital", "heal_price") and player["hospital_heals"] < cfg.get(
                    "hospital", "heal_uses_per_game")
            ):
                player["hp"] = min(player["max_hp"], player["hp"] + cfg.get(
                    "hospital", "heal_amount"))
                continue
            target, _, quote = best_attack(projected, enemy, cfg)
            if target is not None:
                projected = project_hit(projected, enemy, target, quote, cfg)
            if not projected["players"][actor]["alive"]:
                break
        target, owner, volley = estimate_police_response(projected, cfg)
        if target is not None and projected["players"][actor]["alive"]:
            projected = project_hit(projected, owner or "npc/police", target, volley, cfg)
            projected["police"]["attack_round"] = projected["round"]
        player = projected["players"][actor]
        loss = initial_hp - player["hp"]
        own_rate = self.opportunity(view, actor)
        penalty = loss / max(1, initial_hp) * own_rate
        if not player["alive"]:
            penalty += own_rate + cfg.get("scoring", "kill_weight")
        return -penalty
