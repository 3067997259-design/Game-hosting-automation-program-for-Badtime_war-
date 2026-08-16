"""
《起闯战争》CLI DM ver1.94
═══════════════════════════════════

支持双模式启动：
  交互式:  python main.py                          （原有行为）
  命令行:  python main.py --mode all_ai --players 6  （一键启动）

日志系统：
  --debug-level 0-3 控制调试输出详细度
  --log-file 指定日志保存路径
（C7 后仅保留新架构 DecisionOrchestrator，--new-arch 已移除）
"""

import argparse
import random
import sys
import os
from typing import Optional, List, Any

from engine.game_state import GameState
from engine.round_manager import RoundManager
from engine.game_setup import (
    AI_PERSONALITIES, AI_NAME_POOL,
    _ai_pick_talent, AI_DISABLED_TALENTS, assign_talent_entry,
    find_talent_entry, talent_table_for_current_profile,
)
from models.player import Player
from controllers.ai.controller import BasicAIController
from controllers.human import HumanController
from engine.debug_config import enable_debug, DebugConfig
from cli import display as _dm


# ════════════════════════════════════════════════════════════
#  命令行参数
# ════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(description="起闯战争 CLI")
    p.add_argument("--mode", choices=["all_human", "mixed", "all_ai"],
                   help="游戏模式（跳过交互式选择）")
    p.add_argument("--players", type=int, default=0,
                   help="总玩家数（2-6）")
    p.add_argument("--humans", type=int, default=0,
                   help="人类玩家数（mixed模式下）")
    p.add_argument("--debug-level", type=int, default=0, choices=[0, 1, 2, 3],
                   help="调试级别 0=关闭 1=基本 2=详细 3=完整")
    p.add_argument("--log-file", type=str, default="",
                   help="日志保存路径（留空则输出到stdout）")
    # --new-arch 已移除（C7 后仅保留新架构 DecisionOrchestrator）
    p.add_argument("--experiment", action="append", default=[],
                   help="启用实验开关（可多次使用），如 --experiment k_initiative")
    p.add_argument("--profile", type=str, default="",
                   help="启用实验档案（legacy/m1/m2/m3/m4/m5/m6/v2exp/m9-rfc）")
    p.add_argument("--ai", action="append", default=[],
                   help="指定AI配置, 格式: name:talent:personality (可多次使用)")
    p.add_argument("--human-names", type=str, default="",
                   help="CLI 模式人类玩家名（逗号分隔）")
    p.add_argument("--human-talents", type=str, default="",
                   help="CLI 模式人类玩家天赋（逗号分隔，按人类顺序分配，"
                        "如 T3,G4,一刀缭断）")
    p.add_argument("--force-talent", type=str, default="",
                   help="强制第一个AI使用指定天赋名(如 G5, G7, 一刀缭断)")
    p.add_argument("--seed", type=int, default=None,
                   help="固定随机种子（用于可复现的 E2E/风洞）")
    return p.parse_args()


# ════════════════════════════════════════════════════════════
#  Talent 名称映射
# ════════════════════════════════════════════════════════════

TALENT_SHORT = {
    "T1": "一刀缭断", "T2": "剪刀手一突", "T3": "天星",
    "T4": "六爻", "T5": "combo", "T6": "朝阳好市民", "T7": "死者苏生",
    "G0": "砂狼白子*Terror",
    "G1": "火萤IV型-完全燃烧", "G2": "请一直注视着我",
    "G3": "神话之外", "G4": "愿负世，照拂黎明",
    "G5": "往世的涟漪", "G6": "要有笑声！",
    "G7": "大叔我啊，剪短发了",
}
TALENT_REVERSE = {v: k for k, v in TALENT_SHORT.items()}

def talent_short_name(name: str) -> str:
    """天赋名 → 槽位缩写（兼容 M9 注册表带「神代天赋-」前缀的全名）。"""
    if not name:
        return ""
    if name in TALENT_REVERSE:
        return TALENT_REVERSE[name]
    for short, full in TALENT_SHORT.items():
        if name.endswith(full) or full in name:
            return short
    return ""

def _split_cli_list(raw: str) -> List[str]:
    """逗号/中文逗号分隔的 CLI 列表，去空白。"""
    return [part.strip() for part in str(raw).replace("，", ",").split(",")
            if part.strip()]

def resolve_talent(name: str) -> Optional[str]:
    """解析天赋名: G5 → 往世的涟漪, 一刀缭断 → 一刀缭断"""
    if name in TALENT_SHORT:
        return TALENT_SHORT[name]
    for full_name in TALENT_SHORT.values():
        if name in full_name:
            return full_name
    return None


# ════════════════════════════════════════════════════════════
#  非交互式游戏初始化
# ════════════════════════════════════════════════════════════

def setup_game_cli(args) -> GameState:
    """根据命令行参数创建游戏，不询问任何交互问题"""
    game_state = GameState()
    talent_table = talent_table_for_current_profile(game_state)
    used_names = set()

    # 确定人数
    if args.mode == "all_human":
        num_human = args.players or 2
        num_ai = 0
    elif args.mode == "all_ai":
        num_human = 0
        num_ai = args.players or 6
    elif args.mode == "mixed":
        num_human = args.humans or 1
        num_ai = (args.players or 6) - num_human
    else:
        num_human = 0
        num_ai = args.players or 6

    num_human = max(0, min(num_human, 6))
    num_ai = max(0, min(num_ai, 6 - num_human))
    total = num_human + num_ai

    # 准备AI名字池
    ai_names = [n for n in AI_NAME_POOL if n not in used_names]
    random.shuffle(ai_names)

    # 解析 --ai 参数
    ai_configs = []
    for spec in args.ai:
        parts = spec.split(":")
        name = parts[0] if len(parts) > 0 and parts[0] else ""
        talent_raw = parts[1] if len(parts) > 1 else ""
        personality = parts[2] if len(parts) > 2 else ""
        talent = talent_raw.strip()
        ai_configs.append((name, talent, personality))

    human_names = _split_cli_list(getattr(args, "human_names", ""))
    human_talents = _split_cli_list(getattr(args, "human_talents", ""))

    player_index = 1
    taken_talents = set()
    human_pids = []

    # 创建人类玩家（名称 + 天赋都可经 CLI 指定）
    for i in range(num_human):
        if i < len(human_names):
            name = human_names[i]
            if name in used_names:
                name = f"{name}_{i}"
        else:
            name = f"人类{i+1}"
        pid = f"p{player_index}"
        player = Player(pid, name, controller=HumanController())
        game_state.add_player(player)
        used_names.add(name)
        human_pids.append(pid)

        assigned_talent = human_talents[i] if i < len(human_talents) else ""
        if assigned_talent:
            entry = find_talent_entry(
                assigned_talent, talent_table, game_state=game_state)
            if entry is None or entry[0] in taken_talents:
                from engine.m9.gate import m9_enabled
                if m9_enabled(game_state):
                    from engine.m9.text import m9_text
                    raise ValueError(
                        m9_text("main.err_assign_human", name=name,
                                talent=repr(assigned_talent))
                    )
                print(f"  ⚠ 人类玩家 {name} 的天赋 '{assigned_talent}' "
                      "不可用，本次不分配")
            else:
                assign_talent_entry(game_state, player, entry)
                taken_talents.add(entry[0])
                print(f"  👤 {pid}: {name:8s} [{talent_short_name(player.talent_name or '') or '无':6s}]")
        else:
            print(f"  👤 {pid}: {name:8s} [{'无':6s}]")
        player_index += 1

    if num_human and not human_talents:
        print("  ℹ️ 未指定 --human-talents：CLI 模式的人类玩家将以无天赋开始；"
              "可用 --human-talents T3,G4,一刀缭断 指定（逗号分隔）")

    # 创建AI玩家
    for i in range(num_ai):
        # 确定AI名字
        if i < len(ai_configs) and ai_configs[i][0]:
            ai_name = ai_configs[i][0]
        elif ai_names:
            ai_name = ai_names.pop()
        else:
            ai_name = f"AI_{i+1}"
        if ai_name in used_names:
            ai_name = f"{ai_name}_{i}"
        used_names.add(ai_name)

        # 确定人格
        if i < len(ai_configs) and ai_configs[i][2]:
            personality = ai_configs[i][2]
            if personality not in AI_PERSONALITIES:
                print(f"  ⚠ 未知人格 '{personality}', 随机选择")
                personality = random.choice(AI_PERSONALITIES)
        else:
            personality = random.choice(AI_PERSONALITIES)

        pid = f"p{player_index}"
        controller = BasicAIController(personality=personality)
        player = Player(pid, ai_name, controller=controller)
        game_state.add_player(player)

        # 天赋分配
        assigned_talent = ""
        # 命令行指定
        if i < len(ai_configs) and ai_configs[i][1]:
            assigned_talent = ai_configs[i][1]
        # --force-talent（仅第一个AI）
        elif args.force_talent and i == 0:
            assigned_talent = args.force_talent.strip()

        if assigned_talent:
            entry = find_talent_entry(
                assigned_talent, talent_table, game_state=game_state)
            if entry is not None and entry[0] not in taken_talents:
                n, tname, _, _ = entry
                assign_talent_entry(game_state, player, entry)
                taken_talents.add(n)
            else:
                from engine.m9.gate import m9_enabled
                if m9_enabled(game_state):
                    from engine.m9.text import m9_text
                    raise ValueError(
                        m9_text("main.err_assign_ai",
                                talent=repr(assigned_talent))
                    )
                print(f"  ⚠ 天赋 '{assigned_talent}' 不可用，随机分配")
                assigned_talent = ""

        if not assigned_talent:
            avail = [(n, name, cls, desc) for n, name, cls, desc in talent_table
                     if n not in taken_talents and n not in AI_DISABLED_TALENTS]
            if avail:
                chosen = _ai_pick_talent(personality, avail, taken_talents)
                if chosen:
                    n, tname, cls = chosen
                    entry = next(item for item in avail if item[0] == n)
                    assign_talent_entry(game_state, player, entry)
                    taken_talents.add(n)

        short = talent_short_name(player.talent_name or "")
        print(f"  {pid}: {ai_name:8s} [{short or player.talent_name or '无':6s}] 人格={personality:12s}")
        player_index += 1

    random.shuffle(game_state.player_order)
    game_state.max_rounds = GameState.compute_default_max_rounds(total)
    return game_state


# ════════════════════════════════════════════════════════════
#  交互式模式（原有行为）
# ════════════════════════════════════════════════════════════

def setup_game_interactive(args) -> GameState:
    """原有的交互式 setup_game（C7 后统一走新架构 DecisionOrchestrator）"""
    from engine.game_setup import setup_game as _orig_setup
    # 调用原版交互式流程（--debug-level 显式传入，不再重复询问）
    state = _orig_setup(debug_level=args.debug_level)

    # 打印所有 AI 玩家的信息
    print("\n  ═══ AI 玩家信息 ═══")
    for pid in state.player_order:
        p = state.get_player(pid)
        if p and hasattr(p.controller, 'personality'):
            ctrl = p.controller
            pers = getattr(ctrl, 'personality', '?')
            talent_name = getattr(p, 'talent_name', '') or '无'
            # 缩写天赋名
            short = talent_short_name(talent_name)
            talent_str = short if short else talent_name
            arch = type(ctrl).__name__
            print(f"    {pid}: {p.name:8s} | 人格={pers:12s} "
                  f"| 天赋={talent_str:6s} | 控制器={arch}")
    print("  ════════════════\n")
    return state


# ════════════════════════════════════════════════════════════
#  main
# ════════════════════════════════════════════════════════════

def main():
    args = parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    # 实验开关（V2.0 EXP）
    if args.profile or args.experiment:
        from engine import experiments
        if args.profile:
            experiments.set_profile(args.profile)
        for exp_name in args.experiment:
            experiments.enable(exp_name)
        print(f"  ⚗️ 实验开关: {', '.join(experiments.active())}")

    # 日志文件
    log_fp = None
    if args.log_file:
        os.makedirs(os.path.dirname(args.log_file) or "logs", exist_ok=True)
        log_fp = open(args.log_file, 'w', encoding='utf-8')
        # 重定向 debug 输出到文件
        import engine.debug_config as dc
        _orig_print = print
        def _file_print(*a, **kw):
            msg = " ".join(str(x) for x in a)
            log_fp.write(msg + "\n")
            log_fp.flush()
            _orig_print(msg)  # 同时输出到终端
        # 拦截所有 debug_ai 函数
        dc.debug_ai = lambda p, m, min_level=1, **kw: (
            _file_print(f"[{p}] {m}") if DebugConfig.should_show(min_level) else None
        )
        dc.debug_ai_basic = lambda p, m: dc.debug_ai(p, m, 1)
        dc.debug_ai_detailed = lambda p, m: dc.debug_ai(p, m, 2)
        dc.debug_ai_full = lambda p, m: dc.debug_ai(p, m, 3)
        dc.debug_ai_combat_state = lambda p, s: dc.debug_ai(p, f"战斗状态：{s}", 1)
        dc.debug_ai_development_plan = lambda p, pl: dc.debug_ai(p, f"发育计划：{pl}", 2)
        dc.debug_ai_candidate_commands = lambda p, cmds: dc.debug_ai(p, f"候选命令：{cmds}", 2)
        dc.debug_ai_attack_generation = lambda p, w, t: dc.debug_ai(p, f"攻击生成：{w} 对 {t}", 2)
        dc.debug_ai_kill_opportunity = lambda p, t, hp: dc.debug_ai(p, f"击杀机会：{t} (HP:{hp})", 1)

    # 调试级别
    if args.debug_level > 0:
        enable_debug(args.debug_level)
        if args.mode:
            print(f"  调试级别: {args.debug_level} [新架构 DecisionOrchestrator]")
            if args.log_file:
                print(f"  日志文件: {args.log_file}")

    # 静默 display 输出（非交互模式下）
    if args.mode:
        for name in dir(_dm):
            if name.startswith("show_") or name == "prompt_input":
                try:
                    setattr(_dm, name, lambda *a, **kw: None)
                except Exception:
                    pass

    # 选择初始化方式
    if args.mode:
        game_state = setup_game_cli(args)
    else:
        game_state = setup_game_interactive(args)

    # 诊断：确认新架构激活（C7 后仅新架构）
    print(f"\n{'='*52}")
    print(f"  起闯战争 — 新架构 DecisionOrchestrator")
    print(f"{'='*52}")

    # 运行游戏
    round_mgr = RoundManager(game_state)
    try:
        round_mgr.run_game_loop()
    except KeyboardInterrupt:
        print("\n\n  游戏被手动中断。")
    finally:
        if hasattr(game_state, 'logger') and game_state.logger:
            game_state.logger.close()
        if log_fp:
            log_fp.close()
        # 输出结果
        winner_id = getattr(game_state, 'winner', None)
        if winner_id:
            winner = game_state.get_player(winner_id)
            name = winner.name if winner else winner_id
            print(f"\n  胜者: {name} — 共 {game_state.current_round} 轮")
        else:
            print(f"\n  游戏结束 — 共 {game_state.current_round} 轮")


if __name__ == "__main__":
    main()
