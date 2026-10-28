"""GA通用扩展接缝：NPC、选择窗口、濒死替代与已落地章节。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from engine.ga.contracts import (
    ChoiceState, ChoiceWindow, ContractError, DeathOutcome, EffectSpec, freeze_json,
)
from engine.ga.events import EventKind

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.state import GaPlayer


@dataclass(frozen=True)
class IncomingModifier:
    """普通减伤与明文HP修正分开，不能把非外甲防护记为磨甲。"""

    immune: bool = False
    reduction: int = 0
    vulnerability: int = 0
    hp_ratio: float = 1.0
    hp_reduction: int = 0
    ignored_defenses: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Replacement:
    """濒死预览结果；commit只在整批目标预览完成后执行一次。"""

    outcome: DeathOutcome
    hp: int
    reason: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class DeathGate:
    """稳定优先级的通用濒死门，核心不识别角色名字。"""

    priority: int
    preview: Callable[[GameState, EffectSpec, GaPlayer], Replacement | None]
    commit: Callable[[GameState, EffectSpec, GaPlayer, Replacement], None]


class ChoicePending(ContractError):
    """未回答就保持待回答，不能自动接受、出拳或放弃。"""

    def __init__(self, window_id: str) -> None:
        super().__init__("choice", f"explicit response required for {window_id}")
        self.window_id = window_id


def units(state: GameState) -> dict[str, GaPlayer]:
    """只返回实体引用索引，不建立第二份领域状态。"""
    return {**state.players, **state.ga_units}


def unit(state: GameState, actor_id: str) -> GaPlayer:
    """玩家和NPC共用承伤入口，NPC不进入玩家终分表。"""
    result = state.players.get(actor_id) or state.ga_units.get(actor_id)
    if result is None:
        raise ContractError("unit", f"unknown actor: {actor_id}")
    return result


def ask(
    state: GameState, creator: str, recipient: str, options: tuple[str, ...], *,
    kind: str, context: Mapping[str, Any] | None = None, secret: bool = False,
) -> str:
    """同步控制器选择窗口；未答显式暂停失败，不默认选项、不抽替代随机。"""
    from engine.ga.state import emit, next_id, observe, touch

    window = ChoiceWindow(next_id(state, "choice"), creator, recipient, options, None)
    state.ga_choice_windows[window.window_id] = window
    controller = state.ga_controllers.get(recipient)
    responder = getattr(controller, "respond", None)
    if responder is None:
        raise ChoicePending(window.window_id)
    selection = responder(observe(state, recipient), window, freeze_json({
        **dict(context or {}), "kind": kind,
    }))
    if selection is None:
        raise ChoicePending(window.window_id)
    resolved = window.resolve(selection, actor_id=recipient)
    state.ga_choice_windows[window.window_id] = resolved
    touch(state)
    if not secret:
        emit(state, EventKind.CHOICE_RESOLVED, actor_id=recipient,
             payload={"window_id": window.window_id, "kind": kind, "choice": selection})
    return selection


def reveal_choices(state: GameState, window_ids: tuple[str, ...]) -> None:
    """所有秘密承诺完成后才公布，后选者的观察不含先选者答案。"""
    from engine.ga.state import emit

    windows = tuple(state.ga_choice_windows[key] for key in window_ids)
    if any(w.state is not ChoiceState.RESOLVED for w in windows):
        raise ContractError("choices", "cannot reveal incomplete commitments")
    emit(state, EventKind.CHOICE_RESOLVED,
         payload={"commitments": {w.recipient_id: w.selected_option for w in windows}})


def chapter(state: GameState, actor_id: str, number: int) -> bool:
    """已满足条件的章节只记一次，PP按真实钱包继续收支。"""
    from engine.ga.state import emit, touch

    player = state.players[actor_id]
    key = f"chapter/{number}"
    if key in player.chapters:
        return False
    player.chapters.add(key)
    if not player.absolute_dead and not player.exited:
        player.pp += state.ga_config.get("pp", "chapter_gain")
    emit(state, EventKind.CHAPTER_COMPLETED, actor_id=actor_id, effective=True,
         payload={"chapter_id": key})
    touch(state)
    return True


def relocate(state: GameState, actor_id: str, location: str, *, active: bool = False) -> None:
    """通用位移，随身单位由订阅者同步，强制位移不生成移动闪避。"""
    from engine.ga.state import LOCATIONS, touch
    if location not in LOCATIONS or location in state.destroyed_locations:
        raise ContractError("location", "not a standing public building")
    actor = unit(state, actor_id)
    actor.location = location
    actor.home_locked = True
    if active:
        actor.moved = True
        if state.current_grant and state.current_grant.actor_id == actor_id:
            state.current_grant.moved_this_slot = True
    for callback in tuple(state.ga_location_listeners):
        callback(state, actor_id)
    touch(state)


def destroy_location(state: GameState, location: str) -> bool:
    """最后安全建筑保留；按先攻/ID驱逐，不回私人房间。"""
    from engine.ga.state import LOCATIONS, emit, touch
    standing = tuple(loc for loc in LOCATIONS if loc not in state.destroyed_locations)
    if location not in standing or len(standing) <= 1:
        return False
    state.destroyed_locations.add(location)
    destinations = tuple(loc for loc in standing if loc != location)
    actors = sorted((key for key, p in state.players.items() if p.alive and p.location == location),
                    key=lambda key: (-state.ga_initiative.get(key, 0), key))
    for index, actor in enumerate(actors):
        relocate(state, actor, destinations[index % len(destinations)])
    for key, npc in sorted(state.ga_units.items()):
        if npc.alive and npc.location == location:
            relocate(state, key, destinations[0])
    emit(state, EventKind.STATE_CHANGED, effective=True,
         payload={"kind": "location_destroyed", "location": location})
    touch(state)
    return True


def high_impact(state: GameState, actor: str, root: str, target: str | None = None) -> None:
    """真实生效的非伤害演出登记关注，不另开SP额度或给空操作发奖。"""
    from engine.ga.state import emit
    emit(state, EventKind.ACTION_ACTIVATED, actor_id=actor, target_id=target,
         root_id=root, effective=True, payload={"high_impact": True})
