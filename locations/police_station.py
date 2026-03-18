"""
地点：警察局
举报 / 集结 / 加入警察 / 竞选队长 / 追踪指引 / 研究性学习 / 唤醒警察
"""


POLICE_MENU = {
    "举报":       "举报一名违法玩家（消耗1回合）",
    "集结":       "集结警察出动（消耗1回合，需先举报）",
    "加入警察":   "加入警队，三选二获得奖励（消耗1回合）",
    "竞选队长":   "竞选警队队长（需3回合，需先加入警察）",
    "追踪指引":   "指引警察追踪逃跑的目标（消耗1回合，仅举报者）",
    "研究性学习": "队长恢复威信+1（消耗1回合，仅队长）",
    "唤醒警察":   "唤醒同地点处于debuff的警察单位（消耗1回合，见README 10.7）",
}


def get_menu():
    return dict(POLICE_MENU)


def can_interact(player, item_name, game_state=None):
    """检查能否在警察局交互（基础检查，详细检查在 police_system 中）"""
    if item_name not in POLICE_MENU:
        return False, f"警察局没有「{item_name}」"

    if item_name == "举报":
        # 详细检查交给 police_system.can_report
        return True, ""

    elif item_name == "集结":
        return True, ""

    elif item_name == "加入警察":
        return True, ""

    elif item_name == "竞选队长":
        return True, ""

    elif item_name == "追踪指引":
        return True, ""

    elif item_name == "研究性学习":
        if not player.is_captain:
            return False, "只有队长才能进行研究性学习"
        return True, ""

    elif item_name == "唤醒警察":
        # 详细检查交给 validator.validate_wake_police
        return True, ""

    return True, ""


def do_interact(player, item_name, game_state=None):
    """
    警察局的交互需要更复杂的逻辑，大部分转发给 police_system。
    这里只做简单的代理。
    """
    # 注意：大部分实际逻辑在 action_registry 和 action_turn 中处理，
    # 因为需要额外的输入（举报谁？选哪两个奖励？）
    # 这里只做不需要额外输入的简单操作

    if item_name == "研究性学习":
        if game_state and hasattr(game_state, 'police_engine'):
            return game_state.police_engine.do_study(player.player_id)
        return "❌ 警察系统未初始化"

    if item_name == "唤醒警察":
        return "❌ 请使用专用指令执行此操作（wake <警察ID>）"

    return f"❌ 请使用专用指令执行此操作（如 report / recruit / election）"
