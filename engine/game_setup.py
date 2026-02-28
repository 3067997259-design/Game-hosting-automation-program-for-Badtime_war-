"""游戏初始化（Phase 4 完整版）：天赋选择+不重复"""

from models.player import Player
from models.equipment import make_weapon
from engine.game_state import GameState
from engine.response_window import ResponseWindowManager
from cli.display import show_banner, show_info

from talents.t1_one_slash import OneSlash
from talents.t2_oil_the_road import OilTheRoad
from talents.t3_star import Star
from talents.t4_hexagram import Hexagram
from talents.t5_delinquent import Delinquent
from talents.t6_good_citizen import GoodCitizen
from talents.t7_resurrection import Resurrection
from talents.g4_savior import Savior
from talents.g1_blood_fire import BloodFire
from talents.g2_hologram import Hologram
from talents.g3_mythland import Mythland
from talents.g5_ripple import Ripple

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
    (8, "神代天赋-血火啊，燃烧前路",BloodFire,
     "开局触发，见证浴血战神、天生王者的末路"),
    (9, "神代天赋-请一直，注视着我", Hologram,
     "主动1次：给你一次成为聚光灯下最闪耀者的机会"),
    (10, "神代天赋-神话之外", Mythland,
     "主动1次：创造绝命死斗的竞技场"),
    (11, "神代天赋-愿负世，照拂黎明", Savior,
     "背负世界，然后，拯救世界，也拯救你自己"),
    (12, "神代天赋-往昔的涟漪", Ripple,
     "成为唤醒记忆的那颗流星，在命运长河中激起涟漪的石子")
    
]


def setup_game():
    show_banner()
    game_state = GameState()
    game_state.response_window = ResponseWindowManager(game_state)

    # ---- 玩家人数 ----
    while True:
        try:
            num = int(input("  请输入玩家人数（2~6）："))
            if 2 <= num <= 6:
                break
            print("  请输入2到6之间的数字。")
        except ValueError:
            print("  请输入有效数字。")

    # ---- 玩家名字 ----
    print()
    for i in range(num):
        while True:
            name = input(f"  请输入玩家{i+1}的名字：").strip()
            if name:
                if any(p.name == name for p in game_state.players.values()):
                    print("  名字已被使用。")
                    continue
                break
            print("  名字不能为空。")

        pid = f"p{i+1}"
        player = Player(pid, name)
        game_state.add_player(player)
        print(f"  ✓ 玩家{i+1}：{name}（ID: {pid}）")

    # ---- 天赋选择 ----
    print(f"\n  ─── 天赋系统 ───")
    while True:
        enable = input("  是否启用天赋系统？(y/n，默认n)：").strip().lower()
        if enable in ("y", "yes", "是"):
            _talent_selection(game_state)
            break
        elif enable in ("n", "no", "否", ""):
            print("  天赋系统未启用。")
            break
        print("  请输入 y 或 n。")

    print()
    show_info("游戏初始化完成！所有玩家处于睡眠状态。")
    show_info("第一次获得行动权时，必须先「起床」。")
    print()
    input("  按回车键开始游戏...")
    return game_state


def _talent_selection(game_state):
    """天赋选择流程：不允许重复"""
    print(f"\n  可选天赋（每个天赋仅能被1人选取）：")
    for num, name, cls, desc in TALENT_TABLE:
        print(f"    {num}. 【{name}】{desc}")
    print(f"    0. 不选天赋")

    taken = set()  # 已被选走的天赋编号

    for pid in game_state.player_order:
        player = game_state.get_player(pid)
        print(f"\n  ── {player.name} 选择天赋 ──")

        available = [(n, name, cls, desc) for n, name, cls, desc in TALENT_TABLE
                     if n not in taken]

        if not available:
            print(f"  所有天赋已被选完，{player.name} 无天赋。")
            continue

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
            taken.add(n)
            print(f"  ✓ {player.name} 获得天赋【{name}】！")
            break

    # 汇总
    print(f"\n  ─── 天赋分配结果 ───")
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        t = p.talent_name if p.talent_name else "无"
        print(f"    {p.name}: {t}")
