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


def run_single_game(num_players: int, rl_controller=None, rl_talent_mode: str = "random",
                    diag_mode: bool = False) -> dict[str, Any]:
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
        # 重置所有跨局状态（帧堆叠 + 事件日志 + 威胁分等）
        rl_controller.reset_game_state()
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
        controller = BasicAIController(
            personality=personality,
            diag_enabled=diag_mode,
        )
        # C8: shadow_mode 已移除
        player = Player(pid, ai_name, controller=controller)
        game_state.add_player(player)
        ai_players_info.append((pid, ai_name, personality))

    random.shuffle(game_state.player_order)

    ai_personality_map = {info[0]: info[2] for info in ai_players_info}
    taken: set[int] = set()

    # RL random 模式：先选，确保均匀分布
    if rl_pid is not None and rl_talent_mode == "random":
        rl_player = game_state.get_player(rl_pid)
        available = [(n, name, cls, desc) for n, name, cls, desc in TALENT_TABLE
                     if n not in AI_DISABLED_TALENTS]
        if available and rl_player is not None:
            chosen = random.choice(available)
            n, name, cls, desc = chosen
            talent_inst = cls(rl_pid, game_state)
            rl_player.talent = talent_inst
            rl_player.talent_name = name
            talent_inst.on_register()
            taken.add(n)
        if rl_controller is not None and rl_player is not None:
            rl_controller.set_player_ref(rl_player, game_state)

    for pid in game_state.player_order:
        player = game_state.get_player(pid)
        if player is None:
            continue

        # RL 玩家天赋分配
        if pid == rl_pid:
            if rl_talent_mode == "random":
                # 已在循环前分配
                if rl_controller is not None:
                    rl_controller.set_player_ref(player, game_state)
                continue
            elif rl_talent_mode == "0":
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
        crashed = False
        crash_traceback = ""
    except Exception as e:
        game_state.game_over = True
        game_state.winner = "nobody"
        crashed = True
        import traceback
        crash_traceback = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    winner_pid = game_state.winner or "nobody"
    is_draw = winner_pid == "nobody"

    # ── 区分平局原因 ──
    draw_reason = ""
    if crashed:
        is_draw = True
        draw_reason = "crash"
        winner_pid = "nobody"
    elif is_draw:
        # 检查是否达到最大轮数
        if (game_state.max_rounds is not None
                and game_state.current_round >= game_state.max_rounds):
            draw_reason = "max_rounds"
        # 检查是否全员死亡（可能Terror同归于尽或其它AOE互杀）
        elif len(game_state.alive_players()) == 0:
            g7_picked = 14 in taken  # G7 = 星野 = 编号14
            draw_reason = "terror_mutual" if g7_picked else "all_dead_no_g7"
        else:
            draw_reason = "other"
    results: dict[str, Any] = {
        "winner_pid": winner_pid,
        "rounds": game_state.current_round,
        "draw": is_draw,
        "draw_reason": draw_reason,
        "crashed": crashed,
        "crash_traceback": crash_traceback,
        "talent_nums_picked": list(taken),  # 本局选了哪些天赋
        "max_rounds": game_state.max_rounds,
        "players": [],
    }

    # 诊断数据收集
    diag_data = {}
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        if p and hasattr(p.controller, 'export_diagnostics'):
            d = p.controller.export_diagnostics()
            if d:
                diag_data[pid] = d
    # 非 draw 局：清理 round_snapshots 以节省内存
    if not is_draw:
        for pid_data in diag_data.values():
            pid_data.pop("round_snapshots", None)
    results["diagnostics"] = diag_data

    # draw_detail 快速预筛（不依赖 DiagCollector）
    if draw_reason == "max_rounds":
        from collections import Counter as _DrawCounter
        last_20_events = [e for e in game_state.event_log
                          if e.get("round", 0) > game_state.current_round - 20]
        alive = game_state.alive_players()
        results["draw_detail"] = {
            "final_alive": [
                {"pid": p.player_id, "name": p.name,
                 "talent": getattr(p, 'talent_name', ''),
                 "hp": round(p.hp, 1), "max_hp": round(p.max_hp, 1),
                 "loc": p.location, "kills": p.kill_count,
                 "personality": getattr(p.controller, 'personality', '')}
                for p in alive
            ],
            "last_20_action_types": dict(_DrawCounter(
                e.get("type") for e in last_20_events)),
            "last_20_forfeit_count": sum(
                1 for e in last_20_events if e.get("type") == "forfeit"),
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

def run_batch(num_players: int, num_games: int, rl_controller=None, rl_talent_mode: str = "random",
              diag_mode: bool = False, diag_output: str = "logs/diag_report.json") -> None:
    """Run multiple games and collect statistics."""

    talent_stats: dict[int, TalentStats] = defaultdict(TalentStats)
    personality_stats: dict[str, PersonalityStats] = defaultdict(PersonalityStats)

    # RL 专用统计
    rl_games = 0
    rl_wins = 0
    rl_talent_picks: dict[int, int] = defaultdict(int)
    rl_talent_wins: dict[int, int] = defaultdict(int)
    rl_talent_usage: dict[int, list[dict[str, Any]]] = defaultdict(list)

    total_rounds = 0
    total_draws = 0
    errors = 0

    # 崩溃详情收集
    crash_log: list[dict[str, Any]] = []

    # 平局原因统计
    draw_reasons: dict[str, int] = defaultdict(int)  # key → count
    # 诊断报告
    diag_report = None
    if diag_mode:
        from controllers.ai.diagnostics import DiagReport
        diag_report = DiagReport()
    # 分类标签映射
    DRAW_LABELS = {
        "terror_mutual": "Terror同归于尽(含G7)",
        "max_rounds":     "达到轮次上限(AI僵持)",
        "all_dead_no_g7": "全员战死(无G7)",
        "crash":          "引擎异常/崩溃",
        "other":          "其他原因",
        "":               "(未归类)",
    }

    _silence_display()
    _silence_prompt_manager()

    start_time = time.time()

    for game_idx in range(num_games):
        if (game_idx + 1) % 50 == 0 or game_idx == 0:
            elapsed = time.time() - start_time
            rate = (game_idx + 1) / elapsed if elapsed > 0 else 0
            print(f"\r  进度: {game_idx + 1}/{num_games} ({rate:.1f} 局/秒)", end="", flush=True)

        try:
            result = run_single_game(num_players, rl_controller, rl_talent_mode,
                                     diag_mode=diag_mode)
        except Exception:
            errors += 1
            continue

        total_rounds += result["rounds"]
        if result["draw"]:
            total_draws += 1
            reason = result.get("draw_reason", "")
            draw_reasons[reason] += 1
            if reason == "crash":
                crash_log.append({
                    "game_idx": game_idx + 1,
                    "rounds": result["rounds"],
                    "talents": result.get("talent_nums_picked", []),
                    "traceback": result.get("crash_traceback", ""),
                })

        # 诊断数据收集
        if diag_report is not None:
            diag_report.add_game(game_idx, result)

        # shadow模式：保存前N局的详细决策日志
