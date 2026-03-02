"""显示与输出模块（Phase 3 完整版）"""

import getpass


def clear_screen():
    print("\n" * 50)


def show_banner():
    print("=" * 60)
    print("        ⚔️  起 闯 战 争 （ 大 嘘 ⚔️")
    print("        Badtime War CLI DM ver1.6")
    print("=" * 60)
    print()


def show_round_header(round_num):
    print()
    print(f"{'='*60}")
    print(f"  📅 全局轮次 {round_num}")
    print(f"{'='*60}")


def show_phase(phase_name):
    print(f"\n--- {phase_name} ---")


def show_d4_results(results, bonuses, winners):
    print("\n🎲 D4 投掷结果：")
    for name, roll in results.items():
        bonus = bonuses.get(name, 0)
        final = min(roll + bonus, 4)
        bonus_str = f" +{bonus}保底" if bonus > 0 else ""
        cap_str = " (封顶4)" if roll + bonus > 4 else ""
        print(f"  {name}: 骰出 {roll}{bonus_str}{cap_str} → 最终 {final}")
    winner_names = ", ".join(winners)
    print(f"  🏆 本轮胜者：{winner_names}")


def show_action_turn_header(player_name):
    print(f"\n{'─'*50}")
    print(f"  ▶ 轮到 {player_name} 行动")
    print(f"{'─'*50}")


def show_player_status(player, game_state):
    print()
    print(player.describe_status())
    marker_desc = game_state.markers.describe_markers(player.player_id)
    if marker_desc != "无异常":
        print(f"  状态标记：{marker_desc}")
    if player.location:
        others = [p for p in game_state.alive_players()
                  if p.location == player.location
                  and p.player_id != player.player_id]
        if others:
            other_parts = []
            for p in others:
                vis = game_state.markers.is_visible_to(
                    p.player_id, player.player_id, player.has_detection)
                if vis:
                    other_parts.append(p.name)
            if other_parts:
                print(f"  同地点可见玩家：{', '.join(other_parts)}")
            else:
                print(f"  同地点没有可见的其他玩家")
        else:
            print(f"  同地点没有其他玩家")


def show_available_actions(actions):
    print("\n  可执行的行动：")
    for i, act in enumerate(actions, 1):
        print(f"    {i}. {act['usage']:30s} - {act['description']}")
    print(f"    status | allstatus | police | help")


def show_result(msg):
    print(f"\n  📋 {msg}")


def show_error(msg):
    print(f"\n  ❌ {msg}")


def show_info(msg):
    print(f"\n  ℹ️  {msg}")


def show_victory(player_name):
    print()
    print("🎉" * 20)
    print(f"\n  👑 {player_name} 获得了最终胜利！")
    print(f"\n  游戏结束！")
    print("🎉" * 20)


def show_death(player_name, cause):
    print(f"\n  💀 {player_name} 死亡！原因：{cause}")


def show_police_status(game_state):
    """显示警察系统状态"""
    print(f"\n{'='*50}")
    print("  🚔 警察系统状态")
    print(f"{'='*50}")
    print(game_state.police.describe())
    # 犯罪记录
    criminals = [(pid, crimes) for pid, crimes
                 in game_state.police.crime_records.items() if crimes]
    if criminals:
        print(f"\n  犯罪记录：")
        for pid, crimes in criminals:
            p = game_state.get_player(pid)
            name = p.name if p else pid
            print(f"    {name}: {', '.join(crimes)}")
    else:
        print(f"\n  犯罪记录：无")
    print(f"{'='*50}")


def show_virus_status(game_state):
    """显示病毒状态"""
    print(f"  {game_state.virus.describe()}")


def show_police_enforcement(messages):
    """显示警察执法结果"""
    if messages:
        print(f"\n  🚔 警察执法：")
        for msg in messages:
            print(f"  {msg}")


def show_virus_deaths(dead_players):
    """显示病毒致死"""
    for p in dead_players:
        print(f"  💀🦠 {p.name} 因病毒死亡！")


def show_help():
    print("""
╔═══════════════════════════════════════════════════════════════╗
║                        指令帮助                               ║
╠═══════════════════════════════════════════════════════════════╣
║  wake                              - 起床                    ║
║  move <地点>                       - 移动                    ║
║  interact <项目名>                 - 与当前地点交互           ║
║  lock <玩家名或ID>                 - 锁定（远程前置）         ║
║  find <玩家名或ID>                 - 找到（近战前置）         ║
║  attack <目标> <武器名> [层 属性]  - 攻击                    ║
║  special <操作名>                  - 特殊操作                ║
║  forfeit                           - 放弃行动                ║
╠═══════════════════════════════════════════════════════════════╣
║  警察系统：                                                   ║
║  report <玩家名>     - 举报违法者（需在警察局）               ║
║  assemble            - 集结警察（举报者使用）                 ║
║  track               - 追踪指引（举报者使用）                 ║
║  recruit             - 加入警察（需在警察局）                 ║
║  election            - 竞选队长（需在警察局+已加入警察）       ║
║  designate <玩家名>  - 队长指定执法目标                       ║
║  split <警队ID>      - 队长拆分警队                           ║
║  study               - 队长研究性学习（威信+1）               ║
╠═══════════════════════════════════════════════════════════════╣
║  查看：                                                       ║
║  status / allstatus / police / help                           ║
╠═══════════════════════════════════════════════════════════════╣
║  攻击示例：attack Bob 小刀 外层 普通                          ║
║  地点：home 商店 魔法所 医院 军事基地 警察局                  ║
║  克制：普通→魔法 魔法→科技 科技→普通 同属性有效              ║
╚═══════════════════════════════════════════════════════════════╝
""")


def prompt_input(player_name):
    return input(f"\n  [{player_name}] 请输入指令 > ").strip()


def prompt_secret(prompt_text):
    clear_screen()
    print(f"\n  🔒 请其他玩家移开视线！")
    try:
        value = getpass.getpass(f"  {prompt_text} > ")
    except EOFError:
        value = ""
    print(f"  ✓ 已记录")
    return value.strip()


def prompt_choice(prompt_text, options):
    print(f"\n  {prompt_text}")
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    while True:
        raw = input("  请选择（输入编号或名称）> ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        if raw in options:
            return raw
        for opt in options:
            if raw.lower() in opt.lower():
                return opt
        print(f"  请输入有效的选项。")


def show_all_players_status(game_state):
    print(f"\n{'='*50}")
    print("  📊 全场玩家状态")
    print(f"{'='*50}")
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        if p:
            alive_str = "存活" if p.is_alive() else "☠️ 已死亡"
            print(f"\n  [{alive_str}]")
            print(p.describe_status())
            marker_desc = game_state.markers.describe_markers(pid)
            if marker_desc != "无异常":
                print(f"  状态标记：{marker_desc}")
    # 病毒状态
    if game_state.virus.is_active:
        print()
        show_virus_status(game_state)
    print(f"{'='*50}")
