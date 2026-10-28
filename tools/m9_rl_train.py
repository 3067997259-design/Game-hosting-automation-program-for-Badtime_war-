"""
tools/m9_rl_train.py
────────────────────
M9-rfc 下的 maskable-recurrent 训练冒烟（向退出标准 2 推进：loss 稳健/下行 + 胜率曲线）。

运行：
    python tools/m9_rl_train.py --steps 40000 --n-envs 8 --vec subproc \
        --eval-every 8000 --eval-episodes 8 --hidden 96 --out checkpoints/m9_p1

要点：
- profile：子进程工厂内部也 set_profile('m9-rfc')（spawn 子进程不继承主进程 profile）；
- 并行：SubprocVecEnv(8) 多进程，配合 torch 线程抑制（自测 20 逻辑核建议 8×1~2 线程）；
- 评估：rl/winrate_eval 带状态线胜率（单一信源）；
- 训练后解析 TensorBoard 事件的 train/loss 序列，输出 头/尾/趋势 以证明 loss 没有发散。
"""

from __future__ import annotations

import argparse
import os as _os
import sys
from pathlib import Path

# 允许从仓库根直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 线程抑制须在 import numpy/torch 前
_os.environ.setdefault("OMP_NUM_THREADS", "1")
_os.environ.setdefault("MKL_NUM_THREADS", "1")
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np  # noqa: E402
import torch as th  # noqa: E402
from time import perf_counter as th_timer  # noqa: E402
from stable_baselines3.common.utils import get_schedule_fn  # noqa: E402

from engine.experiments import set_profile  # noqa: E402
from rl.env import BadtimeWarEnv  # noqa: E402
from rl.maskable_recurrent import MaskableRecurrentPPO  # noqa: E402
from rl.winrate_eval import evaluate_policy  # noqa: E402

PROFILE = "m9-rfc"


def _next_eval_step(current_step: int, every: int) -> int:
    """返回严格晚于当前步数的下一评估点，供 checkpoint 续训使用。"""
    if every <= 0:
        raise ValueError("eval_every 必须为正整数")
    return (max(0, int(current_step)) // every + 1) * every


def _opponents_for_worker(
    opponent_bots: list[str] | None,
    num_opponents: int,
    worker_idx: int,
) -> list[str] | None:
    """为并行 worker 分配课程对手。

    1v1 时允许传入带重复项的对手池，按 worker 循环分配，重复次数即回放权重；
    多人局继续保留原有“列表按座位指定”语义。
    """
    if not opponent_bots:
        return None
    if num_opponents == 1 and len(opponent_bots) > 1:
        selected = opponent_bots[worker_idx % len(opponent_bots)]
        # ``basic_ai`` 是 1v1 worker 池中的哨兵：None 会让 env 走真实
        # BasicAIController；其余名称继续走 BOT_REGISTRY。这样同一次 PPO
        # 更新可以混合完整对局与少量旧课回放，而不伪造 BasicAI 行为。
        return None if selected == "basic_ai" else [selected]
    return opponent_bots


def _personality_lineup_for_worker(
    lineups: list[list[str]] | None,
    num_opponents: int,
    worker_idx: int,
) -> list[str] | None:
    """Cycle full-BasicAI personality lineups across vector workers.

    A one-token ``random`` lineup preserves the natural uniform draw, so a
    difficult-personality lesson can keep ordinary full-game replay workers.
    """
    if not lineups:
        return None
    selected = lineups[worker_idx % len(lineups)]
    if selected == ["random"]:
        return None
    if len(selected) != num_opponents:
        raise ValueError(
            "每个人格配对必须等于 --n-opponents，或单独写 random"
        )
    return selected


def _course_scenario_for_worker(
    scenarios: list[str] | None, worker_idx: int,
) -> str | None:
    """课程场景按 worker 循环分配；重复项编码回放权重。"""
    if not scenarios:
        return None
    value = scenarios[worker_idx % len(scenarios)]
    return None if value == "none" else value


def _rl_talent_for_scenario(
    rl_talent: int | None, scenario: str | None,
) -> int | None:
    """专项场景固定对应天赋；none worker 保留调用方的基础回放配置。"""
    if scenario == "talent_t1_improvise":
        return 1
    if scenario == "talent_t3_public":
        return 3
    return rl_talent


def _action_contract_for_scenario(
    base_contract: str | None,
    scenario: str | None,
    isolate_combat_scenarios: bool,
) -> str | None:
    """Mixed full-game workers may keep combat-course replay narrowly masked."""
    if isolate_combat_scenarios and scenario in {
        "combat_multi_quarter_hp", "combat_multi_half_hp", "combat_multi_full_hp",
        "combat_retarget", "combat_retarget_static", "combat_assassin_pressure",
    }:
        return "combat_core"
    return base_contract


def _override_learning_rate(model, learning_rate: float) -> None:
    """续训时同时替换 schedule 与已恢复 optimizer 的当前学习率。"""
    if learning_rate <= 0:
        raise ValueError("learning_rate 必须为正数")
    model.learning_rate = float(learning_rate)
    model.lr_schedule = get_schedule_fn(float(learning_rate))
    for group in model.policy.optimizer.param_groups:
        group["lr"] = float(learning_rate)


def make_env(num_opponents: int, n_stack: int, seed: int, max_rounds: int | None = None,
             rl_talent: int | None = 0, opponent_bots: list | None = None,
             opponent_personalities: list[str] | None = None,
             enable_talents: bool = True, enable_opponent_talents: bool = True,
             action_contract: str | None = None,
             course_scenario: str | None = None):
    """返回创建 m9 BadtimeWarEnv 的闭包（子进程工厂内部重设 profile）。

    rl_talent: None=每局均匀随机天赋（M9 全员标配，RL 也应有天赋——历史教训：
    让 RL 自选会退化到总挑小序号，故用随机而非自选）；0=无天赋（对照）。
    opponent_bots: 简单 bot 名单（dummy/turtle/rush/dodge/archer），弱对手课程梯级。
    enable_talents: False = 全员无天赋。课程 Rung 0 用它剥离天赋噪声，
    让"胜/负"只由 move-find-attack 链决定（带天赋时天赋效果会替 RL 制造战果）。
    """
    def _init() -> BadtimeWarEnv:
        set_profile(PROFILE)  # spawn 子进程不继承主进程 profile
        env = BadtimeWarEnv(
            num_opponents=num_opponents, max_rounds=max_rounds, n_stack=n_stack,
            enable_talents=enable_talents, rl_talent=rl_talent,
            enable_opponent_talents=enable_opponent_talents,
            opponent_bots=opponent_bots,
            opponent_personalities=opponent_personalities,
            action_contract=action_contract,
            course_scenario=course_scenario,
        )
        env.reset(seed=seed)
        return env
    return _init


def main() -> int:
    p = argparse.ArgumentParser(description="M9 maskable-recurrent 训练冒烟")
    p.add_argument("--steps", type=int, default=40000)
    p.add_argument("--n-envs", type=int, default=8)
    p.add_argument("--n-opponents", type=int, default=2,
                   help="RL 对手数（默认 2=3 人局；1=1v1 二元胜负，课程起点，随机基线 50%%）")
    p.add_argument("--max-rounds", type=int, default=40,
                   help="每局最大轮数（越小决策越密、单局越短；瓶颈在引擎步进而非 CPU）")
    p.add_argument("--n-steps", type=int, default=128)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--hidden", type=int, default=96)
    p.add_argument("--eval-every", type=int, default=8000)
    p.add_argument("--ckpt-every", type=int, default=100000,
                   help="周期保存 checkpoint 的步数（大预算可中断/可观察）")
    p.add_argument("--eval-episodes", type=int, default=8)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", type=str, default="checkpoints/m9_p1")
    p.add_argument("--torch-threads", type=int, default=2)
    p.add_argument("--device", type=str, default="cpu")
    p.add_argument("--learning-rate", type=float, default=3e-4,
                   help="PPO 学习率；跨课程续训建议 3e-5，降低灾难性遗忘")
    p.add_argument("--vec", type=str, default="subproc", choices=["dummy", "subproc"])
    p.add_argument("--disable-fastpath", action="store_true",
                   help="对照用：把 LSTM 序列处理换回上游慢路径（测量快路径化提速）")
    p.add_argument("--rl-talent", type=str, default="random",
                   help="RL 天赋：random=每局均匀随机（默认；RL 自选会退化到挑小序号，故不提供 model 档）"
                        " / 0=无天赋（对照） / 1-14=指定天赋")
    p.add_argument("--pretrain", type=str, default=None,
                   help="BC warm-start zip 路径（bc_migrate 产出）：载入其 policy 为初始"
                        "（架构按 warm 模板 lstm_hidden=192/net_arch=[384,256]，LSTM 随机、输出头已预热）")
    p.add_argument("--resume", type=str, default=None,
                   help="从同 schema 的 PPO checkpoint 继续训练；--steps 表示追加步数，"
                        "并保留优化器/LSTM/累计步数")
    p.add_argument("--opponent-bots", type=str, default=None,
                   help="逗号分隔的对手 worker 池（dummy/turtle/rush/dodge/archer/basic_ai），弱对手课程"
                        "（缺省=完整 BasicAI；1v1 多名称会按 worker 循环分配，重复项可设回放权重；"
                        "basic_ai 表示该 worker 使用真实 BasicAIController；"
                         "多人局仍按对手座位指定）")
    p.add_argument(
        "--opponent-personalities", type=str, default=None,
        help=(
            "分号分隔的完整 BasicAI 人格配对，配对内用逗号分隔；"
            "按 worker 循环分配，单独写 random 可保留自然随机回放。"
            "例: political,political;assassin,political;random"
        ),
    )
    p.add_argument("--no-talents", action="store_true",
                   help="全员无天赋：课程 Rung 0 用于剥离天赋噪声，使胜负只由攻击链决定")
    p.add_argument("--no-opponent-talents", action="store_true",
                   help="只关闭对手天赋；天赋专项课用来隔离 RL 天赋的因果信号")
    p.add_argument(
        "--action-contract", choices=[
            "auto", "none", "combat_core", "combat_economy", "economy_course",
            "economy_integration_course",
            "pp_course", "survival_course", "police_course", "talent_course",
            "equipment_course",
        ], default="auto",
        help="课程动作合同；auto 在 no-talents+全 dummy 时启用 combat_core",
    )
    p.add_argument(
        "--course-scenarios", type=str, default=None,
        help="逗号分隔的课程场景：combat_multi_quarter_hp/"
             "combat_multi_half_hp/"
             "combat_multi_full_hp/"
             "combat_assassin_pressure/"
             "combat_retarget_static/combat_retarget/"
             "economy_bow_supply/economy_pursuit_supply/"
             "economy_armored_pursuit_supply/"
             "economy_armored_pursuit_supply_slot0/"
             "pp_damage_finish/pp_damage_choice/"
             "survival_heal_counterattack/"
             "survival_armor_counterattack/"
             "survival_armor_repair/"
             "equipment_sharpen_chain/"
             "visibility_thermal_chain/visibility_magic_chain/"
             "visibility_tech_chain/attribute_magic_armor_choice/"
             "attribute_tech_armor_choice/"
             "police_command/police_election/"
             "talent_t1_improvise/talent_t3_public/none；"
             "按 worker 循环分配，重复项可设置专项与基础回放权重",
    )
    p.add_argument(
        "--isolate-combat-scenarios", action="store_true",
        help="混合 full-game/retarget worker 时，仅对 combat_* worker 施加 combat_core 合同",
    )
    args = p.parse_args()
    if args.pretrain and args.resume:
        p.error("--pretrain 与 --resume 不能同时使用")

    opponent_bots = None
    if args.opponent_bots:
        opponent_bots = [s.strip() for s in args.opponent_bots.split(",") if s.strip()]
    personality_lineups = None
    if args.opponent_personalities:
        personality_lineups = [
            [part.strip() for part in lineup.split(",") if part.strip()]
            for lineup in args.opponent_personalities.split(";")
            if lineup.strip()
        ]
        try:
            for worker_idx in range(len(personality_lineups)):
                _personality_lineup_for_worker(
                    personality_lineups, args.n_opponents, worker_idx,
                )
        except ValueError as exc:
            p.error(str(exc))
    course_scenarios = None
    if args.course_scenarios:
        course_scenarios = [
            s.strip() for s in args.course_scenarios.split(",") if s.strip()
        ]
        unknown = set(course_scenarios) - {
            "none", "combat_multi_quarter_hp", "combat_multi_half_hp",
            "combat_multi_full_hp",
            "combat_assassin_pressure",
            "combat_retarget_static", "combat_retarget",
            "economy_bow_supply", "economy_pursuit_supply",
            "economy_armored_pursuit_supply",
            "economy_armored_pursuit_supply_slot0",
            "pp_damage_finish", "pp_damage_choice",
            "survival_heal_counterattack",
            "survival_armor_counterattack",
            "survival_armor_repair",
            "equipment_sharpen_chain",
            "visibility_thermal_chain", "visibility_magic_chain",
            "visibility_tech_chain", "attribute_magic_armor_choice",
            "attribute_tech_armor_choice",
            "police_command", "police_election",
            "talent_t1_improvise", "talent_t3_public",
        }
        if unknown:
            p.error(f"未知 --course-scenarios: {sorted(unknown)}")

    rl_talent: int | None = None if args.rl_talent == "random" else int(args.rl_talent)
    print(f"rl_talent={args.rl_talent!r}（{'每局随机' if rl_talent is None else rl_talent}）")

    set_profile(PROFILE)
    th.set_num_threads(args.torch_threads)
    print(f"profile={PROFILE} | threads={args.torch_threads} | n_envs={args.n_envs} "
          f"vec={args.vec} | steps={args.steps}")

    # 子进程免 GPU：spawn 子进程 import CUDA-torch 会每进程 commit 数 GB 虚拟内存，
    # 多 worker 下可触发 WinError 1455（页面文件不足，tools/probe_env_scaling.py 实证）。
    # 仅在 cpu 档（默认/推荐，GPU 对本管线无收益）设置；父进程 device=cpu 不初始化
    # CUDA，改环境变量无副作用，子进程继承后直接省掉 CUDA commit 与换页抖动。
    if args.vec == "subproc" and args.device == "cpu":
        _os.environ["CUDA_VISIBLE_DEVICES"] = ""

    enable_talents = not args.no_talents
    enable_opponent_talents = not args.no_opponent_talents
    if args.action_contract == "auto":
        scenario_set = set(course_scenarios or []) - {"none"}
        has_talent_course = any(
            scenario.startswith("talent_")
            for scenario in (course_scenarios or [])
        )
        has_police_course = any(
            scenario.startswith("police_")
            for scenario in (course_scenarios or [])
        )
        has_economy_course = bool({
            "economy_bow_supply", "economy_pursuit_supply",
            "economy_armored_pursuit_supply",
            "economy_armored_pursuit_supply_slot0",
        } & scenario_set)
        has_pp_course = bool({
            "pp_damage_finish", "pp_damage_choice",
        } & scenario_set)
        has_survival_course = bool({
            "survival_heal_counterattack", "survival_armor_counterattack",
            "survival_armor_repair",
        } & scenario_set)
        has_equipment_course = bool({
            "equipment_sharpen_chain", "visibility_thermal_chain",
            "visibility_magic_chain", "visibility_tech_chain",
            "attribute_magic_armor_choice", "attribute_tech_armor_choice",
        } & scenario_set)
        action_contract = (
            "talent_course" if has_talent_course else
            "police_course" if has_police_course else
            "economy_course" if has_economy_course else
            "pp_course" if has_pp_course else
            "survival_course" if has_survival_course else
            "equipment_course" if has_equipment_course else
            "combat_core" if scenario_set else
            ("combat_core"
             if (not enable_talents and opponent_bots
                 and all(bot == "dummy" for bot in opponent_bots))
             else None)
        )
    else:
        action_contract = None if args.action_contract == "none" else args.action_contract
    print(f"action_contract={action_contract or 'full_m9'}")
    def make(i):
        scenario = _course_scenario_for_worker(course_scenarios, i)
        return make_env(
            num_opponents=args.n_opponents, n_stack=1, seed=args.seed + i,
            max_rounds=args.max_rounds,
            rl_talent=_rl_talent_for_scenario(rl_talent, scenario),
            opponent_bots=_opponents_for_worker(opponent_bots, args.n_opponents, i),
            opponent_personalities=_personality_lineup_for_worker(
                personality_lineups, args.n_opponents, i,
            ),
            enable_talents=enable_talents,
            enable_opponent_talents=enable_opponent_talents,
            action_contract=_action_contract_for_scenario(
                action_contract, scenario, args.isolate_combat_scenarios,
            ),
            course_scenario=scenario,
        )
    if args.n_opponents == 1 and opponent_bots and len(opponent_bots) > 1:
        assignments = [
            (_opponents_for_worker(opponent_bots, 1, i) or ["basic_ai"])[0]
            for i in range(args.n_envs)
        ]
        print(f"1v1 worker 对手分配: {assignments}")
    if personality_lineups:
        personality_assignments = [
            _personality_lineup_for_worker(
                personality_lineups, args.n_opponents, i,
            ) or ["random"]
            for i in range(args.n_envs)
        ]
        print(f"worker BasicAI 人格分配: {personality_assignments}")
    if course_scenarios:
        scenario_assignments = [
            _course_scenario_for_worker(course_scenarios, i) or "none"
            for i in range(args.n_envs)
        ]
        print(f"worker 课程场景分配: {scenario_assignments}")
        if args.isolate_combat_scenarios:
            print("combat_* worker 动作合同: combat_core；none worker 保持基础合同")
    eval_course_scenario = next(
        (s for s in (course_scenarios or []) if s != "none"), None,
    )
    # 1v1 混合 worker 池用第 0 桶作为统一评估对手；推荐把 basic_ai
    # 放在列表首位，使训练曲线始终回答完整对局表现而不是回放 bot 表现。
    eval_opponent_bots = _opponents_for_worker(
        opponent_bots, args.n_opponents, 0,
    )
    eval_opponent_personalities = _personality_lineup_for_worker(
        personality_lineups, args.n_opponents, 0,
    )
    if args.vec == "subproc" and args.n_envs > 1:
        from stable_baselines3.common.vec_env import SubprocVecEnv
        venv = SubprocVecEnv([make(i) for i in range(args.n_envs)], start_method="spawn")
    else:
        from stable_baselines3.common.vec_env import DummyVecEnv
        venv = DummyVecEnv([make(i) for i in range(args.n_envs)])

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_dir = out_dir / "tb"

    model = MaskableRecurrentPPO(
        "MlpLstmPolicy", venv,
        learning_rate=args.learning_rate,
        n_steps=args.n_steps, batch_size=args.batch_size, n_epochs=4,
        gamma=0.99, gae_lambda=0.95, clip_range=0.2, ent_coef=0.01,
        seed=args.seed, device=args.device, verbose=0,
        policy_kwargs=dict(lstm_hidden_size=args.hidden, enable_critic_lstm=True,
                           net_arch=dict(pi=[args.hidden], vf=[args.hidden])),
        tensorboard_log=str(log_dir),
    )
    if args.pretrain:
        # BC warm-start：架构按 bc_migrate 模板（lstm_hidden=192 / net_arch=[384,256]）
        from rl.maskable_recurrent import MaskableRecurrentPPO as _MRP
        from rl.schema import validate_checkpoint_against_env

        validate_checkpoint_against_env(
            args.pretrain, venv, context="BC warm-start",
        )
        warm = _MRP.load(args.pretrain, device=args.device)
        warm_sd = warm.policy.state_dict()
        # 重建为 warm 架构并载入（LSTM 随机、输出侧已预热）
        model = MaskableRecurrentPPO(
            "MlpLstmPolicy", venv,
            learning_rate=args.learning_rate,
            n_steps=args.n_steps, batch_size=args.batch_size, n_epochs=4,
            gamma=0.99, gae_lambda=0.95, clip_range=0.2, ent_coef=0.01,
            seed=args.seed, device=args.device, verbose=0,
            policy_kwargs=dict(lstm_hidden_size=192, enable_critic_lstm=True,
                               net_arch=dict(pi=[384, 256], vf=[384, 256])),
            tensorboard_log=str(log_dir),
        )
        missing, unexpected = model.policy.load_state_dict(warm_sd, strict=True)
        print(f"[pretrain] 载入 warm-start 初始策略 ← {args.pretrain} "
              f"(missing={len(missing)}, unexpected={len(unexpected)})")
    if args.resume:
        from rl.schema import validate_checkpoint_against_env

        validate_checkpoint_against_env(
            args.resume, venv, context="PPO resume",
        )
        model = MaskableRecurrentPPO.load(
            args.resume, env=venv, device=args.device,
            tensorboard_log=str(log_dir),
        )
        _override_learning_rate(model, args.learning_rate)
        print(
            f"[resume] 从 {args.resume} 的 {model.num_timesteps} 步继续，"
            f"本次追加 {args.steps} 步，learning_rate={args.learning_rate:g}"
        )
    start_timesteps = int(model.num_timesteps)
    print("obs_dim:", model.observation_space.shape[0], "| act_dim:", model.action_space.n)

    # ── 测量对照：等价于上游 _process_sequence 的慢路径（验证快路径化提速）──
    if args.disable_fastpath:
        from rl.maskable_recurrent.policies import MaskableRecurrentActorCriticPolicy
        from sb3_contrib.ppo_recurrent.policies import RecurrentActorCriticPolicy
        # 上游 _process_sequence 是 @staticmethod（无 self）；包一层适配实例方法签名
        MaskableRecurrentActorCriticPolicy._process_sequence = (
            lambda self, *a, **k: RecurrentActorCriticPolicy._process_sequence(*a, **k)
        )
        print("[measure] fast-path 已禁用（对照基线）")

    # 周期评估回调（带状态线，num_timesteps 采样）
    from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback
    curve: list[tuple[int, float]] = []

    class _Eval(BaseCallback):
        def __init__(self, every, eps, seed_, max_rounds_, rl_talent_, n_opponents_,
                     opponent_bots_, opponent_personalities_,
                     enable_talents_=True, start_step_=0,
                     course_scenario_=None, enable_opponent_talents_=True):
            super().__init__(verbose=0)
            self.every = every; self.eps = eps; self.seed = seed_
            self.max_rounds = max_rounds_
            self.rl_talent = rl_talent_
            self.n_opponents = n_opponents_
            self.opponent_bots = opponent_bots_
            self.opponent_personalities = opponent_personalities_
            self.enable_talents = enable_talents_
            self.enable_opponent_talents = enable_opponent_talents_
            self.course_scenario = course_scenario_
            self.next = _next_eval_step(start_step_, every)
        def _on_step(self):
            ts = self.num_timesteps
            if ts >= self.next:
                self.next += self.every
                report = evaluate_policy(
                    self.model, num_opponents=self.n_opponents, n_stack=1, n_episodes=self.eps,
                    seed=9999 + self.seed, recurrent=True,
                    env_factory=lambda: make_env(self.n_opponents, 1, 77777 + self.seed,
                                                 max_rounds=self.max_rounds,
                                                 rl_talent=self.rl_talent,
                                                 opponent_bots=self.opponent_bots,
                                                 opponent_personalities=self.opponent_personalities,
                                                 enable_talents=self.enable_talents,
                                                 enable_opponent_talents=self.enable_opponent_talents,
                                                 action_contract=_action_contract_for_scenario(
                                                     action_contract,
                                                     self.course_scenario,
                                                     args.isolate_combat_scenarios,
                                                 ),
                                                 course_scenario=self.course_scenario)(),
                )
                wr = report.official_win_rate
                if wr is None:
                    wr = report.observed_win_rate
                curve.append((ts, float(wr)))
                print(
                    f"[eval] step {ts}: official_win_rate={wr:.0%}, "
                    f"settlement={report.official_settlement_rate:.0%}, "
                    f"mean_rank={report.mean_rank:.2f} "
                    f"({self.eps} 局, stateful)"
                )
            return True

    ckpt = CheckpointCallback(
        save_freq=max(args.ckpt_every // args.n_envs, 1),
        save_path=str(out_dir / "ckpt"),
        name_prefix="m9", verbose=1,
    )
    _t0 = th_timer()
    model.learn(
        total_timesteps=args.steps,
        callback=[ckpt, _Eval(args.eval_every, args.eval_episodes, args.seed,
                               args.max_rounds,
                               _rl_talent_for_scenario(rl_talent, eval_course_scenario),
                              args.n_opponents, eval_opponent_bots,
                              eval_opponent_personalities,
                              enable_talents, start_timesteps,
                              eval_course_scenario, enable_opponent_talents)],
        progress_bar=False, tb_log_name="m9",
        reset_num_timesteps=not bool(args.resume),
    )
    _learn_s = th_timer() - _t0
    print(f"[measure] 学习段 {_learn_s:.1f}s → {args.steps / max(_learn_s, 1e-9):.1f} steps/s", flush=True)

    # 终局评估
    final_report = evaluate_policy(
        model, num_opponents=args.n_opponents, n_stack=1, n_episodes=args.eval_episodes,
        seed=9999 + args.seed, recurrent=True,
        env_factory=lambda: make_env(args.n_opponents, 1, 77777 + args.seed,
                                     max_rounds=args.max_rounds,
                                     rl_talent=_rl_talent_for_scenario(
                                         rl_talent, eval_course_scenario,
                                     ),
                                     opponent_bots=eval_opponent_bots,
                                     opponent_personalities=eval_opponent_personalities,
                                     enable_talents=enable_talents,
                                     enable_opponent_talents=enable_opponent_talents,
                                     action_contract=_action_contract_for_scenario(
                                         action_contract, eval_course_scenario,
                                         args.isolate_combat_scenarios,
                                     ),
                                     course_scenario=eval_course_scenario)(),
    )
    wr = final_report.official_win_rate
    if wr is None:
        wr = final_report.observed_win_rate
    final_step = int(model.num_timesteps)
    curve.append((final_step, float(wr)))
    print(
        f"[final] official_win_rate={wr:.0%} | "
        f"settlement={final_report.official_settlement_rate:.0%} | "
        f"mean_rank={final_report.mean_rank:.2f} | "
        f"mean_return={final_report.mean_return:.2f}"
    )
    nf = sum(1 for _, pa in model.policy.named_parameters()
             if not bool(th.isfinite(pa).all()))
    print("非有限参数:", nf)

    model.save(str(out_dir / "final"))
    model.env.close()

    # 解析 TensorBoard 事件：train/loss 趋势（证明收敛/不发散）
    try:
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
        event_files = list(Path(log_dir).rglob("events*"))
        root = event_files[0].parent if event_files else log_dir
        acc = EventAccumulator(str(root), size_guidance={"scalars": 0}); acc.Reload()
        losses = [s.value for s in acc.Scalars("train/loss")] if "train/loss" in acc.Tags()["scalars"] else []
        if losses:
            head = losses[: min(5, len(losses))]
            tail = losses[-min(5, len(losses)):]
            print(f"train/loss 样本 {len(losses)}: 头={[round(x,3) for x in head]} 尾={[round(x,3) for x in tail]}")
            head_mean = float(np.mean(losses[:max(len(losses)//3, 1)]))
            tail_mean = float(np.mean(losses[-max(len(losses)//3, 1):]))
            direction = "下行" if tail_mean < head_mean else "上行/未收敛"
            print(
                f"loss 首段均值 {head_mean:.4f} / 末段均值 {tail_mean:.4f}"
                f"（{direction}）"
            )
        else:
            print("注意：未在事件中读到 train/loss（样本过少或 tb 未落盘）")
        rews = acc.Scalars("rollout/ep_rew_mean") if "rollout/ep_rew_mean" in acc.Tags()["scalars"] else []
        if rews:
            print("ep_rew_mean 头/尾:", round(rews[0].value,2), round(rews[-1].value,2))
    except Exception as e:  # noqa: BLE001
        print("tensorboard 解析失败(不影响训练结果):", e)

    print("胜率曲线:", " -> ".join(f"{s}:{w:.0%}" for s, w in curve))
    print("模型:", out_dir / "final.zip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
