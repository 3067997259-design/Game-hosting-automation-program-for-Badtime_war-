class Hoshino(HaloMixin, FusionMixin, TacticalMixin, FacingMixin, TerrorMixin, BaseTalent):
    name = "大叔我啊，剪短发了"
    description = "光环+装备融合+战术指令宏+色彩反转"
    tier = "神代"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        # 形态
        self.form = None  # "水着-shielder" / "临战-Archer" / "临战-shielder"
        # Cost 体力条
        self.cost = 5
        self.max_cost = 5
        # 光环（3层）
        self.halos = [{"active": True, "cooldown_remaining": 0, "recovering": False} for _ in range(3)]
        # 融合装备
        self.iron_horus = None       # 铁之荷鲁斯 ArmorPiece 引用
        self.eye_of_horus = None     # 荷鲁斯之眼 特殊武器引用
        self.iron_horus_hp = 3       # 铁之荷鲁斯当前护甲值
        self.iron_horus_max_hp = 3
        self.fusion_shield_done = False
        self.fusion_weapon_done = False
        self.tactical_unlocked = False
        # 弹药
        self.ammo = []  # list of {"attribute": Attribute, "count": int}
        self.max_ammo = 8
        # 盾牌模式 + 正面/背面
        self.shield_mode = None      # "架盾" / "持盾" / None
        self.shield_snapshot_hp = 0  # 架盾时铁之荷鲁斯的护甲值快照（用于正面伤害过滤阈值）
        self.front_players = set()
        self.back_players = set()
        # 战术道具 + 药物
        self.tactical_items = []     # 最多2
        self.medicines = []
        self.adrenaline_used = False
        # 射击连击（临战-Archer 用）
        self.shoot_streak = 0
        # 色彩反转
        self.color = 0
        self.color_is_null = False   # 献予守夜人之诗后永久null
        self.is_terror = False
        self.self_doubt_pending = False
        self.terror_extra_hp = 0.0
        self.broken_armors_history = set()  # 穿戴过的已破碎护甲名（去重）
        # 临战-shielder 冲刺免cost标记
        self.dash_free_shield_cost = False
        # 额外生命值（光环提供的）
        self.halo_extra_hp = 0.0  # 由光环系统管理

    def on_register(self):
        """选择初始形态"""
        me = self.state.get_player(self.player_id)
        forms = [
            "水着-shielder（起床获盾牌，架盾cost降为1）",
            "临战-Archer（起床获额外行动，射击连续2次后额外1次）",
            "临战-shielder（起床恢复1层光环，冲刺尾部自动冲击+架盾）",
        ]
        choice = me.controller.choose(
            "选择初始形态：", forms,
            context={"phase": "register", "situation": "hoshino_form"}
        )
        if "水着" in choice:
            self.form = "水着-shielder"
        elif "Archer" in choice:
            self.form = "临战-Archer"
        else:
            self.form = "临战-shielder"
        self.initial_player_count = len(self.state.player_order)

    def on_round_start(self, round_num):
        """R0: cost回满 + 光环tick + 融合检查 + 战术解锁检查"""
        pass  # Phase 2/3 实现

    def on_round_end(self, round_num):
        """R4: 架盾cost扣除"""
        pass  # Phase 7 实现

    def on_turn_start(self, player):
        """T0: 自我怀疑/Terror处理 + 色彩≥6选择"""
        pass  # Phase 8 实现

    def get_t0_option(self, player):
        """T0选项：战术指令宏入口"""
        pass  # Phase 7 实现

    def execute_t0(self, player):
        """执行战术指令宏"""
        pass  # Phase 7 实现

    def receive_damage_to_temp_hp(self, damage):
        remaining = damage
        # Terror 模式下用 terror_extra_hp
        if self.is_terror:
            absorbed = min(remaining, self.terror_extra_hp)
            self.terror_extra_hp -= absorbed
            remaining -= absorbed
            return remaining
        # 正常模式：光环额外HP
        while remaining > 0 and any(h['active'] for h in self.halos):
            # 每层光环吸收0.5
            absorb = min(remaining, 0.5)
            remaining -= absorb
            self._halo_consume_one()
        return remaining

    def on_death_check(self, player, damage_source):
        """Terror下无视复活"""
        pass  # Phase 8 实现

    def describe_status(self):
        """状态描述"""
        parts = []
        if self.is_terror:
            parts.append("⚠️Terror")
            parts.append(f"额外HP:{self.terror_extra_hp}")
        else:
            parts.append(f"Cost:{self.cost}/{self.max_cost}")
            active_halos = sum(1 for h in self.halos if h['active'])
            parts.append(f"光环:{active_halos}/3")
            if self.shield_mode:
                parts.append(f"盾:{self.shield_mode}")
            if self.tactical_unlocked:
                total_ammo = sum(a.get('count', 0) for a in self.ammo) if self.ammo else 0
                parts.append(f"弹药:{total_ammo}/{self.max_ammo}")
            parts.append(f"色彩:{self.color}")
            if self.form:
                parts.append(f"形态:{self.form}")
        return " | ".join(parts)

    def describe(self):
        return (
            f"【{self.name}】"
            f"\n  形态：{self.form or '未选择'}"
            f"\n  Cost：{self.cost}/{self.max_cost}"
            f"\n  光环：{sum(1 for h in self.halos if h['active'])}/3"
            f"\n  色彩：{'null' if self.color_is_null else self.color}"
            f"\n  战术：{'已解锁' if self.tactical_unlocked else '未解锁'}"
            f"\n  Terror：{'是' if self.is_terror else '否'}"
        )