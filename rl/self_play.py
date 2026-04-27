"""
rl/self_play.py
───────────────
Self-play 对手控制器 + 对手池管理。
"""

from __future__ import annotations
import logging
import os
import random
from pathlib import Path
from typing import Any, Optional, List, Dict, TYPE_CHECKING

import multiprocessing
import torch
import numpy as np
from sb3_contrib import MaskablePPO

from controllers.base import PlayerController
from rl.action_space import (
    ACTION_COUNT,
    IDX_FORFEIT,
    IDX_CHOOSE_BASE,
    build_action_mask,
    idx_to_choose_option,
    idx_to_command,
)
from rl.obs_builder import OBS_DIM, build_obs
from rl.rl_controller import RLController

if TYPE_CHECKING:
    from stable_baselines3.common.base_class import BaseAlgorithm

logger = logging.getLogger(__name__)


class _BaseOpponentRLController(RLController):
    """Self-play 对手控制器的内部公共基类。

    负责 obs 堆叠、action mask 构造、choose/confirm 模式分发、跨局状态重置等
    共用逻辑。子类只需实现 ``_predict_action(obs, mask) -> int`` 来把观测 +
    掩码映射到具体的动作索引。

    与 _SyncRLController 不同，对手控制器不需要与 env 线程同步；它在游戏引擎
    后台线程中被调用时，直接用自己的模型做推理。继承 RLController 以复用
    choose()/choose_multi()/confirm()/on_event() 的启发式逻辑。
    """

    def __init__(self, n_stack: int = 30):
        # 子进程中单样本推理（batch_size=1），多线程并行没有收益，
        # 反而会导致多个子进程之间的线程争抢，必须限制为单线程。
        # 仅在 SubprocVecEnv 子进程中生效，避免 DummyVecEnv（n_envs=1）
        # 时覆盖主进程的线程数设置。
        if multiprocessing.current_process().name != "MainProcess":
            torch.set_num_threads(1)
        super().__init__()
        self.n_stack = n_stack
        self._obs_stack = np.zeros(OBS_DIM * n_stack, dtype=np.float32)
        self._player_id: Optional[str] = None

    def _predict_action(self, obs: np.ndarray, mask: np.ndarray) -> int:
        """子类必须实现：从堆叠后的观测和合法动作 mask 产出动作索引。"""
        raise NotImplementedError

    def _stack_obs(self, raw_obs: np.ndarray) -> np.ndarray:
        if self.n_stack <= 1:
            return raw_obs
        self._obs_stack[:-OBS_DIM] = self._obs_stack[OBS_DIM:]
        self._obs_stack[-OBS_DIM:] = raw_obs
        return self._obs_stack.copy()

    def reset_stack(self):
        """每局开始时重置帧堆叠缓冲。"""
        self._obs_stack = np.zeros(OBS_DIM * self.n_stack, dtype=np.float32)

    def reset_game_state(self):
        """重置所有跨局会泄漏的状态（用于 stats_runner 等复用 controller 实例的场景）。

        除了 reset_stack()，还清理继承自 RLController 的 per-game 状态：
          - _event_log：否则跨局无限增长，造成内存泄漏
          - _threat_scores：否则上一局的威胁分会干扰启发式 target 选择
          - _been_attacked_by / _round_number
          - pending_action_idx / last_action_type / last_action_success
          - _player_ref / _state_ref / _player_id
        """
        self.reset_stack()
        self._event_log.clear()
        self._threat_scores.clear()
        self._been_attacked_by.clear()
        self._round_number = 0
        self.pending_action_idx = 0
        self.last_action_type = ""
        self.last_action_success = True
        self._player_ref = None
        self._state_ref = None
        self._player_id = None

    def get_command(
        self,
        player: Any,
        game_state: Any,
        available_actions: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        # 缓存 player/state 引用，供 _rl_choose 使用
        self._cache_player_ref(player, game_state)

        # 记录 player_id（首次调用时）
        if self._player_id is None:
            self._player_id = player.player_id

        # 处理重试（和 _SyncRLController 一样的逻辑）
        attempt = (context or {}).get("attempt", 1)
        if attempt > 1:
            return "forfeit"

        # 构建观测
        raw_obs = build_obs(player, game_state, player.player_id)
        obs = self._stack_obs(raw_obs)

        # 构建 action mask
        mask = build_action_mask(player, game_state, player.player_id)

        # 模型推理（deterministic=True，对手用确定性策略）
        action = self._predict_action(obs, mask)

        # 翻译为 CLI 命令
        return idx_to_command(action, player, game_state)

    def _rl_choose(
        self,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        """用模型推理做 choose 决策（覆写基类的默认实现）。"""
        if not options:
            return ""
        if len(options) == 1:
            return options[0]

        player = self._player_ref
        state = self._state_ref
        if player is None or state is None:
            return options[0]

        situation = (context or {}).get("situation", "")

        # 构建观测
        raw_obs = build_obs(player, state, player.player_id)

        # 填充 choose 模式指示维（与训练 env._fill_choose_obs 保持一致）
        # 尾部 3 维 [520-522]：[current_mode=1.0, situation_id/30, n_options/16]
        from rl.obs_builder import _CHOOSE_SITUATION_MAP, _MAX_CHOOSE_SITUATIONS
        base = OBS_DIM - 3  # 520
        raw_obs[base] = 1.0
        raw_obs[base + 1] = _CHOOSE_SITUATION_MAP.get(situation, 0) / max(_MAX_CHOOSE_SITUATIONS, 1)
        raw_obs[base + 2] = min(len(options), 16) / 16.0

        obs = self._stack_obs(raw_obs)

        # 复用训练时的 mask 构造器：target-selection situation 会启用 108-113，
        # 其余通用 situation 启用 114-129，与训练端完全一致
        mask = build_action_mask(
            player, state, player.player_id,
            choose_mode=True,
            choose_situation=situation,
            choose_options=options,
        )

        # 模型推理
        action = self._predict_action(obs, mask)

        # 用 env 侧同款翻译逻辑把索引还原为选项字符串
        return idx_to_choose_option(action, options, situation, player, state)

    def _rl_confirm(
        self,
        prompt: str,
        context: Optional[Dict] = None,
    ) -> bool:
        """用模型推理做 confirm 决策（与训练 env._SyncRLController 一致）。

        复用 _rl_choose 处理 2 选项 ["是", "否"]，避免响应窗口 / 强买通行证
        等决策永远返回 False 而浪费模型学到的策略。
        """
        result = self._rl_choose(prompt, ["是", "否"], context)
        return result == "是"

    def set_player_ref(self, player, state):
        """手动设置 player 和 state 引用（用于 stats_runner 等非 env 场景）。"""
        self._player_ref = player
        self._state_ref = state
        if self._player_id is None:
            self._player_id = player.player_id


class OpponentRLController(_BaseOpponentRLController):
    """基于完整 MaskablePPO.predict() 的 self-play 对手控制器。

    适用于没有 TorchScript 导出版本的 checkpoint（回退路径），以及
    stats_runner 等只手里有 ``.zip`` 的外部调用场景。推理链路上走 SB3 的
    Python/numpy 包装，开销比 ``TorchScriptRLController`` 更高；推荐优先
    使用后者。
    """

    def __init__(self, model_path: str | None = None, n_stack: int = 30,
                 *, _model: MaskablePPO | None = None):
        super().__init__(n_stack=n_stack)
        if _model is not None:
            self.model = _model
        elif model_path is not None:
            self.model = MaskablePPO.load(model_path)
        else:
            raise ValueError("Either model_path or _model must be provided")

    def _predict_action(self, obs: np.ndarray, mask: np.ndarray) -> int:
        action, _ = self.model.predict(obs, action_masks=mask, deterministic=True)
        return int(action)


class TorchScriptRLController(_BaseOpponentRLController):
    """使用 TorchScript 导出模型的轻量级对手控制器。

    相比 :class:`OpponentRLController`（走完整 ``MaskablePPO.predict()``），
    本控制器直接调用 ``torch.jit`` 模型的 ``forward()`` 获取 logits，跳过
    SB3 的 Python/numpy 包装层，推理速度实测提升 2–5 倍。

    行为等价于 ``MaskablePPO.predict(..., action_masks=mask, deterministic=True)``：
    先算 logits，再把 mask 为 ``False`` 的位置置为 ``-inf``，最后取 argmax。
    """

    def __init__(
        self,
        pts_path: str | None = None,
        n_stack: int = 30,
        *,
        _jit_model: "torch.jit.ScriptModule | None" = None,
    ):
        super().__init__(n_stack=n_stack)
        if _jit_model is not None:
            self.jit_model = _jit_model
        elif pts_path is not None:
            jit_model = torch.jit.load(pts_path)
            jit_model.eval()
            self.jit_model = jit_model
        else:
            raise ValueError("Either pts_path or _jit_model must be provided")

    def _predict_action(self, obs: np.ndarray, mask: np.ndarray) -> int:
        with torch.no_grad():
            obs_tensor = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0)
            logits = self.jit_model(obs_tensor)  # (1, ACTION_COUNT)
            mask_tensor = torch.as_tensor(mask, dtype=torch.bool)
            logits[0, ~mask_tensor] = float("-inf")
            return int(logits.argmax(dim=1).item())


class OpponentPool:
    """
    对手模型池。

    管理历史 checkpoint 的存储、加载和采样。
    支持混入 BasicAI 以保证多样性。
    """

    def __init__(
        self,
        pool_dir: str,
        n_stack: int = 30,
        max_pool_size: int = 20,
        basic_ai_prob: float = 0.3,  # 30% 概率使用 BasicAI 而非历史模型
    ):
        self.pool_dir = Path(pool_dir)
        self.pool_dir.mkdir(parents=True, exist_ok=True)
        self.n_stack = n_stack
        self.max_pool_size = max_pool_size
        self.basic_ai_prob = basic_ai_prob
        # 模型缓存：可能同时包含 MaskablePPO 实例（由 .zip 加载）和
        # torch.jit.ScriptModule 实例（由 .pts 加载），用不同的
        # cache_key（完整文件路径，含 .zip / .pts 后缀）区分。
        self._model_cache: Dict[str, Any] = {}

        # ELO 评分系统
        self.elo_scores: Dict[str, float] = {}
        self.elo_k = 32.0
        self.elo_default = 1000.0
        self.elo_cull_threshold = 800.0
        self.elo_min_games = 10
        self.elo_game_counts: Dict[str, int] = {}

    def update_elo(self, model_stem: str, opponent_stem: str, win: bool):
        """更新两个模型的 ELO 评分。"""
        if model_stem not in self.elo_scores:
            self.elo_scores[model_stem] = self.elo_default
        if opponent_stem not in self.elo_scores:
            self.elo_scores[opponent_stem] = self.elo_default

        ra = self.elo_scores[model_stem]
        rb = self.elo_scores[opponent_stem]
        ea = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
        sa = 1.0 if win else 0.0

        self.elo_scores[model_stem] = ra + self.elo_k * (sa - ea)
        self.elo_scores[opponent_stem] = rb + self.elo_k * ((1 - sa) - (1 - ea))

        self.elo_game_counts[model_stem] = self.elo_game_counts.get(model_stem, 0) + 1
        self.elo_game_counts[opponent_stem] = self.elo_game_counts.get(opponent_stem, 0) + 1

    def save_current_model(self, model: BaseAlgorithm, step: int):
        """保存当前模型到对手池，同时导出 TorchScript 快推理版本。"""
        path = self.pool_dir / f"opponent_step_{step}"
        model.save(str(path))

        # 同时导出 TorchScript 版本用于快速推理（导出失败时回退到完整模型）
        try:
            from rl.export_torchscript import export_torchscript
            pts_path = self.pool_dir / f"opponent_step_{step}.pts"
            export_torchscript(model, str(pts_path), n_stack=self.n_stack)
        except Exception as e:
            logger.warning("TorchScript 导出失败，将回退到完整模型: %s", e)

        # 初始化新模型的 ELO
        stem = f"opponent_step_{step}"
        self.elo_scores[stem] = self.elo_default
        self.elo_game_counts[stem] = 0

        # 如果超出池大小，删除最旧的
        self._cleanup_old_models()

    def _cleanup_old_models(self):
        """淘汰低 ELO 模型，保留最新的 max_pool_size 个。"""
        models = sorted(self.pool_dir.glob("opponent_step_*.zip"), key=lambda p: p.stat().st_mtime)

        # 先淘汰 ELO 低于阈值且对战局数足够的模型
        to_remove = []
        for m in models[:-3]:  # 至少保留最新 3 个
            stem = m.stem
            games = self.elo_game_counts.get(stem, 0)
            elo = self.elo_scores.get(stem, self.elo_default)
            if games >= self.elo_min_games and elo < self.elo_cull_threshold:
                to_remove.append(m)

        for m in to_remove:
            self._remove_model(m)
            models.remove(m)

        # 然后按原有逻辑：超出 max_pool_size 时删除最旧的
        while len(models) > self.max_pool_size:
            oldest = models.pop(0)
            self._remove_model(oldest)

    def _remove_model(self, model_path: Path):
        """从磁盘和缓存中移除一个模型。"""
        cache_key = str(model_path)
        if cache_key in self._model_cache:
            del self._model_cache[cache_key]
        pts_file = model_path.with_suffix(".pts")
        pts_key = str(pts_file)
        if pts_key in self._model_cache:
            del self._model_cache[pts_key]
        # 清理 ELO 记录
        stem = model_path.stem
        self.elo_scores.pop(stem, None)
        self.elo_game_counts.pop(stem, None)
        model_path.unlink(missing_ok=True)
        if pts_file.exists():
            pts_file.unlink()

    def get_available_models(self) -> List[Path]:
        """返回池中所有可用的模型路径。"""
        return sorted(self.pool_dir.glob("opponent_step_*.zip"))

    def sample_opponent_controller(self) -> tuple[PlayerController, str]:
        """
        从对手池中采样一个控制器。

        有 basic_ai_prob 的概率返回 BasicAI，
        否则按 ELO 加权从历史模型中采样。
        如果池为空，总是返回 BasicAI。

        返回 (controller, model_stem)，model_stem 用于 ELO 更新。
        BasicAI 返回 "basic_ai"。
        """
        from controllers.ai_basic import create_random_ai_controller

        available = self.get_available_models()

        # 池为空或随机选择 BasicAI
        if not available or random.random() < self.basic_ai_prob:
            return create_random_ai_controller(player_name="AI"), "basic_ai"

        # 清理缓存中已不存在于磁盘的模型（主进程可能已删除旧文件）
        # 同一个 checkpoint 可能有 .zip 和 .pts 两个 cache entry，都要照顧到
        available_set = {str(p) for p in available}
        available_set |= {str(p.with_suffix(".pts")) for p in available}
        stale_keys = [k for k in self._model_cache if k not in available_set]
        for k in stale_keys:
            del self._model_cache[k]

        # 按 ELO 加权采样（ELO 越高被选中概率越大）
        weights = []
        for p in available:
            elo = self.elo_scores.get(p.stem, self.elo_default)
            weights.append(max(elo - 600, 1.0))
        chosen = random.choices(available, weights=weights, k=1)[0]
        available = [chosen]  # 只尝试加载选中的那个

        for model_path in available:
            cache_key = str(model_path)
            pts_path = model_path.with_suffix(".pts")
            pts_key = str(pts_path)
            try:
                # 优先尝试加载 TorchScript 版本（快路径）
                if pts_path.exists():
                    if pts_key not in self._model_cache:
                        jit_model = torch.jit.load(str(pts_path))
                        jit_model.eval()
                        self._model_cache[pts_key] = jit_model
                    ctrl = TorchScriptRLController(
                        n_stack=self.n_stack,
                        _jit_model=self._model_cache[pts_key],
                    )
                    return ctrl, model_path.stem

                # 回退：加载完整 MaskablePPO
                if cache_key not in self._model_cache:
                    self._model_cache[cache_key] = MaskablePPO.load(str(model_path))
                ctrl = OpponentRLController(
                    n_stack=self.n_stack,
                    _model=self._model_cache[cache_key],
                )
                return ctrl, model_path.stem
            except (FileNotFoundError, OSError) as e:
                # 主进程的 _cleanup_old_models 可能在 glob 和 load 之间删除了文件
                logger.warning("对手模型加载失败（可能已被清理）: %s — %s", model_path, e)
                self._model_cache.pop(cache_key, None)
                self._model_cache.pop(pts_key, None)
                continue

        # 所有模型都加载失败，回退到 BasicAI
        return create_random_ai_controller(player_name="AI"), "basic_ai"

    def __getstate__(self):
        state = self.__dict__.copy()
        state['_model_cache'] = {}  # 不序列化模型缓存
        # elo_scores 和 elo_game_counts 保留（轻量级，可序列化）
        return state

