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
    "show_initiative_results",
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
                    diag_mode: bool = False, collect_digest: bool = False,
                    lineup: Optional[list[str]] = None,
                    no_talents: bool = False,
                    force_talent: Optional[str] = None) -> dict[str, Any]:
    """Run a single game (all-AI, or with one RL seat) and return results.

    collect_digest: True 时结果附带 event_digest（golden 回放用，常规批跑不开省内存）。
    lineup: 每个席位的控制器名列表（长度 = AI 席位数）。名字命中 BOT_REGISTRY
            则创建风洞 bot（不选天赋），否则视为 BasicAI 人格名。None = 随机人格。
    no_talents: True 时全员不分配天赋——M2~M6 风洞主通道（天赋量纲 M7 才迁移，
                带天赋的 hp20 局数据无效）。
    """
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

    from controllers.bots import BOT_REGISTRY

    bot_pids: set[str] = set()
    ai_count = num_players - (1 if rl_controller else 0)
    for i in range(ai_count):
        ai_name = available_names[i] if i < len(available_names) else f"AI_{i+1}"
        pid = f"p{i + 1 + start_idx}"
        slot = lineup[i] if lineup and i < len(lineup) else None
        if slot in BOT_REGISTRY:
            controller = BOT_REGISTRY[slot]()
            personality = controller.personality  # = bot 名，复用统计分桶
            ai_name = f"{slot}_{pid}"
            bot_pids.add(pid)
        else:
            personality = slot if slot else random.choice(AI_PERSONALITIES)
            controller = BasicAIController(
                personality=personality,
                diag_enabled=diag_mode,
            )
        player = Player(pid, ai_name, controller=controller)
        game_state.add_player(player)
        ai_players_info.append((pid, ai_name, personality))

    random.shuffle(game_state.player_order)

    ai_personality_map = {info[0]: info[2] for info in ai_players_info}
    taken: set[int] = set()
    force_assigned = False

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

        # 风洞 bot 不选天赋（测机制不测天赋）；--no-talents 全员跳过
        if pid in bot_pids or no_talents:
            continue

        # --force-talent：保证指定天赋出现在一个 AI 席位（逐天赋风洞用）
        # 子串匹配：TALENT_TABLE 注册名带「神代天赋-」前缀，允许传部分名
        if force_talent and not force_assigned:
            for n, name, cls, desc in TALENT_TABLE:
                if force_talent in name and n not in taken:
                    talent_inst = cls(pid, game_state)
                    player.talent = talent_inst
                    player.talent_name = name
                    talent_inst.on_register()
                    taken.add(n)
                    force_assigned = True
                    break
            if force_assigned and player.talent is not None:
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

    # M6 双轨：winner_pid = 终分第一（m6 下）/ 存活者（非 m6）；
    # survival_winner = 存活轨（老平局语义，draw_reason 用它分类）
    winner_pid = game_state.winner or "nobody"
    survival_winner = getattr(game_state, "survival_winner", winner_pid) or "nobody"
    is_draw = survival_winner == "nobody"  # 平局/僵局按存活轨判定

    # ── 区分平局原因 ──
    draw_reason = ""
    if crashed:
        is_draw = True
        draw_reason = "crash"
        winner_pid = "nobody"
        survival_winner = "nobody"
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
        "survival_winner": survival_winner,
        "final_scores": dict(getattr(game_state, "final_scores", {})),
        "rounds": game_state.current_round,
        "draw": is_draw,
        "draw_reason": draw_reason,
        "crashed": crashed,
        "crash_traceback": crash_traceback,
        "talent_nums_picked": list(taken),  # 本局选了哪些天赋
        "max_rounds": game_state.max_rounds,
        "players": [],
    }

    if collect_digest:
        from engine.replay_digest import digest_event_log
        results["event_digest"] = digest_event_log(game_state.event_log)

    # 战斗轮占比分子：发生过攻击事件的轮次数（v0.2 §9 看板指标）
    results["combat_rounds"] = len({
        e.get("round") for e in game_state.event_log if e.get("type") == "attack"
    })
    # M3 擦伤率分子/分母（attack 事件中被闪避擦伤的占比，v0.2 §9）
    attack_events = [e for e in game_state.event_log
                     if e.get("type") in ("attack", "opportunity_attack")]
    results["total_attacks"] = len(attack_events)
    results["grazed_attacks"] = sum(
        1 for e in attack_events
        if isinstance(e.get("result"), dict)
        and e["result"].get("grazed_by_evasion"))

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
            "is_winner": pid == winner_pid,           # 终分轨（m6 下=终分第一）
            "is_survival_winner": pid == survival_winner,  # 存活轨（老指标）
            "is_rl": pid == rl_pid,
            "alive": player.is_alive(),
            "kill_count": player.kill_count,
            "final_score": results["final_scores"].get(pid),
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
              diag_mode: bool = False, diag_output: str = "logs/diag_report.json",
              seed: Optional[int] = None,
              golden_record: Optional[str] = None,
              golden_check: Optional[str] = None,
              lineup: Optional[list[str]] = None,
              no_talents: bool = False,
              force_talent: Optional[str] = None) -> None:
    """Run multiple games and collect statistics.

    seed: 基准随机种子。提供时第 i 局（0-based）使用 random.seed(seed + i)，
          串行单线程下保证逐局可复现（golden 回放的前提）。None = 不固定。
    golden_record: 把每局事件摘要写入该路径（JSON-lines，一局一行）。需要 seed。
    golden_check: 读取该路径的 golden 存档，逐局比对，第一处分歧打印并以
                  非零码退出。需要 seed（应与录制时相同，逐局 seed 会校验）。
    """
    import json as _json
    from engine.replay_digest import digest_game, diff_games

    golden_mode = bool(golden_record or golden_check)
    if golden_mode and seed is None:
        raise ValueError("golden record/check 需要 --seed（无种子的摘要不可复现，没有意义）")

    golden_expected: list[dict[str, Any]] = []
    if golden_check:
        with open(golden_check, "r", encoding="utf-8") as f:
            golden_expected = [_json.loads(line) for line in f if line.strip()]
        if len(golden_expected) != num_games:
            print(f"  ⚠️ golden 存档共 {len(golden_expected)} 局，"
                  f"本次 --games {num_games}，按较小值比对")
    golden_recorded: list[dict[str, Any]] = []
    golden_failures: list[tuple[int, list[str]]] = []

    talent_stats: dict[int, TalentStats] = defaultdict(TalentStats)
    personality_stats: dict[str, PersonalityStats] = defaultdict(PersonalityStats)

    # RL 专用统计
    rl_games = 0
    rl_wins = 0
    rl_talent_picks: dict[int, int] = defaultdict(int)
    rl_talent_wins: dict[int, int] = defaultdict(int)
    rl_talent_usage: dict[int, list[dict[str, Any]]] = defaultdict(list)

    total_rounds = 0
    total_combat_rounds = 0
    total_attacks = 0
    total_grazed = 0
    total_draws = 0
    errors = 0
    # M6 双轨：终分胜 vs 存活胜（per-personality）
    score_wins: dict[str, int] = defaultdict(int)
    survival_wins: dict[str, int] = defaultdict(int)
    dualtrack_games = 0

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

        if seed is not None:
            random.seed(seed + game_idx)

        try:
            result = run_single_game(num_players, rl_controller, rl_talent_mode,
                                     diag_mode=diag_mode, collect_digest=golden_mode,
                                     lineup=lineup, no_talents=no_talents,
                                     force_talent=force_talent)
        except Exception:
            errors += 1
            continue

        result["seed"] = (seed + game_idx) if seed is not None else None

        if golden_mode:
            record = digest_game(result, result.pop("event_digest", []))
            if golden_record:
                golden_recorded.append(record)
            if golden_check and game_idx < len(golden_expected):
                problems = diff_games(golden_expected[game_idx], record)
                if problems:
                    golden_failures.append((game_idx, problems))

        total_rounds += result["rounds"]
        total_combat_rounds += result.get("combat_rounds", 0)
        total_attacks += result.get("total_attacks", 0)
        total_grazed += result.get("grazed_attacks", 0)
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

        # M6 双轨：终分胜 vs 存活胜（per-personality，仅 m6 局有 final_scores）
        if result.get("final_scores"):
            dualtrack_games += 1
            for p in result["players"]:
                if p["is_winner"]:
                    score_wins[p["personality"]] += 1
                if p.get("is_survival_winner"):
                    survival_wins[p["personality"]] += 1

        for p in result["players"]:
            if p.get("is_rl"):
                rl_games += 1
                rl_talent_picks[p["talent_num"]] += 1
                if p["is_winner"]:
                    rl_wins += 1
                    rl_talent_wins[p["talent_num"]] += 1
                rl_talent_usage[p["talent_num"]].append(p["talent_usage"])
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
                  draw_reasons=draw_reasons, draw_labels=DRAW_LABELS,
                  crash_log=crash_log,
                  total_combat_rounds=total_combat_rounds,
                  total_attacks=total_attacks, total_grazed=total_grazed,
                  rl_games=rl_games, rl_wins=rl_wins,
                  rl_talent_picks=rl_talent_picks, rl_talent_wins=rl_talent_wins,
                  rl_talent_usage=rl_talent_usage)

    # M6 双轨指标：终分胜率 vs 存活率（仅 m6 局）
    if dualtrack_games > 0:
        print(f"\n  ── M6 评价体系双轨（{dualtrack_games} 局有终分）──")
        print(f"  {'人格':12s} {'终分胜率':>14s} {'存活率':>14s}")
        allp = sorted(set(score_wins) | set(survival_wins))
        for pers in allp:
            sw = score_wins.get(pers, 0)
            vw = survival_wins.get(pers, 0)
            print(f"  {pers:12s} {sw:>14d} {vw:>14d}")
        print("  （终分胜 = 终分第一；存活胜 = 活到最后。两者背离即评价体系转向生效）")

    # 诊断报告输出
    if diag_report is not None:
        diag_report.print_forfeit_summary()
        diag_report.print_fallback_summary()
        diag_report.print_draw_analysis()
        diag_report.save_raw(diag_output)

    # ── golden 回放收尾 ──
    if golden_record:
        out_dir = os.path.dirname(golden_record)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(golden_record, "w", encoding="utf-8", newline="\n") as f:
            for record in golden_recorded:
                f.write(_json.dumps(record, ensure_ascii=False,
                                    sort_keys=True) + "\n")
        print(f"\n  📼 golden 存档已写入: {golden_record}（{len(golden_recorded)} 局）")

    if golden_check:
        checked = min(num_games, len(golden_expected))
        if golden_failures:
            print(f"\n  ❌ golden 校验失败: {len(golden_failures)}/{checked} 局有分歧")
            first_idx, first_problems = golden_failures[0]
            print(f"  首处分歧 @ 第 {first_idx + 1} 局:")
            for line in first_problems:
                print(f"    {line}")
            sys.exit(2)
        else:
            print(f"\n  ✅ golden 校验通过: {checked} 局全等")


def print_results(
    num_players: int,
    num_games: int,
    completed: int,
    total_rounds: int,
    total_draws: int,
    errors: int,
    talent_stats: dict[int, TalentStats],
    personality_stats: dict[str, PersonalityStats],
    draw_reasons: Optional[dict[str, int]] = None,
    draw_labels: Optional[dict[str, str]] = None,
    crash_log: Optional[list[dict[str, Any]]] = None,
    rl_games: int = 0,
    rl_wins: int = 0,
    rl_talent_picks: Optional[dict[int, int]] = None,
    rl_talent_wins: Optional[dict[int, int]] = None,
    rl_talent_usage: Optional[dict[int, list[dict[str, Any]]]] = None,
    total_combat_rounds: int = 0,
    total_attacks: int = 0,
    total_grazed: int = 0,
) -> None:
    """Print all result tables with CJK-aware alignment."""

    # ── Summary ──
    print(f"\n{'=' * 80}")
    print(f"  自动胜率统计结果")
    print(f"  {num_players}人局 × {num_games}局")
    print(f"  平均轮次: {total_rounds / max(completed, 1):.1f}")
    print(f"  战斗轮占比: {total_combat_rounds / max(total_rounds, 1) * 100:.1f}%"
          f"（{total_combat_rounds}/{total_rounds} 轮发生过攻击）")
    if total_grazed > 0:
        print(f"  擦伤率: {total_grazed / max(total_attacks, 1) * 100:.1f}%"
              f"（{total_grazed}/{total_attacks} 次攻击被闪避擦伤）")
    print(f"  平局率: {total_draws}/{num_games} ({total_draws / max(num_games, 1) * 100:.1f}%)")
    if errors > 0:
        print(f"  错误/崩溃: {errors}")
    # ── 平局原因分解 ──
    if draw_reasons and total_draws > 0:
        print(f"\n  ── 平局原因分解 ──")
        labels = draw_labels or {}
        for reason_key in ("terror_mutual", "max_rounds", "all_dead_no_g7", "crash", "other", ""):
            count = draw_reasons.get(reason_key, 0)
            if count == 0:
                continue
            label = labels.get(reason_key, reason_key or "未归类")
            pct = count / total_draws * 100
            flag = ""
            if reason_key == "terror_mutual":
                flag = "  ← 正常（Terror机制预期行为）"
            elif reason_key == "max_rounds":
                flag = "  ← 异常：AI僵持/警察体系僵局"
            elif reason_key == "crash":
                flag = "  ← 严重：引擎bug"
            elif reason_key == "all_dead_no_g7":
                flag = "  ← 罕见：非Terror的AOE互杀"
            print(f"    {label}: {count}/{total_draws} ({pct:.1f}%){flag}")
    # ── 崩溃详情（如有）──
    if crash_log:
        print(f"\n  ── 崩溃详情（最近{min(10, len(crash_log))}次）──")
        # 按异常类型分组统计
        from collections import Counter
        error_types = Counter()
        for entry in crash_log:
            tb = entry.get("traceback", "")
            # 提取最后一行的异常类型
            for line in tb.split("\n"):
                line = line.strip()
                if line and not line.startswith("File ") and not line.startswith("..."):
                    if "Error" in line or "Exception" in line:
                        error_types[line[:80]] += 1
                        break
        if error_types:
            print(f"    异常类型分布:")
            for err, cnt in error_types.most_common(5):
                print(f"      {cnt}次: {err}")
        # 显示最近几次的详细traceback
        for entry in crash_log[-3:]:
            print(f"\n    ─ 游戏#{entry['game_idx']} (第{entry['rounds']}轮) ─")
            talents = entry.get("talents", [])
            if talents:
                talent_names = [TALENT_NUM_TO_NAME.get(t, f"#{t}") for t in talents]
                print(f"    天赋: {', '.join(talent_names[:6])}")
            tb = entry.get("traceback", "")
            # 只显示最后几行（最关键的错误信息）
            tb_lines = tb.split("\n")
            for line in tb_lines[-5:]:
                if line.strip():
                    print(f"    {line.strip()[:120]}")
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

        # RL 天赋选择偏好
        if rl_talent_picks and rl_games > 0:
            print()
            print(f"  RL 天赋选择偏好:")
            uniform_pct = 100.0 / len(TALENT_NUM_TO_NAME) if TALENT_NUM_TO_NAME else 0
            _print_table_header([
                ("编号", COL_NUM), ("天赋名", COL_NAME),
                ("Pick数", COL_PICKS), ("Pick率", COL_RATE),
                ("偏好度", COL_RATE),
            ])
            sorted_by_picks = sorted(rl_talent_picks.items(), key=lambda x: x[1], reverse=True)
            for talent_num, picks in sorted_by_picks:
                name = TALENT_NUM_TO_NAME.get(talent_num, "无天赋")
                pick_rate = picks / rl_games * 100
                preference = pick_rate / uniform_pct if uniform_pct > 0 else 0
                row = "  "
                row += pad(str(talent_num), COL_NUM)
                row += pad(name, COL_NAME)
                row += pad(str(picks), COL_PICKS)
                row += pad(f"{pick_rate:.1f}%", COL_RATE)
                row += pad(f"{preference:.2f}x", COL_RATE)
                print(row)
            print(f"  （偏好度 = Pick率 / 均匀基线{uniform_pct:.1f}%，>1.0 表示偏好，<1.0 表示回避）")

        # RL 天赋使用统计
        if rl_talent_usage:
            print()
            print(f"  RL 天赋使用详情:")
            print(f"  {_sep(76)}")
            for talent_num in sorted(rl_talent_usage.keys()):
                samples = rl_talent_usage[talent_num]
                if not samples:
                    continue
                name = TALENT_NUM_TO_NAME.get(talent_num, "无")

                used_count = sum(1 for s in samples if s.get("used", False))
                activated_counts = [s.get("times_activated", 0) for s in samples if "times_activated" in s]

                info_parts = [f"{name}(#{talent_num})", f"样本数{len(samples)}"]
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

                anchor_counts = [s for s in samples if s.get("anchor_used")]
                if anchor_counts:
                    info_parts.append(f"锚定使用{len(anchor_counts)}/{len(samples)}")

                print(f"  {' | '.join(info_parts)}")

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
    print(f"  BasicAI 天赋使用次数统计（限定使用次数的天赋）")
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
    # C7: --disable-new-arch 已移除（仅新架构）
    # C8: --shadow / --compare 已移除（旧架构对比已无意义）
    parser.add_argument("--diag", action="store_true",
                        help="启用诊断模式：收集 forfeit/fallback/draw 的结构化数据")
    parser.add_argument("--diag-output", type=str, default="logs/diag_report.json",
                        help="诊断原始数据保存路径")
    parser.add_argument("--seed", type=int, default=None,
                        help="基准随机种子：第 i 局使用 seed+i，固定后逐局可复现")
    parser.add_argument("--experiment", action="append", default=[],
                        help="启用实验开关（可多次使用），如 --experiment k_initiative")
    parser.add_argument("--profile", type=str, default="",
                        help="启用实验档案（legacy/m1/m2/m3/m4/m5/m6/v2exp）")
    parser.add_argument("--golden-record", type=str, default=None,
                        help="录制 golden 存档到该路径（JSON-lines），需要 --seed")
    parser.add_argument("--golden-check", type=str, default=None,
                        help="与 golden 存档逐局比对，分歧则非零退出，需要 --seed")
    parser.add_argument("--lineup", type=str, default=None,
                        help="逗号分隔的席位配置（bot 名或人格名），数量须等于 AI 席位数。"
                             "如 --players 2 --lineup turtle,rush")
    parser.add_argument("--no-talents", action="store_true",
                        help="全员不分配天赋（M2~M6 风洞主通道：天赋量纲 M7 才迁移）")
    parser.add_argument("--force-talent", type=str, default=None,
                        help="保证指定天赋（名称，如「大叔我啊，剪短发了」）出现在一个 AI 席位，"
                             "用于 M7 第二阶段逐天赋风洞")
    args = parser.parse_args()

    if (args.golden_record or args.golden_check) and args.seed is None:
        print("错误：--golden-record / --golden-check 需要 --seed")
        sys.exit(1)

    lineup: Optional[list] = None
    if args.lineup:
        lineup = [s.strip() for s in args.lineup.split(",") if s.strip()]
        ai_seats = args.players - (1 if args.model else 0)
        if len(lineup) != ai_seats:
            print(f"错误：--lineup 共 {len(lineup)} 个席位，但 AI 席位数为 {ai_seats}")
            sys.exit(1)
        from controllers.bots import BOT_REGISTRY
        from engine.game_setup import AI_PERSONALITIES as _PERS
        unknown = [s for s in lineup if s not in BOT_REGISTRY and s not in _PERS]
        if unknown:
            print(f"错误：未知席位名 {unknown}；可用 bot: {sorted(BOT_REGISTRY)}，"
                  f"可用人格: {_PERS}")
            sys.exit(1)
        print(f"  🤖 席位配置: {', '.join(lineup)}")

    from engine import experiments
    if args.profile:
        experiments.set_profile(args.profile)
    for exp_name in args.experiment:
        experiments.enable(exp_name)

    if not 2 <= args.players <= 6:
        print("玩家人数必须在 2-6 之间")
        sys.exit(1)

    print(f"  起闯战争 自动胜率统计")
    print(f"  {args.players}人局 × {args.games}局")
    if experiments.active():
        print(f"  ⚗️ 实验开关: {', '.join(experiments.active())}")
    if args.seed is not None:
        print(f"  随机种子: {args.seed}（第 i 局 = seed+i）")
        if os.environ.get("PYTHONHASHSEED", "random") in ("", "random"):
            print("  ⚠️  PYTHONHASHSEED 未固定——set 迭代序可能随进程变化，"
                  "跨进程复现/golden 回放请先设置 PYTHONHASHSEED=0")

    rl_controller = None
    if args.model:
        if not _rl_available:
            print("错误：RL 模块不可用，请确保 rl/ 目录和依赖已安装")
            sys.exit(1)
        print(f"  加载 RL 模型: {args.model}")
        rl_controller = OpponentRLController(model_path=args.model, n_stack=args.n_stack)
        print(f"  RL 天赋模式: {args.rl_talent}")
    print()

    if args.no_talents:
        print("  🚫 天赋系统：本批次全员禁用（--no-talents）")

    run_batch(args.players, args.games, rl_controller=rl_controller, rl_talent_mode=args.rl_talent,
              diag_mode=args.diag, diag_output=args.diag_output, seed=args.seed,
              golden_record=args.golden_record, golden_check=args.golden_check,
              lineup=lineup, no_talents=args.no_talents, force_talent=args.force_talent)


if __name__ == "__main__":
    main()
