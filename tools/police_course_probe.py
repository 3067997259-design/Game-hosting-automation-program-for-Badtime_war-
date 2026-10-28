"""M9 RL 警察专项探针：只认主动指挥造成的伤害，不把当选或自动执法算成果。"""

from __future__ import annotations

import argparse
import collections
import contextlib
import io
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import numpy as np

from engine.experiments import set_profile
from rl.action_space import idx_to_command
from rl.env import BadtimeWarEnv


def _oracle_action(mask, player, game_state) -> int:
    """课程教师：先取得权限，再下有效攻击命令，最后回退基础攻击链。"""
    commands: dict[str, int] = {}
    for raw_idx in np.flatnonzero(mask):
        idx = int(raw_idx)
        commands[idx_to_command(idx, player, game_state)] = idx
    if "special 竞选队长" in commands:
        return commands["special 竞选队长"]
    command_attacks = [
        idx for cmd, idx in commands.items()
        if cmd.startswith("special 指挥unit") and cmd.endswith("攻击")
    ]
    if command_attacks:
        return command_attacks[0]
    for exact in ("interact 小刀",):
        if exact in commands:
            return commands[exact]
    for prefix in ("attack ", "find ", "lock ", "move "):
        candidates = [idx for cmd, idx in commands.items() if cmd.startswith(prefix)]
        if candidates:
            return candidates[0]
    return int(np.flatnonzero(mask)[0])


def run(model_path: str | None, episodes: int, max_rounds: int, seed: int,
        scenario: str, opponent_bot: str, use_random: bool,
        use_oracle: bool) -> dict:
    set_profile("m9-rfc")
    model = None
    if not (use_random or use_oracle):
        from rl.maskable_recurrent import MaskableRecurrentPPO
        model = MaskableRecurrentPPO.load(model_path, device="cpu")

    rng = np.random.default_rng(seed)
    actions: collections.Counter[str] = collections.Counter()
    effective_episodes = elections = official_wins = personal_kills = 0
    command_hits = command_damage = automatic_hits = 0

    with contextlib.redirect_stdout(io.StringIO()):
        env = BadtimeWarEnv(
            num_opponents=1, max_rounds=max_rounds,
            enable_talents=False, rl_talent=0,
            opponent_bots=[opponent_bot], action_contract="police_course",
            course_scenario=scenario,
        )
        for ep in range(episodes):
            obs, _ = env.reset(seed=seed + 17011 + ep)
            lstm_state = None
            episode_start = np.array([True])
            done = False
            saw_election = False
            while not done:
                mask = env.action_masks()
                if use_oracle:
                    action = _oracle_action(mask, env._rl_player, env._state)
                elif model is None:
                    action = int(rng.choice(np.flatnonzero(mask)))
                else:
                    predicted, lstm_state = model.predict(
                        obs[None], state=lstm_state,
                        episode_start=episode_start,
                        action_masks=mask[None], deterministic=True,
                    )
                    action = int(predicted[0])
                cmd = idx_to_command(action, env._rl_player, env._state)
                actions[cmd] += 1
                saw_election = saw_election or cmd == "special 竞选队长"
                obs, _reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated
                episode_start = np.array([done])

            events = env._state.event_log
            commanded = [
                event for event in events
                if event.get("type") == "m9_police_command_attack"
                and event.get("captain") == "rl_0"
            ]
            automatic = [
                event for event in events
                if event.get("type") == "m9_police_enforcement"
            ]
            damage = sum(int(event.get("damage", 0) or 0) for event in commanded)
            effective_episodes += int(damage > 0)
            command_hits += len(commanded)
            command_damage += damage
            automatic_hits += len(automatic)
            elections += int(saw_election)
            official_wins += int("rl_0" in info.get("official_winners", []))
            personal_kills += int(
                int(getattr(env._rl_player, "kill_count", 0) or 0) > 0
            )
        env.close()

    total_actions = sum(actions.values()) or 1
    return {
        "episodes": episodes,
        "effective_rate": effective_episodes / episodes,
        "election_rate": elections / episodes,
        "official_win_rate": official_wins / episodes,
        "personal_kill_rate": personal_kills / episodes,
        "command_hits_per_episode": command_hits / episodes,
        "command_damage_per_episode": command_damage / episodes,
        "automatic_hits": automatic_hits,
        "actions": actions,
        "total_actions": total_actions,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="M9 RL 警察专项结果探针")
    p.add_argument("--model", default=(
        "checkpoints/codex_m9_course_core_triple100k_s27/final.zip"
    ))
    p.add_argument("--episodes", type=int, default=50)
    p.add_argument("--max-rounds", type=int, default=12)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--scenario", choices=["police_command", "police_election"],
                   default="police_command")
    p.add_argument("--opponent-bot",
                   choices=["dummy", "turtle", "rush", "dodge", "archer"],
                   default="dummy")
    p.add_argument("--random", action="store_true")
    p.add_argument("--oracle", action="store_true")
    args = p.parse_args()
    if args.random and args.oracle:
        p.error("--random 与 --oracle 不能同时使用")

    r = run(
        None if (args.random or args.oracle) else args.model,
        args.episodes, args.max_rounds, args.seed, args.scenario,
        args.opponent_bot, args.random, args.oracle,
    )
    tag = "ORACLE" if args.oracle else "RANDOM" if args.random else args.model
    print(f"── {tag} | {args.scenario} | {args.opponent_bot} | {args.episodes} 局 ──")
    print(f"  有效指挥局率       {r['effective_rate']:.0%}")
    print(f"  竞选局率           {r['election_rate']:.0%}")
    print(f"  主动命中/局        {r['command_hits_per_episode']:.2f}")
    print(f"  主动警察伤害/局    {r['command_damage_per_episode']:.1f}")
    print(f"  自动执法事件       {r['automatic_hits']}（课程应恒为 0）")
    print(f"  亲手击杀率         {r['personal_kill_rate']:.0%}")
    print(f"  正式胜率           {r['official_win_rate']:.0%}")
    top = ", ".join(
        f"{cmd}={count}({count / r['total_actions']:.0%})"
        for cmd, count in r["actions"].most_common(10)
    )
    print(f"  指令分布           {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
