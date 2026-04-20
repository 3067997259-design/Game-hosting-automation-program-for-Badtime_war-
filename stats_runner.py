"""
自动胜率统计脚本
用法: python stats_runner.py --players <人数> --games <局数>
"""

import argparse
import random
import sys
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
import unicodedata

# Suppress prompt_manager init prints
_real_stdout = sys.stdout
sys.stdout = open(os.devnull, 'w')
try:
    from engine.game_state import GameState
    from engine.round_manager import RoundManager
    from engine.game_setup import (
        TALENT_TABLE, AI_TALENT_PREFERENCE, AI_PERSONALITIES,
        AI_NAME_POOL, TALENT_DECAY_FACTOR, _ai_pick_talent,
        AI_DISABLED_TALENTS,
    )
    from models.player import Player
    from controllers.ai_basic import BasicAIController
    from cli import display as _display_module
    from engine.prompt_manager import prompt_manager

    # RL 模型支持（可选）
    _rl_available = False
    try:
        from rl.self_play import OpponentRLController
        from rl.obs_builder import OBS_DIM
        _rl_available = True
    except ImportError:
        pass
finally:
    sys.stdout = _real_stdout

# ── Display silencing (copied from rl/env.py) ──
_DISPLAY_FUNCS = [
    "show_banner", "show_round_header", "show_phase", "show_d4_results",
    "show_action_turn_header", "show_player_status", "show_available_actions",
    "show_result", "show_error", "show_info", "show_victory", "show_death",
    "show_police_status", "show_virus_status", "show_police_enforcement",
    "show_virus_deaths", "show_all_players_status", "show_help",
    "show_critical", "show_warning", "show_prompt", "clear_screen",
]
_original_display: dict[str, Any] = {}


def _silence_display():
    for name in _DISPLAY_FUNCS:
        if hasattr(_display_module, name):
            _original_display[name] = getattr(_display_module, name)
            setattr(_display_module, name, lambda *a, **kw: None)
    if hasattr(_display_module, "prompt_input"):
        _original_display["prompt_input"] = getattr(_display_module, "prompt_input")
        _display_module.prompt_input = lambda *a, **kw: ""  # type: ignore[attr-defined]
    if hasattr(_display_module, "prompt_choice"):
        _original_display["prompt_choice"] = getattr(_display_module, "prompt_choice")
        _display_module.prompt_choice = lambda prompt, options, **kw: options[0] if options else ""  # type: ignore[attr-defined]
    if hasattr(_display_module, "prompt_secret"):
        _original_display["prompt_secret"] = getattr(_display_module, "prompt_secret")
        _display_module.prompt_secret = lambda *a, **kw: ""  # type: ignore[attr-defined]


def _restore_display():
    for name, func in _original_display.items():
        setattr(_display_module, name, func)
    _original_display.clear()


# ── CJK-aware string formatting ──

def display_width(s: str) -> int:
    """Calculate display width accounting for CJK double-width characters."""
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        if eaw in ('F', 'W'):
            w += 2
        else:
            w += 1
    return w


def pad(s: str, width: int) -> str:
    """Left-align: pad string to target display width with trailing spaces."""
    diff = width - display_width(s)
    return s + ' ' * max(diff, 0)


def rpad(s: str, width: int) -> str:
    """Right-align: pad string to target display width with leading spaces."""
    diff = width - display_width(s)
    return ' ' * max(diff, 0) + s


# ── Silence prompt_manager ──
_original_pm_output: Optional[Callable[..., Any]] = None


def _silence_prompt_manager():
    global _original_pm_output
    _original_pm_output = getattr(prompt_manager, '_output', None)
    setattr(prompt_manager, '_output', lambda text, level: None)


def _restore_prompt_manager():
    global _original_pm_output
    if _original_pm_output is not None:
        setattr(prompt_manager, '_output', _original_pm_output)
        _original_pm_output = None


# ── Talent number lookup ──
TALENT_NAME_TO_NUM: dict[str, int] = {}
TALENT_NUM_TO_NAME: dict[int, str] = {}
for _num, _name, _cls, _desc in TALENT_TABLE:
    TALENT_NAME_TO_NUM[_name] = _num
    TALENT_NUM_TO_NAME[_num] = _name


# ── Statistics dataclasses ──
@dataclass
class TalentStats:
    picks: int = 0
    wins: int = 0
    picks_by_personality: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    wins_by_personality: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    usage_samples: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PersonalityStats:
    games: int = 0
    wins: int = 0


# ── Column widths (display-width units) ──
COL_NUM = 6        # 编号
COL_NAME = 30      # 天赋名（最长的神代天赋名约22个显示宽度）
COL_PICKS = 8      # Pick数
COL_RATE = 9       # Pick率 / 胜率
COL_WINS = 7       # 胜场
COL_PERS = 14      # 人格列宽


def run_single_game(num_players: int, rl_controller=None, rl_talent_mode: str = "random") -> dict[str, Any]:
    """Run a single game (all-AI, or with one RL seat) and return results."""
    game_state = GameState()

    available_names = list(AI_NAME_POOL)
    random.shuffle(available_names)

    ai_players_info: list[tuple[str, str, str]] = []

    # RL 玩家创建（占据 p1 席位）
    rl_pid: Optional[str] = None
    if rl_controller is not None:
        rl_pid = "p1"
        rl_name = "RL_Agent"
        rl_controller.reset_stack()
        player = Player(rl_pid, rl_name, controller=rl_controller)
        game_state.add_player(player)
        start_idx = 1  # AI 从 p2 开始
    else:
        start_idx = 0

    ai_count = num_players - (1 if rl_controller else 0)
    for i in range(ai_count):
        ai_name = available_names[i] if i < len(available_names) else f"AI_{i+1}"
        personality = random.choice(AI_PERSONALITIES)
        pid = f"p{i + 1 + start_idx}"
        controller = BasicAIController(personality=personality)  # type: ignore[abstract]
        player = Player(pid, ai_name, controller=controller)
        game_state.add_player(player)
        ai_players_info.append((pid, ai_name, personality))

    random.shuffle(game_state.player_order)

    ai_personality_map = {info[0]: info[2] for info in ai_players_info}
    taken: set[int] = set()

    for pid in game_state.player_order:
        player = game_state.get_player(pid)
        if player is None:
            continue

        # RL 玩家天赋分配
        if pid == rl_pid:
            if rl_talent_mode == "0":
                # 不选天赋
                pass
            elif rl_talent_mode == "model":
                # 模型自选：通过 controller.choose() 走模型推理
                available = [(n, name, cls, desc) for n, name, cls, desc in TALENT_TABLE
                             if n not in taken and n not in AI_DISABLED_TALENTS]
                if available:
                    option_names = [name for n, name, cls, desc in available]
                    option_names.append("不选择天赋")
                    # 设置 player_ref 以便 _rl_choose 能构建观测
                    rl_controller.set_player_ref(player, game_state)
                    chosen_name = rl_controller.choose(
                        "选择你的天赋：",
                        option_names,
                        context={"phase": "pregame", "situation": "talent_pick", "taken": list(taken)},
                    )
                    if chosen_name != "不选择天赋":
                        for n, name, cls, desc in available:
                            if name == chosen_name:
                                talent_inst = cls(pid, game_state)
                                player.talent = talent_inst
                                player.talent_name = name
                                talent_inst.on_register()
                                taken.add(n)
                                break
            elif rl_talent_mode == "random":
                # 均匀随机：从可用天赋中等概率选一个
                available = [(n, name, cls, desc) for n, name, cls, desc in TALENT_TABLE
                             if n not in taken and n not in AI_DISABLED_TALENTS]
                if available:
                    chosen = random.choice(available)
                    n, name, cls, desc = chosen
                    talent_inst = cls(pid, game_state)
                    player.talent = talent_inst
                    player.talent_name = name
                    talent_inst.on_register()
                    taken.add(n)
            else:
                # 指定天赋编号
                try:
                    talent_num = int(rl_talent_mode)
                except ValueError:
                    talent_num = -1
                for n, name, cls, desc in TALENT_TABLE:
                    if n == talent_num and n not in taken:
                        talent_inst = cls(pid, game_state)
                        player.talent = talent_inst
                        player.talent_name = name
                        talent_inst.on_register()
                        taken.add(n)
                        break
            # 设置 player_ref（天赋分配后，确保后续 choose/get_command 能用）
            if rl_controller is not None:
                rl_controller.set_player_ref(player, game_state)
            continue

        # AI 玩家天赋分配（原有逻辑）
        available = [(n, name, cls, desc) for n, name, cls, desc in TALENT_TABLE
                     if n not in taken and n not in AI_DISABLED_TALENTS]
        if not available:
            continue
        personality = ai_personality_map.get(pid, "balanced")
        chosen = _ai_pick_talent(personality, available, taken)
        if not chosen:
            continue
        n, name, cls = chosen  # type: ignore[misc]
        talent_inst = cls(pid, game_state)
        player.talent = talent_inst
        player.talent_name = name
        talent_inst.on_register()
        taken.add(n)

    game_state.max_rounds = GameState.compute_default_max_rounds(num_players)

    round_mgr = RoundManager(game_state)
    try:
        round_mgr.run_game_loop()
    except Exception:
        game_state.game_over = True
        game_state.winner = "nobody"

    winner_pid = game_state.winner or "nobody"
    results: dict[str, Any] = {
        "winner_pid": winner_pid,
        "rounds": game_state.current_round,
        "draw": winner_pid == "nobody",
        "players": [],
    }

    pid_to_personality = {info[0]: info[2] for info in ai_players_info}

    for pid in game_state.player_order:
        player = game_state.get_player(pid)
        if player is None:
            continue
        talent_num = TALENT_NAME_TO_NUM.get(player.talent_name or "", 0)
        personality = pid_to_personality.get(pid, "unknown")
        talent_usage = _extract_talent_usage(player)

        results["players"].append({
            "pid": pid,
            "name": player.name,
            "personality": "RL" if pid == rl_pid else personality,
            "talent_num": talent_num,
            "talent_name": player.talent_name or "无",
            "is_winner": pid == winner_pid,
            "is_rl": pid == rl_pid,
            "alive": player.is_alive(),
            "kill_count": player.kill_count,
            "talent_usage": talent_usage,
        })

    return results


def _extract_talent_usage(player: Player) -> dict[str, Any]:
    """Extract talent-specific usage statistics from a player."""
    usage: dict[str, Any] = {}
    talent = player.talent
    if talent is None:
        return usage

    if hasattr(talent, 'uses_remaining'):
        initial: Optional[int] = getattr(talent, '_initial_uses', None)
        if initial is None:
            if hasattr(talent, 'max_uses'):
                max_uses: int = talent.max_uses
                if hasattr(talent, 'used') and isinstance(talent.used, bool):
                    initial = max_uses + (1 if talent.used else 0)
                else:
                    initial = max_uses
        remaining: int = talent.uses_remaining
        usage['times_activated'] = max(0, (initial or 1) - remaining)

    if hasattr(talent, 'used') and isinstance(talent.used, bool):
        usage['used'] = talent.used

    if hasattr(talent, 'active'):
        usage['was_active'] = talent.active

    # 火萤IV型
    if hasattr(talent, 'kill_count') and hasattr(talent, 'debuff_started'):
        usage['talent_kills'] = talent.kill_count
        usage['debuff_started'] = talent.debuff_started
        usage['action_turns'] = getattr(talent, 'action_turn_count', 0)

    # 愿负世
    if hasattr(talent, 'is_savior'):
        usage['savior_triggered'] = talent.is_savior or getattr(talent, '_was_savior', False)
        usage['divinity_reached'] = getattr(talent, 'divinity', 0)

    # 涟漪
    if hasattr(talent, 'reminiscence'):
        usage['reminiscence'] = talent.reminiscence
        usage['anchor_used'] = getattr(talent, 'anchor_active', False) or getattr(talent, 'used', False)

    # 六爻
    if hasattr(talent, 'charges') and hasattr(talent, 'total_activations'):
        usage['total_activations'] = talent.total_activations

    # 全息影像
    if hasattr(talent, 'enhanced'):
        usage['enhanced_by_ripple'] = talent.enhanced

    return usage


# ── Printing helpers ──

def _sep(width: int = 80) -> str:
    return '─' * width


def _print_table_header(columns: list[tuple[str, int]]) -> None:
    """Print a table header row and separator, CJK-aware."""
    header = "  "
    sep = "  "
    for label, w in columns:
        header += pad(label, w)
        sep += '─' * w
    print(header)
    print(sep)


def _fmt_pct(n: int, d: int) -> str:
    """Format 'n/d(xx%)' or '0/0(-)' """
    if d == 0:
        return "0/0(-)"
    return f"{n}/{d}({n/d*100:.0f}%)"


def _fmt_count_pct(count: int, total: int) -> str:
    """Format 'count(xx.x%)' """
    if total == 0:
        return "0(0%)"
    return f"{count}({count/total*100:.0f}%)"


# ── Main batch runner ──

def run_batch(num_players: int, num_games: int, rl_controller=None, rl_talent_mode: str = "random") -> None:
    """Run multiple games and collect statistics."""

    talent_stats: dict[int, TalentStats] = defaultdict(TalentStats)
    personality_stats: dict[str, PersonalityStats] = defaultdict(PersonalityStats)

    # RL 专用统计
    rl_games = 0
    rl_wins = 0
    rl_talent_picks: dict[int, int] = defaultdict(int)
    rl_talent_wins: dict[int, int] = defaultdict(int)

    total_rounds = 0
    total_draws = 0
    errors = 0

    _silence_display()
    _silence_prompt_manager()

    start_time = time.time()

    for game_idx in range(num_games):
        if (game_idx + 1) % 50 == 0 or game_idx == 0:
            elapsed = time.time() - start_time
            rate = (game_idx + 1) / elapsed if elapsed > 0 else 0
            print(f"\r  进度: {game_idx + 1}/{num_games} ({rate:.1f} 局/秒)", end="", flush=True)

        try:
            result = run_single_game(num_players, rl_controller, rl_talent_mode)
        except Exception:
            errors += 1
            continue

        total_rounds += result["rounds"]
        if result["draw"]:
            total_draws += 1

        for p in result["players"]:
            if p.get("is_rl"):
                rl_games += 1
                rl_talent_picks[p["talent_num"]] += 1
                if p["is_winner"]:
                    rl_wins += 1
                    rl_talent_wins[p["talent_num"]] += 1
                continue  # RL 不计入 talent_stats 和 personality_stats

            talent_num: int = p["talent_num"]
            personality: str = p["personality"]

            ts = talent_stats[talent_num]
            ts.picks += 1
            ts.picks_by_personality[personality] += 1

            ps = personality_stats[personality]
            ps.games += 1

            if p["is_winner"]:
                ts.wins += 1
                ts.wins_by_personality[personality] += 1
                ps.wins += 1

            ts.usage_samples.append(p["talent_usage"])

    _restore_prompt_manager()
    _restore_display()

    elapsed = time.time() - start_time
    completed = num_games - errors
    print(f"\r  完成: {num_games} 局, 耗时 {elapsed:.1f}秒 ({num_games / max(elapsed, 0.01):.1f} 局/秒)    ")

    print_results(num_players, num_games, completed, total_rounds, total_draws, errors,
                  talent_stats, personality_stats,
                  rl_games=rl_games, rl_wins=rl_wins,
                  rl_talent_picks=rl_talent_picks, rl_talent_wins=rl_talent_wins)


def print_results(
    num_players: int,
    num_games: int,
    completed: int,
    total_rounds: int,
    total_draws: int,
    errors: int,
    talent_stats: dict[int, TalentStats],
    personality_stats: dict[str, PersonalityStats],
    rl_games: int = 0,
    rl_wins: int = 0,
    rl_talent_picks: Optional[dict[int, int]] = None,
    rl_talent_wins: Optional[dict[int, int]] = None,
) -> None:
    """Print all result tables with CJK-aware alignment."""

    # ── Summary ──
    print(f"\n{'=' * 80}")
    print(f"  自动胜率统计结果")
    print(f"  {num_players}人局 × {num_games}局")
    print(f"  平均轮次: {total_rounds / max(completed, 1):.1f}")
    print(f"  平局率: {total_draws}/{num_games} ({total_draws / max(num_games, 1) * 100:.1f}%)")
    if errors > 0:
        print(f"  错误/崩溃: {errors}")
    print(f"{'=' * 80}")

    # ── RL 统计表（仅在 RL 参与时显示）──
    if rl_games > 0:
        print(f"\n{'=' * 80}")
        print(f"  RL 模型统计")
        print(f"{'=' * 80}")
        rl_wr = rl_wins / rl_games * 100 if rl_games > 0 else 0
        random_baseline = 1.0 / num_players * 100
        print(f"  总局数: {rl_games} | 胜场: {rl_wins} | 胜率: {rl_wr:.1f}% (随机基线: {random_baseline:.1f}%)")
        print()
        print(f"  RL 各天赋胜率:")
        _print_table_header([
            ("编号", COL_NUM), ("天赋名", COL_NAME),
            ("Pick数", COL_PICKS), ("胜场", COL_WINS), ("胜率", COL_RATE),
        ])
        if rl_talent_picks:
            sorted_rl = sorted(
                rl_talent_picks.items(),
                key=lambda x: (rl_talent_wins or {}).get(x[0], 0) / max(x[1], 1),
                reverse=True,
            )
            for talent_num, picks in sorted_rl:
                name = TALENT_NUM_TO_NAME.get(talent_num, "无天赋")
                wins = (rl_talent_wins or {}).get(talent_num, 0)
                wr = wins / picks * 100 if picks > 0 else 0
                row = "  "
                row += pad(str(talent_num), COL_NUM)
                row += pad(name, COL_NAME)
                row += pad(str(picks), COL_PICKS)
                row += pad(str(wins), COL_WINS)
                row += pad(f"{wr:.1f}%", COL_RATE)
                print(row)

    total_picks = sum(ts.picks for ts in talent_stats.values())
    sorted_talents = sorted(
        talent_stats.items(),
        key=lambda x: x[1].wins / max(x[1].picks, 1),
        reverse=True,
    )
    personalities_list = sorted(personality_stats.keys())

    # Compute personality baselines for adjusted win rate
    pers_baseline: dict[str, float] = {}
    overall_baseline = sum(ts.wins for ts in talent_stats.values()) / max(total_picks, 1)
    for p_name in personalities_list:
        ps = personality_stats[p_name]
        pers_baseline[p_name] = ps.wins / ps.games if ps.games > 0 else overall_baseline

    # ── Table 1: Talent overview ──
    print(f"\n{_sep()}")
    print(f"  天赋统计")
    print(f"{_sep()}")
    _print_table_header([
        ("编号", COL_NUM), ("天赋名", COL_NAME), ("Pick数", COL_PICKS),
        ("Pick率", COL_RATE), ("胜场", COL_WINS), ("胜率", COL_RATE),
        ("校正胜率", COL_RATE),
    ])

    for talent_num, ts in sorted_talents:
        name = TALENT_NUM_TO_NAME.get(talent_num, "无天赋")
        pick_rate = ts.picks / total_picks * 100 if total_picks > 0 else 0.0
        win_rate = ts.wins / ts.picks * 100 if ts.picks > 0 else 0.0
        adj_rate = _calc_adjusted_winrate(ts, pers_baseline, overall_baseline) * 100

        row = "  "
        row += pad(str(talent_num), COL_NUM)
        row += pad(name, COL_NAME)
        row += pad(str(ts.picks), COL_PICKS)
        row += pad(f"{pick_rate:.1f}%", COL_RATE)
        row += pad(str(ts.wins), COL_WINS)
        row += pad(f"{win_rate:.1f}%", COL_RATE)
        row += pad(f"{adj_rate:.1f}%", COL_RATE)
        print(row)

    # ── Table 2: Per-personality pick rate ──
    print(f"\n{_sep()}")
    print(f"  各人格 × 天赋 Pick率")
    print(f"{_sep()}")

    cols: list[tuple[str, int]] = [("天赋", COL_NAME)]
    for p_name in personalities_list:
        cols.append((p_name, COL_PERS))
    _print_table_header(cols)

    for talent_num, ts in sorted_talents:
        name = TALENT_NUM_TO_NAME.get(talent_num, "无")
        row = "  " + pad(name, COL_NAME)
        for pers in personalities_list:
            count = ts.picks_by_personality.get(pers, 0)
            total_pers = personality_stats[pers].games
            cell = _fmt_count_pct(count, total_pers)
            row += pad(cell, COL_PERS)
        print(row)

    # ── Table 3: Per-personality win rate ──
    print(f"\n{_sep()}")
    print(f"  各人格 × 天赋 胜率")
    print(f"{_sep()}")
    _print_table_header(cols)  # same header as pick rate table

    for talent_num, ts in sorted_talents:
        name = TALENT_NUM_TO_NAME.get(talent_num, "无")
        row = "  " + pad(name, COL_NAME)
        for pers in personalities_list:
            wins = ts.wins_by_personality.get(pers, 0)
            picks = ts.picks_by_personality.get(pers, 0)
            cell = _fmt_pct(wins, picks)
            row += pad(cell, COL_PERS)
        print(row)

    # ── Table 4: Personality overall ──
    print(f"\n{_sep()}")
    print(f"  人格总体胜率")
    print(f"{_sep()}")
    for pers in personalities_list:
        ps = personality_stats[pers]
        rate = ps.wins / ps.games * 100 if ps.games > 0 else 0.0
        print(f"  {pad(pers, 14)}{ps.wins}/{ps.games} ({rate:.1f}%)")

    # ── Table 5: Talent usage summary ──
    print(f"\n{_sep()}")
    print(f"  天赋使用次数统计（限定使用次数的天赋）")
    print(f"{_sep()}")

    for talent_num, ts in sorted_talents:
        name = TALENT_NUM_TO_NAME.get(talent_num, "无")
        samples = ts.usage_samples
        if not samples:
            continue

        used_count = sum(1 for s in samples if s.get("used", False))
        activated_counts = [s.get("times_activated", 0) for s in samples if "times_activated" in s]

        info_parts = [f"{name}(#{talent_num})"]
        if activated_counts:
            avg_act = sum(activated_counts) / len(activated_counts)
            info_parts.append(f"平均发动{avg_act:.2f}次")
        if used_count > 0:
            info_parts.append(f"使用率{used_count}/{len(samples)}({used_count / len(samples) * 100:.0f}%)")

        debuff_counts = [s for s in samples if s.get("debuff_started")]
        if debuff_counts:
            info_parts.append(f"debuff触发{len(debuff_counts)}/{len(samples)}")

        savior_counts = [s for s in samples if s.get("savior_triggered")]
        if savior_counts:
            info_parts.append(f"救世主触发{len(savior_counts)}/{len(samples)}")

        print(f"  {' | '.join(info_parts)}")

    # ── Table 6: Adjusted win rate explanation ──
    print(f"\n{_sep()}")
    print(f"  校正胜率说明")
    print(f"{_sep()}")
    print(f"  校正胜率 = 消除人格强度差异后的天赋纯粹胜率估计")
    print(f"  算法：对每个天赋，计算其在各人格下的胜率与该人格基准胜率的差值，")
    print(f"        取加权平均后加上全局基准胜率。样本<5的人格组合不参与计算。")
    print(f"  人格基准胜率:")
    for p_name in personalities_list:
        print(f"    {pad(p_name, 14)}{pers_baseline[p_name] * 100:.1f}%")


def _calc_adjusted_winrate(
    ts: TalentStats,
    pers_baseline: dict[str, float],
    overall_baseline: float,
) -> float:
    """
    Calculate personality-adjusted win rate for a talent.

    For each personality that picked this talent >= 5 times:
      excess = (talent win rate in that personality) - (personality baseline win rate)
    Adjusted = overall_baseline + weighted_average(excess, weighted by picks)

    This removes the effect of strong/weak personalities inflating/deflating
    a talent's raw win rate.
    """
    if ts.picks == 0:
        return 0.0

    weighted_excess = 0.0
    weight_total = 0

    for p_name, p_picks in ts.picks_by_personality.items():
        if p_picks < 5:
            continue
        p_wins = ts.wins_by_personality.get(p_name, 0)
        talent_rate = p_wins / p_picks
        baseline = pers_baseline.get(p_name, overall_baseline)
        excess = talent_rate - baseline
        weighted_excess += excess * p_picks
        weight_total += p_picks

    if weight_total == 0:
        # Not enough data in any personality, fall back to raw
        return ts.wins / ts.picks

    return overall_baseline + weighted_excess / weight_total


def main():
    parser = argparse.ArgumentParser(description="起闯战争 自动胜率统计")
    parser.add_argument("--players", type=int, default=6, help="每局玩家人数 (2-6)")
    parser.add_argument("--games", type=int, default=5000, help="总局数")
    parser.add_argument("--model", type=str, default=None,
                        help="RL 模型路径（.zip），启用后一个 AI 席位替换为 RL")
    parser.add_argument("--rl-talent", type=str, default="random",
                        help="RL 天赋选择模式：'model'=模型自选, 'random'=均匀随机14天赋, 数字=指定天赋编号, '0'=无天赋")
    parser.add_argument("--n-stack", type=int, default=30,
                        help="RL 帧堆叠数量（需与训练时一致）")
    args = parser.parse_args()

    if not 2 <= args.players <= 6:
        print("玩家人数必须在 2-6 之间")
        sys.exit(1)

    print(f"  起闯战争 自动胜率统计")
    print(f"  {args.players}人局 × {args.games}局")

    rl_controller = None
    if args.model:
        if not _rl_available:
            print("错误：RL 模块不可用，请确保 rl/ 目录和依赖已安装")
            sys.exit(1)
        print(f"  加载 RL 模型: {args.model}")
        rl_controller = OpponentRLController(model_path=args.model, n_stack=args.n_stack)
        print(f"  RL 天赋模式: {args.rl_talent}")
    print()

    run_batch(args.players, args.games, rl_controller=rl_controller, rl_talent_mode=args.rl_talent)


if __name__ == "__main__":
    main()