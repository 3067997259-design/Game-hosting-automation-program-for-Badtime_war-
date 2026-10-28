"""无记忆的局势分析；只读公开观察，不持有GameState或控制器。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from engine.ga.config import GaConfig


@dataclass(frozen=True)
class Situation:
    """真实计分与时间窗口，生存不是独立于争胜的第二套目标。"""

    actor_id: str
    enemies: tuple[str, ...]
    score_lead: float
    remaining_slots: int
    environment_next: int


class ScoreMind:
    """比较所有参与者，普通死者仍可能是最高分竞争者。"""

    @staticmethod
    def assess(view: Mapping[str, Any], config: GaConfig) -> Situation:
        """时钟窗口按末日递增伤害预算，而不是假定还有40个免费行动。"""
        actor = view["actor_id"]
        player = view["players"][actor]
        scores = {key: p["score"]["components"]["total"] for key, p in view["players"].items()}
        lead = scores[actor] - max(value for key, value in scores.items() if key != actor)
        tick = view["world_tick"]
        apocalypse = config.get("clock", "apocalypse_start")
        hp, slots = player["hp"], 0
        cap = config.get("clock", "max_rounds") - view["round"] + 1
        while slots < cap and hp > 0:
            hp -= max(0, tick + slots - apocalypse + 1) * config.get(
                "clock", "apocalypse_damage_per_tick")
            slots += 1
        environment = max(0, tick - apocalypse + 1) * config.get(
            "clock", "apocalypse_damage_per_tick")
        return Situation(actor, tuple(key for key, p in view["players"].items()
                                      if key != actor and p["alive"]), lead,
                         max(1, slots), environment)

    @staticmethod
    def terminal_margin(
        view: Mapping[str, Any], actor: str, killed: str, gain: float, config: GaConfig,
        *, breaks: int = 0,
    ) -> float | None:
        """条件于本击致死，依次分破甲/击杀/王冠池；gain含本击实际战果及PP。"""
        if type(breaks) is not int or breaks < 0:
            raise ValueError("terminal breaks must be a nonnegative integer")
        if killed not in view["players"] or not view["players"][killed]["alive"] or actor == killed:
            return None
        alive = [key for key, p in view["players"].items() if p["alive"] and key != killed]
        if len(alive) != 1:
            return None
        scores = {key: float(p["score"]["components"]["total"])
                  for key, p in view["players"].items()}
        scores[actor] += gain
        survivor = alive[0]
        p = view["players"][survivor]
        kills = p["score"]["kills"] + int(survivor == actor)
        crown = kills >= config.get("scoring", "crown_min_kills") or len(
            p["score"]["chapters"]
        ) >= config.get("scoring", "crown_min_chapters")
        afterlife = view.get("afterlife") or {}
        cursor = afterlife.get("remainder_cursor", 0)

        def distribute(beneficiary: str, pool: int) -> None:
            """与正式固定总池一致：无押中者不动游标；新死者没有押注不会凭空受益。"""
            nonlocal cursor
            ghosts = sorted(key for key, target in afterlife.get("bets", {}).items()
                            if target == beneficiary and
                            (not view["players"][key]["alive"] or key == killed) and
                            not view["players"][key]["absolute_dead"] and
                            not view["players"][key]["exited"])
            if ghosts:
                share, remainder = divmod(pool, len(ghosts))
                for ghost in ghosts:
                    scores[ghost] += share * config.get("scoring", "pp_weight")
                for index in range(remainder):
                    scores[ghosts[(cursor + index) % len(ghosts)]] += config.get(
                        "scoring", "pp_weight")
                cursor = (cursor + remainder) % len(ghosts)

        # 正式事件在同目标内先发布破甲，再发布死亡；终局钩子最后分王冠池。
        used_breaks = {tuple(item) for item in afterlife.get("break_rounds", ())}
        if breaks and (actor, view["round"]) not in used_breaks:
            distribute(actor, config.get("afterlife", "bet_break_pool"))
        distribute(actor, config.get("afterlife", "bet_kill_pool"))
        if crown:
            scores[survivor] += config.get("scoring", "crown")
            distribute(survivor, config.get("afterlife", "bet_crown_pool"))
        return scores[actor] - max(value for key, value in scores.items() if key != actor)
