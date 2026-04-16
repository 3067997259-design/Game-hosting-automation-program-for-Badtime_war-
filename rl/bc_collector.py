"""
rl/bc_collector.py
──────────────────
G7（星野）BasicAI 行为克隆数据收集器。

设计目标：
- 复用 BasicAIController 作为"老师"，运行全 AI 对局，强制某个玩家拿到 G7 天赋。
- 在该玩家每次被调用 get_command() / choose() 时，额外记录
  (obs, action_idx, mask) 三元组。
- 动作索引使用与 RL 完全相同的动作空间（ACTION_COUNT=130）。

动作索引映射策略：
- 正常 get_command：按 rl/action_space.py 的布局反向映射（0 – 107）。
- 战术宏模式（situation="hoshino_tactical_input"）：
    * "terminal"                     → IDX_CHOOSE_BASE + 0
    * 无参数战术动作 / 参数无槽位版    → IDX_CHOOSE_BASE + 1..10
    * 带目标槽位参数的射击/find/lock   → IDX_TALENT_T0_TARGET_BASE + slot (108-112)
    * 对自身的天赋发动                 → IDX_TALENT_T0_SELF (113)
- 战略 choose：按 options.index(choice) → IDX_CHOOSE_BASE + opt_idx。
  只记录 RL 实际会同步的 situation（非 _HEURISTIC_CHOOSE）。

用法：
    python -m rl.bc_collector --games 5000 --players 6 --output bc_data/g7
"""

from __future__ import annotations

import argparse
import os
import random
from typing import Any, Dict, List, Optional

import numpy as np

from controllers.ai_basic import BasicAIController
from rl.action_space import (
    ACTION_COUNT,
    IDX_ATTACK_BASE,
    IDX_CHOOSE_BASE,
    IDX_FIND_BASE,
    IDX_FORFEIT,
    IDX_INTERACT_BASE,
    IDX_LOCK_BASE,
    IDX_MOVE_BASE,
    IDX_POLICE_BASE,
    IDX_SPECIAL_BASE,
    IDX_TALENT_T0_SELF,
    IDX_TALENT_T0_TARGET_BASE,
    IDX_WAKE,
    INTERACT_ITEMS,
    LOCATIONS,
    POLICE_CMDS,
    SPECIAL_OPS,
    WEAPONS,
    build_action_mask,
    get_opponent_slots,
)
from rl.obs_builder import OBS_DIM, build_obs
from rl.rl_controller import RLController


# ─────────────────────────────────────────────────────────────────────────────
#  战术宏动作 → choose 索引偏移（IDX_CHOOSE_BASE + offset）
# ─────────────────────────────────────────────────────────────────────────────

_TACTICAL_CHOOSE_OFFSET: Dict[str, int] = {
    "terminal":      0,
    "架盾":          1,
    "重新装填":      2,
    "持盾":          3,
    "服药":          4,
    "冲刺":          5,
    "取消":          6,
    "转向":          7,
    "排弹":          8,
    "投掷":          9,
    "lock":          10,
    # 带目标的动作 ("射击"/"find") 特殊处理，映射到 target slot
}


# ─────────────────────────────────────────────────────────────────────────────
#  数据收集器
# ─────────────────────────────────────────────────────────────────────────────

class DataCollector:
    """收集 (obs, action_idx, mask) 三元组并保存为 .npz。"""

    def __init__(self):
        self.recording: bool = False
        self._obs_list: List[np.ndarray] = []
        self._action_list: List[int] = []
        self._mask_list: List[np.ndarray] = []

    def start_recording(self) -> None:
        self.recording = True

    def stop_recording(self) -> None:
        self.recording = False

    def add_sample(self, obs: np.ndarray, action_idx: int,
                   mask: np.ndarray) -> None:
        self._obs_list.append(obs.astype(np.float32, copy=True))
        self._action_list.append(int(action_idx))
        self._mask_list.append(mask.astype(bool, copy=True))

    @property
    def n_samples(self) -> int:
        return len(self._action_list)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if not self._action_list:
            print(f"[BC] Warning: no samples collected, nothing saved to {path}")
            return
        np.savez_compressed(
            path,
            obs=np.array(self._obs_list, dtype=np.float32),
            actions=np.array(self._action_list, dtype=np.int64),
            masks=np.array(self._mask_list, dtype=bool),
        )
        print(f"[BC] Saved {len(self._action_list)} samples to {path}")


# ─────────────────────────────────────────────────────────────────────────────
#  收集器 Controller
# ─────────────────────────────────────────────────────────────────────────────

class BCCollectorController(BasicAIController):
    """包装 BasicAIController，在每次决策后记录 (obs, action_idx, mask)。

    仅作为数据收集用途；其游戏行为与 BasicAIController 完全一致（super() 返回）。
    """

    # 与 rl/rl_controller.py 保持一致：这些 situation RL 走启发式，不进入动作空间，
    # 因此不应出现在 BC 数据中。
    _HEURISTIC_CHOOSE_SITUATIONS = RLController._HEURISTIC_CHOOSE

    def __init__(self, personality: str = "balanced",
                 collector: Optional[DataCollector] = None):
        super().__init__(personality=personality)
        self._collector = collector
        # 当前决策点上下文缓存（供 choose 使用）
        self._player_ref = None
        self._state_ref = None
        self._player_id: Optional[str] = None

    # ════════════════════════════════════════════════════════
    #  get_command —— 普通 / 战术宏模式
    # ════════════════════════════════════════════════════════

    def get_command(self, player, game_state, available_actions, context=None):
        # 缓存引用供 choose / 反向映射使用
        self._player_ref = player
        self._state_ref = game_state
        self._player_id = player.player_id

        command = super().get_command(player, game_state, available_actions, context)

        if self._collector is not None and self._collector.recording:
            try:
                self._record_get_command(
                    player, game_state, available_actions, context, command
                )
            except Exception:
                # 记录错误绝不能影响游戏流程
                pass

        return command

    def _record_get_command(self, player, game_state, available_actions,
                            context, command: str) -> None:
        situation = (context or {}).get("situation", "")

        # 战术宏 / 排弹 situation：只记录 "hoshino_tactical_input"，
        # 排弹（hoshino_reorder_ammo）不进 RL 动作空间，跳过。
        if situation == "hoshino_reorder_ammo":
            return

        action_idx = self._command_to_action_idx(
            command, player, game_state, situation
        )
        if action_idx is None:
            return

        # 战术宏模式的动作索引位于 108-129，属于 choose 动作空间；
        # 因此 obs 也必须标记为 choose 模式，否则 [512-514] 指示维度为 0，
        # 网络无法区分"正常 get_command"与"战术宏"两种动作空间（obs-action 错配）。
        is_tactical = (situation == "hoshino_tactical_input")

        if is_tactical:
            obs = build_obs(
                player, game_state, player.player_id,
                choose_mode=True,
                choose_situation=situation,
                choose_n_options=0,
            )
            mask = build_action_mask(
                player, game_state, player.player_id,
                choose_mode=True,
                choose_situation=situation,
                choose_options=[],
            )
            # 战术宏 mask 由 _build_choose_mask 处理，但它依赖于
            # choose_options；为避免 mask 过严，在战术宏下退化为允许
            # 108-129 全部合法（这部分 BC 训练自行从监督信号学习合法子集）。
            mask = mask.copy()
            mask[IDX_TALENT_T0_TARGET_BASE:IDX_CHOOSE_BASE + 16] = True
        else:
            obs = build_obs(player, game_state, player.player_id)
            mask = build_action_mask(player, game_state, player.player_id)

        if 0 <= action_idx < ACTION_COUNT and mask[action_idx]:
            self._collector.add_sample(obs, action_idx, mask)

    # ════════════════════════════════════════════════════════
    #  choose —— 战略决策
    # ════════════════════════════════════════════════════════

    def choose(self, prompt, options, context=None):
        choice = super().choose(prompt, options, context)

        if self._collector is not None and self._collector.recording \
                and self._player_ref is not None and options:
            situation = (context or {}).get("situation", "")
            # 跳过 RL 走启发式的 situation
            if situation not in self._HEURISTIC_CHOOSE_SITUATIONS:
                try:
                    if choice in options:
                        opt_idx = options.index(choice)
                        if 0 <= opt_idx < 16:
                            obs = build_obs(
                                self._player_ref, self._state_ref,
                                self._player_id,
                                choose_mode=True,
                                choose_situation=situation,
                                choose_n_options=len(options),
                            )
                            mask = build_action_mask(
                                self._player_ref, self._state_ref,
                                self._player_id,
                                choose_mode=True,
                                choose_situation=situation,
                                choose_options=list(options),
                            )
                            action_idx = IDX_CHOOSE_BASE + opt_idx
                            if mask[action_idx]:
                                self._collector.add_sample(
                                    obs, action_idx, mask
                                )
                except Exception:
                    pass

        return choice

    # ════════════════════════════════════════════════════════
    #  命令字符串 → 动作索引的反向映射
    # ════════════════════════════════════════════════════════

    def _command_to_action_idx(self, command: str, player, game_state,
                               situation: str) -> Optional[int]:
        cmd = (command or "").strip()
        if not cmd:
            return None

        if situation == "hoshino_tactical_input":
            return self._tactical_command_to_idx(cmd, player, game_state)

        if cmd == "forfeit":
            return IDX_FORFEIT
        if cmd == "wake":
            return IDX_WAKE

        if cmd.startswith("move "):
            loc = cmd[len("move "):].strip()
            if loc in LOCATIONS:
                return IDX_MOVE_BASE + LOCATIONS.index(loc)
            return None

        if cmd.startswith("interact "):
            item = cmd[len("interact "):].strip()
            if item in INTERACT_ITEMS:
                return IDX_INTERACT_BASE + INTERACT_ITEMS.index(item)
            return None

        if cmd.startswith("lock "):
            target_name = cmd[len("lock "):].strip()
            slot = self._name_to_slot(target_name, player, game_state)
            if slot is not None:
                return IDX_LOCK_BASE + slot
            return None

        if cmd.startswith("find "):
            target_name = cmd[len("find "):].strip()
            slot = self._name_to_slot(target_name, player, game_state)
            if slot is not None:
                return IDX_FIND_BASE + slot
            return None

        if cmd.startswith("attack "):
            parts = cmd.split()
            if len(parts) >= 3:
                target_name = parts[1]
                weapon_name = parts[2]
                slot = self._name_to_slot(target_name, player, game_state)
                if weapon_name in WEAPONS and slot is not None:
                    return IDX_ATTACK_BASE + slot * 10 + WEAPONS.index(weapon_name)
            return None

        if cmd.startswith("special "):
            op = cmd[len("special "):].strip()
            # special 可能带参数（e.g. "special 释放病毒"），取首关键字
            op_key = op.split()[0] if op else ""
            for i, sop in enumerate(SPECIAL_OPS):
                if op_key == sop or op_key.startswith(sop):
                    return IDX_SPECIAL_BASE + i
            return None

        # 警察命令（POLICE_CMDS 列表首词匹配）
        first_word = cmd.split()[0]
        if first_word in POLICE_CMDS:
            return IDX_POLICE_BASE + POLICE_CMDS.index(first_word)

        return None

    def _tactical_command_to_idx(self, cmd: str, player,
                                 game_state) -> Optional[int]:
        """将战术宏命令映射到 108-129 动作空间。"""
        if cmd.lower() == "terminal":
            return IDX_CHOOSE_BASE + _TACTICAL_CHOOSE_OFFSET["terminal"]

        parts = cmd.split(maxsplit=1)
        action = parts[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

        # 带目标槽位的动作
        if action == "射击":
            slot = self._name_to_slot(arg, player, game_state)
            if slot is not None:
                return IDX_TALENT_T0_TARGET_BASE + slot
            return None

        if action == "find":
            slot = self._name_to_slot(arg, player, game_state)
            if slot is not None:
                return IDX_TALENT_T0_TARGET_BASE + slot
            return None

        if action == "lock":
            slot = self._name_to_slot(arg, player, game_state)
            if slot is not None:
                return IDX_TALENT_T0_TARGET_BASE + slot
            # lock 作为无目标关键字：回退到通用槽位
            return IDX_CHOOSE_BASE + _TACTICAL_CHOOSE_OFFSET["lock"]

        # 无目标槽位的战术动作（包括带内嵌参数的：服药 海豚巧克力、冲刺 军事基地、
        # 投掷 闪光弹 军事基地、重新装填 EPO 等）—— 参数由启发式 choose 处理，
        # BC 只记录宏条目的动作类型
        if action in _TACTICAL_CHOOSE_OFFSET:
            return IDX_CHOOSE_BASE + _TACTICAL_CHOOSE_OFFSET[action]

        return None

    # ════════════════════════════════════════════════════════
    #  辅助：名字 → 对手槽位（0-4）
    # ════════════════════════════════════════════════════════

    def _name_to_slot(self, name: str, player, game_state) -> Optional[int]:
        """get_opponent_slots 返回 Player 对象列表；按 name / pid 匹配。"""
        if not name:
            return None
        try:
            slots = get_opponent_slots(player, game_state)
        except Exception:
            return None
        for slot, opp in enumerate(slots):
            if opp is None:
                continue
            if opp.name == name or getattr(opp, "player_id", None) == name:
                return slot
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  数据收集主流程
# ─────────────────────────────────────────────────────────────────────────────

def _find_g7_talent_entry(talent_table):
    """从 TALENT_TABLE 中找到 G7（星野/编号 14）的条目。"""
    for entry in talent_table:
        n, name, cls, desc = entry[0], entry[1], entry[2], entry[3]
        if n == 14:
            return entry
    return None


def run_collection(num_games: int, num_players: int, output_dir: str) -> None:
    """运行多局全 AI 对局，收集 G7 玩家的 BC 数据。"""
    from engine.game_state import GameState
    from engine.round_manager import RoundManager
    from models.player import Player
    from engine.game_setup import (
        TALENT_TABLE, AI_PERSONALITIES, _ai_pick_talent, AI_NAME_POOL,
    )
    from stats_runner import _silence_prompt_manager, _restore_prompt_manager
    from rl.env import _silence_display, _restore_display

    collector = DataCollector()
    total_g7_games = 0

    g7_entry = _find_g7_talent_entry(TALENT_TABLE)
    if g7_entry is None:
        raise RuntimeError("未在 TALENT_TABLE 中找到编号 14 的 G7 天赋")
    g7_num, g7_name, g7_cls, _g7_desc = g7_entry

    _silence_display()
    _silence_prompt_manager()
    try:
        for game_idx in range(num_games):
            state = GameState()
            names = list(AI_NAME_POOL)
            random.shuffle(names)

            g7_pid: Optional[str] = None
            personality_map: Dict[str, str] = {}

            for i in range(num_players):
                ai_name = names[i] if i < len(names) else f"AI_{i + 1}"
                personality = random.choice(AI_PERSONALITIES)
                pid = f"p{i + 1}"
                personality_map[pid] = personality

                if i == 0:
                    controller = BCCollectorController(
                        personality=personality, collector=collector,
                    )
                    g7_pid = pid
                else:
                    controller = BasicAIController(personality=personality)

                player = Player(pid, ai_name, controller=controller)  # type: ignore[abstract]
                state.add_player(player)

            random.shuffle(state.player_order)

            # 天赋分配：强制 g7_pid 拿 G7
            taken: set = set()
            for pid in state.player_order:
                p = state.get_player(pid)
                if p is None:
                    continue
                if pid == g7_pid:
                    talent_inst = g7_cls(pid, state)
                    p.talent = talent_inst
                    p.talent_name = g7_name
                    talent_inst.on_register()
                    taken.add(g7_num)
                else:
                    available = [
                        (n, name, cls, desc)
                        for n, name, cls, desc in TALENT_TABLE
                        if n not in taken and n != g7_num
                    ]
                    if not available:
                        continue
                    chosen = _ai_pick_talent(
                        personality_map.get(pid, "balanced"),
                        available, taken,
                    )
                    if chosen is None:
                        continue
                    n, name, cls = chosen  # type: ignore[misc]
                    talent_inst = cls(pid, state)
                    p.talent = talent_inst
                    p.talent_name = name
                    talent_inst.on_register()
                    taken.add(n)

            state.max_rounds = GameState.compute_default_max_rounds(num_players)

            collector.start_recording()
            round_mgr = RoundManager(state)
            try:
                round_mgr.run_game_loop()
            except Exception:
                state.game_over = True
            collector.stop_recording()
            total_g7_games += 1

            if (game_idx + 1) % 100 == 0:
                print(f"[BC] Game {game_idx + 1}/{num_games}, "
                      f"samples so far: {collector.n_samples}")
    finally:
        _restore_prompt_manager()
        _restore_display()

    output_path = os.path.join(output_dir, "g7_bc_data.npz")
    collector.save(output_path)
    print(f"[BC] Collection done. {total_g7_games} G7 games, "
          f"{collector.n_samples} total samples.")


def main():
    parser = argparse.ArgumentParser(description="G7 BC 数据收集")
    parser.add_argument("--games", type=int, default=5000,
                        help="游戏局数（默认 5000）")
    parser.add_argument("--players", type=int, default=6,
                        help="每局玩家数（默认 6）")
    parser.add_argument("--output", type=str, default="bc_data/g7",
                        help="输出目录（默认 bc_data/g7）")
    args = parser.parse_args()
    run_collection(args.games, args.players, args.output)


if __name__ == "__main__":
    main()
