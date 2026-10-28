"""GA公共系统与已实现天赋唯一装配入口。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from engine.ga.afterlife import Afterlife
from engine.ga.contracts import ContractError
from engine.ga.police import Police
from engine.ga.talents.g0 import G0
from engine.ga.talents.g1 import G1
from engine.ga.talents.g2 import G2
from engine.ga.talents.g3 import G3
from engine.ga.talents.g4 import G4
from engine.ga.talents.g5 import G5
from engine.ga.talents.g6 import G6
from engine.ga.talents.g7 import G7
from engine.ga.poems import Poems
from engine.ga.world import World
from engine.ga.talents.t1 import T1
from engine.ga.talents.t2 import T2
from engine.ga.talents.t3 import T3
from engine.ga.talents.t4 import T4
from engine.ga.talents.t6 import T6
from engine.ga.talents.t7 import T7

if TYPE_CHECKING:
    from engine.game_state import GameState


TALENTS = {role.talent_id: role for role in (
    T1, T2, T3, T4, T6, T7, G0, G1, G2, G3, G4, G5, G6, G7)}


def install(state: GameState, selected: Mapping[str, str] | None = None) -> None:
    """缺实现明确拒绝；公共系统不依赖是否有人选择某一天赋。"""
    selection = dict(selected or {})
    if selection.keys() - state.players.keys():
        raise ContractError("talents", "unknown participant")
    for actor, slot in selection.items():
        if slot not in TALENTS:
            raise ContractError(f"talents.{actor}", f"unimplemented GA talent: {slot}")
    World().register(state)
    Police().register(state)
    for actor in sorted(selection):
        role = TALENTS[selection[actor]](actor)
        state.ga_roles[actor] = role
        role.register(state)
    Poems().register(state)
    Afterlife().register(state)
