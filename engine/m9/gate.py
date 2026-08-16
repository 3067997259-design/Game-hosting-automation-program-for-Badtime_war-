"""m9-rfc 接入层：天赋类替换工厂 + state 级 M9 机制挂载。

v2exp 天赋类文件字节冻结；m9_rfc 时经 `m9_talent_class` 把实例化换成 M9 类
（同名 `name` 保持字符串引用兼容）。`ensure_state_mechanisms` 在开局把
M9 机制（ActionSystem / G6 模板池 / PP 台账等）挂到 game_state 上。
"""
from __future__ import annotations

from typing import Any, Type

from engine import experiments


def m9_enabled(game_state: Any | None = None) -> bool:
    if game_state is not None and hasattr(game_state, "m9_enabled"):
        return bool(game_state.m9_enabled)
    return experiments.is_enabled("m9_rfc")


def m9_talent_class(cls: Type) -> Type:
    """M9 返回注册类；未迁移槽 fail closed，其他 profile 原样返回。"""
    if not m9_enabled():
        return cls
    from engine.m9.talent_registry import m9_class_for_legacy
    return m9_class_for_legacy(cls)


def instantiate_talent(game_state: Any, cls: Type, player_id: str) -> Any:
    """唯一的 profile-aware 天赋实例化入口。"""
    ensure_state_mechanisms(game_state)
    if not m9_enabled(game_state):
        return cls(player_id, game_state)
    from engine.m9.talent_registry import m9_class_for_legacy
    return m9_class_for_legacy(cls)(player_id, game_state)


def ensure_state_mechanisms(game_state: Any) -> None:
    """m9_rfc：在 game_state 挂 M9 机制（幂等）。"""
    if not m9_enabled(game_state):
        return
    if hasattr(game_state, "m9_system"):
        return
    from engine.m9.action_system import ActionSystem
    from engine.m9.arc import ChapterLedger
    from engine.m9.talents.g6 import G6TemplatePool
    from engine.m9.pp import PPLedger, ScoringEngine
    from engine.m9.petrify import PetrifyRegistry
    from engine.m9.insurance import InsuranceRegistry
    from engine.m9.police import PoliceStation
    from engine.m9.resolution import SuppressRegistry
    game_state.m9_system = ActionSystem()
    game_state.m9_arc = ChapterLedger(game_state)
    game_state.m9_system.attach_arc(game_state.m9_arc, game_state)
    game_state.g6_template_pool = G6TemplatePool()
    game_state.m9_pp = PPLedger()
    game_state.m9_scoring = ScoringEngine(game_state.m9_pp, game_state)
    game_state.m9_petrify = PetrifyRegistry()
    game_state.m9_insurance = InsuranceRegistry()
    game_state.m9_police = PoliceStation()
    # RFC §3.1：固定警力池必须在开局建立。此前只有测试夹具手工调用
    # ensure_roster，真实 GameState 的编制恒为 0，使案件、掩体与 T6 联防整备
    # 全部不可达。固定编制从警察局生成；后续 R2/队长命令再负责移动。
    game_state.m9_police.ensure_roster(initial_location="警察局")
    game_state.m9_suppress = SuppressRegistry()
    game_state.m9_shadows = {}        # G2:shadow@<pid> → ShadowActor
    game_state.m9_terminal_areas = {}  # g2_pid → TerminalArea（Hologram9 持有）
    game_state.m9_destroyed_locations = set()  # 繁育超新星摧毁的地点
    for player_id in getattr(game_state, "player_order", []):
        game_state.m9_system.register_player(player_id)
