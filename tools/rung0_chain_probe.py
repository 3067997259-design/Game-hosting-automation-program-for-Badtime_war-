"""Rung 0 复合攻击链探针：证明 RL 学到的是 move → find → attack，而非蹭赢。

为什么不能只看胜率：在 40 轮上限、带天赋的 1v1 dummy 局里，随机策略也能拿到
96% 胜率——而它的 kill_count 是 0，赢全是警察/世界事件替它杀的。所以本探针只
认三件可归因的事实：

1. **击杀归因**：RL 自己的 `player.kill_count`（引擎记账），排除环境击杀；
2. **链路成形**：同一目标上出现 find（或已锁定）之后的 attack，且 attack 之前
   曾有 move 把自己送到对手所在地点 —— 即 move→find→attack 的有序组合；
3. **动作分布**：决策动词直方图。若 forfeit/无关动词占绝大多数而仍"赢"，
   说明信号来自环境而非策略。

用法：
    python tools/rung0_chain_probe.py --model checkpoints/rung0_dummy/final.zip \
        --episodes 30 [--random]   # --random = 同口径随机策略基线
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import io
import os
from pathlib import Path
import random
import subprocess
import sys


def _ensure_cli_hash_seed() -> None:
    """Restart the CLI before imports so ``--seed`` also controls hash order."""
    if __name__ != "__main__":
        return
    seed = "0"
    if "--seed" in sys.argv:
        index = sys.argv.index("--seed")
        if index + 1 >= len(sys.argv):
            return  # argparse will report the malformed option below.
        seed = sys.argv[index + 1]
    if os.environ.get("PYTHONHASHSEED") == seed:
        return
    child_env = os.environ.copy()
    child_env["PYTHONHASHSEED"] = seed
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
        env=child_env,
        check=False,
    )
    raise SystemExit(completed.returncode)


_ensure_cli_hash_seed()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import numpy as np

from engine.experiments import set_profile
from rl.action_space import apply_action_contract, idx_to_choose_option, idx_to_command
from rl.env import BadtimeWarEnv

PROFILE = "m9-rfc"


def _verb(cmd: str) -> str:
    return cmd.split(" ", 1)[0] if cmd else "?"


def _decode_action(env: BadtimeWarEnv, action: int) -> str:
    """Decode against the decision mode captured before ``env.step``."""
    if env._choose_mode:
        situation = env._choose_context.get("situation", "") or "unknown"
        option = idx_to_choose_option(
            action,
            env._choose_options,
            situation,
            env._current_player,
            env._current_game_state,
        )
        return f"choose[{situation}] {option}"
    return idx_to_command(action, env._rl_player, env._state)


def _oracle_action(mask, player, game_state) -> int:
    """多目标贪心链：备小刀 > 收残血 > 调查/射击 > 追击/锁定。

    它只用 RL 自己的动作空间与 mask（不走 BasicAI 通道），所以能回答一个
    比"rush bot 打得死 dummy"更强的问题：**这条链在 RL 的编码下走得通吗**。
    oracle 打不死 ⇒ 编码/掩码/引擎侧还有堵点；oracle 打得死而学不到 ⇒ 纯学习问题。
    """
    from rl.action_space import get_opponent_slots

    opponents = [
        opponent for opponent in get_opponent_slots(player, game_state)
        if opponent is not None and opponent.is_alive()
    ]
    by_name = {
        str(opponent.name): (slot, opponent)
        for slot, opponent in enumerate(opponents)
    }
    cand: dict[str, list[tuple[float, int, int]]] = collections.defaultdict(list)

    def add(key: str, idx: int, target=None, slot: int = 99) -> None:
        hp = float(getattr(target, "hp", 999.0)) if target is not None else 999.0
        cand[key].append((hp, slot, idx))

    def command_target(cmd: str):
        for name, (slot, opponent) in by_name.items():
            if cmd == f"lock {name}" or cmd == f"find {name}" or cmd == f"shoot {name}":
                return slot, opponent
            if cmd.startswith(f"attack {name} "):
                return slot, opponent
        return None, None

    for i in np.flatnonzero(mask):
        try:
            cmd = idx_to_command(int(i), player, game_state)
        except Exception:
            continue
        verb = _verb(cmd)
        slot, target = command_target(cmd)
        add(verb, int(i), target, slot if slot is not None else 99)
        if cmd == "interact 小刀":
            add("_arm", int(i))
        if cmd.startswith("attack ") and cmd.endswith(" 小刀"):
            add("_knife_attack", int(i), target, slot if slot is not None else 99)
        if verb == "lock" and target is not None:
            already_locked = game_state.markers.has_relation(
                target.player_id, "LOCKED_BY", player.player_id,
            )
            if not already_locked:
                add("_lock_new", int(i), target, slot)
        if verb == "move":
            for opponent_slot, opponent in enumerate(opponents):
                if cmd != f"move {opponent.location}":
                    continue
                locked = game_state.markers.has_relation(
                    opponent.player_id, "LOCKED_BY", player.player_id,
                )
                add("_chase_locked" if locked else "_chase",
                    int(i), opponent, opponent_slot)

    # 不再用“第一个存活对手是否已锁定”关闭整个 lock 族。
    # 否则 slot0 已锁定但暂时不可追时，教师会忽略可处理的 slot1，
    # 并把大量复杂多人状态错标为 forfeit。
    priority = [
        "_arm", "_knife_attack", "attack", "find", "shoot",
        "_chase_locked", "_lock_new", "_chase",
    ]
    for key in priority:
        if cand.get(key):
            return min(cand[key])[2]
    return int(np.flatnonzero(mask)[0])


def _economy_supply_oracle_action(mask, player, game_state) -> int:
    """Atomic economy teacher: spend earned credit before earning again."""
    by_command: dict[str, int] = {}
    for idx in np.flatnonzero(mask):
        try:
            by_command[idx_to_command(int(idx), player, game_state)] = int(idx)
        except Exception:
            continue
    for command in (
        "shoot AI_0", "interact 箭矢补给", "interact 打工",
    ):
        if command in by_command:
            return by_command[command]
    return int(np.flatnonzero(mask)[0])


def _economy_pursuit_oracle_action(mask, player, game_state) -> int:
    """Post-kill teacher: replenish only when empty, then relock a moving target."""
    by_command: dict[str, int] = {}
    for idx in np.flatnonzero(mask):
        try:
            by_command[idx_to_command(int(idx), player, game_state)] = int(idx)
        except Exception:
            continue

    alive = [
        candidate for candidate in game_state.players.values()
        if candidate is not player and candidate.is_alive()
    ]
    target = min(alive, key=lambda candidate: candidate.player_id) if alive else None
    if int(getattr(player, "arrows", 0) or 0) <= 0:
        for command in (
            "interact 箭矢补给" if int(getattr(player, "credits", 0) or 0) >= 1
            else "interact 打工",
            "move 商店",
        ):
            if command in by_command:
                return by_command[command]
    elif target is not None:
        for command in (
            f"shoot {target.name}",
            f"lock {target.name}",
            f"move {target.location}",
            f"find {target.name}",
        ):
            if command in by_command:
                return by_command[command]
    return _oracle_action(mask, player, game_state)


def _economy_pursuit_finish_oracle_action(mask, player, game_state) -> int:
    """Use the held knife when an armored pursuit target closes to melee.

    This is deliberately contextual rather than a global melee preference:
    ``attack`` only exists for a co-located engaged target.  At range the
    existing work/supply/lock/shoot pursuit teacher remains unchanged.
    """
    candidates: list[tuple[int, str, int]] = []
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            command = idx_to_command(idx, player, game_state)
        except Exception:
            continue
        if not command.startswith("attack ") or not command.endswith(" 小刀"):
            continue
        target_name = command[len("attack "):-len(" 小刀")]
        target = next(
            (
                candidate for candidate in game_state.players.values()
                if candidate is not player and candidate.is_alive()
                and candidate.name == target_name
            ),
            None,
        )
        if target is not None:
            target_hp = int(getattr(target, "hp", 0) or 0)
            player_hp = int(getattr(player, "hp", 0) or 0)
            if target_hp <= 12 and player_hp >= target_hp:
                candidates.append((target_hp, target.player_id, idx))
    if candidates:
        return min(candidates)[2]
    alive = [
        candidate for candidate in game_state.players.values()
        if candidate is not player and candidate.is_alive()
    ]
    target = min(
        alive,
        key=lambda candidate: (
            int(getattr(candidate, "hp", 0) or 0), candidate.player_id,
        ),
        default=None,
    )
    if (
        target is not None
        and getattr(target, "location", None) == "医院"
        and getattr(player, "location", None) != "医院"
        and int(getattr(target, "hp", 0) or 0) <= 12
        and int(getattr(player, "hp", 0) or 0)
        >= int(getattr(target, "hp", 0) or 0)
    ):
        for raw_idx in np.flatnonzero(mask):
            idx = int(raw_idx)
            try:
                if idx_to_command(idx, player, game_state) == "move 医院":
                    return idx
            except Exception:
                continue
    return _economy_pursuit_oracle_action(mask, player, game_state)


def _fire_antiarmor_oracle_action(mask, player, game_state) -> int:
    """Install one fire module, then reuse the proven pursuit-supply chain."""
    if "火矢" not in (getattr(player, "bow_modules", []) or []):
        for raw_idx in np.flatnonzero(mask):
            idx = int(raw_idx)
            try:
                if idx_to_command(idx, player, game_state) == "interact 火矢模块":
                    return idx
            except Exception:
                continue
    return _economy_pursuit_oracle_action(mask, player, game_state)


def _bow_loadout_oracle_action(mask, player, game_state) -> int:
    """Shared six-module teacher; the case label is never part of observation.

    The label persists across the one-turn trip to a facility so collection can
    supervise a recurrent move→install sequence.  Each label is generated from
    a distinct visible initial state by ``equipment_bow_loadout``.
    """
    target_module = getattr(
        game_state, "rl_bow_loadout_target_module", None,
    )
    if target_module and target_module not in (
        getattr(player, "bow_modules", []) or []
    ):
        action = _bow_module_oracle_action(
            mask, player, game_state, target_module,
        )
        if action is not None:
            return action
    return _economy_pursuit_oracle_action(mask, player, game_state)


def _bow_module_oracle_action(mask, player, game_state, module_name: str):
    """Return the move/install label for a named module, if currently legal."""
    from engine.bow_modules import module_specs

    location = module_specs().get(module_name, {}).get("location")
    if not location:
        return None
    desired = (
        f"interact {module_name}模块"
        if getattr(player, "location", None) == location
        else f"move {location}"
    )
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            if idx_to_command(idx, player, game_state) == desired:
                return idx
        except Exception:
            continue
    return None


def _bow_loadout_course_skill_mask(mask, player, game_state):
    """Give the shared expert ownership, but never reveal the correct action."""
    target_module = getattr(
        game_state, "rl_bow_loadout_target_module", None,
    )
    if not target_module or target_module in (
        getattr(player, "bow_modules", []) or []
    ):
        return mask, None
    # Keep the whole course contract.  The model must choose both facility and
    # paired module from observation; this gate only decides when to hand back.
    return mask, "bow_loadout"


def _old_policy_observation(obs, bow_context_enabled: bool):
    """Hide the new bow tail from checkpoints trained with an all-zero tail."""
    if not bow_context_enabled:
        return obs
    from rl.obs_builder_m9 import M9_BOW_CONTEXT_BASE, M9_BOW_CONTEXT_DIM

    sanitized = np.asarray(obs, dtype=np.float32).copy()
    start = M9_BOW_CONTEXT_BASE
    sanitized[..., start:start + M9_BOW_CONTEXT_DIM] = 0.0
    return sanitized


def _natural_bow_loadout_skill_mask(mask, player, game_state):
    """Route the shared bow expert only for clear, observable natural needs.

    The router chooses neither an action nor a course label.  It only decides
    whether the two-step bow expert owns the current turn; that expert still
    has to choose the facility and module from the complete bow-loadout
    contract.  Conservative gating keeps unvalidated upgrades out of normal
    play until their action-economy benefit is demonstrated.
    """
    if player is None or game_state is None:
        return mask, None, None
    if int(getattr(player, "kill_count", 0) or 0) < 1:
        return mask, None, None
    if int(getattr(player, "hp", 0) or 0) < 11:
        return mask, None, None
    if not getattr(player, "has_weapon", lambda _name: False)("弓"):
        return mask, None, None
    if list(getattr(player, "bow_modules", []) or []):
        return mask, None, None

    opponents = [
        target for target in game_state.players.values()
        if target is not player and target.is_alive()
    ]
    if len(opponents) != 1:
        return mask, None, None
    target = opponents[0]

    armor = getattr(target, "armor", None)
    pieces = list(armor.get_all_active()) if armor is not None else []
    armor_names = {getattr(piece, "name", "") for piece in pieces}
    fire_favored = any(
        float((getattr(piece, "defense_map", {}) or {}).get("普通", 0)) >= 2
        and float((getattr(piece, "defense_map", {}) or {}).get("魔法", 0)) == 0
        for piece in pieces
    )
    arrows = int(getattr(player, "arrows", 0) or 0)
    credits = int(getattr(player, "credits", 0) or 0)
    target_hp = int(getattr(target, "hp", 0) or 0)

    module_name = None
    if arrows <= 0 and credits >= 4:
        module_name = "无限"
    elif target_hp >= 8 and fire_favored:
        module_name = "火矢"
    elif (
        target_hp >= 8
        and "魔法护盾" in armor_names
        and bool(getattr(player, "has_military_pass", False))
    ):
        module_name = "穿甲"
    if module_name is None:
        return mask, None, None

    from engine.bow_modules import can_install, module_specs

    can_get_module, _reason = can_install(player, module_name, game_state)
    if not can_get_module:
        return mask, None, None
    destination = module_specs()[module_name]["location"]
    destination_occupied = any(
        candidate is not player and candidate.is_alive()
        and getattr(candidate, "location", None) == destination
        for candidate in game_state.players.values()
    )
    if getattr(player, "location", None) != destination and destination_occupied:
        return mask, None, None

    desired = (
        f"interact {module_name}模块"
        if getattr(player, "location", None) == destination
        else f"move {destination}"
    )
    legal_commands = set()
    for raw_idx in np.flatnonzero(mask):
        try:
            legal_commands.add(idx_to_command(int(raw_idx), player, game_state))
        except Exception:
            continue
    if desired not in legal_commands:
        return mask, None, None

    routed = apply_action_contract(mask, "bow_loadout_drill")
    if not routed.any():
        return mask, None, None
    return routed, target.player_id, module_name


def _pp_damage_oracle_action(mask, player, game_state) -> int:
    """Atomic PP teacher: arm the +1 modifier, then spend it on the knife hit."""
    by_command: dict[str, int] = {}
    for idx in np.flatnonzero(mask):
        try:
            by_command[idx_to_command(int(idx), player, game_state)] = int(idx)
        except Exception:
            continue
    for command in ("special PP加伤", "attack AI_0 小刀"):
        if command in by_command:
            return by_command[command]
    return int(np.flatnonzero(mask)[0])


def _survival_heal_oracle_action(mask, player, game_state) -> int:
    """Take the scenario's survival purchase once, then resume combat."""
    by_command: dict[str, int] = {}
    for idx in np.flatnonzero(mask):
        try:
            by_command[idx_to_command(int(idx), player, game_state)] = int(idx)
        except Exception:
            continue
    for command in (
        "interact 治疗", "interact 陶瓷护甲", "interact 修理陶瓷护甲",
    ):
        if command in by_command:
            return by_command[command]
    return _oracle_action(mask, player, game_state)


def _sharpen_oracle_action(mask, player, game_state) -> int:
    """Pay the upgrade cost once, then amortize it across both targets."""
    by_command: dict[str, int] = {}
    for idx in np.flatnonzero(mask):
        try:
            by_command[idx_to_command(int(idx), player, game_state)] = int(idx)
        except Exception:
            continue
    for command in ("interact 磨刀石", "special 磨刀"):
        if command in by_command:
            return by_command[command]
    return _oracle_action(mask, player, game_state)


_DETECTION_COMMANDS = {
    "普通": "interact 热成像仪",
    "魔法": "interact 探测魔法",
    "科技": "interact 雷达",
}
_DETECTION_LOCATIONS = {
    "普通": "商店",
    "魔法": "魔法所",
    "科技": "军事基地",
}


def _detection_oracle_action(mask, player, game_state) -> int:
    """Acquire the detector matching the currently hidden live target."""
    missing_attrs: set[str] = set()
    owned = set(getattr(player, "detection_attrs", None) or set())
    for target in game_state.players.values():
        if target is player or not target.is_alive():
            continue
        missing_attrs.update(
            set(getattr(target, "stealth_attrs", None) or set()) - owned
        )
    wanted = {_DETECTION_COMMANDS[attr] for attr in missing_attrs
              if attr in _DETECTION_COMMANDS}
    for idx in np.flatnonzero(mask):
        try:
            if idx_to_command(int(idx), player, game_state) in wanted:
                return int(idx)
        except Exception:
            continue
    return _oracle_action(mask, player, game_state)


def _thermal_oracle_action(mask, player, game_state) -> int:
    """Backward-compatible alias for the generalized detector teacher."""
    return _detection_oracle_action(mask, player, game_state)


def _detection_skill_mask(mask, player, game_state):
    """Route one detector action only for a matching hidden target attribute."""
    if player is None or game_state is None:
        return mask, None, None

    owned = set(getattr(player, "detection_attrs", None) or set())
    hidden_targets: list[tuple[str, str]] = []
    from engine.visibility import can_see
    for pid in game_state.player_order:
        target = game_state.get_player(pid)
        if target is None or target is player or not target.is_alive():
            continue
        if can_see(player, target, game_state):
            continue
        for attr in sorted(set(getattr(target, "stealth_attrs", None) or set())):
            if attr not in owned and attr in _DETECTION_COMMANDS:
                hidden_targets.append((target.player_id, attr))
    if not hidden_targets:
        return mask, None, None

    by_command: dict[str, int] = {}
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            by_command[idx_to_command(idx, player, game_state)] = idx
        except Exception:
            continue
    target_id, attr = min(hidden_targets)
    detector_action = by_command.get(_DETECTION_COMMANDS[attr])
    if detector_action is None:
        if int(getattr(player, "kill_count", 0) or 0) < 1:
            # Before the first kill, do not abandon a visible opponent just
            # because another one hides.  If every live opponent is currently
            # undamageable, however, refusing the cross-location detector
            # route creates a permanent work/supply loop against assassins.
            has_direct_offense = any(
                command.startswith("attack ") or command.startswith("shoot ")
                for command in by_command
            )
            if has_direct_offense:
                return mask, None, None
        destination = _DETECTION_LOCATIONS[attr]
        if attr == "科技" and not bool(
            getattr(player, "has_military_pass", False)
        ):
            return mask, None, None
        detector_action = by_command.get(f"move {destination}")
        if detector_action is None:
            return mask, None, None

    focused = np.zeros_like(mask)
    focused[detector_action] = True
    return focused, target_id, attr


def _thermal_skill_mask(mask, player, game_state):
    """Backward-compatible ordinary-stealth view of the generalized router."""
    focused, target_id, attr = _detection_skill_mask(mask, player, game_state)
    if attr not in {None, "普通"}:
        return mask, None
    return focused, target_id


def _attribute_armor_oracle_action(mask, player, game_state) -> int:
    """Choose the legal attack with the largest real read-only hit projection."""
    from engine.m9.combat import resolve_hit_probe

    candidates: list[tuple[int, int]] = []
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            command = idx_to_command(idx, player, game_state)
        except Exception:
            continue
        if not command.startswith("attack "):
            continue
        parts = command.split()
        if len(parts) < 3:
            continue
        target = next((
            candidate for candidate in game_state.players.values()
            if candidate.name == parts[1]
        ), None)
        weapon = player.get_weapon(" ".join(parts[2:]))
        if target is None or weapon is None:
            continue
        hit = resolve_hit_probe(
            target,
            int(round(float(weapon.get_effective_damage()))),
            getattr(weapon.attribute, "value", "普通"),
        )
        candidates.append((int(hit.damage), idx))
    if candidates:
        return max(candidates)[1]
    return _oracle_action(mask, player, game_state)


def _attribute_armor_skill_mask(mask, player, game_state):
    """Let the expert choose weapon attribute for one engaged armored target."""
    if player is None or game_state is None:
        return mask, None
    engaged = []
    for target in game_state.players.values():
        if target is player or not target.is_alive():
            continue
        armor = getattr(target, "armor", None)
        pieces = list(armor.get_all_active()) if armor is not None else []
        if not any(piece.name in {"魔法护盾", "AT力场"} for piece in pieces):
            continue
        if game_state.markers.has_relation(
            player.player_id, "ENGAGED_WITH", target.player_id,
        ):
            engaged.append(target)
    if not engaged:
        return mask, None
    target = min(engaged, key=lambda candidate: candidate.player_id)

    attack_indices: list[int] = []
    attributes: set[str] = set()
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            command = idx_to_command(idx, player, game_state)
        except Exception:
            continue
        prefix = f"attack {target.name} "
        if not command.startswith(prefix):
            continue
        weapon = player.get_weapon(command[len(prefix):])
        if weapon is None:
            continue
        attributes.add(getattr(weapon.attribute, "value", "普通"))
        attack_indices.append(idx)
    if len(attributes) < 2:
        return mask, None
    focused = np.zeros_like(mask)
    focused[attack_indices] = True
    return focused, target.player_id


def _fire_antiarmor_skill_mask(
        mask, player, game_state, min_hp=11, require_lowhp_arrows=False):
    """Route an archer against ordinary-primary, magic-open armor."""
    if player is None or game_state is None:
        return mask, None, None
    if int(getattr(player, "kill_count", 0) or 0) < 1:
        return mask, None, None
    hp = int(getattr(player, "hp", 0) or 0)
    if hp < int(min_hp):
        return mask, None, None
    if (
        require_lowhp_arrows and hp < 11
        and int(getattr(player, "arrows", 0) or 0) <= 0
    ):
        return mask, None, None
    if not getattr(player, "has_weapon", lambda _name: False)("弓"):
        return mask, None, None
    if "火矢" in (getattr(player, "bow_modules", []) or []):
        return mask, None, None

    candidates = []
    for target in game_state.players.values():
        if target is player or not target.is_alive():
            continue
        armor = getattr(target, "armor", None)
        pieces = list(armor.get_all_active()) if armor is not None else []
        fire_favored = any(
            float((getattr(piece, "defense_map", {}) or {}).get("普通", 0)) >= 2
            and float((getattr(piece, "defense_map", {}) or {}).get("魔法", 0)) == 0
            for piece in pieces
        )
        if int(getattr(target, "hp", 0) or 0) >= 8 and fire_favored:
            candidates.append(target)
    if not candidates:
        return mask, None, None
    target = min(
        candidates,
        key=lambda candidate: (
            int(getattr(candidate, "hp", 0) or 0), candidate.player_id,
        ),
    )

    from engine.bow_modules import can_install
    can_get_fire, _reason = can_install(player, "火矢", game_state)
    if not can_get_fire:
        return mask, None, None
    magic_occupied = any(
        candidate is not player and candidate.is_alive()
        and getattr(candidate, "location", None) == "魔法所"
        for candidate in game_state.players.values()
    )
    if getattr(player, "location", None) != "魔法所" and magic_occupied:
        return mask, None, None

    desired = (
        "interact 火矢模块"
        if getattr(player, "location", None) == "魔法所"
        else "move 魔法所"
    )
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            if idx_to_command(idx, player, game_state) != desired:
                continue
        except Exception:
            continue
        focused = np.zeros_like(mask)
        focused[idx] = True
        kind = "install" if desired.startswith("interact ") else "move"
        return focused, target.player_id, kind
    return mask, None, None


def _police_skill_mask(mask, player, game_state, proactive_election=False):
    """Route police only for a real wanted target and immediate useful control.

    Personal attacks keep priority.  The router never exposes unit-move
    commands: R2 already assigns the lead unit to the wanted target, and the
    old broad command policy collapsed into repeated unit1 movement.
    """
    if player is None or game_state is None:
        return mask, None
    station = getattr(game_state, "m9_police", None)
    if station is None or station.is_disabled() or station.suspended:
        return mask, None

    by_command: dict[str, int] = {}
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            by_command[idx_to_command(idx, player, game_state)] = idx
        except Exception:
            continue

    election_idx = by_command.get("special 竞选队长")
    if (
        proactive_election
        and int(getattr(player, "kill_count", 0) or 0) == 0
        and station.captain_id is None
        and not station.candidates()
        and not any(
            candidate is not player
            and candidate.is_alive()
            and bool(getattr(candidate, "acted_this_round", False))
            for candidate in game_state.players.values()
        )
        and election_idx is not None
        and station.can_apply_captain(player.player_id, game_state)
        and any(unit.is_active() for unit in station.active_units())
    ):
        focused = np.zeros_like(mask)
        focused[election_idx] = True
        return focused, "precombat_election"

    wanted = station.open_wanted()
    if wanted is None:
        return mask, None
    target = game_state.get_player(wanted.suspect_id)
    if target is None or not target.is_alive():
        return mask, None
    if any(
        command.startswith(f"attack {target.name} ")
        or command == f"shoot {target.name}"
        for command in by_command
    ):
        return mask, None

    if station.captain_id == player.player_id:
        attack_indices: list[int] = []
        for command, idx in by_command.items():
            if not (
                command.startswith("special 指挥unit")
                and command.endswith("攻击")
            ):
                continue
            unit_id = command[len("special 指挥"):-len("攻击")]
            unit = station.get_unit(unit_id)
            if (
                unit is not None and unit.is_active()
                and not unit.acted_this_round
                and unit.location == target.location
            ):
                attack_indices.append(idx)
        if attack_indices:
            focused = np.zeros_like(mask)
            focused[attack_indices] = True
            return focused, "attack"
        return mask, None

    if (
        station.captain_id is None
        and election_idx is not None
        and station.can_apply_captain(player.player_id, game_state)
        and any(unit.is_active() for unit in station.active_units())
    ):
        focused = np.zeros_like(mask)
        focused[election_idx] = True
        return focused, "election"
    return mask, None


def _sharpen_skill_mask(mask, player, game_state):
    """Route the equipment expert only when sharpening saves total actions.

    Movement and detection costs are shared by both plans, so the comparison is
    deliberately limited to the differing part: remaining knife hits plus the
    one or two setup actions.  Armored targets are excluded until a later course
    can model attribute and durability interactions without optimistic guesses.
    """
    if player is None or game_state is None:
        return mask, None

    by_command: dict[str, int] = {}
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            by_command[idx_to_command(idx, player, game_state)] = idx
        except Exception:
            continue

    buy_action = by_command.get("interact 磨刀石")
    sharpen_action = by_command.get("special 磨刀")
    if buy_action is None and sharpen_action is None:
        return mask, None

    from models.equipment import knife_sharpened_damage

    knife = next((
        weapon for weapon in (getattr(player, "weapons", []) or [])
        if weapon is not None and getattr(weapon, "name", "") == "小刀"
    ), None)
    if knife is None:
        return mask, None
    current_damage = float(knife.get_effective_damage())
    sharpened_damage = float(knife_sharpened_damage())
    if current_damage <= 0 or current_damage >= sharpened_damage:
        return mask, None

    targets = []
    for pid in game_state.player_order:
        target = game_state.get_player(pid)
        if target is None or target is player or not target.is_alive():
            continue
        armor = getattr(target, "armor", None)
        active_armor = (
            list(armor.get_all_active())
            if armor is not None and callable(getattr(armor, "get_all_active", None))
            else []
        )
        if active_armor or getattr(target, "inner_defense", None):
            return mask, None
        targets.append(target)
    if not targets:
        return mask, None

    current_hits = sum(
        int(np.ceil(float(target.hp) / current_damage)) for target in targets
    )
    sharpened_hits = sum(
        int(np.ceil(float(target.hp) / sharpened_damage)) for target in targets
    )
    setup_actions = 1 if sharpen_action is not None else 2
    if sharpened_hits + setup_actions >= current_hits:
        return mask, None

    chosen_action = sharpen_action if sharpen_action is not None else buy_action
    kind = "sharpen" if sharpen_action is not None else "buy"
    focused = np.zeros_like(mask)
    focused[chosen_action] = True
    return focused, kind


def _survival_heal_skill_mask(mask, player, game_state):
    """Focus the survival expert on a heal that converts lethal into survivable."""
    if player is None or game_state is None:
        return mask, None
    heal_action = None
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            if idx_to_command(idx, player, game_state) == "interact 治疗":
                heal_action = idx
                break
        except Exception:
            continue
    if heal_action is None:
        return mask, None
    armor = getattr(player, "armor", None)
    active_armor = (
        list(armor.get_all_active())
        if armor is not None and callable(getattr(armor, "get_all_active", None))
        else []
    )
    if active_armor or getattr(player, "inner_defense", None):
        return mask, None
    threats = []
    for pid in game_state.player_order:
        target = game_state.get_player(pid)
        if (
            target is None or target is player or not target.is_alive()
            or target.location != player.location
            or not game_state.markers.has_relation(
                target.player_id, "ENGAGED_WITH", player.player_id,
            )
        ):
            continue
        damage = max((
            float(weapon.get_effective_damage())
            for weapon in (getattr(target, "weapons", []) or [])
            if weapon is not None
        ), default=0.0)
        if damage > 0:
            threats.append((damage, target.player_id))
    if not threats:
        return mask, None
    incoming, threat_id = max(threats)
    from engine.balance import get as balance_get
    heal_amount = float(balance_get("hospital", "heal_amount", default=6))
    if float(player.hp) <= incoming < float(player.hp) + heal_amount:
        focused = np.zeros_like(mask)
        focused[heal_action] = True
        return focused, threat_id
    return mask, None


def _postkill_recovery_skill_mask(mask, player, game_state):
    """Route a badly wounded killer through work -> hospital -> heal."""
    if player is None or game_state is None:
        return mask, None
    if int(getattr(player, "kill_count", 0) or 0) < 1:
        return mask, None
    hp = float(getattr(player, "hp", 0) or 0)
    max_hp = float(getattr(player, "max_hp", hp) or hp)
    if hp <= 0 or hp > 10 or hp >= max_hp:
        return mask, None

    by_command: dict[str, int] = {}
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            by_command[idx_to_command(idx, player, game_state)] = idx
        except Exception:
            continue
    from engine.balance import get as balance_get
    heal_cost = int(balance_get("hospital", "heal_cost", default=2) or 2)
    hospital_occupied = any(
        candidate is not player
        and candidate.is_alive()
        and getattr(candidate, "location", None) == "医院"
        for candidate in game_state.players.values()
    )
    if hospital_occupied:
        # 进医院会立刻面对面，届时移动和治疗都不合法；保留原 mask，
        # 让远程续战/护甲专家处理，而不是把低血策略送进死锁。
        return mask, None
    if int(getattr(player, "credits", 0) or 0) < heal_cost:
        desired = (
            "interact 打工"
            if getattr(player, "location", None) == "商店"
            else "move 商店"
        )
    else:
        desired = (
            "interact 治疗"
            if getattr(player, "location", None) == "医院"
            else "move 医院"
        )
    action = by_command.get(desired)
    if action is None:
        return mask, None
    focused = np.zeros_like(mask)
    focused[action] = True
    return focused, "postkill_recovery"


def _assassin_opening_prep_skill_mask(
        mask, player, game_state, require_armor=True):
    """Prepare armor and arrows after a double-magic detector handoff."""
    if player is None or game_state is None:
        return mask, None, False
    if int(getattr(player, "kill_count", 0) or 0) >= 1 or not player.is_alive():
        return mask, None, True

    by_command: dict[str, int] = {}
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            by_command[idx_to_command(idx, player, game_state)] = idx
        except Exception:
            continue
    armor = getattr(player, "armor", None)
    has_armor = bool(armor and armor.get_all_active())
    if require_armor and not has_armor:
        desired = (
            "move 商店"
            if getattr(player, "location", None) != "商店"
            else "interact 陶瓷护甲"
            if "interact 陶瓷护甲" in by_command
            else "interact 打工"
        )
        kind = "move_shop" if desired.startswith("move ") else (
            "buy_armor" if desired == "interact 陶瓷护甲" else "work"
        )
    elif int(getattr(player, "arrows", 0) or 0) <= 0:
        desired = (
            "move 商店"
            if getattr(player, "location", None) != "商店"
            else "interact 箭矢补给"
            if "interact 箭矢补给" in by_command
            else "interact 打工"
        )
        kind = "move_shop" if desired.startswith("move ") else (
            "supply" if desired == "interact 箭矢补给" else "work"
        )
    else:
        return mask, None, True

    action = by_command.get(desired)
    if action is None:
        return mask, None, False
    focused = np.zeros_like(mask)
    focused[action] = True
    return focused, kind, False


def _assassin_opening_prep_needed(
        player, game_state, require_armor=True) -> bool:
    """Detect the public double-magic-stealth state before or after detection."""
    if player is None or game_state is None or not player.is_alive():
        return False
    if int(getattr(player, "kill_count", 0) or 0) >= 1:
        return False
    armor = getattr(player, "armor", None)
    has_armor = bool(armor and armor.get_all_active())
    has_arrows = int(getattr(player, "arrows", 0) or 0) > 0
    prepared = has_arrows and (has_armor or not require_armor)
    if prepared:
        return False
    magic_hidden = sum(
        candidate is not player
        and candidate.is_alive()
        and "魔法" in set(getattr(candidate, "stealth_attrs", None) or set())
        for candidate in game_state.players.values()
    )
    return magic_hidden >= 2


def _postkill_recovery_budget_open(
        enabled: bool, completed: bool, used_actions: int,
        max_actions: int) -> bool:
    """Return whether the experimental recovery route may own this action."""
    return bool(
        enabled and not completed
        and (max_actions <= 0 or used_actions < max_actions)
    )


def _postkill_armor_recovery_skill_mask(mask, player, game_state):
    """Offer work/buy-armor actions for a low-credit post-kill armor route.

    The caller deliberately starts this route only when ``work`` is required.
    That leaves already-funded post-kill states untouched; they are an 86%
    second-kill stratum under s148 and are not the failure population.
    """
    if player is None or game_state is None:
        return mask, None
    if int(getattr(player, "kill_count", 0) or 0) < 1:
        return mask, None
    if getattr(player, "location", None) != "商店":
        return mask, None
    armor = getattr(player, "armor", None)
    if armor is not None and list(armor.get_all_active()):
        return mask, None
    has_armored_target = any(
        candidate is not player
        and candidate.is_alive()
        and getattr(candidate, "armor", None) is not None
        and bool(candidate.armor.get_all_active())
        for candidate in game_state.players.values()
    )
    if not has_armored_target:
        return mask, None

    by_command: dict[str, int] = {}
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            by_command[idx_to_command(idx, player, game_state)] = idx
        except Exception:
            continue
    desired = (
        "interact 陶瓷护甲"
        if "interact 陶瓷护甲" in by_command
        else "interact 打工"
    )
    action = by_command.get(desired)
    if action is None:
        return mask, None
    focused = np.zeros_like(mask)
    focused[action] = True
    return focused, "buy" if desired == "interact 陶瓷护甲" else "work"


def _postkill_funded_armor_skill_mask(mask, player, game_state):
    """Buy affordable armor in the critical no-shot post-kill entry state.

    Unlike the emergency survival route, this counterfactual deliberately does
    not require same-location engagement: the real failure stratum is taking
    ranged damage while both players keep moving. It is limited to at most 5 HP
    with no legal shot, never works for credits, and returns control immediately
    after the single purchase.
    """
    if player is None or game_state is None:
        return mask, None
    if int(getattr(player, "kill_count", 0) or 0) < 1:
        return mask, None
    if getattr(player, "location", None) != "商店":
        return mask, None
    if not 0 < int(getattr(player, "hp", 0) or 0) <= 5:
        return mask, None
    armor = getattr(player, "armor", None)
    if armor is not None and list(armor.get_all_active()):
        return mask, None
    has_armored_target = any(
        candidate is not player
        and candidate.is_alive()
        and getattr(candidate, "armor", None) is not None
        and bool(candidate.armor.get_all_active())
        for candidate in game_state.players.values()
    )
    if not has_armored_target:
        return mask, None

    armor_action = None
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            command = idx_to_command(idx, player, game_state)
        except Exception:
            continue
        if command.startswith("shoot "):
            return mask, None
        if command == "interact 陶瓷护甲":
            armor_action = idx
    if armor_action is not None:
        focused = np.zeros_like(mask)
        focused[armor_action] = True
        return focused, "postkill_funded_armor"
    return mask, None


def _opening_armor_recovery_skill_mask(mask, player, game_state):
    """Work for or buy ceramic armor in a natural pre-kill danger window.

    This is an opt-in causal probe for the failure state seen against long-game
    BasicAI lineups: the player is already at the shop, has no armor, is badly
    wounded, and has no legal immediate damage action.  It deliberately does
    not key on opponent personality, so a retained route would describe a
    reusable survival mechanism rather than memorizing ``political``.
    """
    if player is None or game_state is None:
        return mask, None
    if int(getattr(player, "kill_count", 0) or 0) != 0:
        return mask, None
    if getattr(player, "location", None) != "商店":
        return mask, None
    if not 0 < int(getattr(player, "hp", 0) or 0) <= 8:
        return mask, None
    armor = getattr(player, "armor", None)
    if armor is not None and list(armor.get_all_active()):
        return mask, None

    by_command: dict[str, int] = {}
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            command = idx_to_command(idx, player, game_state)
        except Exception:
            continue
        if command.startswith("attack ") or command.startswith("shoot "):
            return mask, None
        by_command[command] = idx

    desired = (
        "interact 陶瓷护甲"
        if "interact 陶瓷护甲" in by_command
        else "interact 打工"
    )
    action = by_command.get(desired)
    if action is None:
        return mask, None
    focused = np.zeros_like(mask)
    focused[action] = True
    kind = "buy" if desired == "interact 陶瓷护甲" else "work"
    return focused, f"opening_armor_{kind}"


def _opening_arrow_supply_skill_mask(mask, player, game_state):
    """Recall the graduated supply skill when the opening bow is empty.

    The route owns exactly one action, only when no immediate damage action is
    legal.  It therefore repairs the observed ``lock/work`` stall without
    delaying a shot or teaching a personality-specific shortcut.
    """
    if player is None or game_state is None:
        return mask, None
    if int(getattr(player, "kill_count", 0) or 0) != 0:
        return mask, None
    if getattr(player, "location", None) != "商店":
        return mask, None
    if not getattr(player, "has_weapon", lambda _name: False)("弓"):
        return mask, None
    if int(getattr(player, "arrows", 0) or 0) > 0:
        return mask, None

    supply_action = None
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            command = idx_to_command(idx, player, game_state)
        except Exception:
            continue
        if command.startswith("attack ") or command.startswith("shoot "):
            return mask, None
        if command == "interact 箭矢补给":
            supply_action = idx
    if supply_action is None:
        return mask, None
    focused = np.zeros_like(mask)
    focused[supply_action] = True
    return focused, "opening_arrow_supply"


def _postkill_melee_skill_mask(mask, player, game_state):
    """Hand post-kill contact establishment and melee to the base policy."""
    if player is None or game_state is None:
        return mask, None, None
    if int(getattr(player, "kill_count", 0) or 0) < 1:
        return mask, None, None
    candidates: list[tuple[int, str, int]] = []
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            command = idx_to_command(idx, player, game_state)
        except Exception:
            continue
        if not command.startswith("attack ") or not command.endswith(" 小刀"):
            continue
        target_name = command[len("attack "):-len(" 小刀")]
        target = next(
            (
                candidate for candidate in game_state.players.values()
                if candidate is not player and candidate.is_alive()
                and candidate.name == target_name
            ),
            None,
        )
        if target is not None:
            target_hp = int(getattr(target, "hp", 0) or 0)
            player_hp = int(getattr(player, "hp", 0) or 0)
            if target_hp <= 12 and player_hp >= target_hp:
                candidates.append((target_hp, target.player_id, idx))
    if not candidates:
        for raw_idx in np.flatnonzero(mask):
            idx = int(raw_idx)
            try:
                command = idx_to_command(idx, player, game_state)
            except Exception:
                continue
            if not command.startswith("find "):
                continue
            target_name = command[len("find "):]
            target = next(
                (
                    candidate for candidate in game_state.players.values()
                    if candidate is not player and candidate.is_alive()
                    and candidate.name == target_name
                    and candidate.location == player.location
                ),
                None,
            )
            if target is not None:
                target_hp = int(getattr(target, "hp", 0) or 0)
                player_hp = int(getattr(player, "hp", 0) or 0)
                if target_hp <= 12 and player_hp >= target_hp:
                    candidates.append((target_hp, target.player_id, idx))
        kind = "find"
    else:
        kind = "melee"
    if not candidates:
        return mask, None, None
    _, target_id, action = min(candidates)
    focused = np.zeros_like(mask)
    focused[action] = True
    return focused, target_id, kind


def _survival_armor_skill_mask(mask, player, game_state):
    """Focus on ceramic armor only when it changes the next hit from lethal."""
    if player is None or game_state is None:
        return mask, None
    armor_action = None
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            if idx_to_command(int(idx), player, game_state) == "interact 陶瓷护甲":
                armor_action = idx
                break
        except Exception:
            continue
    if armor_action is None:
        return mask, None
    armor = getattr(player, "armor", None)
    active_armor = (
        list(armor.get_all_active())
        if armor is not None and callable(getattr(armor, "get_all_active", None))
        else []
    )
    if active_armor or getattr(player, "inner_defense", None):
        return mask, None
    from engine.balance import get as balance_get
    defenses = balance_get(
        "armor", "陶瓷护甲", "defense", default={},
    ) or {}
    candidates = []
    for pid in game_state.player_order:
        target = game_state.get_player(pid)
        if (
            target is None or target is player or not target.is_alive()
            or target.location != player.location
            or not game_state.markers.has_relation(
                target.player_id, "ENGAGED_WITH", player.player_id,
            )
        ):
            continue
        for weapon in (getattr(target, "weapons", []) or []):
            if weapon is None:
                continue
            raw = float(weapon.get_effective_damage())
            attr_obj = getattr(weapon, "attribute", None)
            attr = getattr(attr_obj, "value", str(attr_obj or "普通"))
            defense = float(defenses.get(attr, 0) or 0)
            mitigated = max(raw * 0.25, raw - defense)
            if raw >= float(player.hp) and mitigated < float(player.hp):
                candidates.append((raw, target.player_id))
    if candidates:
        focused = np.zeros_like(mask)
        focused[armor_action] = True
        return focused, max(candidates)[1]
    return mask, None


def _survival_repair_skill_mask(mask, player, game_state):
    """Repair only when current durability cannot cover the lethal exchange."""
    if player is None or game_state is None:
        return mask, None
    repair_action = None
    by_command: dict[str, int] = {}
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            command = idx_to_command(idx, player, game_state)
        except Exception:
            continue
        by_command[command] = idx
        if command == "interact 修理陶瓷护甲":
            repair_action = idx
    if repair_action is None:
        return mask, None

    armor = getattr(player, "armor", None)
    pieces = (
        list(armor.get_all_active())
        if armor is not None and callable(getattr(armor, "get_all_active", None))
        else []
    )
    ceramic = next((piece for piece in pieces if piece.name == "陶瓷护甲"), None)
    if ceramic is None or float(getattr(ceramic, "durability", 0) or 0) <= 0:
        return mask, None

    from engine.balance import get as balance_get
    spec = balance_get("armor", "陶瓷护甲", default={}) or {}
    defenses = spec.get("defense", {}) or {}
    repair_amount = float(spec.get("repair_amount", 6) or 6)
    current_durability = float(getattr(ceramic, "durability", 0) or 0)
    max_durability = float(getattr(ceramic, "max_durability", 0) or 0)

    def survives(hp: float, durability: float, hits: int, raw: float, defense: float) -> bool:
        mitigated = max(raw * 0.25, raw - defense)
        wear = max(0.0, raw - mitigated)
        for _ in range(max(0, hits)):
            if durability > 0:
                hp -= mitigated
                durability = max(0.0, durability - wear)
            else:
                hp -= raw
            if hp <= 0:
                return False
        return True

    candidates: list[tuple[float, str]] = []
    for pid in game_state.player_order:
        target = game_state.get_player(pid)
        if (
            target is None or target is player or not target.is_alive()
            or target.location != player.location
            or not game_state.markers.has_relation(
                target.player_id, "ENGAGED_WITH", player.player_id,
            )
        ):
            continue
        target_armor = getattr(target, "armor", None)
        if target_armor is not None and target_armor.get_all_active():
            continue
        attacks = [
            float(weapon.get_effective_damage())
            for weapon in (getattr(player, "weapons", []) or [])
            if weapon is not None
            and f"attack {target.name} {weapon.name}" in by_command
        ]
        incoming_weapons = [
            weapon for weapon in (getattr(target, "weapons", []) or [])
            if weapon is not None
        ]
        if not attacks or not incoming_weapons:
            continue
        best_attack = max(attacks)
        hits_to_kill = int(np.ceil(float(target.hp) / best_attack))
        incoming = max(float(w.get_effective_damage()) for w in incoming_weapons)
        attr_obj = getattr(max(
            incoming_weapons, key=lambda w: float(w.get_effective_damage())
        ), "attribute", None)
        attr = getattr(attr_obj, "value", str(attr_obj or "普通"))
        defense = float(defenses.get(attr, 0) or 0)
        repaired = min(max_durability, current_durability + repair_amount)
        hp = float(player.hp)
        without_repair = survives(
            hp, current_durability, hits_to_kill - 1, incoming, defense,
        )
        with_repair = survives(
            hp, repaired, hits_to_kill, incoming, defense,
        )
        if not without_repair and with_repair:
            candidates.append((incoming, target.player_id))
    if candidates:
        focused = np.zeros_like(mask)
        focused[repair_action] = True
        return focused, max(candidates)[1]
    return mask, None


def _pp_damage_skill_mask(mask, player, game_state):
    """Focus the PP expert only on an unambiguous +1 lethal conversion.

    This is deliberately narrower than a generic damage calculator: armored or
    non-engaged targets remain owned by the base policy until a later course can
    represent their full settlement semantics without guessing.
    """
    if player is None or game_state is None:
        return mask, None
    from engine.balance import get as balance_get

    armed_bonus = int(getattr(player, "_m9_pp_damage_bonus", 0) or 0)
    pp = getattr(game_state, "m9_pp", None)
    balance = int(pp.balance(player.player_id)) if pp is not None else 0
    available_bonus = int(balance_get(
        "m9_system", "pp", "bonus_damage", default=1,
    ))
    candidates: list[tuple[float, int, str]] = []
    pp_action = None
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            command = idx_to_command(idx, player, game_state)
        except Exception:
            continue
        if command == "special PP加伤":
            pp_action = idx
            continue
        if not command.startswith("attack "):
            continue
        parts = command.split(" ", 2)
        if len(parts) != 3:
            continue
        target = next((
            game_state.get_player(pid)
            for pid in game_state.player_order
            if game_state.get_player(pid) is not None
            and game_state.get_player(pid).name == parts[1]
        ), None)
        weapon = player.get_weapon(parts[2])
        if target is None or weapon is None or not target.is_alive():
            continue
        if target.location != player.location:
            continue
        if not game_state.markers.has_relation(
            player.player_id, "ENGAGED_WITH", target.player_id,
        ):
            continue
        armor = getattr(target, "armor", None)
        active_armor = (
            list(armor.get_all_active())
            if armor is not None and callable(getattr(armor, "get_all_active", None))
            else []
        )
        if active_armor or getattr(target, "inner_defense", None):
            continue
        damage = float(weapon.get_effective_damage())
        hp = float(target.hp)
        bonus = armed_bonus or available_bonus
        if damage < hp <= damage + bonus:
            candidates.append((hp, idx, target.player_id))

    if armed_bonus > 0 and candidates:
        chosen = min(candidates)
        focused = np.zeros_like(mask)
        focused[chosen[1]] = True
        return focused, chosen[2]
    if armed_bonus <= 0 and balance >= 1 and pp_action is not None and candidates:
        focused = np.zeros_like(mask)
        focused[pp_action] = True
        return focused, min(candidates)[2]
    return mask, None


def _kill_threshold(course_scenario: str | None) -> int:
    return 2 if course_scenario in {
        "combat_multi_quarter_hp", "combat_multi_half_hp", "combat_multi_full_hp",
        "combat_retarget", "combat_retarget_static",
        "economy_pursuit_supply", "economy_armored_pursuit_supply",
        "economy_armored_pursuit_supply_slot0",
        "equipment_fire_antiarmor",
        "equipment_bow_loadout",
        "survival_heal_counterattack", "survival_armor_counterattack",
        "survival_armor_repair",
        "equipment_sharpen_chain",
        "visibility_thermal_chain", "visibility_magic_chain",
        "visibility_tech_chain",
    } else 1


def _use_continuation_policy(kill_count: int, threshold: int) -> bool:
    """Return whether the post-kill policy owns the current decision."""
    if threshold < 1:
        raise ValueError("continuation threshold must be at least 1")
    return int(kill_count) >= threshold


def _postkill_entry_signature(mask, player, game_state) -> str:
    """Summarize the first normal-action state after the policy's first kill."""
    legal_commands = (
        idx_to_command(int(index), player, game_state)
        for index in np.flatnonzero(mask)
    )
    commands = tuple(legal_commands)
    hp = int(getattr(player, "hp", 0) or 0)
    hp_bucket = (
        "0-5" if hp <= 5 else "6-10" if hp <= 10
        else "11-15" if hp <= 15 else "16+"
    )
    arrows = int(getattr(player, "arrows", 0) or 0)
    arrow_bucket = "0" if arrows <= 0 else "1-2" if arrows <= 2 else "3+"
    credits = int(getattr(player, "credits", 0) or 0)
    credit_bucket = "0" if credits <= 0 else "1" if credits == 1 else "2+"
    has_weapon = getattr(player, "has_weapon", lambda _name: False)
    armor = getattr(player, "armor", None)
    has_armor = bool(armor and armor.get_all_active())
    return ";".join((
        f"bow={int(bool(has_weapon('弓')))}",
        f"knife={int(bool(has_weapon('小刀')))}",
        f"armor={int(has_armor)}",
        f"attack={int(any(cmd.startswith('attack ') for cmd in commands))}",
        f"shoot={int(any(cmd.startswith('shoot ') for cmd in commands))}",
        f"armor_buy={int('interact 陶瓷护甲' in commands)}",
        f"arrows={arrow_bucket}",
        f"credits={credit_bucket}",
        f"hp={hp_bucket}",
        f"loc={getattr(player, 'location', None)}",
    ))


def _postkill_trace_state(command: str, mask, player, game_state) -> str:
    """Compact pre-action snapshot for explaining a failed continuation."""
    alive = [
        candidate for candidate in game_state.players.values()
        if candidate is not player and candidate.is_alive()
    ]
    target = min(
        alive,
        key=lambda candidate: (
            int(getattr(candidate, "hp", 0) or 0), candidate.player_id,
        ),
        default=None,
    )
    if target is None:
        target_text = "none"
    else:
        armor = getattr(target, "armor", None)
        legal_commands = []
        legal_detectors = []
        for raw_idx in np.flatnonzero(mask):
            try:
                legal_command = idx_to_command(
                    int(raw_idx), player, game_state,
                )
            except Exception:
                continue
            if legal_command in {
                f"shoot {target.name}", f"lock {target.name}",
                f"find {target.name}", f"move {target.location}",
            } or legal_command.startswith(f"attack {target.name} "):
                legal_commands.append(_verb(legal_command))
            if legal_command in set(_DETECTION_COMMANDS.values()):
                legal_detectors.append(legal_command.removeprefix("interact "))
        legal_text = "+".join(sorted(set(legal_commands))) or "none"
        detector_text = "+".join(sorted(set(legal_detectors))) or "none"
        stealth_text = "+".join(sorted(
            str(value) for value in (
                getattr(target, "stealth_attrs", None) or set()
            )
        )) or "none"
        target_text = (
            f"{target.player_id}:hp{int(getattr(target, 'hp', 0) or 0)}"
            f":{getattr(target, 'location', None)}"
            f":armor{int(bool(armor and armor.get_all_active()))}"
            f":inv{int(bool(getattr(target, 'is_invisible', False)))}"
            f":stealth[{stealth_text}]"
            f":legal[{legal_text}]"
            f":det[{detector_text}]"
        )
    station = getattr(game_state, "m9_police", None)
    wanted = station.open_wanted() if station is not None else None
    captain = getattr(station, "captain_id", None) if station is not None else None
    crimes = getattr(getattr(game_state, "police", None), "crime_records", {})
    crime_count = len(crimes.get(player.player_id, []) or [])
    pp = getattr(game_state, "m9_pp", None)
    pp_balance = int(pp.balance(player.player_id)) if pp is not None else 0
    return (
        f"r{int(getattr(game_state, 'current_round', 0) or 0)}"
        f"/hp{int(getattr(player, 'hp', 0) or 0)}"
        f"/c{int(getattr(player, 'credits', 0) or 0)}"
        f"/a{int(getattr(player, 'arrows', 0) or 0)}"
        f"/pp{pp_balance}/crime{crime_count}"
        f"/captain[{captain or 'none'}]"
        f"/wanted[{getattr(wanted, 'suspect_id', None) or 'none'}]"
        f"/detect[{'+'.join(sorted(str(value) for value in (getattr(player, 'detection_attrs', None) or set()))) or 'none'}]"
        f"/{getattr(player, 'location', None)}"
        f"/{target_text}/{command}"
    )


def _target_commitment_mask(mask, player, game_state):
    """Keep a post-kill policy focused on one live opponent when possible.

    Engagement and lock relations win over HP/slot order.  The mask does not
    prescribe a single command: the policy still chooses among arming, chasing,
    finding, locking, shooting and attacking the committed target.  If none of
    those actions is currently legal, the original mask is returned.
    """
    from rl.action_space import get_opponent_slots

    alive = [
        (slot, target)
        for slot, target in enumerate(get_opponent_slots(player, game_state))
        if target is not None and target.is_alive()
    ]
    if not alive:
        return mask, None

    markers = game_state.markers

    def target_rank(entry):
        slot, target = entry
        engaged = markers.has_relation(
            player.player_id, "ENGAGED_WITH", target.player_id,
        )
        locked = markers.has_relation(
            target.player_id, "LOCKED_BY", player.player_id,
        )
        relation_rank = 0 if engaged else 1 if locked else 2
        return relation_rank, float(getattr(target, "hp", 999.0)), slot

    _, target = min(alive, key=target_rank)
    target_name = str(target.name)
    focused = np.zeros_like(mask, dtype=bool)
    needs_knife = not player.has_weapon("小刀")
    target_is_locked = markers.has_relation(
        target.player_id, "LOCKED_BY", player.player_id,
    )
    for idx in np.flatnonzero(mask):
        try:
            cmd = idx_to_command(int(idx), player, game_state)
        except Exception:
            continue
        if (
            cmd == "wake"
            or (needs_knife and cmd == "interact 小刀")
            or cmd == f"move {target.location}"
            or (not target_is_locked and cmd == f"lock {target_name}")
            or cmd == f"find {target_name}"
            or cmd == f"shoot {target_name}"
            or cmd.startswith(f"attack {target_name} ")
        ):
            focused[int(idx)] = True
    if focused.any():
        return focused, target_name
    return mask, target_name


def _captain_target_commitment_mask(mask, player, game_state):
    """Focus combat progress on a living enemy captain, if one exists."""
    if player is None or game_state is None:
        return mask, None
    station = getattr(game_state, "m9_police", None)
    captain_id = getattr(station, "captain_id", None) if station is not None else None
    if captain_id in (None, player.player_id):
        return mask, None
    target = game_state.get_player(captain_id)
    if target is None or not target.is_alive():
        return mask, None

    focused = np.zeros_like(mask, dtype=bool)
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        try:
            command = idx_to_command(idx, player, game_state)
        except Exception:
            continue
        if (
            command == f"move {target.location}"
            or command == f"lock {target.name}"
            or command == f"find {target.name}"
            or command == f"shoot {target.name}"
            or command.startswith(f"attack {target.name} ")
        ):
            focused[idx] = True
    if focused.any():
        return focused, target.player_id
    return mask, None


def _blend_commitment_probabilities(
    full_probabilities: np.ndarray,
    focused_probabilities: np.ndarray,
    strength: float,
) -> np.ndarray:
    """Blend flat-policy and target-conditioned distributions."""
    if full_probabilities.shape != focused_probabilities.shape:
        raise ValueError("commitment probability shapes must match")
    if not 0.0 <= strength <= 1.0:
        raise ValueError("commitment strength must be in [0, 1]")
    return (
        (1.0 - strength) * full_probabilities
        + strength * focused_probabilities
    )


def _predict_with_commitment_prior(
    model,
    obs,
    state,
    episode_start,
    full_mask,
    focused_mask,
    strength: float,
):
    """Predict once while keeping recurrent state independent of the prior."""
    if strength <= 0.0 or np.array_equal(full_mask, focused_mask):
        return model.predict(
            obs[None], state=state, episode_start=episode_start,
            action_masks=full_mask[None], deterministic=True,
        )
    if strength >= 1.0:
        return model.predict(
            obs[None], state=state, episode_start=episode_start,
            action_masks=focused_mask[None], deterministic=True,
        )

    import torch

    policy = model.policy
    policy.set_training_mode(False)
    obs_tensor, _ = policy.obs_to_tensor(obs[None])
    if state is None:
        hidden = np.zeros(policy.lstm_hidden_state_shape, dtype=np.float32)
        state = (hidden, hidden.copy())
    torch_state = tuple(
        torch.as_tensor(value, dtype=torch.float32, device=policy.device)
        for value in state
    )
    starts = torch.as_tensor(
        episode_start, dtype=torch.float32, device=policy.device,
    )
    with torch.no_grad():
        full_distribution, next_state = policy.get_distribution(
            obs_tensor, torch_state, starts, action_masks=full_mask[None],
        )
        # ``policy.action_dist`` is reused in-place by the next call; copy the
        # full probabilities before focused masking mutates that object.
        full_probs = full_distribution.distribution.probs.detach().cpu().numpy()[0].copy()
        focused_distribution, _ = policy.get_distribution(
            obs_tensor, torch_state, starts, action_masks=focused_mask[None],
        )
        focused_probs = (
            focused_distribution.distribution.probs.detach().cpu().numpy()[0].copy()
        )
    blended = _blend_commitment_probabilities(
        full_probs, focused_probs, strength,
    )
    action = np.asarray([int(np.argmax(blended))], dtype=np.int64)
    numpy_state = tuple(value.cpu().numpy() for value in next_state)
    return action, numpy_state


def run(model_path: str | None, episodes: int, max_rounds: int,
        seed: int, use_random: bool, use_oracle: bool = False,
        ablate_home: bool = False, opponent_bot: str = "dummy",
        num_opponents: int = 1, full_actions: bool = False,
        course_scenario: str | None = None,
        opponent_bots: list[str] | None = None,
        continuation_model_path: str | None = None,
        continuation_after_kills: int = 1,
        warm_continuation: bool = True,
        target_commitment: bool = False,
        target_commitment_strength: float = 1.0,
        economy_actions: bool = False,
        pp_model_path: str | None = None,
        survival_model_path: str | None = None,
        repair_model_path: str | None = None,
        equipment_model_path: str | None = None,
        visibility_model_path: str | None = None,
        continuation_contract: str | None = None,
        base_contract: str | None = None,
        postkill_recovery: bool = False,
        attribute_model_path: str | None = None,
        police_model_path: str | None = None,
        postkill_recovery_max_actions: int = 0,
        postkill_armor_recovery: bool = False,
        postkill_melee_router: bool = False,
        postkill_funded_armor: bool = False,
        antiarmor_model_path: str | None = None,
        bow_loadout_model_path: str | None = None,
        opponent_personalities: list[str] | None = None,
        assassin_opening_prep: bool = False,
        opening_armor_recovery: bool = False,
        opening_arrow_supply: bool = False,
        precombat_police_election: bool = False,
        assassin_opening_supply_only: bool = False,
        captain_target_priority: bool = False,
        lowhp_antiarmor: bool = False) -> dict:
    set_profile(PROFILE)
    model = None
    continuation_model = None
    pp_model = None
    survival_model = None
    repair_model = None
    equipment_model = None
    visibility_model = None
    attribute_model = None
    police_model = None
    antiarmor_model = None
    bow_loadout_model = None
    if not (use_random or use_oracle):
        from rl.maskable_recurrent import MaskableRecurrentPPO
        model = MaskableRecurrentPPO.load(model_path, device="cpu")
        if continuation_model_path:
            continuation_model = MaskableRecurrentPPO.load(
                continuation_model_path, device="cpu",
            )
        if pp_model_path:
            pp_model = MaskableRecurrentPPO.load(pp_model_path, device="cpu")
        if survival_model_path:
            survival_model = MaskableRecurrentPPO.load(
                survival_model_path, device="cpu",
            )
        if repair_model_path:
            repair_model = MaskableRecurrentPPO.load(
                repair_model_path, device="cpu",
            )
        if equipment_model_path:
            equipment_model = MaskableRecurrentPPO.load(
                equipment_model_path, device="cpu",
            )
        if visibility_model_path:
            visibility_model = MaskableRecurrentPPO.load(
                visibility_model_path, device="cpu",
            )
        if attribute_model_path:
            attribute_model = MaskableRecurrentPPO.load(
                attribute_model_path, device="cpu",
            )
        if police_model_path:
            police_model = MaskableRecurrentPPO.load(
                police_model_path, device="cpu",
            )
        if antiarmor_model_path:
            antiarmor_model = MaskableRecurrentPPO.load(
                antiarmor_model_path, device="cpu",
            )
        if bow_loadout_model_path:
            bow_loadout_model = MaskableRecurrentPPO.load(
                bow_loadout_model_path, device="cpu",
            )

    rng = np.random.default_rng(seed)
    verbs: collections.Counter = collections.Counter()
    commands: collections.Counter = collections.Counter()
    pre_kill_verbs: collections.Counter = collections.Counter()
    post_kill_verbs: collections.Counter = collections.Counter()
    postkill_entry_states: collections.Counter = collections.Counter()
    postkill_entry_fields: collections.Counter = collections.Counter()
    postkill_entry_success_fields: collections.Counter = collections.Counter()
    postkill_entry_outcome_fields: collections.Counter = collections.Counter()
    postkill_entry_outcome_states: collections.Counter = collections.Counter()
    postkill_outcomes: collections.Counter = collections.Counter()
    postkill_failure_target_states: collections.Counter = collections.Counter()
    postkill_failure_traces: list[dict[str, object]] = []
    opening_failure_traces: list[dict[str, object]] = []
    opponent_lineups: collections.Counter = collections.Counter()
    lineup_official_wins: collections.Counter = collections.Counter()
    lineup_first_kills: collections.Counter = collections.Counter()
    lineup_continuation_successes: collections.Counter = collections.Counter()
    opening_outcomes: collections.Counter = collections.Counter()
    pre_kill_reward = post_kill_reward = 0.0
    episode_reward_ledger: collections.Counter = collections.Counter()
    wins = official_wins = official_settled = kills = chains = 0
    at_least_one_kill = at_least_two_kills = 0
    forced_forfeits = voluntary_forfeits = 0
    voluntary_forfeit_oracle_labels: collections.Counter = collections.Counter()
    oracle_forfeit_legal_signatures: collections.Counter = collections.Counter()
    oracle_forfeit_index_signatures: collections.Counter = collections.Counter()
    choose_situations: collections.Counter = collections.Counter()
    policy_phases: collections.Counter = collections.Counter()
    bow_loadout_skill_commands: collections.Counter = collections.Counter()
    commitment_targets: collections.Counter = collections.Counter()
    commitment_filtered_steps = 0
    pp_skill_steps = 0
    survival_skill_steps = 0
    equipment_skill_steps = 0
    visibility_skill_steps = 0
    attribute_skill_steps = 0
    police_skill_steps = 0
    antiarmor_skill_steps = 0
    bow_loadout_skill_steps = 0
    rounds: list[int] = []
    sample_chain: list[str] = []

    with contextlib.redirect_stdout(io.StringIO()):
        env = BadtimeWarEnv(num_opponents=num_opponents, max_rounds=max_rounds,
                            enable_talents=False, rl_talent=0,
                            opponent_bots=(
                                opponent_bots
                                if opponent_bots is not None
                                else (None if opponent_bot == "basic_ai"
                                      else [opponent_bot] * num_opponents)
                            ),
                            opponent_personalities=opponent_personalities,
                            action_contract=(
                                None if full_actions
                                else "economy_course"
                                if course_scenario in {
                                    "economy_bow_supply", "economy_pursuit_supply",
                                    "economy_armored_pursuit_supply",
                                    "economy_armored_pursuit_supply_slot0",
                                }
                                else "pp_course"
                                if course_scenario in {
                                    "pp_damage_finish", "pp_damage_choice",
                                }
                                else "survival_course"
                                if course_scenario in {
                                    "survival_heal_counterattack",
                                    "survival_armor_counterattack",
                                    "survival_armor_repair",
                                }
                                else "equipment_course"
                                if course_scenario in {
                                    "equipment_sharpen_chain",
                                    "equipment_fire_antiarmor",
                                    "equipment_bow_loadout",
                                    "visibility_thermal_chain",
                                    "visibility_magic_chain",
                                    "visibility_tech_chain",
                                    "attribute_magic_armor_choice",
                                    "attribute_tech_armor_choice",
                                }
                                else "combat_economy" if economy_actions
                                else None if full_actions else "combat_core"
                            ),
                            course_scenario=course_scenario,
                            enable_bow_context=bool(bow_loadout_model_path))
        # 引擎保持 full-M9 mask 供条件专家读取；基础合同只负责基础策略的
        # 推理子空间，同时复用课程环境的中性 choose 时序语义。
        env._rl_base_action_contract = base_contract
        for ep in range(episodes):
            episode_seed = seed + 10999 + ep
            # Badtime War 的 BasicAI 人格和若干合法随机分支使用 Python
            # 全局 random，而 Gym reset 只播种 env.np_random。逐局同步播种，
            # 才能让相同 --seed 的基线/候选真正面对同一批对手。
            random.seed(episode_seed)
            np.random.seed(episode_seed)
            obs, _ = env.reset(seed=episode_seed)
            episode_lineup = "+".join(sorted(
                str(getattr(player.controller, "personality", "?"))
                for player in env._state.players.values()
                if player.player_id != "rl_0"
            )) or "none"
            opponent_lineups[episode_lineup] += 1
            state = None
            continuation_state = None
            pp_state = None
            survival_state = None
            repair_state = None
            equipment_state = None
            visibility_state = None
            attribute_state = None
            police_state = None
            antiarmor_state = None
            bow_loadout_state = None
            bow_loadout_route_actions = 0
            episode_start = np.array([True])
            done = False
            postkill_entry_recorded = False
            postkill_entry_signature = None
            postkill_recovery_actions = 0
            postkill_recovery_completed = False
            postkill_armor_recovery_active = False
            postkill_armor_recovery_completed = False
            postkill_armor_recovery_actions = 0
            assassin_prep_active = False
            info: dict = {}
            trace: list[str] = []
            opening_trace: list[str] = []
            postkill_trace: list[str] = []
            while not done:
                selected_postkill_recovery = False
                selected_postkill_armor_recovery = False
                selected_bow_loadout = False
                selected_visibility = False
                mask = env.action_masks()
                if (
                    (assassin_opening_prep or assassin_opening_supply_only)
                    and not assassin_prep_active
                    and not env._choose_mode
                    and _assassin_opening_prep_needed(
                        env._rl_player, env._state,
                        require_armor=not assassin_opening_supply_only,
                    )
                ):
                    # Prepare before taking the detector detour whenever the
                    # double-magic threat is already public.  This avoids the
                    # old magic-facility -> shop -> magic-facility round trip.
                    assassin_prep_active = True
                if ablate_home:
                    # 反事实：屏蔽 home move 段 ≈ 回到修复前的动作空间。
                    # dummy 全程蹲在自家 home，3 支箭打不穿 20HP，所以缺了这段
                    # 追击槽，move→find→attack 链在物理上就闭合不了（实测归零）。
                    from rl.action_space import IDX_MOVE_HOME_M9_BASE
                    mask = mask.copy()
                    mask[IDX_MOVE_HOME_M9_BASE:] = False
                    if not mask.any():
                        mask[0] = True
                if use_oracle:
                    action = (
                        int(np.flatnonzero(mask)[0])
                        if env._choose_mode
                        else _economy_supply_oracle_action(
                            mask, env._rl_player, env._state,
                        )
                        if course_scenario == "economy_bow_supply"
                        else _economy_pursuit_oracle_action(
                            mask, env._rl_player, env._state,
                        )
                        if course_scenario in {
                            "economy_pursuit_supply",
                            "economy_armored_pursuit_supply",
                            "economy_armored_pursuit_supply_slot0",
                        }
                        else _fire_antiarmor_oracle_action(
                            mask, env._rl_player, env._state,
                        )
                        if course_scenario == "equipment_fire_antiarmor"
                        else _bow_loadout_oracle_action(
                            mask, env._rl_player, env._state,
                        )
                        if course_scenario == "equipment_bow_loadout"
                        else _pp_damage_oracle_action(
                            mask, env._rl_player, env._state,
                        )
                        if course_scenario in {
                            "pp_damage_finish", "pp_damage_choice",
                        }
                        else _survival_heal_oracle_action(
                            mask, env._rl_player, env._state,
                        )
                        if course_scenario in {
                            "survival_heal_counterattack",
                            "survival_armor_counterattack",
                            "survival_armor_repair",
                        }
                                else _sharpen_oracle_action(
                                    mask, env._rl_player, env._state,
                                )
                                if course_scenario == "equipment_sharpen_chain"
                                else _detection_oracle_action(
                                    mask, env._rl_player, env._state,
                                )
                                if course_scenario in {
                                    "visibility_thermal_chain",
                                    "visibility_magic_chain",
                                    "visibility_tech_chain",
                                }
                                else _attribute_armor_oracle_action(
                                    mask, env._rl_player, env._state,
                                )
                                if course_scenario in {
                                    "attribute_magic_armor_choice",
                                    "attribute_tech_armor_choice",
                                }
                                else _oracle_action(mask, env._rl_player, env._state)
                    )
                elif model is None:
                    action = int(rng.choice(np.flatnonzero(mask)))
                else:
                    old_policy_obs = _old_policy_observation(
                        obs,
                        bool(bow_loadout_model_path)
                        or course_scenario == "equipment_bow_loadout",
                    )
                    use_continuation = _use_continuation_policy(
                        getattr(env._rl_player, "kill_count", 0) or 0,
                        continuation_after_kills,
                    )
                    base_mask = (
                        apply_action_contract(
                            mask, base_contract,
                            choose_mode=env._choose_mode,
                        )
                        if base_contract is not None
                        else mask
                    )
                    policy_mask = base_mask
                    captain_commitment_target = None
                    postkill_melee_target = None
                    postkill_melee_kind = None
                    if (
                        postkill_melee_router and use_continuation
                        and not env._choose_mode
                    ):
                        melee_mask, postkill_melee_target, postkill_melee_kind = (
                            _postkill_melee_skill_mask(
                                base_mask, env._rl_player, env._state,
                            )
                        )
                        if postkill_melee_target is not None:
                            policy_mask = melee_mask
                    if (
                        captain_target_priority
                        and postkill_melee_target is None
                        and not env._choose_mode
                    ):
                        policy_mask, captain_commitment_target = (
                            _captain_target_commitment_mask(
                                base_mask, env._rl_player, env._state,
                            )
                        )
                        if captain_commitment_target is not None:
                            commitment_targets[captain_commitment_target] += 1
                            commitment_filtered_steps += 1
                    if (
                        target_commitment and use_continuation
                        and postkill_melee_target is None
                        and captain_commitment_target is None
                        and not env._choose_mode
                    ):
                        policy_mask, commitment_target = _target_commitment_mask(
                            base_mask, env._rl_player, env._state,
                        )
                        if commitment_target is not None:
                            commitment_targets[commitment_target] += 1
                        if np.count_nonzero(policy_mask) < np.count_nonzero(mask):
                            commitment_filtered_steps += 1
                    act, state = _predict_with_commitment_prior(
                        model, old_policy_obs, state, episode_start,
                        base_mask, policy_mask, target_commitment_strength,
                    )
                    continuation_act = None
                    if continuation_model is not None and (
                        warm_continuation or use_continuation
                    ):
                        continuation_episode_start = (
                            episode_start
                            if warm_continuation or continuation_state is not None
                            else np.array([True])
                        )
                        continuation_mask = (
                            apply_action_contract(
                                mask, continuation_contract,
                                choose_mode=env._choose_mode,
                            )
                            if continuation_contract is not None
                            else mask
                        )
                        continuation_focus_mask = continuation_mask
                        if (
                            (target_commitment and use_continuation)
                            or captain_commitment_target is not None
                        ):
                            combined = continuation_mask & policy_mask
                            if combined.any():
                                continuation_focus_mask = combined
                        continuation_act, continuation_state = (
                            _predict_with_commitment_prior(
                                continuation_model, old_policy_obs, continuation_state,
                                continuation_episode_start,
                                continuation_mask, continuation_focus_mask,
                                target_commitment_strength,
                            )
                        )
                    pp_act = None
                    pp_target = None
                    if pp_model is not None:
                        pp_mask, pp_target = _pp_damage_skill_mask(
                            mask, env._rl_player, env._state,
                        ) if not env._choose_mode else (mask, None)
                        pp_act, pp_state = _predict_with_commitment_prior(
                            pp_model, old_policy_obs, pp_state, episode_start,
                            mask, pp_mask, 1.0,
                        )
                    survival_act = None
                    survival_threat = None
                    survival_kind = None
                    if survival_model is not None:
                        survival_mask = mask
                        assassin_prep_done = False
                        if assassin_prep_active and not env._choose_mode:
                            (
                                survival_mask,
                                assassin_prep_kind,
                                assassin_prep_done,
                            ) = _assassin_opening_prep_skill_mask(
                                mask, env._rl_player, env._state,
                                require_armor=not assassin_opening_supply_only,
                            )
                            if assassin_prep_kind is not None:
                                survival_threat = "assassin_opening_prep"
                                survival_kind = (
                                    f"assassin_prep_{assassin_prep_kind}"
                                )
                            elif assassin_prep_done:
                                assassin_prep_active = False
                        if (
                            survival_threat is None
                            and opening_armor_recovery
                            and not env._choose_mode
                        ):
                            survival_mask, survival_threat = (
                                _opening_armor_recovery_skill_mask(
                                    mask, env._rl_player, env._state,
                                )
                            )
                            if survival_threat is not None:
                                survival_kind = survival_threat
                        if (
                            survival_threat is None
                            and opening_arrow_supply
                            and not env._choose_mode
                        ):
                            survival_mask, survival_threat = (
                                _opening_arrow_supply_skill_mask(
                                    mask, env._rl_player, env._state,
                                )
                            )
                            if survival_threat is not None:
                                survival_kind = survival_threat
                        if (
                            survival_threat is None
                            and postkill_funded_armor
                            and not postkill_entry_recorded
                            and use_continuation and not env._choose_mode
                        ):
                            survival_mask, survival_threat = (
                                _postkill_funded_armor_skill_mask(
                                    mask, env._rl_player, env._state,
                                )
                            )
                            if survival_threat is not None:
                                survival_kind = "postkill_funded_armor"
                        armor_route_kind = None
                        if survival_threat is None:
                            survival_mask, armor_route_kind = (
                                _postkill_armor_recovery_skill_mask(
                                    mask, env._rl_player, env._state,
                                ) if postkill_armor_recovery
                                and not postkill_armor_recovery_completed
                                and use_continuation and not env._choose_mode
                                else (mask, None)
                            )
                            if (
                                armor_route_kind == "work"
                                and not postkill_armor_recovery_active
                            ):
                                postkill_armor_recovery_active = True
                            if (
                                armor_route_kind is not None
                                and postkill_armor_recovery_active
                            ):
                                survival_threat = "postkill_armor_recovery"
                                survival_kind = f"postkill_armor_{armor_route_kind}"
                        if survival_threat is None:
                            survival_mask, survival_threat = (
                                _postkill_recovery_skill_mask(
                                    mask, env._rl_player, env._state,
                                ) if _postkill_recovery_budget_open(
                                    postkill_recovery,
                                    postkill_recovery_completed,
                                    postkill_recovery_actions,
                                    postkill_recovery_max_actions,
                                ) and use_continuation
                                and not env._choose_mode
                                else (mask, None)
                            )
                            if survival_threat is not None:
                                survival_kind = "postkill_recovery"
                        if survival_threat is None:
                            survival_mask, survival_threat = (
                                _survival_heal_skill_mask(
                                    mask, env._rl_player, env._state,
                                ) if not env._choose_mode else (mask, None)
                            )
                            if survival_threat is not None:
                                survival_kind = "heal"
                        if survival_threat is None and not env._choose_mode:
                            survival_mask, survival_threat = (
                                _survival_armor_skill_mask(
                                    mask, env._rl_player, env._state,
                                )
                            )
                            if survival_threat is not None:
                                survival_kind = "armor"
                        survival_act, survival_state = (
                            _predict_with_commitment_prior(
                                survival_model, old_policy_obs, survival_state,
                                episode_start, mask, survival_mask, 1.0,
                            )
                        )
                    if (
                        survival_threat is None
                        and (repair_model is not None or survival_model is not None)
                        and not env._choose_mode
                    ):
                        repair_mask, survival_threat = _survival_repair_skill_mask(
                            mask, env._rl_player, env._state,
                        )
                        if survival_threat is not None:
                            survival_kind = "repair"
                            selected_repair_model = repair_model or survival_model
                            selected_repair_state = (
                                repair_state if repair_model is not None
                                else survival_state
                            )
                            survival_act, selected_repair_state = (
                                _predict_with_commitment_prior(
                                    selected_repair_model, old_policy_obs,
                                    selected_repair_state,
                                    episode_start, mask, repair_mask, 1.0,
                                )
                            )
                            if repair_model is not None:
                                repair_state = selected_repair_state
                            else:
                                survival_state = selected_repair_state
                    equipment_act = None
                    equipment_kind = None
                    if equipment_model is not None:
                        equipment_mask, equipment_kind = (
                            _bow_loadout_course_skill_mask(
                                mask, env._rl_player, env._state,
                            ) if course_scenario == "equipment_bow_loadout"
                            and not env._choose_mode
                            else _sharpen_skill_mask(
                                mask, env._rl_player, env._state,
                            ) if not env._choose_mode else (mask, None)
                        )
                        equipment_act, equipment_state = (
                            _predict_with_commitment_prior(
                                equipment_model,
                                obs if course_scenario == "equipment_bow_loadout"
                                else old_policy_obs,
                                equipment_state,
                                episode_start, mask, equipment_mask, 1.0,
                            )
                        )
                    visibility_act = None
                    visibility_target = None
                    visibility_attr = None
                    if visibility_model is not None:
                        visibility_mask, visibility_target, visibility_attr = (
                            _detection_skill_mask(
                                mask, env._rl_player, env._state,
                            ) if not env._choose_mode else (mask, None, None)
                        )
                        visibility_act, visibility_state = (
                            _predict_with_commitment_prior(
                                visibility_model, old_policy_obs, visibility_state,
                                episode_start, mask, visibility_mask, 1.0,
                            )
                        )
                    attribute_act = None
                    attribute_target = None
                    if attribute_model is not None:
                        attribute_mask, attribute_target = (
                            _attribute_armor_skill_mask(
                                mask, env._rl_player, env._state,
                            ) if not env._choose_mode else (mask, None)
                        )
                        attribute_act, attribute_state = (
                            _predict_with_commitment_prior(
                                attribute_model, old_policy_obs, attribute_state,
                                episode_start, mask, attribute_mask, 1.0,
                            )
                        )
                    antiarmor_act = None
                    antiarmor_target = None
                    antiarmor_kind = None
                    if antiarmor_model is not None:
                        antiarmor_mask, antiarmor_target, antiarmor_kind = (
                            _fire_antiarmor_skill_mask(
                                mask, env._rl_player, env._state,
                                min_hp=6 if lowhp_antiarmor else 11,
                                require_lowhp_arrows=lowhp_antiarmor,
                            ) if not env._choose_mode else (mask, None, None)
                        )
                        antiarmor_act, antiarmor_state = (
                            _predict_with_commitment_prior(
                                antiarmor_model, old_policy_obs, antiarmor_state,
                                episode_start, mask, antiarmor_mask, 1.0,
                            )
                        )
                    bow_loadout_act = None
                    bow_loadout_target = None
                    bow_loadout_kind = None
                    if (
                        bow_loadout_model is not None
                        and bow_loadout_route_actions < 2
                        and survival_threat is None
                        and pp_target is None
                        and visibility_target is None
                        and attribute_target is None
                    ):
                        (
                            bow_loadout_mask,
                            bow_loadout_target,
                            bow_loadout_kind,
                        ) = (
                            _natural_bow_loadout_skill_mask(
                                mask, env._rl_player, env._state,
                            ) if not env._choose_mode else (mask, None, None)
                        )
                        if bow_loadout_target is not None:
                            bow_episode_start = np.array([
                                bow_loadout_state is None
                            ])
                            bow_loadout_act, bow_loadout_state = (
                                _predict_with_commitment_prior(
                                    bow_loadout_model, obs, bow_loadout_state,
                                    bow_episode_start,
                                    mask, bow_loadout_mask, 1.0,
                                )
                            )
                    police_act = None
                    police_kind = None
                    if police_model is not None:
                        police_mask, police_kind = (
                            _police_skill_mask(
                                mask, env._rl_player, env._state,
                                proactive_election=precombat_police_election,
                            ) if not env._choose_mode else (mask, None)
                        )
                        police_act, police_state = (
                            _predict_with_commitment_prior(
                                police_model, old_policy_obs, police_state,
                                episode_start, mask, police_mask, 1.0,
                            )
                        )
                    if survival_threat is not None and survival_act is not None:
                        action = int(survival_act[0])
                        selected_postkill_recovery = (
                            survival_kind == "postkill_recovery"
                        )
                        selected_postkill_armor_recovery = bool(
                            survival_kind
                            and survival_kind.startswith("postkill_armor_")
                        )
                        survival_phase = f"survival_{survival_kind}_skill"
                        policy_phases[survival_phase] += 1
                        survival_skill_steps += 1
                    elif pp_target is not None and pp_act is not None:
                        action = int(pp_act[0])
                        policy_phases["pp_damage_skill"] += 1
                        pp_skill_steps += 1
                    elif visibility_target is not None and visibility_act is not None:
                        action = int(visibility_act[0])
                        selected_visibility = True
                        policy_phases[f"visibility_{visibility_attr}_skill"] += 1
                        visibility_skill_steps += 1
                    elif attribute_target is not None and attribute_act is not None:
                        action = int(attribute_act[0])
                        policy_phases["attribute_armor_skill"] += 1
                        attribute_skill_steps += 1
                    elif (
                        bow_loadout_target is not None
                        and bow_loadout_act is not None
                    ):
                        action = int(bow_loadout_act[0])
                        policy_phases[
                            f"bow_loadout_{bow_loadout_kind}_skill"
                        ] += 1
                        bow_loadout_skill_steps += 1
                        bow_loadout_route_actions += 1
                        selected_bow_loadout = True
                    elif antiarmor_target is not None and antiarmor_act is not None:
                        action = int(antiarmor_act[0])
                        policy_phases[f"fire_antiarmor_{antiarmor_kind}_skill"] += 1
                        antiarmor_skill_steps += 1
                    elif police_kind is not None and police_act is not None:
                        action = int(police_act[0])
                        policy_phases[f"police_{police_kind}_skill"] += 1
                        police_skill_steps += 1
                    elif equipment_kind is not None and equipment_act is not None:
                        action = int(equipment_act[0])
                        policy_phases[f"equipment_{equipment_kind}_skill"] += 1
                        equipment_skill_steps += 1
                    elif postkill_melee_target is not None and act is not None:
                        action = int(act[0])
                        policy_phases[
                            f"postkill_{postkill_melee_kind}_base_skill"
                        ] += 1
                    elif use_continuation and continuation_act is not None:
                        action = int(continuation_act[0])
                        policy_phases["continuation"] += 1
                    else:
                        action = int(act[0])
                        policy_phases["opening"] += 1
                # 解码"打算做什么"必须在 step 之前（step 后局势已变）
                cmd = ""
                if env._rl_player is not None and env._state is not None:
                    try:
                        cmd = _decode_action(env, action)
                    except Exception:
                        cmd = "?"
                if env._choose_mode:
                    situation = env._choose_context.get("situation", "") or "unknown"
                    choose_situations[situation] += 1
                if selected_bow_loadout:
                    bow_loadout_skill_commands[cmd] += 1
                trace.append(cmd)
                if selected_postkill_recovery:
                    postkill_recovery_actions += 1
                    if cmd == "interact 治疗" or (
                        postkill_recovery_max_actions > 0
                        and postkill_recovery_actions >= postkill_recovery_max_actions
                    ):
                        postkill_recovery_completed = True
                if selected_postkill_armor_recovery:
                    postkill_armor_recovery_actions += 1
                    if cmd == "interact 陶瓷护甲" or (
                        postkill_armor_recovery_actions >= 3
                    ):
                        postkill_armor_recovery_completed = True
                        postkill_armor_recovery_active = False
                verbs[_verb(cmd)] += 1
                commands[cmd] += 1
                kills_before_action = int(
                    getattr(env._rl_player, "kill_count", 0) or 0
                )
                if kills_before_action < 1:
                    opening_trace.append(_postkill_trace_state(
                        cmd, mask, env._rl_player, env._state,
                    ))
                if kills_before_action >= 1:
                    postkill_trace.append(_postkill_trace_state(
                        cmd, mask, env._rl_player, env._state,
                    ))
                if (
                    kills_before_action >= 1
                    and not postkill_entry_recorded
                    and not env._choose_mode
                ):
                    postkill_entry_signature = _postkill_entry_signature(
                        mask, env._rl_player, env._state,
                    )
                    postkill_entry_states[postkill_entry_signature] += 1
                    postkill_entry_recorded = True
                phase_verbs = (
                    post_kill_verbs if kills_before_action >= 1 else pre_kill_verbs
                )
                phase_verbs[_verb(cmd)] += 1
                if cmd == "forfeit":
                    if np.count_nonzero(mask) == 1 and bool(mask[0]):
                        forced_forfeits += 1
                    else:
                        voluntary_forfeits += 1
                        try:
                            oracle_idx = _oracle_action(
                                mask, env._rl_player, env._state,
                            )
                            oracle_cmd = idx_to_command(
                                oracle_idx, env._rl_player, env._state,
                            )
                            voluntary_forfeit_oracle_labels[_verb(oracle_cmd)] += 1
                            if oracle_cmd == "forfeit":
                                legal_verbs = []
                                decoded_forfeit_indices = []
                                for legal_idx in np.flatnonzero(mask):
                                    legal_cmd = idx_to_command(
                                        int(legal_idx), env._rl_player, env._state,
                                    )
                                    if legal_cmd != "forfeit":
                                        legal_verbs.append(_verb(legal_cmd))
                                    elif int(legal_idx) != 0:
                                        decoded_forfeit_indices.append(int(legal_idx))
                                signature = "+".join(sorted(set(legal_verbs))) or "none"
                                oracle_forfeit_legal_signatures[signature] += 1
                                index_signature = "+".join(
                                    str(idx) for idx in decoded_forfeit_indices
                                ) or "only-0"
                                oracle_forfeit_index_signatures[index_signature] += 1
                        except Exception:
                            voluntary_forfeit_oracle_labels["?"] += 1
                obs, _r, term, trunc, info = env.step(action)
                if (
                    selected_visibility
                    and cmd in set(_DETECTION_COMMANDS.values())
                ):
                    # Acquiring a detector terminates the visibility option.
                    # Reset only the opening policy state: otherwise it has
                    # advanced through two actions it did not execute and
                    # resumes from its old work/supply loop distribution.
                    state = None
                    if (
                        (assassin_opening_prep or assassin_opening_supply_only)
                        and cmd == "interact 探测魔法"
                    ):
                        assassin_prep_active = _assassin_opening_prep_needed(
                            env._rl_player, env._state,
                            require_armor=not assassin_opening_supply_only,
                        )
                if kills_before_action >= 1:
                    post_kill_reward += float(_r)
                else:
                    pre_kill_reward += float(_r)
                episode_start = np.array([term or trunc])
                done = term or trunc

            rl = env._state.get_player("rl_0")
            if info.get("winner") == "rl_0":
                wins += 1
            if info.get("official_settled"):
                official_settled += 1
                official_wins += int(
                    "rl_0" in info.get("official_winners", [])
                )
            for key, value in (info.get("episode_reward_ledger") or {}).items():
                if isinstance(value, (int, float)):
                    episode_reward_ledger[str(key)] += float(value)
            kill_threshold = _kill_threshold(course_scenario)
            killed = int(getattr(rl, "kill_count", 0) or 0) >= kill_threshold
            final_kills = int(getattr(rl, "kill_count", 0) or 0)
            at_least_one_kill += int(final_kills >= 1)
            at_least_two_kills += int(final_kills >= 2)
            official_rl_win = bool(
                info.get("official_settled")
                and "rl_0" in (info.get("official_winners", []) or [])
            )
            lineup_official_wins[episode_lineup] += int(official_rl_win)
            lineup_first_kills[episode_lineup] += int(final_kills >= 1)
            postkill_success = False
            if final_kills >= 1:
                if final_kills >= 2:
                    postkill_outcome = "second_kill"
                elif official_rl_win:
                    # The rules can award the game after another source removes
                    # the final opponent.  That is a successful continuation,
                    # even though kill_count remains one.
                    postkill_outcome = "official_win_before_second"
                elif int(getattr(rl, "hp", 0) or 0) <= 0:
                    postkill_outcome = "rl_dead"
                elif int(getattr(env._state, "current_round", 0) or 0) >= max_rounds:
                    postkill_outcome = "round_limit"
                elif info.get("official_settled"):
                    postkill_outcome = "official_loss_before_second"
                else:
                    postkill_outcome = "ended_other"
                postkill_outcomes[postkill_outcome] += 1
                postkill_success = final_kills >= 2 or official_rl_win
                lineup_continuation_successes[episode_lineup] += int(
                    postkill_success
                )
                if not postkill_success:
                    postkill_failure_traces.append({
                        "seed": episode_seed,
                        "outcome": postkill_outcome,
                        "entry": postkill_entry_signature,
                        "round": int(
                            getattr(env._state, "current_round", 0) or 0
                        ),
                        "rl_hp": int(getattr(rl, "hp", 0) or 0),
                        "official_winners": tuple(
                            info.get("official_winners", []) or []
                        ),
                        "trace": tuple(postkill_trace),
                    })
                if postkill_entry_signature:
                    postkill_entry_outcome_states[
                        f"{postkill_outcome}|{postkill_entry_signature}"
                    ] += 1
                    for field in postkill_entry_signature.split(";"):
                        postkill_entry_fields[field] += 1
                        postkill_entry_outcome_fields[
                            f"{postkill_outcome};{field}"
                        ] += 1
                        if postkill_success:
                            postkill_entry_success_fields[field] += 1
                if not postkill_success:
                    alive_targets = [
                        player for player in env._state.players.values()
                        if player.player_id != "rl_0"
                        and int(getattr(player, "hp", 0) or 0) > 0
                    ]
                    if alive_targets:
                        remaining = min(
                            alive_targets,
                            key=lambda player: int(getattr(player, "hp", 0) or 0),
                        )
                        target_hp = int(getattr(remaining, "hp", 0) or 0)
                        target_hp_bucket = (
                            "0-5" if target_hp <= 5 else
                            "6-10" if target_hp <= 10 else
                            "11-15" if target_hp <= 15 else "16+"
                        )
                        target_armor = getattr(remaining, "armor", None)
                        has_target_armor = bool(
                            target_armor and target_armor.get_all_active()
                        )
                        failure_state = ";".join((
                            postkill_outcome,
                            f"target_hp={target_hp_bucket}",
                            f"target_armor={int(has_target_armor)}",
                            f"target_invisible={int(bool(getattr(remaining, 'is_invisible', False)))}",
                            f"rl_hp={'0-5' if rl.hp <= 5 else '6-10' if rl.hp <= 10 else '11-15' if rl.hp <= 15 else '16+'}",
                        ))
                        postkill_failure_target_states[failure_state] += 1
            if final_kills >= 1:
                opening_outcomes["first_kill"] += 1
            elif official_rl_win:
                opening_outcomes["official_win_without_kill"] += 1
            elif int(getattr(rl, "hp", 0) or 0) <= 0:
                opening_outcomes["rl_dead_without_kill"] += 1
            elif info.get("official_settled"):
                opening_outcomes["official_loss_without_kill"] += 1
            else:
                opening_outcomes["ended_without_kill"] += 1
            if final_kills < 1 and not official_rl_win:
                opening_failure_traces.append({
                    "seed": episode_seed,
                    "round": int(
                        getattr(env._state, "current_round", 0) or 0
                    ),
                    "rl_hp": int(getattr(rl, "hp", 0) or 0),
                    "official_winners": tuple(
                        info.get("official_winners", []) or []
                    ),
                    "lineup": episode_lineup,
                    "trace": tuple(opening_trace),
                })
            if killed:
                kills += 1
            if _has_chain(trace):
                chains += 1
                if not sample_chain:
                    sample_chain = [c for c in trace if c][:14]
            rounds.append(env._state.current_round)
        env.close()

    return {
        "episodes": episodes,
        "win_rate": wins / episodes,
        "official_win_rate": (
            official_wins / official_settled if official_settled else None
        ),
        "official_settlement_rate": official_settled / episodes,
        "kill_rate": kills / episodes,
        "at_least_one_kill_rate": at_least_one_kill / episodes,
        "at_least_two_kill_rate": at_least_two_kills / episodes,
        "kill_threshold": _kill_threshold(course_scenario),
        "chain_rate": chains / episodes,
        "forced_forfeits": forced_forfeits,
        "voluntary_forfeits": voluntary_forfeits,
        "voluntary_forfeit_oracle_labels": voluntary_forfeit_oracle_labels,
        "oracle_forfeit_legal_signatures": oracle_forfeit_legal_signatures,
        "oracle_forfeit_index_signatures": oracle_forfeit_index_signatures,
        "choose_situations": choose_situations,
        "policy_phases": policy_phases,
        "bow_loadout_skill_commands": bow_loadout_skill_commands,
        "commitment_targets": commitment_targets,
        "commitment_filtered_steps": commitment_filtered_steps,
        "pp_skill_steps": pp_skill_steps,
        "survival_skill_steps": survival_skill_steps,
        "equipment_skill_steps": equipment_skill_steps,
        "visibility_skill_steps": visibility_skill_steps,
        "attribute_skill_steps": attribute_skill_steps,
        "police_skill_steps": police_skill_steps,
        "antiarmor_skill_steps": antiarmor_skill_steps,
        "bow_loadout_skill_steps": bow_loadout_skill_steps,
        "mean_rounds": sum(rounds) / max(len(rounds), 1),
        "verbs": verbs,
        "commands": commands,
        "pre_kill_verbs": pre_kill_verbs,
        "post_kill_verbs": post_kill_verbs,
        "postkill_entry_states": postkill_entry_states,
        "postkill_entry_fields": postkill_entry_fields,
        "postkill_entry_success_fields": postkill_entry_success_fields,
        "postkill_entry_outcome_fields": postkill_entry_outcome_fields,
        "postkill_entry_outcome_states": postkill_entry_outcome_states,
        "postkill_outcomes": postkill_outcomes,
        "postkill_failure_target_states": postkill_failure_target_states,
        "postkill_failure_traces": postkill_failure_traces,
        "opening_failure_traces": opening_failure_traces,
        "opponent_lineups": opponent_lineups,
        "lineup_official_wins": lineup_official_wins,
        "lineup_first_kills": lineup_first_kills,
        "lineup_continuation_successes": lineup_continuation_successes,
        "opening_outcomes": opening_outcomes,
        "pre_kill_mean_step_reward": pre_kill_reward / max(sum(pre_kill_verbs.values()), 1),
        "post_kill_mean_step_reward": post_kill_reward / max(sum(post_kill_verbs.values()), 1),
        "mean_episode_reward_ledger": {
            key: value / episodes for key, value in episode_reward_ledger.items()
        },
        "sample_chain": sample_chain,
    }


def _has_chain(trace: list[str]) -> bool:
    """轨迹里是否出现 move …（lock|find）… attack 的有序组合。

    只认顺序，不要求相邻：中间夹 interact（买武器）等发育动作仍算成链。
    """
    stage = 0  # 0=待 move，1=待 lock/find，2=待 attack
    for cmd in trace:
        v = _verb(cmd)
        if stage == 0 and v == "move":
            stage = 1
        elif stage == 1 and v in ("find", "lock"):
            stage = 2
        elif stage == 2 and v in ("attack", "shoot"):
            return True
    return False


def main() -> int:
    p = argparse.ArgumentParser(description="Rung 0 攻击链探针")
    p.add_argument("--model", type=str, default="checkpoints/rung0_dummy/final.zip")
    p.add_argument(
        "--continuation-model", type=str, default=None,
        help="首杀后接管动作的第二个循环策略；默认与 --model 相同（即不门控）",
    )
    p.add_argument(
        "--continuation-contract",
        choices=[
            "none", "economy_pursuit_drill", "economy_pursuit_finish",
        ], default="none",
        help="续战专家的部署动作合同；资源追击专家应与训练时子空间一致",
    )
    p.add_argument(
        "--base-contract", choices=["none", "combat_economy"], default="none",
        help="full-M9 引擎下基础策略的部署子空间；条件专家仍读取完整合法 mask",
    )
    p.add_argument(
        "--postkill-recovery", action="store_true",
        help="实验性旧主策略恢复路由；默认关闭，避免打断已会远程续战的策略",
    )
    p.add_argument(
        "--postkill-recovery-max-actions", type=int, default=0,
        help="后杀恢复最多接管动作数；0为旧版无限制，一次治疗后总会交还续战策略",
    )
    p.add_argument(
        "--postkill-armor-recovery", action="store_true",
        help="实验性低信用点路线：首杀后最多打工两次、买甲一次，再交还续战策略",
    )
    p.add_argument(
        "--postkill-funded-armor", action="store_true",
        help="实验性一拍路线：首杀后入口≤5HP、无射击且陶瓷甲已可买时购买",
    )
    p.add_argument(
        "--postkill-melee-router", action="store_true",
        help="首杀后已接敌且小刀合法时，交给基础策略的近战动作头收尾",
    )
    p.add_argument(
        "--assassin-opening-prep", action="store_true",
        help="实验性：双魔法隐身时先筹资、购甲、补箭，再取得探测并交还主策略",
    )
    p.add_argument(
        "--assassin-opening-supply-only", action="store_true",
        help="实验性：双魔法隐身时只在探测前筹资补箭，不承担购甲两拍",
    )
    p.add_argument(
        "--opening-armor-recovery", action="store_true",
        help="实验性：首杀前低血、无甲、无直接伤害且位于商店时筹资购甲",
    )
    p.add_argument(
        "--opening-arrow-supply", action="store_true",
        help="实验性：首杀前弓空、无直接伤害且补给已合法时由经济技能接管一拍",
    )
    p.add_argument(
        "--pp-model", type=str, default=None,
        help="仅在无护甲的 +1 斩杀窗口接管两拍动作的 PP 专家 checkpoint",
    )
    p.add_argument(
        "--survival-model", type=str, default=None,
        help="仅在治疗或购甲能把明确败局改成可存活时接管一拍的生存专家",
    )
    p.add_argument(
        "--repair-model", type=str, default=None,
        help="仅在当前护甲耐久撑不到完成击杀、而修理后可存活时接管一拍",
    )
    p.add_argument(
        "--equipment-model", type=str, default=None,
        help="仅在磨刀可降低剩余总行动数时接管购买与磨刀两拍",
    )
    p.add_argument(
        "--visibility-model", type=str, default=None,
        help="仅在普通/魔法/科技隐身目标不可见时取得对应探测器",
    )
    p.add_argument(
        "--attribute-model", type=str, default=None,
        help="已接敌且目标有魔盾/AT力场时，在多种武器属性间选择",
    )
    p.add_argument(
        "--antiarmor-model", type=str, default=None,
        help="首杀后高血普通主防/魔法无防目标存在时，最多两拍取得火矢模块",
    )
    p.add_argument(
        "--lowhp-antiarmor", action="store_true",
        help=(
            "已拒绝的诊断开关：有箭时把火矢反甲下限从11HP降到6HP；"
            "跨种子方向不稳定，不用于毕业策略"
        ),
    )
    p.add_argument(
        "--bow-loadout-model", type=str, default=None,
        help=(
            "真实三人局中仅在明确收益窗口接管两拍的统一弓模块专家；"
            "专家从可观察状态自行选择设施与模块"
        ),
    )
    p.add_argument(
        "--police-model", type=str, default=None,
        help="仅在真实通缉、无个人直接攻击时接管竞选或即时警察攻击",
    )
    p.add_argument(
        "--precombat-police-election", action="store_true",
        help=(
            "已拒绝的诊断开关：候选为空且尚未观察到其他玩家行动时先报名队长；"
            "acted_this_round 不能可靠表达公开先攻顺位，不用于毕业策略"
        ),
    )
    p.add_argument(
        "--captain-target-priority", action="store_true",
        help="实验性：存在存活敌方队长时，把攻击/追击承诺集中到该公开目标",
    )
    p.add_argument(
        "--continuation-after-kills", type=int, default=1,
        help="累计亲手击杀达到该数后切换到 continuation policy（默认 1）",
    )
    p.add_argument(
        "--cold-continuation", action="store_true",
        help="仅在切换时初始化续战模型；默认从开局并行维护其循环隐状态",
    )
    p.add_argument(
        "--target-commitment", action="store_true",
        help="首杀后仅保留对当前存活目标有推进作用的合法动作",
    )
    p.add_argument(
        "--target-commitment-strength", type=float, default=1.0,
        help="完整策略与目标条件策略的混合强度 0..1；1 等价硬目标 mask",
    )
    p.add_argument("--episodes", type=int, default=30)
    p.add_argument(
        "--failure-traces", type=int, default=0,
        help="打印指定数量的首杀后二杀失败逐拍资源/目标轨迹",
    )
    p.add_argument("--max-rounds", type=int, default=25)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--opponent-bot", default="dummy",
        choices=["dummy", "turtle", "rush", "dodge", "archer", "basic_ai"],
        help="基础作战课对手；basic_ai 使用完整 BasicAIController",
    )
    p.add_argument("--n-opponents", type=int, default=1,
                   help="对手数；多人桥接门禁使用 2-5")
    p.add_argument(
        "--opponent-bots", default=None,
        help="逗号分隔的逐座位 bot，例如 dummy,dodge；覆盖 --opponent-bot",
    )
    p.add_argument(
        "--opponent-personalities", default=None,
        help="逗号分隔的逐座位完整 BasicAI 人格；用于困难配对课程验收",
    )
    p.add_argument("--full-actions", action="store_true",
                   help="取消 combat_core 裁剪，暴露完整 M9 动作空间")
    p.add_argument(
        "--economy-actions", action="store_true",
        help="开放通用战斗与非警察经济/装备交互，继续隔离 special",
    )
    p.add_argument("--course-scenario", choices=[
        "combat_multi_quarter_hp", "combat_multi_half_hp", "combat_multi_full_hp",
        "combat_assassin_pressure",
        "combat_retarget_static", "combat_retarget",
        "economy_bow_supply", "economy_pursuit_supply",
        "economy_armored_pursuit_supply",
        "economy_armored_pursuit_supply_slot0",
        "equipment_fire_antiarmor",
        "equipment_bow_loadout",
        "pp_damage_finish", "pp_damage_choice",
        "survival_heal_counterattack",
        "survival_armor_counterattack",
        "survival_armor_repair",
        "equipment_sharpen_chain",
        "visibility_thermal_chain", "visibility_magic_chain",
        "visibility_tech_chain", "attribute_magic_armor_choice",
        "attribute_tech_armor_choice",
    ],
                   default=None, help="真实中盘专项状态")
    p.add_argument("--random", action="store_true",
                   help="不载模型，用随机合法动作跑同口径基线")
    p.add_argument("--oracle", action="store_true",
                   help="不载模型，用手写贪心链（attack>find>shoot>追击）跑可达性上界")
    p.add_argument("--ablate-home", action="store_true",
                   help="反事实：屏蔽 home move 段（≈修复前动作空间），验证该段承重")
    args = p.parse_args()
    seat_bots = None
    if args.opponent_bots:
        seat_bots = [part.strip() for part in args.opponent_bots.split(",") if part.strip()]
        if len(seat_bots) != args.n_opponents:
            p.error("--opponent-bots 数量必须等于 --n-opponents")
    opponent_personalities = None
    if args.opponent_personalities:
        opponent_personalities = [
            part.strip() for part in args.opponent_personalities.split(",")
            if part.strip()
        ]
        if len(opponent_personalities) != args.n_opponents:
            p.error("--opponent-personalities 数量必须等于 --n-opponents")
    if args.continuation_after_kills < 1:
        p.error("--continuation-after-kills 必须至少为 1")
    if args.postkill_recovery_max_actions < 0:
        p.error("--postkill-recovery-max-actions 不能为负数")
    if args.failure_traces < 0:
        p.error("--failure-traces 不能为负数")
    if args.continuation_model and (args.random or args.oracle):
        p.error("--continuation-model 不能与 --random/--oracle 合用")
    if args.pp_model and (args.random or args.oracle):
        p.error("--pp-model 不能与 --random/--oracle 合用")
    if args.survival_model and (args.random or args.oracle):
        p.error("--survival-model 不能与 --random/--oracle 合用")
    if args.repair_model and (args.random or args.oracle):
        p.error("--repair-model 不能与 --random/--oracle 合用")
    if args.equipment_model and (args.random or args.oracle):
        p.error("--equipment-model 不能与 --random/--oracle 合用")
    if args.visibility_model and (args.random or args.oracle):
        p.error("--visibility-model 不能与 --random/--oracle 合用")
    if args.attribute_model and (args.random or args.oracle):
        p.error("--attribute-model 不能与 --random/--oracle 合用")
    if args.antiarmor_model and (args.random or args.oracle):
        p.error("--antiarmor-model 不能与 --random/--oracle 合用")
    if args.bow_loadout_model and (args.random or args.oracle):
        p.error("--bow-loadout-model 不能与 --random/--oracle 合用")
    if args.police_model and (args.random or args.oracle):
        p.error("--police-model 不能与 --random/--oracle 合用")
    if args.assassin_opening_prep and not args.survival_model:
        p.error("--assassin-opening-prep 需要 --survival-model")
    if args.assassin_opening_supply_only and not args.survival_model:
        p.error("--assassin-opening-supply-only 需要 --survival-model")
    if args.assassin_opening_prep and args.assassin_opening_supply_only:
        p.error("双刺客两种开局整备模式不能同时启用")
    if args.opening_armor_recovery and not args.survival_model:
        p.error("--opening-armor-recovery 需要 --survival-model")
    if args.full_actions and args.economy_actions:
        p.error("--full-actions 与 --economy-actions 不能同时使用")
    if not 0.0 <= args.target_commitment_strength <= 1.0:
        p.error("--target-commitment-strength 必须在 0..1")

    tag = ("ORACLE" if args.oracle else
           "RANDOM" if args.random else Path(args.model).as_posix())
    if args.continuation_model:
        mode = "cold" if args.cold_continuation else "warm"
        tag += (f" -> {Path(args.continuation_model).as_posix()} "
               f"[kill>={args.continuation_after_kills}, {mode}]")
        if args.continuation_contract != "none":
            tag += f" [合同={args.continuation_contract}]"
    if args.pp_model:
        tag += f" + PP专家={Path(args.pp_model).as_posix()}"
    if args.survival_model:
        tag += f" + 生存专家={Path(args.survival_model).as_posix()}"
    if args.repair_model:
        tag += f" + 修甲专家={Path(args.repair_model).as_posix()}"
    if args.equipment_model:
        tag += f" + 装备专家={Path(args.equipment_model).as_posix()}"
    if args.visibility_model:
        tag += f" + 可见性专家={Path(args.visibility_model).as_posix()}"
    if args.attribute_model:
        tag += f" + 属性护甲专家={Path(args.attribute_model).as_posix()}"
    if args.antiarmor_model:
        tag += f" + 反陶瓷专家={Path(args.antiarmor_model).as_posix()}"
        if args.lowhp_antiarmor:
            tag += " [有箭时反甲下限=6HP]"
    if args.bow_loadout_model:
        tag += f" + 统一弓模块专家={Path(args.bow_loadout_model).as_posix()}"
    if args.police_model:
        tag += f" + 警察专家={Path(args.police_model).as_posix()}"
    if args.precombat_police_election:
        tag += " [无候选时开战前竞选]"
    if args.captain_target_priority:
        tag += " [敌方队长目标优先]"
    if args.ablate_home:
        tag += " [屏蔽 home 槽]"
    if args.target_commitment:
        tag += f" [目标承诺={args.target_commitment_strength:.2f}]"
    if args.base_contract != "none":
        tag += f" [基础合同={args.base_contract}]"
    if args.postkill_recovery:
        recovery_budget = (
            "无限" if args.postkill_recovery_max_actions == 0
            else str(args.postkill_recovery_max_actions)
        )
        tag += f" [后杀恢复≤{recovery_budget}动作/一次治疗]"
    if args.postkill_armor_recovery:
        tag += " [后杀低信用点购甲≤3动作]"
    if args.postkill_funded_armor:
        tag += " [后杀已筹资购甲一拍]"
    if args.postkill_melee_router:
        tag += " [后杀接敌交还基础近战]"
    if args.assassin_opening_prep:
        tag += " [双魔隐探测前购甲补箭]"
    if args.assassin_opening_supply_only:
        tag += " [双魔隐探测前只补箭]"
    if args.opening_armor_recovery:
        tag += " [首杀前低血购甲]"
    if args.opening_arrow_supply:
        tag += " [首杀前空弓即时补箭]"
    r = run(None if (args.random or args.oracle) else args.model, args.episodes,
            args.max_rounds, args.seed, args.random, args.oracle,
            args.ablate_home, args.opponent_bot, args.n_opponents,
            args.full_actions, args.course_scenario, seat_bots,
            args.continuation_model, args.continuation_after_kills,
            not args.cold_continuation, args.target_commitment,
            args.target_commitment_strength, args.economy_actions,
            args.pp_model, args.survival_model, args.repair_model,
            args.equipment_model, args.visibility_model,
            None if args.continuation_contract == "none"
            else args.continuation_contract,
            None if args.base_contract == "none" else args.base_contract,
            args.postkill_recovery, args.attribute_model, args.police_model,
            args.postkill_recovery_max_actions, args.postkill_armor_recovery,
            args.postkill_melee_router, args.postkill_funded_armor,
            args.antiarmor_model, args.bow_loadout_model,
            opponent_personalities, args.assassin_opening_prep,
            args.opening_armor_recovery, args.opening_arrow_supply,
            args.precombat_police_election,
            args.assassin_opening_supply_only,
            args.captain_target_priority, args.lowhp_antiarmor)
    personality_tag = (
        f"({','.join(opponent_personalities)})"
        if opponent_personalities else ""
    )
    print(f"── {tag} | {r['episodes']} 局 | max_rounds={args.max_rounds} "
          f"| {args.n_opponents + 1}人局 "
          f"{','.join(seat_bots) if seat_bots else args.opponent_bot}"
          f"{personality_tag}"
          f" | 无天赋 "
          f"| {('full_m9' if args.full_actions else 'economy_pursuit_drill' if args.course_scenario in {'economy_pursuit_supply', 'economy_armored_pursuit_supply', 'economy_armored_pursuit_supply_slot0'} else 'economy_drill' if args.course_scenario == 'economy_bow_supply' else 'pp_damage_drill' if args.course_scenario in {'pp_damage_finish', 'pp_damage_choice'} else 'survival_heal_drill' if args.course_scenario == 'survival_heal_counterattack' else 'survival_armor_drill' if args.course_scenario == 'survival_armor_counterattack' else 'survival_repair_drill' if args.course_scenario == 'survival_armor_repair' else 'sharpen_drill' if args.course_scenario == 'equipment_sharpen_chain' else 'bow_loadout_drill' if args.course_scenario == 'equipment_bow_loadout' else 'visibility_drill' if args.course_scenario in {'visibility_thermal_chain', 'visibility_magic_chain', 'visibility_tech_chain'} else 'attribute_armor_drill' if args.course_scenario in {'attribute_magic_armor_choice', 'attribute_tech_armor_choice'} else 'combat_economy' if args.economy_actions else 'combat_core')}"
          f"{f' | {args.course_scenario}' if args.course_scenario else ''} ──")
    print(f"  胜率           {r['win_rate']:.0%}")
    official = r["official_win_rate"]
    print(f"  正式胜率       {official:.0%}" if official is not None
          else "  正式胜率       N/A")
    print(f"  正式结算率     {r['official_settlement_rate']:.0%}")
    print(f"  亲手击杀率     {r['kill_rate']:.0%}   "
          f"（kill_count≥{r['kill_threshold']}，排除环境击杀）")
    print(f"  至少一杀/二杀 {r['at_least_one_kill_rate']:.0%} / "
          f"{r['at_least_two_kill_rate']:.0%}")
    print(f"  成链率         {r['chain_rate']:.0%}   （move→find/lock→attack 有序）")
    total_forfeits = r["forced_forfeits"] + r["voluntary_forfeits"]
    if total_forfeits:
        print(f"  放弃归因       主动 {r['voluntary_forfeits']} / "
              f"规则强制 {r['forced_forfeits']}")
    if r["voluntary_forfeit_oracle_labels"]:
        oracle_labels = ", ".join(
            f"{verb}={count}"
            for verb, count in r["voluntary_forfeit_oracle_labels"].most_common()
        )
        print(f"  主动放弃校正   {oracle_labels}")
    if r["oracle_forfeit_legal_signatures"]:
        signatures = ", ".join(
            f"{signature}={count}"
            for signature, count in r["oracle_forfeit_legal_signatures"].most_common(8)
        )
        print(f"  教师放弃时可用 {signatures}")
    if r["oracle_forfeit_index_signatures"]:
        index_signatures = ", ".join(
            f"{signature}={count}"
            for signature, count in r["oracle_forfeit_index_signatures"].most_common(8)
        )
        print(f"  伪合法索引     {index_signatures}")
    print(f"  均轮           {r['mean_rounds']:.1f}")
    total = sum(r["verbs"].values()) or 1
    top = ", ".join(f"{v}={c}({c / total:.0%})"
                    for v, c in r["verbs"].most_common(8))
    print(f"  动词分布       {top}")
    command_top = ", ".join(
        f"{cmd}={count}({count / total:.0%})"
        for cmd, count in r["commands"].most_common(8)
    )
    print(f"  指令分布       {command_top}")
    if r["choose_situations"]:
        choose_top = ", ".join(
            f"{situation}={count}"
            for situation, count in r["choose_situations"].most_common(8)
        )
        print(f"  旁路选择回调   {choose_top}")
    if r["policy_phases"]:
        phase_total = sum(r["policy_phases"].values()) or 1
        phase_text = ", ".join(
            f"{phase}={count}({count / phase_total:.0%})"
            for phase, count in r["policy_phases"].items()
        )
        print(f"  策略门控       {phase_text}")
    if r["bow_loadout_skill_commands"]:
        bow_commands = ", ".join(
            f"{command}={count}"
            for command, count in r[
                "bow_loadout_skill_commands"
            ].most_common()
        )
        print(f"  弓模块接管动作 {bow_commands}")
    if r["opening_outcomes"]:
        opening_text = ", ".join(
            f"{outcome}={count}"
            for outcome, count in r["opening_outcomes"].most_common()
        )
        print(f"  首杀阶段结局   {opening_text}")
    if r["opponent_lineups"]:
        lineup_rows = []
        for lineup, count in r["opponent_lineups"].most_common():
            first_kills = r["lineup_first_kills"][lineup]
            continuation_successes = r[
                "lineup_continuation_successes"
            ][lineup]
            lineup_rows.append(
                f"{lineup}:胜{r['lineup_official_wins'][lineup]}/{count},"
                f"首杀{first_kills}/{count},"
                f"续战{continuation_successes}/{first_kills or 1}"
            )
        print(f"  人格配对分层   {' | '.join(lineup_rows)}")
    if r["postkill_entry_states"]:
        entry_text = " | ".join(
            f"{signature} x{count}"
            for signature, count in r["postkill_entry_states"].most_common(8)
        )
        print(f"  首杀后入口     {entry_text}")
    if r["postkill_outcomes"]:
        outcome_text = ", ".join(
            f"{outcome}={count}"
            for outcome, count in r["postkill_outcomes"].most_common()
        )
        print(f"  首杀后结局     {outcome_text}")
        layered = []
        for prefix in (
            "hp=", "arrows=", "credits=", "shoot=", "attack=", "armor_buy=",
        ):
            for field, total_count in sorted(
                (item for item in r["postkill_entry_fields"].items()
                 if item[0].startswith(prefix)),
            ):
                success_count = r["postkill_entry_success_fields"][field]
                layered.append(f"{field}:{success_count}/{total_count}")
        print(f"  入口续战成功分层 {', '.join(layered)}")
        death_layers = []
        for prefix in ("hp=", "credits=", "armor_buy=", "shoot="):
            outcome_prefix = f"rl_dead;{prefix}"
            for field, count in sorted(
                (item for item in r["postkill_entry_outcome_fields"].items()
                 if item[0].startswith(outcome_prefix)),
            ):
                death_layers.append(f"{field.removeprefix('rl_dead;')}:{count}")
        if death_layers:
            print(f"  死亡入口分层   {', '.join(death_layers)}")
    if r["postkill_failure_target_states"]:
        failure_text = " | ".join(
            f"{signature} x{count}"
            for signature, count in r["postkill_failure_target_states"].most_common(8)
        )
        print(f"  二杀失败终态   {failure_text}")
    for failure in r["opening_failure_traces"][:args.failure_traces]:
        trace_text = " → ".join(failure["trace"])
        winners = "+".join(failure["official_winners"]) or "none"
        print(
            f"  首杀失败轨迹 seed={failure['seed']} lineup={failure['lineup']} "
            f"round={failure['round']} rl_hp={failure['rl_hp']} "
            f"official_winners={winners} {trace_text}"
        )
    for failure in r["postkill_failure_traces"][:args.failure_traces]:
        trace_text = " → ".join(failure["trace"])
        winners = "+".join(failure["official_winners"]) or "none"
        print(
            f"  失败轨迹 {failure['outcome']} seed={failure['seed']} "
            f"round={failure['round']} rl_hp={failure['rl_hp']} "
            f"official_winners={winners} entry={failure['entry']} "
            f"{trace_text}"
        )
    if r["commitment_targets"]:
        target_text = ", ".join(
            f"{target}={count}"
            for target, count in r["commitment_targets"].most_common()
        )
        print(f"  目标承诺       {target_text} | "
              f"过滤步={r['commitment_filtered_steps']}")
    for label, counter, mean_reward in (
        ("首杀前", r["pre_kill_verbs"], r["pre_kill_mean_step_reward"]),
        ("首杀后", r["post_kill_verbs"], r["post_kill_mean_step_reward"]),
    ):
        count = sum(counter.values())
        if not count:
            continue
        phase_top = ", ".join(
            f"{verb}={n}({n / count:.0%})"
            for verb, n in counter.most_common(6)
        )
        print(f"  {label}动作   {phase_top} | mean_step_reward={mean_reward:.3f}")
    if r["mean_episode_reward_ledger"]:
        ledger_text = ", ".join(
            f"{key}={value:.1f}"
            for key, value in r["mean_episode_reward_ledger"].items()
        )
        print(f"  局均奖励账本   {ledger_text}")
    if r["sample_chain"]:
        print(f"  样例轨迹       {' → '.join(r['sample_chain'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
