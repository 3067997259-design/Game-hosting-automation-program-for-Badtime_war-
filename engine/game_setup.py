"""游戏初始化（Phase 4 完整版 + AI 支持）：玩家类型+天赋选择+不重复"""

from models.player import Player
from models.equipment import make_weapon
from engine.game_state import GameState
from engine.response_window import ResponseWindowManager
from engine.debug_config import enable_debug, debug_info, debug_system
from engine.prompt_manager import prompt_manager
from cli.display import show_banner, show_info

from controllers.human import HumanController
from controllers.ai_basic import BasicAIController

from talents.t1_one_slash import OneSlash
from talents.t2_oil_the_road import OilTheRoad
from talents.t3_star import Star
from talents.t4_hexagram import Hexagram
from talents.t5_delinquent import Delinquent
from talents.t6_good_citizen import GoodCitizen
from talents.t7_resurrection import Resurrection
from talents.g4_savior import Savior
from talents.g1_firefly import G1MythFire
from talents.g2_hologram import Hologram
from talents.g3_mythland import Mythland
from talents.g5_ripple import Ripple

import random

TALENT_TABLE = [
    (1, "一刀缭断", OneSlash,
     "主动1次：近战攻击伤害×2+无视克制"),
    (2, "你给路打油", OilTheRoad,
     "反应2次：R3期间声明获得额外行动回合"),
    (3, "天星", Star,
     "主动1次：同地点全体1伤害(无视克制)+石化"),
    (4, "六爻", Hexagram,
     "每5轮充能(上限2)：猜拳，6种效果"),
    (5, "不良少年", Delinquent,
     "常驻：热那亚之刃(攻击不犯罪)+每种新犯罪+1行动"),
    (6, "朝阳好市民", GoodCitizen,
     "常驻：远程举报+扩展犯罪名单+竞选-1回合"),
    (7, "死者苏生", Resurrection,
     "学习2回合→挂载目标→目标死亡时在家重生"),
    (8, "神代天赋-火萤IV型-完全燃烧", G1MythFire,
     "常驻：伤害+100%/受伤-50%/0.5血不眩晕。后期debuff扣护甲。"),
    (9, "神代天赋-请一直，注视着我", Hologram,
     "主动1次：给你一次成为聚光灯下最闪耀者的机会"),
    (10, "神代天赋-神话之外", Mythland,
     "主动1次：创造绝命死斗的竞技场"),
    (11, "神代天赋-愿负世，照拂黎明", Savior,
     "背负世界，然后，拯救世界，也拯救你自己"),
    (12, "神代天赋-往昔的涟漪", Ripple,
     "成为唤醒记忆的那颗流星，在命运长河中激起涟漪的石子"),
]

# ════════════════════════════════════════════════════════
#  AI 人格 → 天赋偏好映射（AI 自动选天赋用）
# ════════════════════════════════════════════════════════

AI_TALENT_PREFERENCE = {
    "aggressive": [8, 1, 3, 5, 4, 2, 9, 6, 10, 11, 7, 12],
    "defensive":  [11, 7, 2, 6, 4, 3, 10, 9, 1, 8, 12, 5],
    "political":  [6, 5, 7, 2, 4, 11, 3, 1, 9, 10, 8, 12],
    "assassin":   [5, 1, 10, 2, 4, 9, 8, 3, 6, 7, 11, 12],
    "builder":    [12, 7, 11, 6, 4, 2, 3, 9, 1, 10, 5, 8],
    "balanced":   [4, 2, 1, 3, 6, 5, 7, 11, 8, 9, 10, 12],
}

AI_PERSONALITIES = ["balanced", "aggressive", "defensive",
                    "political", "assassin", "builder"]

AI_NAME_POOL = [
    "阿尔法", "贝塔", "伽马", "德尔塔", "艾普西隆", "泽塔",
    "影", "刃", "霜", "焰", "雷", "风",
]


def setup_game():
    # ════════════════════════════════════════════════════════
    #  初始化提示管理系统
    # ════════════════════════════════════════════════════════
    # 提示管理器单例已在导入时自动初始化，无需重复加载

    # 使用新的提示系统显示横幅
    from engine.prompt_manager import show_info as pm_show_info
    pm_show_info("ui", "banner")

    game_state = GameState()
    # ════════════════════════════════════════════════════════
    #  调试模式选择
    # ════════════════════════════════════════════════════════

    print("\n  ─── 调试模式 ───")
    print("    0. 正常模式（无调试输出）")
    print("    1. 基本调试（AI关键决策）")
    print("    2. 详细调试（AI详细过程）")
    print("    3. 完整调试（所有调试信息）")

    while True:
        debug_raw = input("  请选择调试模式（0/1/2/3，默认0）：").strip()
        if debug_raw in ("", "0"):
            debug_level = 0
            break
        elif debug_raw == "1":
            debug_level = 1
            break
        elif debug_raw == "2":
            debug_level = 2
            break
        elif debug_raw == "3":
            debug_level = 3
            break
        print("  请输入 0~3。")

    if debug_level > 0:
        enable_debug(debug_level)
        debug_system(f"调试模式已启用，级别: {debug_level}")

    # ════════════════════════════════════════════════════════
    #  第一步：选择游戏模式
    # ════════════════════════════════════════════════════════

    print("  ─── 游戏模式 ───")
    print("    1. 全人类玩家（经典热座）")
    print("    2. 人类 + AI 混合")
    print("    3. 全 AI 观战（自动演示）")

    while True:
        mode_raw = input("  请选择模式（1/2/3，默认1）：").strip()
        if mode_raw in ("", "1"):
            game_mode = "all_human"
            break
        elif mode_raw == "2":
            game_mode = "mixed"
            break
        elif mode_raw == "3":
            game_mode = "all_ai"
            break
        print("  请输入 1、2 或 3。")

    # ════════════════════════════════════════════════════════
    #  第二步：确定人数
    # ════════════════════════════════════════════════════════

    if game_mode == "all_human":
        num_human, num_ai = _ask_player_count("人类玩家", 2, 6)
        num_ai = 0
    elif game_mode == "all_ai":
        num_human = 0
        num_ai_tuple = _ask_player_count("AI玩家", 2, 6)
        num_ai = num_ai_tuple[0]
    else:  # mixed
        print()
        num_human, _ = _ask_player_count("人类玩家", 1, 5)
        remaining = 6 - num_human
        if remaining < 1:
            num_ai = 0
            print("  人类玩家已达上限，无法加入AI。")
        else:
            _, num_ai = _ask_ai_count(1, remaining)

    total = num_human + num_ai
    print(f"\n  本局共 {total} 名玩家（{num_human} 人类 + {num_ai} AI）")

    # ════════════════════════════════════════════════════════
    #  第三步：创建人类玩家
    # ════════════════════════════════════════════════════════

    print()
    used_names = set()
    player_index = 1

    for i in range(num_human):
        while True:
            name = input(f"  请输入人类玩家{i+1}的名字：").strip()
            if not name:
                print("  名字不能为空。")
                continue
            if name in used_names:
                print("  名字已被使用。")
                continue
            break

        pid = f"p{player_index}"
        player = Player(pid, name, controller=HumanController())
        game_state.add_player(player)
        used_names.add(name)
        print(f"  ✓ 人类玩家{i+1}：{name}（ID: {pid}）")
        player_index += 1

    # ════════════════════════════════════════════════════════
    #  第四步：创建 AI 玩家
    # ════════════════════════════════════════════════════════

    ai_players_info = []  # 记录 (pid, name, personality) 供天赋选择用

    if num_ai > 0:
        print(f"\n  ─── AI 玩家配置 ───")

        # 问是否手动配置每个AI
        auto_ai = True
        if game_mode == "mixed":
            while True:
                raw = input("  是否自动配置AI？(y=自动随机/n=逐个配置，默认y)：").strip().lower()
                if raw in ("", "y", "yes", "是"):
                    auto_ai = True
                    break
                elif raw in ("n", "no", "否"):
                    auto_ai = False
                    break
                print("  请输入 y 或 n。")

        # 准备可用的AI名字
        available_ai_names = [n for n in AI_NAME_POOL if n not in used_names]
        random.shuffle(available_ai_names)

        for i in range(num_ai):
            if auto_ai:
                # 自动：随机名字 + 随机人格
                if available_ai_names:
                    ai_name = available_ai_names.pop()
                else:
                    ai_name = f"AI_{i+1}"
                personality = random.choice(AI_PERSONALITIES)
            else:
                # 手动配置名字
                while True:
                    ai_name = input(f"  AI玩家{i+1}的名字（留空自动）：").strip()
                    if not ai_name:
                        if available_ai_names:
                            ai_name = available_ai_names.pop()
                        else:
                            ai_name = f"AI_{i+1}"
                        break
                    if ai_name in used_names:
                        print("  名字已被使用。")
                        continue
                    break

                # 手动配置人格
                print(f"  可选AI人格：")
                for j, p in enumerate(AI_PERSONALITIES, 1):
                    desc = {
                        "balanced": "均衡型",
                        "aggressive": "进攻型",
                        "defensive": "防守型",
                        "political": "政治型",
                        "assassin": "暗杀型",
                        "builder": "发育型",
                    }.get(p, p)
                    print(f"    {j}. {p}（{desc}）")
                print(f"    0. 随机")

                while True:
                    p_raw = input(f"  [{ai_name}] 选择人格（编号，默认随机）：").strip()
                    if p_raw in ("", "0"):
                        personality = random.choice(AI_PERSONALITIES)
                        break
                    try:
                        p_idx = int(p_raw) - 1
                        if 0 <= p_idx < len(AI_PERSONALITIES):
                            personality = AI_PERSONALITIES[p_idx]
                            break
                    except ValueError:
                        pass
                    print("  请输入有效编号。")

            pid = f"p{player_index}"
            controller = BasicAIController(personality=personality)
            player = Player(pid, ai_name, controller=controller)
            game_state.add_player(player)
            used_names.add(ai_name)
            ai_players_info.append((pid, ai_name, personality))

            personality_cn = {
                "balanced": "均衡型",
                "aggressive": "进攻型",
                "defensive": "防守型",
                "political": "政治型",
                "assassin": "暗杀型",
                "builder": "发育型",
            }.get(personality, personality)
            print(f"  🤖 AI玩家{i+1}：{ai_name}（ID: {pid}，{personality_cn}）")
            player_index += 1
    # ════════════════════════════════════════════════════════
    #  第四步半：随机化玩家顺序（非全AI模式）
    # ════════════════════════════════════════════════════════

    if game_mode != "all_ai":
        # 随机打乱玩家顺序，避免人类玩家永远在前面吃到AI前期针对
        # 同时天赋选择也按此顺序进行
        random.shuffle(game_state.player_order)

        # 隐藏设定：AfterRain 固定在零号位（用于测试特定天赋）
        for i, pid in enumerate(game_state.player_order):
            p = game_state.get_player(pid)
            if p and p.name == "AfterRain":
                # 将 AfterRain 移到列表头部
                game_state.player_order.pop(i)
                game_state.player_order.insert(0, pid)
                break

        # 显示随机化后的顺序
        print(f"\n  ─── 玩家顺序（已随机化）───")
        for i, pid in enumerate(game_state.player_order):
            p = game_state.get_player(pid)
            is_ai = "🤖" if pid not in [info[0] for info in ai_players_info] else "🤖"
            # Determine if human or AI
            is_human = isinstance(p.controller, HumanController) # type: ignore
            icon = "👤" if is_human else "🤖"
            print(f"    {i+1}. {icon} {p.name}") # type: ignore

    # ════════════════════════════════════════════════════════
    #  第五步：天赋选择
    # ════════════════════════════════════════════════════════

    print(f"\n  ─── 天赋系统 ───")
    while True:
        enable = input("  是否启用天赋系统？(y/n，默认n)：").strip().lower()
        if enable in ("y", "yes", "是"):
            _talent_selection(game_state, ai_players_info)
            break
        elif enable in ("n", "no", "否", ""):
            print("  天赋系统未启用。")
            break
        print("  请输入 y 或 n。")
    # ════════════════════════════════════════════════════════
    #  第六步：演示速度（仅含AI时询问）
    # ════════════════════════════════════════════════════════
    if num_ai > 0:
        print(f"\n  ─── 演示速度 ───")
        print("    1. 逐步（每次行动按回车继续）← 推荐观战")
        print("    2. 慢速（每次行动后暂停2秒）")
        print("    3. 中速（每次行动后暂停0.5秒）")
        print("    4. 全速（无暂停）")
        while True:
            speed_raw = input("  选择速度（1/2/3/4，默认1）：").strip()
            if speed_raw in ("", "1"):
                game_state.pause_mode = True
                game_state.ai_delay = 0
                break
            elif speed_raw == "2":
                game_state.pause_mode = False
                game_state.ai_delay = 2.0
                break
            elif speed_raw == "3":
                game_state.pause_mode = False
                game_state.ai_delay = 0.5
                break
            elif speed_raw == "4":
                game_state.pause_mode = False
                game_state.ai_delay = 0
                break
            print("  请输入 1~4。")
    # ════════════════════════════════════════════════════════
    #  完成
    # ════════════════════════════════════════════════════════

    print()
    show_info("游戏初始化完成！所有玩家处于睡眠状态。")
    show_info("第一次获得行动权时，必须先「起床」。")

    if num_ai > 0:
        show_info(f"本局包含 {num_ai} 名AI玩家，AI行动将自动执行。")

    print()

    # 全AI模式不需要按回车
    if game_mode != "all_ai":
        input("  按回车键开始游戏...")

    return game_state


# ════════════════════════════════════════════════════════
#  人数输入辅助
# ════════════════════════════════════════════════════════

def _ask_player_count(label: str, min_n: int, max_n: int):
    """询问玩家人数，返回 (count, 0)"""
    while True:
        try:
            num = int(input(f"  请输入{label}人数（{min_n}~{max_n}）："))
            if min_n <= num <= max_n:
                return num, 0
            print(f"  请输入{min_n}到{max_n}之间的数字。")
        except ValueError:
            print("  请输入有效数字。")

def _ask_ai_count(min_n: int, max_n: int):
    """询问AI人数，返回 (0, count)"""
    while True:
        try:
            num = int(input(f"  请输入AI玩家人数（{min_n}~{max_n}）："))
            if min_n <= num <= max_n:
                return 0, num
            print(f"  请输入{min_n}到{max_n}之间的数字。")
        except ValueError:
            print("  请输入有效数字。")


# ════════════════════════════════════════════════════════
#  天赋选择（支持 AI 自动选择）
# ════════════════════════════════════════════════════════

def _talent_selection(game_state, ai_players_info=None):
    """
    天赋选择流程：不允许重复。
    人类玩家手动选；AI 玩家根据人格自动选。
    """
    if ai_players_info is None:
        ai_players_info = []

    # AI 玩家 pid 集合，用于判断是否自动选择
    ai_pids = {info[0] for info in ai_players_info}
    # AI pid → personality
    ai_personality_map = {info[0]: info[2] for info in ai_players_info}

    print(f"\n  可选天赋（每个天赋仅能被1人选取）：")
    for num, name, cls, desc in TALENT_TABLE:
        print(f"    {num}. 【{name}】{desc}")
    print(f"    0. 不选天赋")

    taken = set()  # 已被选走的天赋编号

    for pid in game_state.player_order:
        player = game_state.get_player(pid)

        available = [(n, name, cls, desc) for n, name, cls, desc in TALENT_TABLE
                     if n not in taken]

        if not available:
            print(f"  所有天赋已被选完，{player.name} 无天赋。")
            continue

        # ──── AI 自动选择 ────
        if pid in ai_pids:
            personality = ai_personality_map.get(pid, "balanced")
            chosen = _ai_pick_talent(personality, available, taken)
            if chosen:
                n, name, cls = chosen
                talent_inst = cls(pid, game_state)
                player.talent = talent_inst
                player.talent_name = name
                talent_inst.on_register()
                # 显示天赋激活效果
                talent_inst.show_activation(player_name=player.name, show_lore=True)
                taken.add(n)
                print(f"  🤖 {player.name}（AI·{personality}）自动选择天赋【{name}】")
            else:
                print(f"  🤖 {player.name}（AI）选择不使用天赋。")
            continue

        # ──── 人类手动选择（原逻辑保留） ────
        print(f"\n  ── {player.name} 选择天赋 ──")

        for n, name, cls, desc in available:
            print(f"    {n}. 【{name}】{desc}")
        print(f"    0. 不选")

        while True:
            raw = input(f"  [{player.name}] 请输入天赋编号：").strip()

            # 彩蛋检测：天赋6废案
            if raw == "所以我放弃了死亡":
                print("  「你确定？为了DM不至于掀桌子，还是算了吧」")
                print("  （天赋六暂未确认实现，请选择其他天赋。）")
                continue

            if raw == "0":
                print(f"  {player.name} 选择不使用天赋。")
                break

            try:
                choice_num = int(raw)
            except ValueError:
                print("  请输入有效编号。")
                continue

            if choice_num in taken:
                print("  该天赋已被其他玩家选走！")
                continue

            matched = None
            for n, name, cls, desc in TALENT_TABLE:
                if n == choice_num:
                    matched = (n, name, cls)
                    break

            if not matched:
                print("  无效编号。")
                continue

            n, name, cls = matched
            talent_inst = cls(pid, game_state)
            player.talent = talent_inst
            player.talent_name = name
            talent_inst.on_register()
            # 显示天赋激活效果
            talent_inst.show_activation(player_name=player.name, show_lore=True)
            taken.add(n)
            print(f"  ✓ {player.name} 获得天赋【{name}】！")
            break

    # 汇总
    print(f"\n  ─── 天赋分配结果 ───")
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        t = p.talent_name if p.talent_name else "无"
        is_ai = "🤖" if pid in ai_pids else "👤"
        print(f"    {is_ai} {p.name}: {t}")

def _ai_pick_talent(personality: str, available, taken: set):
    """
    AI 根据人格偏好从可用天赋中选择。
    返回 (编号, 名称, 类) 或 None（不选）。

    【调试增强】添加详细的选择过程日志，便于验证AI是否按倾向选择。
    """
    # 导入调试函数
    from engine.debug_config import debug_system, is_debug_enabled

    # 获取该人格的偏好列表
    preference = AI_TALENT_PREFERENCE.get(personality,
                                           AI_TALENT_PREFERENCE["balanced"])

    # 调试信息：显示人格和偏好列表
    if is_debug_enabled():
        debug_system(f"AI人格: {personality}")
        debug_system(f"偏好顺序: {preference}")
        debug_system(f"已选天赋: {taken}")
        debug_system(f"可用天赋: {[n for n, _, _, _ in available]}")

    # 按照偏好顺序查找
    for talent_num in preference:
        if talent_num in taken:
            if is_debug_enabled():
                debug_system(f"天赋 {talent_num} 已被选，跳过")
            continue
        for n, name, cls, desc in available:
            if n == talent_num:
                if is_debug_enabled():
                    debug_system(f"根据偏好选择天赋 {talent_num}: {name}")
                return (n, name, cls)

    # 偏好列表里的都被选走了 → 随机选一个
    if available:
        chosen = random.choice(available)
        if is_debug_enabled():
            debug_system(f"偏好天赋均不可用，随机选择: {chosen[0]}: {chosen[1]}")
        return (chosen[0], chosen[1], chosen[2])

    if is_debug_enabled():
        debug_system(f"无可用天赋")
    return None