"""命令解析器（Phase 3 完整版）"""


def parse(raw_input, player_id):
    raw = raw_input.strip()
    if not raw:
        return None

    parts = raw.split()
    cmd = parts[0].lower()

    # ---- 起床 ----
    if cmd in ("wake", "起床", "w"):
        return {"action": "wake"}

    # ---- 移动 ----
    if cmd in ("move", "移动", "m", "go"):
        if len(parts) < 2:
            return None
        dest = parts[1]
        if dest in ("home", "家", "回家"):
            dest = f"home_{player_id}"
        return {"action": "move", "destination": dest}

    # ---- 交互 ----
    if cmd in ("interact", "交互", "i", "get", "拿", "学", "做", "买"):
        if len(parts) < 2:
            return None
        item = " ".join(parts[1:])
        shorthand = {
            "凭证": "拿凭证", "钱": "拿凭证", "会员": "拿凭证",
            "刀": "拿刀", "小刀": "拿刀",
            "盾": "拿盾", "盾牌": "拿盾",
            "通行证": "办理通行证", "办通行证": "办理通行证",
            "导弹": "导弹控制权",
        }
        item = shorthand.get(item, item)
        return {"action": "interact", "item": item}

    # ---- 锁定 ----
    if cmd in ("lock", "锁定", "l"):
        if len(parts) < 2:
            return None
        return {"action": "lock", "target": parts[1]}

    # ---- 找到 ----
    if cmd in ("find", "找到", "找"):
        if len(parts) < 2:
            return None
        return {"action": "find", "target": parts[1]}

    # ---- 攻击 ----
    if cmd in ("attack", "攻击", "atk", "打"):
        if len(parts) < 2:
            return None
        target = parts[1]
        weapon = parts[2] if len(parts) >= 3 else None
        layer_str = parts[3] if len(parts) >= 4 else None
        attr_str = parts[4] if len(parts) >= 5 else None
        return {"action": "attack", "target": target, "weapon": weapon,
                "layer": layer_str, "attr": attr_str}
        target = parts[1]
        weapon = parts[2]
        layer_str = parts[3] if len(parts) >= 4 else None
        attr_str = parts[4] if len(parts) >= 5 else None
        return {"action": "attack", "target": target, "weapon": weapon,
                "layer": layer_str, "attr": attr_str}

    # ---- 特殊操作 ----
    if cmd in ("special", "特殊", "sp", "操作"):
        if len(parts) < 2:
            return None
        op = " ".join(parts[1:])
        shorthand = {"磨": "磨刀", "吟唱": "吟唱魔法护盾", "展开": "展开AT力场",
                      "病毒": "释放病毒", "放毒": "释放病毒"}
        op = shorthand.get(op, op)
        return {"action": "special", "operation": op}

    # ---- 举报 ----
    if cmd in ("report", "举报"):
        if len(parts) < 2:
            return None
        return {"action": "report", "target": parts[1]}

    # ---- 集结 ----
    if cmd in ("assemble", "集结"):
        return {"action": "assemble"}

    # ---- 追踪指引 ----
    if cmd in ("track", "追踪", "指引"):
        return {"action": "track_guide"}

    # ---- 加入警察 ----
    if cmd in ("recruit", "加入警察", "入警"):
        return {"action": "recruit"}

    # ---- 竞选队长 ----
    if cmd in ("election", "竞选", "竞选队长"):
        return {"action": "election"}

    # ---- 队长指定目标 ----
    if cmd in ("designate", "指定目标", "指定"):
        if len(parts) < 2:
            return None
        return {"action": "designate", "target": parts[1]}

    # ---- 队长拆分 ----
    if cmd in ("split", "拆分"):
        team_id = parts[1] if len(parts) >= 2 else "alpha"
        return {"action": "split", "team": team_id}

    # ---- 研究性学习 ----
    if cmd in ("study", "研究", "研究性学习"):
        return {"action": "study"}

    # ---- 放弃 ----
    if cmd in ("forfeit", "放弃", "f", "pass", "skip"):
        return {"action": "forfeit"}

    # ---- 查看 ----
    if cmd in ("status", "状态", "s"):
        return {"action": "status"}
    if cmd in ("allstatus", "全场", "all", "a"):
        return {"action": "allstatus"}
    if cmd in ("police", "警察", "警察状态"):
        return {"action": "police_status"}
    if cmd in ("help", "帮助", "h", "?"):
        return {"action": "help"}

    return None


def resolve_player_target(target_str, game_state):
    if game_state.get_player(target_str):
        return target_str
    for p in game_state.players.values():
        if p.name == target_str:
            return p.player_id
    for p in game_state.players.values():
        if p.name.lower() == target_str.lower():
            return p.player_id
    return None
