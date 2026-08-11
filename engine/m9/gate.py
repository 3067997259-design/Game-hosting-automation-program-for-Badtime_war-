"""m9-rfc 接入层：天赋类替换工厂 + state 级 M9 机制挂载。

v2exp 天赋类文件字节冻结；m9_rfc 时经 `m9_talent_class` 把实例化换成 M9 类
（同名 `name` 保持字符串引用兼容）。`ensure_state_mechanisms` 在开局把
M9 机制（ActionSystem / G6 模板池 / PP 台账等）挂到 game_state 上。
"""
from __future__ import annotations

from typing import Any, Optional, Type

from engine import experiments


def m9_enabled() -> bool:
    return experiments.is_enabled("m9_rfc")


def _load_m9_classes() -> dict:
    """v2exp 天赋类名 → M9 类（惰性导入，避免 v2exp 路径引入 engine.m9）。"""
    from engine.m9.talents.g6 import CutawayJoke9
    from engine.m9.talents.g7 import Hoshino9
    from engine.m9.talents.g1 import G1MythFire9
    from engine.m9.talents.g4 import Savior9
    from engine.m9.talents.g2 import Hologram9
    from engine.m9.talents.g5 import Ripple9
    return {
        "要有笑声！": CutawayJoke9,
        "大叔我啊，剪短发了": Hoshino9,
        "火萤IV型-完全燃烧": G1MythFire9,
        "愿负世，照拂黎明": Savior9,
        "神代天赋-请一直注视着我": Hologram9,
        "神代天赋-往世的涟漪": Ripple9,
    }


def m9_talent_class(cls: Type) -> Type:
    """m9_rfc 时返回对应 M9 天赋类；否则原类（v2exp 字节不变）。"""
    if not m9_enabled():
        return cls
    name = getattr(cls, "name", "")
    return _load_m9_classes().get(name, cls)


def ensure_state_mechanisms(game_state: Any) -> None:
    """m9_rfc：在 game_state 挂 M9 机制（幂等）。"""
    if not m9_enabled():
        return
    if hasattr(game_state, "m9_system"):
        return
    from engine.m9.action_system import ActionSystem
    from engine.m9.talents.g6 import G6TemplatePool
    from engine.m9.pp import PPLedger
    game_state.m9_system = ActionSystem()
    game_state.g6_template_pool = G6TemplatePool()
    game_state.m9_pp = PPLedger()
    game_state.m9_shadows = {}        # G2:shadow@<pid> → ShadowActor
    game_state.m9_terminal_areas = {}  # g2_pid → TerminalArea（Hologram9 持有）
