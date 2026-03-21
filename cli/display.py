"""显示与输出模块（Phase 3 完整版）"""
"""
注意：本模块已重构为使用统一的提示管理系统。
所有提示文本现在存储在 data/prompts.json 中，便于玩家自定义修改。
"""

import getpass
from engine.prompt_manager import prompt_manager, show_info as pm_show_info, show_error as pm_show_error, show_warning as pm_show_warning


def clear_screen():
    """清屏：使用提示管理器的clear_screen文本，如果为空则使用50个换行符"""
    clear_text = prompt_manager.get_prompt("system", "clear_screen", default="")
    if clear_text:
        print(clear_text)
    else:
        print("\n" * 50)


def show_banner():
    """显示游戏横幅"""
    banner_text = prompt_manager.get_prompt("ui", "banner", default="=" * 60 + "\n        ⚔️  起 闯 战 争 （ 大 嘘 ⚔️\n        Badtime War CLI DM ver1.6\n" + "=" * 60 + "\n")
    print(banner_text)


def show_round_header(round_num):
    """显示全局轮次标题"""
    header_text = prompt_manager.get_prompt("ui", "round_header", default=f"{'='*60}\n  📅 全局轮次 {round_num}\n{'='*60}\n")
    # 安全格式化：如果文本包含 {round_num}，则进行格式化
    if isinstance(header_text, str) and "{round_num}" in header_text:
        header_text = header_text.format(round_num=round_num)
    print(header_text)


def show_phase(phase_name):
    """显示阶段标题"""
    phase_text = prompt_manager.get_prompt("ui", "phase_header", default=f"\n--- {phase_name} ---")
    # 安全格式化：如果文本包含 {phase_name}，则进行格式化
    if isinstance(phase_text, str) and "{phase_name}" in phase_text:
        phase_text = phase_text.format(phase_name=phase_name)
    print(phase_text)


def show_d4_results(results, bonuses, winners):
    """显示D4投掷结果"""
    # 标题
    title = prompt_manager.get_prompt("game", "d4_results", default="\n🎲 D4 投掷结果：")
    print(title)

    # 每个玩家的结果
    for name, roll in results.items():
        bonus = bonuses.get(name, 0)
        final = min(roll + bonus, 4)
        bonus_str = f" +{bonus}保底" if bonus > 0 else ""
        cap_str = " (封顶4)" if roll + bonus > 4 else ""

        result_text = prompt_manager.get_prompt("game", "d4_player_result", default="  {name}: 骰出 {roll}{bonus_str}{cap_str} → 最终 {final}")
        if isinstance(result_text, str):
            try:
                print(result_text.format(
                    name=name, roll=roll, bonus_str=bonus_str,
                    cap_str=cap_str, final=final
                ))
            except (KeyError, ValueError):
                print(result_text)
        else:
            print(result_text)

    # 胜者
    winner_names = ", ".join(winners)
    winner_text = prompt_manager.get_prompt("game", "d4_winners", default="  🏆 本轮胜者：{winner_names}")
    if isinstance(winner_text, str):
        try:
            print(winner_text.format(winner_names=winner_names))
        except (KeyError, ValueError):
            print(winner_text)
    else:
        print(winner_text)


def show_action_turn_header(player_name):
    """显示行动回合标题"""
    header_text = prompt_manager.get_prompt("ui", "action_turn_header", default=f"\n{'─'*50}\n  ▶ 轮到 {player_name} 行动\n{'─'*50}")
    # 安全格式化：如果文本包含 {player_name}，则进行格式化
    if isinstance(header_text, str) and "{player_name}" in header_text:
        header_text = header_text.format(player_name=player_name)
    print(header_text)


def show_player_status(player, game_state):
    """显示单个玩家状态"""
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
    """显示可执行行动列表"""
    header = prompt_manager.get_prompt("ui", "available_actions_header", default="\n  可执行的行动：")
    print(header)

    for i, act in enumerate(actions, 1):
        action_item = prompt_manager.get_prompt("ui", "action_item", default="    {index}. {usage:30s} - {description}")
        if isinstance(action_item, str):
            try:
                print(action_item.format(index=i, usage=act['usage'], description=act['description']))
            except (KeyError, ValueError):
                print(action_item)
        else:
            print(action_item)

    print(f"    status | allstatus | police | help")


def show_result(msg):
    """显示结果信息"""
    result_text = prompt_manager.get_prompt("game", "result", default="\n  📋 {msg}")
    if isinstance(result_text, str):
        try:
            print(result_text.format(msg=msg))
        except (KeyError, ValueError):
            print(result_text)
    else:
        print(result_text)


def show_error(msg):
    """显示错误信息"""
    error_text = prompt_manager.get_prompt("game", "error", default="\n  ❌ {msg}")
    if isinstance(error_text, str):
        try:
            print(error_text.format(msg=msg))
        except (KeyError, ValueError):
            print(error_text)
    else:
        print(error_text)


def show_info(msg):
    """显示一般信息"""
    info_text = prompt_manager.get_prompt("game", "info", default="\n  ℹ️  {msg}")
    # 确保info_text是字符串
    if not isinstance(info_text, str):
        info_text = str(info_text)
    try:
        print(info_text.format(msg=msg))
    except (KeyError, ValueError):
        print(info_text)


def show_victory(player_name):
    """显示胜利信息"""
    victory_text = prompt_manager.get_prompt("game", "victory", default="\n🎉" * 20 + f"\n\n  👑 {player_name} 获得了最终胜利！\n\n  游戏结束！\n" + "🎉" * 20)
    if isinstance(victory_text, str):
        try:
            print(victory_text.format(player_name=player_name))
        except (KeyError, ValueError):
            print(victory_text)
    else:
        print(victory_text)


def show_death(player_name, cause):
    """显示死亡信息"""
    death_text = prompt_manager.get_prompt("game", "death", default="\n  💀 {player_name} 死亡！原因：{cause}")
    if isinstance(death_text, str):
        try:
            print(death_text.format(player_name=player_name, cause=cause))
        except (KeyError, ValueError):
            print(death_text)
    else:
        print(death_text)


def show_police_status(game_state):
    """显示警察系统状态"""
    header = prompt_manager.get_prompt("game", "police_status_header", default=f"\n{'='*50}\n  🚔 警察系统状态\n{'='*50}")
    print(header)

    print(game_state.police.describe())

    # 犯罪记录
    criminals = [(pid, crimes) for pid, crimes
                 in game_state.police.crime_records.items() if crimes]

    if criminals:
        crime_header = prompt_manager.get_prompt("game", "police_crime_records", default="\n  犯罪记录：")
        print(crime_header)

        for pid, crimes in criminals:
            p = game_state.get_player(pid)
            name = p.name if p else pid
            crime_item = prompt_manager.get_prompt("game", "police_crime_record_item", default="    {name}: {crimes}")
            if isinstance(crime_item, str):
                try:
                    print(crime_item.format(name=name, crimes=", ".join(crimes)))
                except (KeyError, ValueError):
                    print(crime_item)
            else:
                print(crime_item)
    else:
        no_crimes = prompt_manager.get_prompt("game", "police_no_crimes", default="\n  犯罪记录：无")
        print(no_crimes)

    print(f"{'='*50}")


def show_virus_status(game_state):
    """显示病毒状态"""
    print(f"  {game_state.virus.describe()}")


def show_police_enforcement(messages):
    """显示警察执法结果"""
    if messages:
        enforcement_header = prompt_manager.get_prompt("game", "police_enforcement", default="\n  🚔 警察执法：")
        print(enforcement_header)

        for msg in messages:
            enforcement_item = prompt_manager.get_prompt("game", "police_enforcement_item", default="  {msg}")
            if isinstance(enforcement_item, str):
                try:
                    print(enforcement_item.format(msg=msg))
                except (KeyError, ValueError):
                    print(enforcement_item)
            else:
                print(enforcement_item)


def show_virus_deaths(dead_players):
    """显示病毒致死"""
    for p in dead_players:
        virus_death_text = prompt_manager.get_prompt("game", "virus_death", default="  💀🦠 {player_name} 因病毒死亡！")
        if isinstance(virus_death_text, str):
            try:
                print(virus_death_text.format(player_name=p.name))
            except (KeyError, ValueError):
                print(virus_death_text)
        else:
            print(virus_death_text)


def show_help():
    """显示指令帮助"""
    help_text = prompt_manager.get_prompt("help", "main", default="""\n╔═══════════════════════════════════════════════════════════════╗
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
╚═══════════════════════════════════════════════════════════════╝\n""")
    print(help_text)


def prompt_input(player_name):
    """提示输入指令"""
    return input(f"\n  [{player_name}] 请输入指令 > ").strip()


def prompt_secret(prompt_text):
    """提示秘密输入"""
    clear_screen()

    secret_prompt = prompt_manager.get_prompt("system", "secret_prompt", default="\n  🔒 请其他玩家移开视线！")
    print(secret_prompt)

    try:
        value = getpass.getpass(f"  {prompt_text} > ")
    except EOFError:
        value = ""

    recorded_text = prompt_manager.get_prompt("system", "secret_recorded", default="  ✓ 已记录")
    print(recorded_text)

    return value.strip()


def prompt_choice(prompt_text, options):
    """提示选择"""
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
    """显示全场玩家状态"""
    header = prompt_manager.get_prompt("ui", "all_players_status_header", default=f"\n{'='*50}\n  📊 全场玩家状态\n{'='*50}")
    print(header)

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


# 兼容性函数：直接使用提示管理器的高级功能
def show_critical(msg):
    """显示关键信息"""
    pm_show_error("error", "critical", msg=msg)


def show_warning(msg):
    """显示警告信息"""
    pm_show_warning("error", "warning", msg=msg)


# 直接访问提示管理器的便捷函数
def get_prompt(category, key, **kwargs):
    """获取提示文本"""
    return prompt_manager.get_prompt(category, key, **kwargs)


def show_prompt(category, key, **kwargs):
    """显示提示"""
    text = prompt_manager.get_prompt(category, key, **kwargs)
    print(text)