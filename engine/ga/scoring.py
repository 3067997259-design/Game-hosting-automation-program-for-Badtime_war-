"""GA正式计分与控制器公开进度共用的纯计算。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.ga.config import GaConfig
    from engine.ga.state import GaPlayer


def score_components(player: GaPlayer, config: GaConfig, *, crown: int = 0) -> dict[str, int]:
    """按当前真实钱包计算，不提前兑现存活王冠，也不预测未来得分。"""
    parts = {
        "kills": player.kills * config.get("scoring", "kill_weight"),
        "damage": player.hp_damage // config.get("scoring", "damage_divisor"),
        "armor_breaks": player.armor_breaks * config.get("scoring", "armor_break_weight"),
        "chapters": len(player.chapters) * config.get("scoring", "chapter_weight"),
        "pp": player.pp * config.get("scoring", "pp_weight"), "crown": crown,
    }
    return {**parts, "total": sum(parts.values())}


def crown_eligible(player: GaPlayer, config: GaConfig) -> bool:
    """只判断战果资格；实际王冠仍须在终局证明唯一生还。"""
    return (player.kills >= config.get("scoring", "crown_min_kills") or
            len(player.chapters) >= config.get("scoring", "crown_min_chapters"))
