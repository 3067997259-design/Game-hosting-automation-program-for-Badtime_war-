"""GA普通状态：硬控单槽、逐层灼烧、实际轮计时与有限回复。"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from engine.ga.contracts import ContractError, TimedEffect, TimerUnit
from engine.ga.state import GaPlayer, StatusEntry, touch

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.config import GaConfig


CONTROL_PRIORITY = {"SHOCKED": 1, "PETRIFIED": 2, "ACTION_SUPPRESSED": 3}
STATUS_NAMES = frozenset({*CONTROL_PRIORITY, "DISORIENTED", "VULNERABLE", "WEAK", "FLASHED",
                          "FEAR"})
# 减益状态（手册 §3.7）：全部普通状态加灼烧；“免疫所有减益”就用这一组。
DEBUFFS = STATUS_NAMES | {"BURN"}


def put_status(
    player: GaPlayer, config: GaConfig, global_round: int, name: str, *, source_id: str,
    actor_id: str | None = None, score_owner_id: str | None = None,
    crime_owner_id: str | None = None,
) -> bool:
    """普通硬控只存一位；弱不覆盖强，同名刷新，不叠跳槽。"""
    if name not in STATUS_NAMES:
        raise ContractError("status", f"unsupported GA status: {name}")
    if name in player.status_immunities:
        return False
    key = "control" if name in CONTROL_PRIORITY else name
    previous = player.statuses.get(key)
    if previous and key == "control" and (
        CONTROL_PRIORITY[previous.name] > CONTROL_PRIORITY[name]
    ):
        return False
    if name == "FEAR":
        timer = TimedEffect(f"{source_id}/{player.player_id}/{name}", player.player_id,
                            global_round, 1, TimerUnit.SLOT)
        player.statuses[name] = StatusEntry(name, timer, actor_id, score_owner_id, crime_owner_id)
        return True
    ticks = config.get("status", "unconsumed_control_expiry_ticks") if name in (
        "SHOCKED", "ACTION_SUPPRESSED", "DISORIENTED"
    ) else config.get("status", "petrify_ticks" if name == "PETRIFIED" else "modifier_ticks")
    timer = TimedEffect(f"{source_id}/{player.player_id}/{name}", player.player_id,
                        global_round, ticks, TimerUnit.FUTURE_R4)
    player.statuses[key] = StatusEntry(name, timer, actor_id, score_owner_id, crime_owner_id)
    if name == "DISORIENTED":
        player.hidden = False
    return True


def add_burns(
    player: GaPlayer, config: GaConfig, global_round: int, count: int, *, source_id: str,
    actor_id: str | None = None, score_owner_id: str | None = None,
    crime_owner_id: str | None = None,
) -> bool:
    """每层分别计时，满层后刷新最早到期者并保存新来源；免疫灼烧时一层也不挂。"""
    if "BURN" in player.status_immunities or count <= 0:
        return False
    for index in range(count):
        timer = TimedEffect(f"{source_id}/{player.player_id}/burn/{index}", player.player_id,
                            global_round, config.get("status", "burn_ticks"),
                            TimerUnit.FUTURE_R4)
        entry = StatusEntry("BURN", timer, actor_id, score_owner_id, crime_owner_id)
        if len(player.burns) < config.get("status", "burn_cap"):
            player.burns.append(entry)
        else:
            oldest = min(range(len(player.burns)), key=lambda i: (
                player.burns[i].timer.remaining, player.burns[i].timer.created_round, i
            ))
            player.burns[oldest] = entry
    return True


def apply_status(state: GameState, actor_id: str, name: str, *, source_id: str) -> bool:
    """效果/场景入口；不向旧Player写相似但语义不同的标记。"""
    if state.game_over:
        raise ContractError("status", "game is already terminal")
    changed = put_status(state.players[actor_id], state.ga_config, state.current_round,
                         name, source_id=source_id)
    if changed:
        touch(state)
    return changed


def effective_hit(player: GaPlayer, config: GaConfig) -> None:
    """石化两次有效受伤解除；擦伤实际扣血也计一次。"""
    control = player.statuses.get("control")
    if control and control.name == "PETRIFIED" and not control.anchored:
        hits = control.hits + 1
        if hits >= config.get("status", "petrify_release_hits"):
            del player.statuses["control"]
        else:
            player.statuses["control"] = replace(control, hits=hits)


def expire_r4(state: GameState) -> None:
    """只在未来实际R4扣tick；世界时钟加速/暂停都不改变年龄。"""
    from engine.ga.extensions import units
    for player in units(state).values():
        if not player.alive:
            continue
        if player.hidden_timer:
            player.hidden_timer = player.hidden_timer.on_r4(state.current_round)
            if not player.hidden_timer.remaining:
                player.hidden = False
                player.hidden_timer = None
        for key, entry in tuple(player.statuses.items()):
            timer = entry.timer.on_r4(state.current_round)
            if timer.remaining:
                player.statuses[key] = replace(entry, timer=timer)
            else:
                del player.statuses[key]
        player.burns = [replace(entry, timer=entry.timer.on_r4(state.current_round))
                        for entry in player.burns
                        if entry.timer.on_r4(state.current_round).remaining]
        for key, timer in tuple(player.barrier_timers.items()):
            updated = timer.on_r4(state.current_round)
            if updated.remaining:
                player.barrier_timers[key] = updated
            else:
                player.defenses.pop(key, None)
                del player.barrier_timers[key]
        for key, timer in tuple(player.disabled_weapons.items()):
            updated = timer.on_r4(state.current_round)
            if updated.remaining:
                player.disabled_weapons[key] = updated
            else:
                del player.disabled_weapons[key]
    touch(state)


def heal(player: GaPlayer, amount: int) -> int:
    """普通治疗不能复活，不超过上限，返回实际量。"""
    if not player.alive or not player.healable:
        return 0
    actual = min(amount, player.max_hp - player.hp)
    player.hp += actual
    return actual


def cleanse_burns(player: GaPlayer) -> None:
    """普通净化不能取消明文献诗代价；死亡/复活仍可清理普通肉身状态。"""
    player.burns = [entry for entry in player.burns if entry.uncleansable]
