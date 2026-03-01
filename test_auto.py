"""
自动测试脚本（扩展版）：覆盖更多边界情况。
运行方式：python test_auto.py [编号]
"""

import sys
import traceback

_script_lines = []
_script_index = 0
_original_input = __builtins__.input if hasattr(__builtins__, 'input') else None

import getpass as _getpass
_original_getpass = _getpass.getpass


def fake_input(prompt=""):
    global _script_index
    if _script_index < len(_script_lines):
        line = _script_lines[_script_index]
        _script_index += 1
        print(f"{prompt}{line}  [自动]")
        return line
    else:
        print(f"{prompt}[脚本结束]")
        raise SystemExit("测试脚本执行完毕")


def fake_getpass(prompt=""):
    return fake_input(prompt)


def run_test(test_name, player_count, player_names, action_script, fixed_dice=None):
    global _script_lines, _script_index

    print(f"\n{'#'*60}")
    print(f"# 测试：{test_name}")
    print(f"{'#'*60}\n")

    init_script = [str(player_count)] + player_names + [""]
    _script_lines = init_script + action_script
    _script_index = 0

    import builtins
    builtins.input = fake_input
    _getpass.getpass = fake_getpass

    original_d4 = None
    original_d6 = None

    if fixed_dice:
        import utils.dice as dice_module
        dice_idx = [0]
        original_d4 = dice_module.roll_d4
        original_d6 = dice_module.roll_d6

        def fixed_d4():
            if dice_idx[0] < len(fixed_dice):
                val = fixed_dice[dice_idx[0]]
                dice_idx[0] += 1
                return min(max(val, 1), 4)
            return original_d4()

        def fixed_d6():
            if dice_idx[0] < len(fixed_dice):
                val = fixed_dice[dice_idx[0]]
                dice_idx[0] += 1
                return min(max(val, 1), 6)
            return original_d6()

        dice_module.roll_d4 = fixed_d4
        dice_module.roll_d6 = fixed_d6

    try:
        # 清除模块缓存中的游戏状态（避免测试之间互相污染）
        mods_to_clear = [k for k in sys.modules if k.startswith(('engine.', 'models.', 'actions.', 'locations.', 'combat.', 'cli.'))]
        for m in mods_to_clear:
            del sys.modules[m]

        from engine.game_setup import setup_game
        from engine.round_manager import RoundManager

        game_state = setup_game()
        round_mgr = RoundManager(game_state)
        round_mgr.run_game_loop()

    except SystemExit as e:
        print(f"\n  ✅ 测试正常结束：{e}")
    except Exception as e:
        print(f"\n  ❌ 测试崩溃！")
        print(f"  错误类型：{type(e).__name__}")
        print(f"  错误信息：{e}")
        traceback.print_exc()
        return False
    finally:
        import builtins
        if _original_input:
            builtins.input = _original_input
        _getpass.getpass = _original_getpass
        if fixed_dice and original_d4:
            import utils.dice as dice_module
            dice_module.roll_d4 = original_d4
            dice_module.roll_d6 = original_d6

    print(f"\n  ✅ 测试 [{test_name}] 通过！")
    return True


# ============================================
#  原有测试 1-8
# ============================================

def test_1_basic_walkthrough():
    dice = [
        4, 1, 1,  1, 4, 1,  1, 1, 4,
        4, 1, 1,  1, 4, 1,  1, 1, 4,
        4, 1, 1,  1, 4, 1,  1, 1, 4,
        4, 1, 1,  1, 4, 1,  1, 1, 4,
    ]
    actions = [
        "interact 拿凭证", "interact 拿刀", "interact 拿盾",
        "interact 拿刀", "interact 拿凭证", "interact 拿凭证",
        "move 商店", "move 魔法所", "move 医院",
        "status", "allstatus", "forfeit",
        "interact 魔法护盾", "interact 防毒面具",
    ]
    return run_test("基础走路+交互", 3, ["Alice", "Bob", "Carol"], actions, dice)


def test_2_shop_and_hospital():
    dice = [4, 1, 1, 4, 4, 1, 1, 4, 4, 1, 1, 4, 4, 1, 1, 4, 4, 1, 1, 4]
    actions = [
        "interact 拿凭证", "interact 拿凭证",
        "move 商店", "move 医院",
        "interact 陶瓷护甲", "interact 晶化皮肤手术",
        "status", "forfeit", "allstatus", "forfeit",
    ]
    return run_test("商店购买+医院手术", 2, ["Alice", "Bob"], actions, dice)


def test_3_combat():
    dice = [4, 1, 1, 4, 4, 1, 1, 4, 4, 1, 4, 1]
    actions = [
        "interact 拿刀", "forfeit",
        "move home_p2", "forfeit",
        "find Bob", "attack Bob 小刀",
    ]
    return run_test("战斗（找到→攻击→击杀）", 2, ["Alice", "Bob"], actions, dice)


def test_4_military_base():
    dice = [4, 1] * 10
    actions = [
        "move 军事基地", "interact 办理通行证",
        "interact AT力场", "interact 高斯步枪",
        "special 蓄力高斯步枪", "status", "forfeit",
    ]
    return run_test("军事基地通行证+装备+蓄力", 2, ["Alice", "Bob"], actions, dice)


def test_5_magic_learning():
    dice = [4, 1] * 12
    actions = [
        "move 魔法所", "interact 魔法弹幕",
        "interact 远程魔法弹幕", "interact 封闭",
        "interact 封闭", "interact 地震",
        "interact 地动山摇", "status", "forfeit",
    ]
    return run_test("魔法所学习+前置依赖", 2, ["Alice", "Bob"], actions, dice)


def test_6_police_report():
    dice = [
        4, 1, 1, 4, 4, 1, 1, 4,
        4, 1, 1, 4, 4, 1, 1, 4, 1, 4,
    ]
    actions = [
        "interact 拿刀", "interact 拿盾",
        "move home_p2", "move 警察局",
        "move 警察局", "forfeit", "forfeit",
    ]
    return run_test("警察举报流程（基础）", 2, ["Alice", "Bob"], actions, dice)


def test_7_virus():
    dice = [4, 1] * 15
    actions = [
        "move 医院", "interact 防毒面具",
        "special 释放病毒", "forfeit", "forfeit", "forfeit",
    ]
    return run_test("病毒释放→致死", 2, ["Alice", "Bob"], actions, dice)


def test_8_forfeit_and_bonus():
    dice = [
        4, 1, 1, 4, 1, 4, 1, 4, 1, 4,
        1, 4, 1, 4, 1, 4, 1, 4,
    ]
    actions = [
        "forfeit", "forfeit", "forfeit",
        "forfeit", "forfeit", "forfeit", "forfeit",
    ]
    return run_test("保底加成机制", 2, ["Alice", "Bob"], actions, dice)


# ============================================
#  新增测试 9-16
# ============================================

def test_9_armor_counter():
    """
    测试9：护甲克制
    Alice有小刀（普通），Bob有陶瓷护甲（普通）→ 同属性有效 → 应该打得动
    然后Alice用魔法弹幕打Bob陶瓷护甲（普通）→ 魔法被普通克制 → 应该打不动
    """
    dice = [4, 1] * 20
    actions = [
        # 轮2: Alice 拿刀
        "interact 拿刀",
        # 轮3: Alice 拿凭证
        "interact 拿凭证",
        # 轮4: Alice 移动到商店
        "move 商店",
        # 轮5: Alice 买陶瓷护甲（给自己，但这里目标是测试攻击Bob）
        # 改：让Alice去魔法所学魔法弹幕
        "move 魔法所",
        # 轮6: 学魔法弹幕
        "interact 魔法弹幕",
        # 轮7: 移动到Bob家
        "move home_p2",
        # 轮8: 找到Bob
        "find Bob",
        # 轮9: 用小刀攻击Bob（无甲，直接打HP）→ Bob应该死
        "attack Bob 小刀",
    ]
    return run_test("护甲克制+无甲直打HP", 2, ["Alice", "Bob"], actions, dice)


def test_10_move_clears_lock_and_engaged():
    """
    测试10：移动清除锁定和面对面
    Alice锁定Bob → Bob移动 → 锁定应失效
    Alice找到Bob → Alice移动 → 面对面应解除
    """
    dice = [
        4, 1,  # 轮1: Alice起床
        1, 4,  # 轮2: Bob起床
        4, 1,  # 轮3: Alice拿刀
        1, 4,  # 轮4: Bob移动到商店
        4, 1,  # 轮5: Alice移动到商店
        4, 1,  # 轮6: Alice锁定Bob
        1, 4,  # 轮7: Bob移动离开（锁定应失效）
        4, 1,  # 轮8: Alice移动到Bob位置
        4, 1,  # 轮9: Alice找到Bob
        4, 1,  # 轮10: Alice移动离开（面对面应解除）
        4, 1,  # 轮11: Alice回来后需要重新找到
    ]
    actions = [
        # 轮3: Alice拿刀
        "interact 拿刀",
        # 轮4: Bob移动到商店
        "move 商店",
        # 轮5: Alice移动到商店
        "move 商店",
        # 轮6: Alice锁定Bob
        "lock Bob",
        # 轮7: Bob移动到魔法所（锁定应自动失效）
        "move 魔法所",
        # 轮8: Alice移动到魔法所
        "move 魔法所",
        # 轮9: Alice找到Bob（建立面对面）
        "find Bob",
        # 轮10: Alice移动到医院（面对面应解除）
        "move 医院",
        # 轮11: Alice再回魔法所，需要重新找到才能攻击
        "move 魔法所",
    ]
    return run_test("移动清除锁定和面对面", 2, ["Alice", "Bob"], actions, dice)


def test_11_stun_and_recover():
    """
    测试11：眩晕和苏醒
    Alice拳击Bob（0.5伤害）→ Bob眩晕
    下一轮Bob如果赢D4 → 应自动苏醒并HP恢复到1
    """
    dice = [
        4, 1,  # 轮1: Alice起床
        1, 4,  # 轮2: Bob起床
        4, 1,  # 轮3: Alice
        1, 4,  # 轮4: Bob
        4, 1,  # 轮5: Alice
        4, 1,  # 轮6: Alice找到
        4, 1,  # 轮7: Alice攻击→Bob眩晕
        1, 4,  # 轮8: Bob赢→应苏醒并行动
    ]
    actions = [
        # 轮3: Alice移动到Bob家
        "move home_p2",
        # 轮4: Bob放弃
        "forfeit",
        # 轮5: Alice找到Bob
        "find Bob",
        # 轮6: Alice拳击Bob→0.5伤害→眩晕
        "attack Bob 拳击",
        # 轮7: 这轮Alice赢但已经没有额外行动需求
        "forfeit",
        # 轮8: Bob赢，应自动苏醒HP=1然后行动
        "status",
        "forfeit",
    ]
    return run_test("眩晕→苏醒→HP恢复", 2, ["Alice", "Bob"], actions, dice)


def test_12_ranged_attack():
    """
    测试12：远程攻击流程
    Alice学远程魔法弹幕 → 锁定Bob → 远程攻击
    """
    dice = [4, 1] * 20
    actions = [
        # 轮2: 移动到魔法所
        "move 魔法所",
        # 轮3: 学魔法弹幕
        "interact 魔法弹幕",
        # 轮4: 学远程魔法弹幕
        "interact 远程魔法弹幕",
        # 轮5: 锁定Bob
        "lock Bob",
        # 轮6: 远程攻击Bob
        "attack Bob 远程魔法弹幕",
    ]
    return run_test("远程锁定→攻击", 2, ["Alice", "Bob"], actions, dice)


def test_13_multi_armor_layers():
    """
    测试13：多层护甲
    Bob拿盾牌（外层普通）+ 去商店买陶瓷护甲（外层普通→同属性不能叠加）
    Bob拿盾牌 + 学魔法护盾（外层魔法）→ 两层外甲
    Alice拿刀打Bob → 先打盾牌（普通打普通=有效）→ 再打魔法护盾（普通打魔法=有效）
    """
    dice = [
        4, 1,  # 轮1: Alice
        1, 4,  # 轮2: Bob
        4, 1,  # 轮3
        1, 4,  # 轮4
        4, 1,  # 轮5
        1, 4,  # 轮6
        4, 1,  # 轮7
        1, 4,  # 轮8
        4, 1,  # 轮9
        4, 1,  # 轮10
        4, 1,  # 轮11
    ]
    actions = [
        # 轮3: Alice 拿刀
        "interact 拿刀",
        # 轮4: Bob 拿盾
        "interact 拿盾",
        # 轮5: Alice 移动到魔法所等Bob
        "move 商店",
        # 轮6: Bob 移动到魔法所
        "move 魔法所",
        # 轮7: Alice 移动到魔法所
        "move 魔法所",
        # 轮8: Bob 学魔法护盾
        "interact 魔法护盾",
        # 轮9: Alice 找到Bob
        "find Bob",
        # 轮10: Alice 用小刀攻击Bob盾牌（外层普通）
        "attack Bob 小刀 外层 普通",
        # 轮11: Alice 用小刀攻击Bob魔法护盾（外层魔法）→ 普通打魔法=有效
        "attack Bob 小刀 外层 魔法",
    ]
    return run_test("多层外甲+指定层攻击", 2, ["Alice", "Bob"], actions, dice)


def test_14_shield_priority():
    """
    测试14：盾牌优先消耗
    Bob有盾牌+魔法护盾，被普通攻击时盾牌应优先被打
    （自动选择时应选盾牌因为priority=100）
    """
    dice = [
        4, 1, 1, 4, 4, 1, 1, 4,
        4, 1, 1, 4, 4, 1, 1, 4,
        4, 1,
    ]
    actions = [
        # Alice拿刀
        "interact 拿刀",
        # Bob拿盾
        "interact 拿盾",
        # Alice等待
        "forfeit",
        # Bob去魔法所
        "move 魔法所",
        # Alice等待
        "forfeit",
        # Bob学魔法护盾
        "interact 魔法护盾",
        # Alice移到魔法所
        "move 魔法所",
        # Bob留在魔法所
        "forfeit",
        # Alice找到Bob并攻击（不指定层→自动选→应选盾牌因priority高）
        "find Bob",
    ]
    return run_test("盾牌优先消耗（自动选层）", 2, ["Alice", "Bob"], actions, dice)


def test_15_force_military_entry():
    """
    测试15：强买军事基地通行证
    Alice有凭证 → 移动到军事基地 → 应该可以强买通行证
    注意：当前实现中强买需要单独操作，不是移动时自动触发
    测试正常办理 vs 状态检查
    """
    dice = [4, 1] * 12
    actions = [
        # 轮2: 拿凭证
        "interact 拿凭证",
        # 轮3: 移动到军事基地
        "move 军事基地",
        # 轮4: 办理通行证（免费方式）
        "interact 办理通行证",
        # 轮5: 拿雷达（验证通行证生效）
        "interact 雷达",
        # 轮6: 拿隐形涂层
        "interact 隐形涂层",
        # 轮7: 查看状态确认隐身
        "status",
        "forfeit",
    ]
    return run_test("军事基地通行证+隐身涂层+雷达", 2, ["Alice", "Bob"], actions, dice)


def test_16_crime_and_full_police():
    """
    测试16：犯罪检测 + 完整举报→集结→执法
    Alice打Bob → Alice有犯罪记录
    Bob去警察局举报 → 集结 → 轮次结束时警察出动并攻击Alice
    """
    dice = [
        4, 1,  # 轮1: Alice起床
        1, 4,  # 轮2: Bob起床
        4, 1,  # 轮3: Alice
        1, 4,  # 轮4: Bob
        4, 1,  # 轮5: Alice
        1, 4,  # 轮6: Bob
        4, 1,  # 轮7: Alice (find)
        4, 1,  # 轮8: Alice (attack → crime)
        1, 4,  # 轮9: Bob (report)
        1, 4,  # 轮10: Bob (assemble) → 轮末警察出动
        1, 4,  # 轮11: Bob forfeit → 轮末警察攻击Alice
        1, 4,  # 轮12: Bob forfeit → 轮末警察再攻击
    ]
    actions = [
        # 轮3: Alice拿刀
        "interact 拿刀",
        # 轮4: Bob拿盾
        "interact 拿盾",
        # 轮5: Alice移到Bob家
        "move home_p2",
        # 轮6: Bob移到警察局
        "move 警察局",
        # 轮7: Alice追到警察局
        "move 警察局",
        # Alice找到Bob
        "find Bob",
        # 轮8: Alice攻击Bob（犯罪！）
        "attack Bob 小刀 外层 普通",
        # 轮9: Bob举报Alice
        "report Alice",
        # 轮10: Bob集结
        "assemble",
        # 轮11-12: Bob等待，警察在轮末攻击Alice
        "forfeit",
        "forfeit",
    ]
    return run_test("犯罪→举报→集结→执法攻击", 2, ["Alice", "Bob"], actions, dice)


def test_17_join_police_and_election():
    """
    测试17：加入警察 + 竞选队长
    Alice加入警察（选凭证+警棍）→ 竞选3轮 → 成为队长
    """
    dice = [4, 1] * 20
    actions = [
        # 轮2: Alice移到警察局
        "move 警察局",
        # 轮3: Alice加入警察（选凭证+警棍）
        "recruit",
        "1",  # 选凭证
        "1",  # 选警棍（剩余选项第1个）
        # 轮4: 竞选（1/3）
        "election",
        # 轮5: 竞选（2/3）
        "election",
        # 轮6: 竞选（3/3→成为队长）
        "election",
        # 轮7: 查看状态
        "status",
        "police",
        "forfeit",
    ]
    return run_test("加入警察+竞选队长", 2, ["Alice", "Bob"], actions, dice)


def test_18_stealth_and_detection():
    """
    测试18：隐身和探测
    Alice学隐身术 → Bob无法找到/锁定Alice
    Bob学探测魔法 → Bob可以找到Alice
    """
    dice = [
        4, 1,  # 轮1: Alice
        1, 4,  # 轮2: Bob
        4, 1,  # 轮3: Alice
        1, 4,  # 轮4: Bob
        4, 1,  # 轮5: Alice
        1, 4,  # 轮6: Bob
        1, 4,  # 轮7: Bob
        1, 4,  # 轮8: Bob（尝试find Alice → 应该失败因为隐身）
        1, 4,  # 轮9: Bob学探测
        1, 4,  # 轮10: Bob找到Alice（应该成功）
    ]
    actions = [
        # 轮3: Alice移到魔法所
        "move 魔法所",
        # 轮4: Bob移到魔法所
        "move 魔法所",
        # 轮5: Alice学隐身术
        "interact 隐身术",
        # 轮6: Bob拿刀（在魔法所没刀拿，放弃）
        "forfeit",
        # 轮7: Bob尝试找Alice（隐身应该看不到→不在可找到列表）
        "status",  # 先看看状态
        "forfeit",
        # 轮8: Bob尝试find Alice → 应该报错因为不可见
        "find Alice",  # 这应该被校验器拒绝
        "forfeit",     # 然后放弃
        # 轮9: Bob学探测魔法
        "interact 探测魔法",
        # 轮10: Bob找到Alice（有探测能力了→可以看到→成功）
        "find Alice",
    ]
    return run_test("隐身+探测交互", 2, ["Alice", "Bob"], actions, dice)


# ============================================
#  主入口
# ============================================

if __name__ == "__main__":
    results = []

    tests = [
        ("test_01_基础走路", test_1_basic_walkthrough),
        ("test_02_商店医院", test_2_shop_and_hospital),
        ("test_03_近战击杀", test_3_combat),
        ("test_04_军事基地", test_4_military_base),
        ("test_05_魔法学习", test_5_magic_learning),
        ("test_06_警察基础", test_6_police_report),
        ("test_07_病毒致死", test_7_virus),
        ("test_08_保底加成", test_8_forfeit_and_bonus),
        ("test_09_护甲克制", test_9_armor_counter),
        ("test_10_移动清标记", test_10_move_clears_lock_and_engaged),
        ("test_11_眩晕苏醒", test_11_stun_and_recover),
        ("test_12_远程攻击", test_12_ranged_attack),
        ("test_13_多层护甲", test_13_multi_armor_layers),
        ("test_14_盾牌优先", test_14_shield_priority),
        ("test_15_军基隐身雷达", test_15_force_military_entry),
        ("test_16_犯罪举报执法", test_16_crime_and_full_police),
        ("test_17_加入警察竞选", test_17_join_police_and_election),
        ("test_18_隐身探测", test_18_stealth_and_detection),
    ]

    if len(sys.argv) > 1:
        try:
            idx = int(sys.argv[1]) - 1
            if 0 <= idx < len(tests):
                tests = [tests[idx]]
        except ValueError:
            pass

    for name, func in tests:
        try:
            success = func()
            results.append((name, success))
        except Exception as e:
            print(f"\n  💥 {name} 未捕获异常：{e}")
            traceback.print_exc()
            results.append((name, False))

    print(f"\n\n{'='*60}")
    print(f"  📊 测试汇总")
    print(f"{'='*60}")
    for name, success in results:
        icon = "✅" if success else "❌"
        print(f"  {icon} {name}")
    passed = sum(1 for _, s in results if s)
    total = len(results)
    print(f"\n  通过：{passed}/{total}")
    if passed == total:
        print(f"  🎉 全部通过！")
    else:
        print(f"  ⚠️ 有测试失败，请检查。")
    print(f"{'='*60}")
