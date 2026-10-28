"""M9 RL T1 天赋首课探针：区分发动意图、真实斩击、伤害与终局。"""

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
from rl.action_space import idx_to_choose_option, idx_to_command
from rl.env import BadtimeWarEnv


def _semantic(env: BadtimeWarEnv, action: int) -> str:
    if env._choose_mode:
        return idx_to_choose_option(
            action,
            env._choose_options,
            env._choose_context.get("situation", ""),
            env._rl_player,
            env._state,
        )
    return idx_to_command(action, env._rl_player, env._state)


def _oracle_action(env: BadtimeWarEnv, mask: np.ndarray) -> int:
    semantics = {
        _semantic(env, int(raw_idx)): int(raw_idx)
        for raw_idx in np.flatnonzero(mask)
    }
    if env._choose_context.get("situation") == "talent_t0":
        return semantics["发动天赋"]
    for exact in (
        "报名公演", "小刀", "即演", "发动天赋", "interact 小刀",
    ):
        if exact in semantics:
            return semantics[exact]
    for prefix in ("attack ", "find ", "lock ", "move "):
        for semantic, idx in semantics.items():
            if semantic.startswith(prefix):
                return idx
    return int(np.flatnonzero(mask)[0])


def run(model_path: str | None, episodes: int, max_rounds: int, seed: int,
        scenario: str, use_random: bool, use_oracle: bool) -> dict:
    set_profile("m9-rfc")
    model = None
    if not (use_random or use_oracle):
        from rl.maskable_recurrent import MaskableRecurrentPPO
        model = MaskableRecurrentPPO.load(model_path, device="cpu")

    rng = np.random.default_rng(seed)
    decisions: collections.Counter[str] = collections.Counter()
    activations = first_activations = registrations = first_registrations = 0
    effect_episodes = kill_episodes = official_wins = 0
    effect_hits = effect_damage = 0.0
    talent_num = 1 if scenario == "talent_t1_improvise" else 3
    effect_type = "oneslash_attack" if talent_num == 1 else "star_attack"

    with contextlib.redirect_stdout(io.StringIO()):
        env = BadtimeWarEnv(
            num_opponents=1, max_rounds=max_rounds,
            enable_talents=True, enable_opponent_talents=False,
            rl_talent=talent_num, opponent_bots=["dummy"],
            action_contract="talent_course",
            course_scenario=scenario,
        )
        for ep in range(episodes):
            obs, _ = env.reset(seed=seed + 21011 + ep)
            lstm_state = None
            episode_start = np.array([True])
            done = False
            activated = False
            registered = False
            saw_activation_offer = False
            saw_registration_offer = False
            while not done:
                mask = env.action_masks()
                if use_oracle:
                    action = _oracle_action(env, mask)
                elif model is None:
                    action = int(rng.choice(np.flatnonzero(mask)))
                else:
                    predicted, lstm_state = model.predict(
                        obs[None], state=lstm_state,
                        episode_start=episode_start,
                        action_masks=mask[None], deterministic=True,
                    )
                    action = int(predicted[0])
                situation = env._choose_context.get("situation", "")
                semantic = _semantic(env, action)
                decisions[f"{situation or 'command'}:{semantic}"] += 1
                if situation == "talent_t0":
                    if not saw_activation_offer:
                        first_activations += int(semantic == "发动天赋")
                        saw_activation_offer = True
                    activated = activated or semantic == "发动天赋"
                if env._choose_context.get("phase") == "M9_PUBLIC_REGISTRATION":
                    if not saw_registration_offer:
                        first_registrations += int(semantic == "报名公演")
                        saw_registration_offer = True
                    registered = registered or semantic == "报名公演"
                obs, _reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated
                episode_start = np.array([done])

            effects = [
                event for event in env._state.event_log
                if event.get("type") == effect_type
                and event.get("player") == "rl_0"
            ]
            activations += int(activated)
            registrations += int(registered)
            effect_episodes += int(bool(effects))
            if talent_num == 1:
                effect_hits += len(effects)
                effect_damage += sum(
                    float(event.get("damage", 0) or 0) for event in effects
                )
                kill_episodes += int(any(event.get("killed") for event in effects))
            else:
                effect_hits += sum(
                    float(event.get("hits", 0) or 0) for event in effects
                )
                kill_episodes += int(any(event.get("kills", 0) for event in effects))
            official_wins += int("rl_0" in info.get("official_winners", []))
        env.close()

    total_decisions = sum(decisions.values()) or 1
    return {
        "episodes": episodes,
        "first_activation_rate": first_activations / episodes,
        "activation_rate": activations / episodes,
        "first_registration_rate": first_registrations / episodes,
        "registration_rate": registrations / episodes,
        "effect_rate": effect_episodes / episodes,
        "kill_rate": kill_episodes / episodes,
        "official_win_rate": official_wins / episodes,
        "effect_hits_per_episode": effect_hits / episodes,
        "effect_damage_per_episode": effect_damage / episodes,
        "decisions": decisions,
        "total_decisions": total_decisions,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="M9 RL T1 天赋首课结果探针")
    p.add_argument("--model", default=(
        "checkpoints/codex_m9_course_police_election50k_s78/final.zip"
    ))
    p.add_argument("--episodes", type=int, default=100)
    p.add_argument("--max-rounds", type=int, default=6)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--scenario", choices=[
        "talent_t1_improvise", "talent_t3_public",
    ], default="talent_t3_public")
    p.add_argument("--random", action="store_true")
    p.add_argument("--oracle", action="store_true")
    args = p.parse_args()
    if args.random and args.oracle:
        p.error("--random 与 --oracle 不能同时使用")

    result = run(
        None if (args.random or args.oracle) else args.model,
        args.episodes, args.max_rounds, args.seed, args.scenario,
        args.random, args.oracle,
    )
    tag = "ORACLE" if args.oracle else "RANDOM" if args.random else args.model
    lesson = "T1 即演" if args.scenario == "talent_t1_improvise" else "T3 公演"
    print(f"── {tag} | {lesson}课 | {args.episodes} 局 ──")
    if args.scenario == "talent_t3_public":
        print(f"  首次报名率         {result['first_registration_rate']:.0%}")
        print(f"  最终报名率         {result['registration_rate']:.0%}")
    print(f"  首次发动率         {result['first_activation_rate']:.0%}")
    print(f"  最终发动率         {result['activation_rate']:.0%}")
    print(f"  真实核心局率       {result['effect_rate']:.0%}")
    print(f"  天赋击杀率         {result['kill_rate']:.0%}")
    print(f"  正式胜率           {result['official_win_rate']:.0%}")
    print(f"  核心命中/局        {result['effect_hits_per_episode']:.2f}")
    if args.scenario == "talent_t1_improvise":
        print(f"  T1 伤害/局         {result['effect_damage_per_episode']:.2f}")
    top = ", ".join(
        f"{semantic}={count}({count / result['total_decisions']:.0%})"
        for semantic, count in result["decisions"].most_common(8)
    )
    print(f"  决策分布           {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
