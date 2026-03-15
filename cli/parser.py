"""命令解析器（Phase 3 完整版 - ver1.9适配）"""


def parse(raw_input, player_id):
    raw = raw_input.strip()
    if not raw:
        return None

    parts = raw.split()
    cmd = parts[0].lower()

    # ---- 起床 ----
    if cmd in ("wake", "起床", "w"):
        # 区分唤醒警察和自己起床
        if len(parts) >= 2:
            target = parts[1].lower()
            # 如果第二个参数看起来像警察ID，则解析为唤醒警察
            if target.startswith("police") or target.startswith("警察"):
                return {"action": "wake_police", "police_id": parts[1]}
        return {"action": "wake"}

    # ---- 唤醒警察（专用命令） ----
    if cmd in ("wake_police", "唤醒警察", "唤醒"):
        if len(parts) < 2:
            return None
        return {"action": "wake_police", "police_id": parts[1]}

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
        # 只做不会冲突的缩写，具体物品名保持原样传给地点处理
        shorthand = {
            "通行证": "办理通行证",
            "办通行证": "办理通行证",
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

    # ---- 研究性学习 ----
    if cmd in ("study", "研究", "研究性学习"):
        return {"action": "study"}

    # ---- 队长操控警察 ----
    # 注意：这个命令必须在"警察状态查看"之前解析，因为格式更复杂
    if cmd in ("police", "警察命令"):
        if len(parts) < 3:
            # 如果只有"police"，或者"police status"则查看状态
            if len(parts) == 1:
                return {"action": "police_status"}
            if len(parts) == 2 and parts[1].lower() in ("status", "状态", "s"):
                return {"action": "police_status"}
            return None
            
        sub_cmd = parts[1].lower()  # move, equip, attack
        police_id = parts[2]  # police1, police2, police3, 或警察ID
        
        if sub_cmd == "move":
            if len(parts) < 4:
                return None
            location = parts[3]
            return {"action": "police_command", "subcommand": "move", 
                    "police_id": police_id, "location": location}
        elif sub_cmd == "equip":
            if len(parts) < 4:
                return None
            equipment = parts[3]  # 武器或护甲名称
            # 支持武器/护甲分别指定
            if len(parts) >= 5 and parts[3].lower() in ("weapon", "武器", "armor", "护甲"):
                equipment_type = parts[3].lower()
                equipment = parts[4]
                if equipment_type in ("weapon", "武器"):
                    return {"action": "police_command", "subcommand": "equip",
                            "police_id": police_id, "equipment": equipment, "equipment_type": "weapon"}
                else:
                    return {"action": "police_command", "subcommand": "equip",
                            "police_id": police_id, "equipment": equipment, "equipment_type": "armor"}
            # 传统模式：只指定一个装备
            return {"action": "police_command", "subcommand": "equip",
                    "police_id": police_id, "equipment": equipment}
        elif sub_cmd == "attack":
            if len(parts) < 4:
                return None
            target = parts[3]
            return {"action": "police_command", "subcommand": "attack",
                    "police_id": police_id, "target": target}
        else:
            return None

    # ---- 警察状态查看 ----
    # 注意：这个必须在队长操控警察命令之后，因为格式更简单
    if cmd in ("police_status", "警察状态", "警状态"):
        return {"action": "police_status"}

    # ---- 放弃 ----
    if cmd in ("forfeit", "放弃", "f", "pass", "skip"):
        return {"action": "forfeit"}

    # ---- 查看 ----
    if cmd in ("status", "状态", "s"):
        return {"action": "status"}
    if cmd in ("allstatus", "全场", "all", "a"):
        return {"action": "allstatus"}
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
