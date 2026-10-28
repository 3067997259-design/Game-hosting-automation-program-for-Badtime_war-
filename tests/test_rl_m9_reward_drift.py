"""② 防漂移回归：M9 reward 事件表必须与引擎日志事件对齐。

三类断言：
  (a) reward 表不得存在"挂空键"（引用了引擎不产生的事件）；
  (b) 核心演出/天赋事件必须已登记（引擎改名/删除 → 红）；
  (c) 引擎产生的事件若非：已被映射、由 legacy 层处理、显式登记、或属天赋细粒度前缀，
      则必须红（防"引擎新增机制我们没给奖励"）。
"""

from __future__ import annotations

import re
from pathlib import Path

from rl.reward_m9 import LEGACY_M9_EVENT_TYPES, M9_EVENT_REWARD

ROOT = Path(__file__).resolve().parents[1]

ENGINE_M9_EVENTS: set[str] = set()
for f in (ROOT / "engine" / "m9").rglob("*.py"):
    ENGINE_M9_EVENTS |= set(
        re.findall(r'log_event\(\s*"([^"]+)"', f.read_text(encoding="utf-8"))
    )

# legacy rl/reward.event_reward 已处理的通用/旧天赋事件（无需在 M9 表重复）
LEGACY_HANDLED: set[str] = {
    "attack", "death",
    "firefly_supernova", "scissor_rush_shield_recovery", "scissor_rush_vigilance",
}

# 天赋细粒度记账前缀：内部书库事件，M9 表只登记影响策略的精选项
TALENT_PREFIX_IGNORED: tuple[str, ...] = (
    "g0_", "g1_", "g2_", "g3_", "g4_", "g5_", "g6_", "g7_",
    "t1_", "t2_", "t3_", "t4_", "t6_", "t7_", "poem_", "love_wish_",
)

# 顶层/生命周期事件：显式登记为"暂不映射"（说明理由）
KNOWN_M9_UNMAPPED_EVENTS: dict[str, str] = {
    "CYRENE_HOMECOMING":          "G5 德谬歌返家（生命周期，无单体即时策略收益）",
    "G5_DUSK_FORCED_DEMIURGE":    "G5 黄昏强制诞生德谬歌（生命周期）",
    "MATERIALIZED_NATURALLY":     "G5 锚定自然实现（与 MATERIALIZED_BY_ANCHOR 同类）",
    "location_destroy_failed":    "地点摧毁失败（无正向/负向信号价值）",
    "m9_police_attacked":         "警察被攻击（非玩家本人得失的直接信号）",
}

# 核心演出/天赋事件——必须已映射（防引擎改名/删除后静默丢失奖励）
CORE_REWARD_EVENTS: set[str] = {
    "m9_captain", "m9_police_enforcement", "m9_police_command_attack",
    "special_clue", "hotline",
    "oneslash_attack", "t1_chase", "star_attack", "hexagram_cast",
    "resurrection_trigger", "g4_savior_enter", "g6_borrow_core",
    "g0_drone_summon", "g0_crossfire", "SHADOW_CREATED",
    "TERMINAL_SONG_COMMITTED", "crystal_flower", "MATERIALIZED_BY_ANCHOR",
    "poem_stars", "m9_g3_expand", "m9_g3_ideal_burn", "m9_g3_dismiss",
    "m9_g3_release", "TERMINAL_SONG_ENDED", "MOVE_REDIRECTED",
    "location_destroyed", "world_poem_shock",
}


def test_no_hanging_reward_keys() -> None:
    hanging = set(M9_EVENT_REWARD) - ENGINE_M9_EVENTS
    assert not hanging, f"reward 表出现挂空键（引擎不产生这些事件）：{sorted(hanging)}"


def test_core_reward_events_registered_and_exist() -> None:
    for e in CORE_REWARD_EVENTS:
        assert e in ENGINE_M9_EVENTS, (
            f"核心事件已在 reward 表但引擎不再产生（引擎改名/删除？）：{e}"
        )
        assert e in M9_EVENT_REWARD or e in LEGACY_M9_EVENT_TYPES, (
            f"核心事件应在 M9 增量表或 legacy 单计分表登记：{e}"
        )


def test_engine_events_not_silently_dropped() -> None:
    dropped = {
        e for e in ENGINE_M9_EVENTS
        if e not in M9_EVENT_REWARD
        and e not in LEGACY_M9_EVENT_TYPES
        and e not in LEGACY_HANDLED
        and e not in KNOWN_M9_UNMAPPED_EVENTS
        and not any(e.startswith(p) for p in TALENT_PREFIX_IGNORED)
    }
    assert not dropped, (
        "引擎产生的事件未映射也未登记（新增机制漏奖励/漂移）："
        f"{sorted(dropped)}。要么加入 M9_EVENT_REWARD，要么登记 KNOWN/LEGACY/TALENT 前缀"
    )
