"""神代天赋7：战术道具和药物数据定义"""

TACTICAL_ITEMS = {
    "破片手雷": {
        "cost": 0,  # 投掷本身消耗1cost，道具不额外消耗
        "description": "对投掷地点除自己外所有单位造成0.5伤害+脆弱debuff",
        "damage": 0.5,
        "effect": "fragile",  # 脆弱：破甲几率+20%
    },
    "震撼弹": {
        "cost": 0,
        "description": "对投掷地点除自己外所有单位施加震荡（含警察）",
        "effect": "shock",
    },
    "闪光弹": {
        "cost": 0,
        "description": "致盲（持续到下轮R4）：无法看到其他玩家行动/状态",
        "effect": "blind",
        "duration": "next_r4",
    },
    "烟雾弹": {
        "cost": 0,
        "description": "区域烟雾（持续到下轮R4）：星野进入获隐身，他人无法find/lock",
        "effect": "smoke",
        "duration": "next_r4",
    },
    "燃烧瓶": {
        "cost": 0,
        "description": "叠加2层灼烧（每层0.5，逻辑同神代1）",
        "effect": "burn",
        "burn_stacks": 2,
        "burn_damage": 0.5,
    },
}

MEDICINES = {
    "EPO": {
        "description": "cost立刻+1",
        "effect": "cost_plus_1",
    },
    "海豚巧克力": {
        "description": "立刻回复1层光环",
        "effect": "restore_halo",
    },
    "肾上腺素": {
        "description": "全局仅1次，回满cost和光环",
        "effect": "full_restore",
        "global_once": True,
    },
}