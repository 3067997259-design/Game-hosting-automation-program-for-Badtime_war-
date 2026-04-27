"""
rl/watch_all.py
───────────────
观战脚本（完整版）：同时显示 RL 智能体和所有 BasicAI 对手的行动。

特性：
  • 支持天赋局（默认启用），开局显示各玩家天赋分配
  • 每步 RL 状态显示天赋关键字段
  • 对手摘要显示天赋名与特殊状态标记（Terror/救世主/锚定/超新星）
  • choose 决策可视化（显示 situation 与选项列表）
  • AI 行动日志含天赋信息
  • 对局结束输出详细玩家状态 + 天赋/choose 决策统计
  • 支持 --games N 多局批量模式，输出胜率与天赋选择分布

用法：
    python -m rl.watch_all --model <模型路径> --opponents 3 --n-stack 30
    python -m rl.watch_all --model <模型路径> --opponents 2 --no-talents
    python -m rl.watch_all --model <模型路径> --opponents 5 --games 20
    python -m rl.watch_all --model <模型路径> --opponents 5 --games 20 --rl-talent random
"""

import random as _random
import sys
import threading
from collections import Counter
from typing import Optional

import numpy as np
from sb3_contrib import MaskablePPO

from engine.game_setup import TALENT_TABLE
from rl.env import BadtimeWarEnv
from rl.action_space import (
    ACTION_COUNT, LOCATIONS, INTERACT_ITEMS, WEAPONS,
    SPECIAL_OPS, POLICE_CMDS,
    IDX_FORFEIT, IDX_WAKE, IDX_MOVE_BASE, IDX_INTERACT_BASE,
    IDX_LOCK_BASE, IDX_FIND_BASE, IDX_ATTACK_BASE,
    IDX_SPECIAL_BASE, IDX_POLICE_BASE,
    IDX_TALENT_T0_TARGET_BASE, IDX_TALENT_T0_SELF, IDX_CHOOSE_BASE,
    get_opponent_slots,
)


def _resolve_rl_talent(raw: str | None) -> int | None:
    """将 --rl-talent 参数解析为 BadtimeWarEnv 接受的 int | None。

    - None / "none" → None（RL 自选）
    - "random"      → 从 TALENT_TABLE 中随机选一个编号
    - "0"           → 0（无天赋）
    - "1"-"14"      → 对应天赋编号
    """
    if raw is None or raw.lower() == "none":
        return None
    if raw.lower() == "random":
        available = [n for n, name, cls, desc in TALENT_TABLE]
        return _random.choice(available)
    try:
        return int(raw)
    except ValueError:
        print(f"警告: 无法识别的 --rl-talent 值 '{raw}'，使用自选模式")
        return None


# ─────────────────────────────────────────────────────────────
#  动作名翻译
# ─────────────────────────────────────────────────────────────

def _slot_name(slot: int, player, game_state) -> str:
    if player is None or game_state is None:
        return f"slot{slot}"
    opponents = get_opponent_slots(player, game_state)
    if slot < 0 or slot >= len(opponents):
        return f"slot{slot}(越界)"
    opp = opponents[slot]
    return opp.name if opp else f"slot{slot}(空)"


def action_name(idx: int, player=None, game_state=None) -> str:
    """将动作索引翻译为人类可读的描述。"""
    if idx == IDX_FORFEIT:
        return "放弃行动 (forfeit)"
    if idx == IDX_WAKE:
        return "起床 (wake)"
    if IDX_MOVE_BASE <= idx < IDX_INTERACT_BASE:
        return f"移动 → {LOCATIONS[idx - IDX_MOVE_BASE]}"
    if IDX_INTERACT_BASE <= idx < IDX_LOCK_BASE:
        return f"交互: {INTERACT_ITEMS[idx - IDX_INTERACT_BASE]}"
    if IDX_LOCK_BASE <= idx < IDX_FIND_BASE:
        slot = idx - IDX_LOCK_BASE
        name = _slot_name(slot, player, game_state)
        return f"锁定: {name} (slot {slot})"
    if IDX_FIND_BASE <= idx < IDX_ATTACK_BASE:
        slot = idx - IDX_FIND_BASE
        name = _slot_name(slot, player, game_state)
        return f"寻找: {name} (slot {slot})"
    if IDX_ATTACK_BASE <= idx < IDX_SPECIAL_BASE:
        offset = idx - IDX_ATTACK_BASE
        target_slot = offset // 10
        weapon_slot = offset % 10
        name = _slot_name(target_slot, player, game_state)
        return f"攻击: {name} 使用 {WEAPONS[weapon_slot]}"
    if IDX_SPECIAL_BASE <= idx < IDX_POLICE_BASE:
        return f"特殊: {SPECIAL_OPS[idx - IDX_SPECIAL_BASE]}"
    if IDX_POLICE_BASE <= idx < IDX_TALENT_T0_TARGET_BASE:
        return f"警察: {POLICE_CMDS[idx - IDX_POLICE_BASE]}"
    # ── 天赋扩展 ──
    if IDX_TALENT_T0_TARGET_BASE <= idx <= IDX_TALENT_T0_TARGET_BASE + 4:
        slot = idx - IDX_TALENT_T0_TARGET_BASE
        name = _slot_name(slot, player, game_state)
        return f"天赋T0: 对 {name} 发动 (slot {slot})"
    if idx == IDX_TALENT_T0_SELF:
        return "天赋T0: 对自己发动"
    if IDX_CHOOSE_BASE <= idx < IDX_CHOOSE_BASE + 16:
        opt_idx = idx - IDX_CHOOSE_BASE
        return f"选择选项 #{opt_idx}"
    return f"未知动作 ({idx})"


def action_name_with_context(idx, player, game_state, env) -> str:
    """带 choose 上下文的动作名翻译。"""
    if env._choose_mode and IDX_CHOOSE_BASE <= idx < IDX_CHOOSE_BASE + 16:
        opt_idx = idx - IDX_CHOOSE_BASE
        options = env._choose_options
        context = env._choose_context
        situation = context.get("situation", "unknown")
        chosen = options[opt_idx] if opt_idx < len(options) else f"选项{opt_idx}(越界)"
        return f"[choose:{situation}] → {chosen}"
    return action_name(idx, player, game_state)


# ─────────────────────────────────────────────────────────────
#  天赋状态辅助
# ─────────────────────────────────────────────────────────────

def _print_talent_status(talent, cls_name):
    """打印天赋的关键状态信息。"""
    parts = []
    if hasattr(talent, 'uses_remaining'):
        parts.append(f"剩余次数={talent.uses_remaining}")
    if hasattr(talent, 'charges'):
        parts.append(f"充能={talent.charges}")
    if hasattr(talent, 'consecutive_actions'):
        parts.append(f"连续行动={talent.consecutive_actions}")
    if hasattr(talent, 'reminiscence'):
        parts.append(f"追忆={talent.reminiscence}")
    if hasattr(talent, 'anchor_active') and talent.anchor_active:
        parts.append(f"锚定中(剩余{getattr(talent, 'anchor_rounds_remaining', '?')}轮)")
    if hasattr(talent, 'is_savior') and talent.is_savior:
        parts.append(f"救世主状态(神性={getattr(talent, 'divinity', 0)})")
    if hasattr(talent, 'is_terror') and talent.is_terror:
        parts.append(f"Terror状态(HP={getattr(talent, 'terror_extra_hp', 0)})")
    if hasattr(talent, 'has_supernova') and talent.has_supernova:
        parts.append("超新星可用")
    if hasattr(talent, 'debuff_started') and talent.debuff_started:
        parts.append("失熵症已触发")
    if hasattr(talent, 'laugh_points'):
        parts.append(f"笑点={talent.laugh_points}")
    if hasattr(talent, 'cutaway_charges'):
        parts.append(f"插入式笑话充能={talent.cutaway_charges}")
    if hasattr(talent, 'form') and talent.form:
        parts.append(f"形态={talent.form}")
    if hasattr(talent, 'cost'):
        parts.append(f"cost={talent.cost}")
    if parts:
        print(f"      状态: {' | '.join(parts)}")


def _get_talent_brief(talent) -> str:
    """返回天赋的简短状态字符串（用于行内显示）。"""
    parts = []
    if hasattr(talent, 'is_terror') and talent.is_terror:
        parts.append("Terror")
    if hasattr(talent, 'is_savior') and talent.is_savior:
        parts.append("救世主")
    if hasattr(talent, 'has_supernova') and talent.has_supernova:
        parts.append("超新星")
    if hasattr(talent, 'anchor_active') and talent.anchor_active:
        parts.append("锚定")
    return ','.join(parts) if parts else ""


# ─────────────────────────────────────────────────────────────
#  线程安全的行动日志
# ─────────────────────────────────────────────────────────────

class _ActionLog:
    """线程安全的行动日志，用于捕获后台线程中 AI 的行动。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: list[dict] = []

    def append(self, entry: dict):
        with self._lock:
            self._entries.append(entry)

    def drain(self) -> list[dict]:
        """取出并清空所有日志条目。"""
        with self._lock:
            entries = self._entries[:]
            self._entries.clear()
            return entries


# ─────────────────────────────────────────────────────────────
#  包装 AI 控制器
# ─────────────────────────────────────────────────────────────

def _wrap_ai_controller(player, action_log):
    ctrl = player.controller
    original_get_command = ctrl.get_command

    def logged_get_command(player, game_state, available_actions, context=None):
        cmd = original_get_command(
            player=player,
            game_state=game_state,
            available_actions=available_actions,
            context=context,
        )
        attempt = context.get("attempt", 1) if context else 1
        if attempt == 1:
            action_log.append({
                "round": game_state.current_round,
                "player": player.name,
                "player_id": player.player_id,
                "cmd": cmd,
                "hp": player.hp,
                "max_hp": player.max_hp,
                "location": player.location,
                "vouchers": getattr(player, "vouchers", 0),
                "weapons": [w.name for w in getattr(player, "weapons", [])],
                "armor": [a.name for a in player.armor.get_all_active()] if hasattr(player.armor, "get_all_active") else [],
                "personality": getattr(ctrl, "personality", "?"),
                "virus_immunity": any(
                    getattr(a, "name", "") == "防毒面具"
                    for a in (player.armor.get_all_active() if hasattr(player.armor, "get_all_active") else [])
                ) or getattr(player, "has_seal", False),
                "kills": getattr(player, "kills", 0),
                "talent_name": getattr(player, 'talent_name', None) or "无",
                "talent_status": _get_talent_brief(player.talent) if player.talent else "",
            })
        return cmd

    ctrl.get_command = logged_get_command


# ─────────────────────────────────────────────────────────────
#  打印 AI 行动日志
# ─────────────────────────────────────────────────────────────

def _print_ai_actions(actions):
    if not actions:
        return
    print(f"\n  📋 期间 AI 行动 ({len(actions)} 条):")
    current_round = None
    for e in actions:
        r = e.get("round", "?")
        if r != current_round:
            if current_round is not None:
                print("    └─")
            print(f"    ┌─ 轮次 {r} ─")
            current_round = r

        name = e.get("player", "?")
        personality = e.get("personality", "?")
        hp = e.get("hp", 0)
        max_hp = e.get("max_hp", 0)
        loc = e.get("location", "?")
        cmd = e.get("cmd", "?")
        vouchers = e.get("vouchers", 0)
        weapons = e.get("weapons", [])
        armor = e.get("armor", [])
        immunity = "🛡️免疫" if e.get("virus_immunity", False) else ""

        talent_name = e.get("talent_name", "无")
        talent_status = e.get("talent_status", "")
        talent_str = f"天赋=【{talent_name}】"
        if talent_status:
            talent_str += f"[{talent_status}]"

        print(
            f"    │ [{name}] ({personality}) "
            f"HP={hp:.1f}/{max_hp:.1f} "
            f"位置={loc} "
            f"{talent_str} "
            f"凭证={vouchers} "
            f"武器={weapons} "
            f"护甲={armor} "
            f"{immunity}"
        )
        print(f"    │   >>> {cmd}")
    print("    └─")


# ─────────────────────────────────────────────────────────────
#  主观战函数
# ─────────────────────────────────────────────────────────────

def watch_all(
    model_path: str,
    num_opponents: int = 1,
    max_rounds: Optional[int] = None,
    n_stack: int = 1,
    enable_talents: bool = True,
    rl_talent: Optional[int] = None,
    verbose: bool = True,
):
    """
    跑一局可视化对局。

    Parameters
    ----------
    verbose : bool
        True  → 详细打印每步信息（单局观战）
        False → 静默跑，仅返回结果 dict（多局批量用）
    """
    if verbose:
        print(f"加载模型: {model_path}")
    model = MaskablePPO.load(model_path)

    env = BadtimeWarEnv(
        num_opponents=num_opponents,
        max_rounds=max_rounds,
        render_mode=None,
        n_stack=n_stack,
        enable_talents=enable_talents,
        rl_talent=rl_talent,
    )

    obs, info = env.reset()
    state = env._state
    player = env._rl_player
    assert state is not None
    assert player is not None

    # 包装所有 AI 控制器
    action_log = _ActionLog()
    for pid in state.player_order:
        p = state.get_player(pid)
        if p and p.player_id != "rl_0":
            _wrap_ai_controller(p, action_log)

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"  对局开始: RL_Agent vs {num_opponents} AI")
        print(f"  最大轮数: {state.max_rounds}")
        print(f"\n  玩家顺序及天赋:")
        for i, pid in enumerate(state.player_order):
            p = state.get_player(pid)
            if p:
                is_rl = pid == "rl_0"
                role = "🤖 RL" if is_rl else f"🎮 AI ({getattr(p.controller, 'personality', '?')})"
                talent_name = getattr(p, 'talent_name', None) or "无天赋"
                talent_cls = p.talent.__class__.__name__ if p.talent else ""
                print(f"    {i+1}. {p.name} — {role} — 【{talent_name}】({talent_cls})")
        print(f"{'=' * 60}\n")

    done = False
    step_num = 0
    action_history: list[str] = []

    # 清空 reset 期间产生的 AI 日志（起床等）
    initial_ai_actions = action_log.drain()
    if verbose and initial_ai_actions:
        print("  📋 开局阶段 AI 行动:")
        _print_ai_actions(initial_ai_actions)
        print()

    terminated = False
    truncated = False

    while not done:
        mask = info["action_masks"]
        valid_actions = mask.nonzero()[0]

        # 模型预测
        action, _ = model.predict(obs, action_masks=mask, deterministic=True)
        action = int(action)

        desc = action_name_with_context(action, player, state, env)

        if verbose:
            # 打印 RL 状态
            print(f"── 第 {step_num + 1} 步 (轮次 {state.current_round}) ──")
            print(f"  🤖 RL_Agent:")
            print(f"    HP: {player.hp:.1f}/{player.max_hp:.1f}")
            print(f"    位置: {player.location or '未知'}")
            print(f"    凭证: {player.vouchers}")
            weapons = [w.name for w in player.weapons] if player.weapons else []  # type: ignore
            print(f"    武器: {weapons}")
            armor_names = [a.name for a in player.armor.get_all_active()] if player.armor else []
            print(f"    护甲: {armor_names}")

            # 天赋信息
            talent = getattr(player, 'talent', None)
            if talent:
                talent_name = getattr(player, 'talent_name', '未知')
                talent_cls = talent.__class__.__name__
                print(f"    天赋: 【{talent_name}】({talent_cls})")
                _print_talent_status(talent, talent_cls)
            else:
                print(f"    天赋: 无")

            print(f"    合法动作数: {len(valid_actions)}")

            # choose 模式额外上下文
            if env._choose_mode:
                situation = env._choose_context.get("situation", "unknown")
                options = env._choose_options
                print(f"    [Choose 模式] situation={situation}")
                print(f"    选项: {options}")

            print(f"    >>> 选择: {desc}")

            # 打印对手摘要（天赋增强版）
            opponents = get_opponent_slots(player, state)
            for i, opp in enumerate(opponents):
                if opp is not None and opp.is_alive():
                    personality = getattr(opp.controller, 'personality', '?')
                    opp_talent = getattr(opp, 'talent_name', None) or "无"
                    opp_talent_cls = opp.talent.__class__.__name__ if opp.talent else ""
                    talent_brief = ""
                    if opp.talent:
                        _parts = []
                        if hasattr(opp.talent, 'is_terror') and opp.talent.is_terror:
                            _parts.append("Terror!")
                        if hasattr(opp.talent, 'is_savior') and opp.talent.is_savior:
                            _parts.append("救世主!")
                        if hasattr(opp.talent, 'anchor_active') and opp.talent.anchor_active:
                            _parts.append("锚定中")
                        if hasattr(opp.talent, 'has_supernova') and opp.talent.has_supernova:
                            _parts.append("超新星")
                        if _parts:
                            talent_brief = f" [{','.join(_parts)}]"
                    print(
                        f"    [对手 {opp.name}({personality})] "
                        f"HP={opp.hp:.1f} 位置={opp.location or '?'} "
                        f"击杀={opp.kill_count} "
                        f"天赋=【{opp_talent}】{talent_brief}"
                    )

        action_history.append(desc)

        # 执行动作
        obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated
        step_num += 1

        if verbose:
            print(f"    奖励: {reward:.2f}")

            ai_actions = action_log.drain()
            if ai_actions:
                _print_ai_actions(ai_actions)

            if state.virus.is_active:
                print(f"  🦠 病毒状态: 激活 (倒计时={state.virus.countdown})")

            print()
        else:
            # 非详细模式下也要清空日志，防止堆积
            action_log.drain()

    rl_won = state.winner == "rl_0"
    rl_talent_name = getattr(player, 'talent_name', None) or "无"

    if verbose:
        # 对局结束
        print(f"{'=' * 60}")
        print(f"  对局结束!")
        print(f"  总步数: {step_num}")
        print(f"  最终轮次: {state.current_round}")
        print(f"  胜者: {state.winner}")
        print(f"  RL 结果: {'🏆 胜利!' if rl_won else '💀 失败'}")

        print(f"\n  最终玩家状态:")
        for pid in state.player_order:
            p = state.get_player(pid)
            if p:
                status = "✅存活" if p.is_alive() else "❌死亡"
                is_rl = pid == "rl_0"
                role = "RL" if is_rl else getattr(p.controller, 'personality', '?')
                talent_name = getattr(p, 'talent_name', None) or "无"
                winner_mark = " 👑" if pid == state.winner else ""
                print(
                    f"    {p.name}({role}): HP={p.hp:.1f} "
                    f"位置={p.location or '?'} "
                    f"击杀={p.kill_count} "
                    f"天赋=【{talent_name}】 [{status}]{winner_mark}"
                )
        print(f"{'=' * 60}")

        # 行动统计
        print(f"\n── RL 行动统计 ──")
        action_types: Counter = Counter()
        for d in action_history:
            atype = d.split(":")[0].split("→")[0].split("(")[0].strip()
            action_types[atype] += 1
        total = max(len(action_history), 1)
        for atype, count in action_types.most_common():
            pct = count / total * 100
            print(f"  {atype}: {count} 次 ({pct:.1f}%)")

        # 天赋决策统计
        choose_actions = [d for d in action_history if d.startswith("[choose:")]
        if choose_actions:
            print(f"\n── RL 天赋/Choose 决策统计 ──")
            choose_types: Counter = Counter()
            for d in choose_actions:
                situation = d.split(":")[1].split("]")[0] if ":" in d else "unknown"
                choose_types[situation] += 1
            for sit, count in choose_types.most_common():
                print(f"  {sit}: {count} 次")

    env.close()

    return {
        "rl_won": rl_won,
        "winner": state.winner,
        "steps": step_num,
        "final_round": state.current_round,
        "rl_talent": rl_talent_name,
        "rl_hp": player.hp,
        "terminated": terminated,
        "truncated": truncated,
        "action_history": action_history,
    }


def watch_all_silent(
    model_path: str,
    num_opponents: int = 1,
    max_rounds: Optional[int] = None,
    n_stack: int = 1,
    enable_talents: bool = True,
    rl_talent: Optional[int] = None,
):
    """watch_all 的静默版，返回结果 dict。"""
    return watch_all(
        model_path=model_path,
        num_opponents=num_opponents,
        max_rounds=max_rounds,
        n_stack=n_stack,
        enable_talents=enable_talents,
        rl_talent=rl_talent,
        verbose=False,
    )


# ─────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="观战 RL 对局（完整版，含 AI 行动）")
    p.add_argument("--model", type=str, required=True, help="模型路径 (.zip)")
    p.add_argument("--opponents", type=int, default=1)
    p.add_argument("--max-rounds", type=int, default=None)
    p.add_argument("--n-stack", type=int, default=30)
    p.add_argument("--enable-talents", action="store_true", default=True,
                   help="启用天赋系统（默认启用）")
    p.add_argument("--no-talents", action="store_false", dest="enable_talents",
                   help="关闭天赋系统（向后兼容旧模型）")
    p.add_argument("--rl-talent", type=str, default=None,
                   help="RL 天赋（None=自选, 0=无天赋, 1-14=指定, random=随机）")
    p.add_argument("--games", type=int, default=1,
                   help="连续跑多局并汇总统计")
    args = p.parse_args()

    if args.games > 1:
        wins = 0
        total_steps = 0
        talent_picks: Counter = Counter()
        print(f"\n{'=' * 60}")
        print(f"  批量模式: 连续跑 {args.games} 局")
        print(f"  对手数: {args.opponents}  天赋={'启用' if args.enable_talents else '关闭'}")
        print(f"{'=' * 60}\n")

        for i in range(args.games):
            resolved_talent = _resolve_rl_talent(args.rl_talent)
            result = watch_all_silent(
                model_path=args.model,
                num_opponents=args.opponents,
                max_rounds=args.max_rounds,
                n_stack=args.n_stack,
                enable_talents=args.enable_talents,
                rl_talent=resolved_talent,
            )
            if result["rl_won"]:
                wins += 1
            total_steps += result["steps"]
            talent_picks[result["rl_talent"]] += 1
            status = "🏆胜" if result["rl_won"] else "💀负"
            print(
                f"  [{i+1}/{args.games}] {status} "
                f"步数={result['steps']} "
                f"轮次={result['final_round']} "
                f"天赋={result['rl_talent']} "
                f"胜者={result['winner']}"
            )

        print(f"\n{'=' * 60}")
        print(f"  {args.games} 局汇总")
        print(f"  RL 胜率: {wins}/{args.games} ({wins/args.games*100:.1f}%)")
        print(f"  平均步数: {total_steps/args.games:.1f}")
        print(f"  天赋选择分布:")
        for name, cnt in talent_picks.most_common():
            print(f"    {name}: {cnt} 次 ({cnt/args.games*100:.1f}%)")
        print(f"{'=' * 60}")
    else:
        resolved_talent = _resolve_rl_talent(args.rl_talent)
        watch_all(
            model_path=args.model,
            num_opponents=args.opponents,
            max_rounds=args.max_rounds,
            n_stack=args.n_stack,
            enable_talents=args.enable_talents,
            rl_talent=resolved_talent,
        )
