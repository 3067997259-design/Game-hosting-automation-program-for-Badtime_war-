"""
神代天赋4：愿负世，照拂黎明

火种积累（常驻）：
  - 每次被其他玩家攻击（无论是否造成伤害）→ +1火种
  - 每次其他玩家对你使用正面效果天赋 → +1火种
  - 若上述来自限定次数天赋 → 额外+1火种
  - 上限12

绝境触发（自动）：
  - 受到致命攻击时自动触发
  - 消耗所有火种 → 进入「救世主」状态
  - 免疫该次致命伤害

「救世主」状态（每1点火种提供）：
  - 0.5点临时额外生命值
  - 消耗火种≤6时AOE+0.5，>6时AOE+1.0
  - 0.5点临时攻击力加成
  - 持续时间+1轮
  禁用远程攻击。
  免疫死亡，但再次受到致命伤害→立刻退出状态。
  每轮末持续轮次-1，归零→状态结束。

永久转化（状态结束时）：
  - 剩余临时HP → 永久额外HP（总上限不超过3）
  - 攻击力恢复原始值
  - 天赋永久失效
"""

from talents.base_talent import BaseTalent
from cli import display


class Savior(BaseTalent):
    name = "愿负世，照拂黎明"
    description = "被打积累火种(上限12)。致命时消耗全部火种进入救世主状态。"
    tier = "神代"

    MAX_DIVINITY = 12

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)

        # 火种
        self.divinity = 0

        # 救世主状态
        self.is_savior = False
        self.savior_duration = 0        # 剩余轮次
        self.temp_hp = 0.0              # 临时额外生命
        self.temp_hp_max = 0.0          # 初始临时生命（用于计算剩余）
        self.temp_attack_bonus = 0.0    # 临时攻击力加成
        self.aoe_bonus = 0.0            # 范围攻击加成

        # 是否已永久失效
        self.spent = False
        # 涟漪强化（献予「负世」之诗）
        self.ripple_enhanced = False      # 是否已被涟漪方式2强化
        self.can_active_start = False     # 是否解锁主动发动
        self.passive_bonus_divinity = 0   # 被动触发时额外获得的火种

    # ============================================
    #  火种积累
    # ============================================

    def gain_divinity(self, amount, reason=""):
        """增加火种"""
        if self.spent:
            return
        old = self.divinity
        self.divinity = min(self.divinity + amount, self.MAX_DIVINITY)
        gained = self.divinity - old
        if gained > 0:
            me = self.state.get_player(self.player_id)
            name = me.name if me else self.player_id
            display.show_info(
                f"✨ {name} 获得 {gained} 点火种"
                f"（{self.divinity}/{self.MAX_DIVINITY}）"
                f"{f'：{reason}' if reason else ''}")

    def on_being_attacked(self, attacker, weapon, is_limited_talent=False):
        """
        被攻击时调用（无论是否造成伤害）。
        由 damage_resolver 或 action_turn 在攻击结算后调用。

        【修复】救世主状态下不能再获得火种
        """
        if self.spent or self.is_savior:  # 添加 is_savior 检查
            return
        self.gain_divinity(1, f"被 {attacker.name} 攻击")
        if is_limited_talent:
            self.gain_divinity(1, "来自限定次数天赋技能")

    def on_positive_talent_used(self, source_player, is_limited=False):
        """
        其他玩家对自己使用正面效果天赋时调用。

        【修复】救世主状态下不能再获得火种
        """
        if self.spent or self.is_savior:  # 添加 is_savior 检查
            return
        self.gain_divinity(1, f"{source_player.name} 使用了正面天赋")
        if is_limited:
            self.gain_divinity(1, "来自限定次数天赋技能")

    def enhance_by_ripple(self):
        """
        被 g5_ripple 方式2「献予负世之诗」强化时调用。
        README: "立刻额外获得2点火种，并为此天赋追加新的启动方式……
                 若最终仍是在被攻击至濒死时自动触发，则再额外获得2点火种。"

        注意：g5 基础的 2 点火种由 _poem_bear 在调用本方法之前单独给予，
        本方法只处理「额外」的 2 点和后续效果。
        """
        # 效果1：立刻额外获得 2 点火种
        self.gain_divinity(2, "献予负世之诗-额外奖励")

        # 效果2：解锁主动发动方式
        self.can_active_start = True
        self.ripple_enhanced = True

        # 效果3：被动触发时额外火种（叠加式）
        self.passive_bonus_divinity = getattr(self, 'passive_bonus_divinity', 0) + 1

        me = self.state.get_player(self.player_id)
        name = me.name if me else self.player_id
        display.show_info(
            f"🌅 {name} 的「愿负世，照拂黎明」受到涟漪强化！\n"
            f"   当前火种：{self.divinity}/{self.MAX_DIVINITY}\n"
            f"   解锁主动发动方式（花1回合启动，启动后获1额外行动）\n"
            f"   被动触发时再+2火种"
        )

    # ============================================
    #  绝境触发（死亡检查）
    # ============================================

    def on_death_check(self, player, damage_source):
        """
        致命攻击时自动触发。
        优先级应高于死者苏生（免死优先于复活）。
        涟漪强化：被动触发时额外获得火种。
        """
        if player.player_id != self.player_id:
            return None
        if self.spent:
            return None
        if self.divinity <= 0:
            return None

        # 已经在救世主状态中再次致命 → 退出状态而非再次触发
        if self.is_savior:
            self._exit_savior_state()
            return None

        # 涟漪强化：被动触发（被攻击至濒死）时额外获得火种
        if self.ripple_enhanced and self.passive_bonus_divinity > 0:
            bonus = self.passive_bonus_divinity
            self.gain_divinity(bonus, "献予负世之诗-被动触发奖励")
            display.show_info(
                f"🌅 涟漪强化生效：{player.name} 被动触发前额外获得 {bonus} 点火种"
            )

        # 触发救世主
        return self._enter_savior_state(player, is_manual=False)

    def _enter_savior_state(self, player, is_manual=False):
        """进入救世主状态（支持主动/被动触发）"""
        consumed = self.divinity
        self.divinity = 0

        self.is_savior = True
        self.savior_duration = consumed
        self.temp_hp = float(consumed) * 0.5
        self.temp_hp_max = float(consumed) * 0.5
        self.temp_attack_bonus = consumed * 0.5
        self.aoe_bonus = 0.5 if consumed <= 6 else 1.0

        trigger_type = "主动发动" if is_manual else "被动触发"
        display.show_info(
            f"\n{'='*50}"
            f"\n  🌅 {player.name} {trigger_type}「愿负世，照拂黎明」！"
            f"\n  消耗 {consumed} 点火种 → 进入「救世主」状态！"
            f"\n  临时额外生命：{self.temp_hp}"
            f"\n  临时攻击力加成：+{self.temp_attack_bonus}"
            f"\n  范围攻击加成：+{self.aoe_bonus}"
            f"\n  持续轮次：{self.savior_duration}"
            f"\n  ⛔ 禁用远程攻击 | 🛡️ 免疫死亡"
            f"\n{'='*50}")

        # 恢复HP到1（免疫该次致命伤害）
        return {"prevent_death": True, "new_hp": 1.0}

    # ============================================
    #  救世主状态维护
    # ============================================

    def on_round_end(self, round_num):
        """R4：救世主倒计时"""
        if not self.is_savior:
            return

        self.savior_duration -= 1
        me = self.state.get_player(self.player_id)
        name = me.name if me else self.player_id

        display.show_info(
            f"🌅 {name} 救世主状态剩余 {self.savior_duration} 轮"
            f"（临时HP: {self.temp_hp}，攻击加成: +{self.temp_attack_bonus}）")

        if self.savior_duration <= 0:
            self._exit_savior_state()

    def _exit_savior_state(self):
        """退出救世主状态 → 永久转化"""
        me = self.state.get_player(self.player_id)
        if not me:
            self.is_savior = False
            self.spent = True
            return

        name = me.name

        # 永久转化：剩余临时HP → 永久额外HP
        remaining_temp = max(0, self.temp_hp)
        old_max = me.max_hp
        new_max = min(old_max + remaining_temp, 3.0)
        actual_gain = new_max - old_max

        me.max_hp = new_max
        # 当前HP也加上转化量（不超过新上限）
        me.hp = min(me.hp + actual_gain, new_max)

        # 攻击力恢复原始值（移除加成）
        self.temp_attack_bonus = 0.0
        self.aoe_bonus = 0.0
        self.temp_hp = 0.0

        # 标记状态结束
        self.is_savior = False
        self.spent = True

        display.show_info(
            f"\n{'='*50}"
            f"\n  🌅 {name} 的「救世主」状态结束。"
            f"\n  永久转化：+{actual_gain} 生命上限"
            f"\n  → HP: {me.hp}/{me.max_hp}"
            f"\n  攻击力恢复为原始值。"
            f"\n  天赋永久失效。"
            f"\n{'='*50}")

    # ============================================
    #  伤害修正（临时HP作为缓冲层）
    # ============================================

    def receive_damage_to_temp_hp(self, damage):
        """
        救世主状态下，伤害先扣临时HP，溢出到真实HP。
        由 damage_resolver 调用。
        返回溢出到真实HP的伤害。
        """
        if not self.is_savior or self.temp_hp <= 0:
            return damage

        if damage <= self.temp_hp:
            self.temp_hp -= damage
            self.temp_hp = round(self.temp_hp, 2)
            display.show_info(
                f"🛡️ 临时生命吸收 {damage} 伤害"
                f"（剩余临时HP: {self.temp_hp}）")
            return 0
        else:
            overflow = damage - self.temp_hp
            display.show_info(
                f"🛡️ 临时生命吸收 {self.temp_hp} 伤害，"
                f"溢出 {overflow} 到真实生命")
            self.temp_hp = 0
            return overflow

    # ============================================
    #  攻击力加成
    # ============================================

    def modify_outgoing_damage(self, attacker, target, weapon, base_damage):
        """救世主状态下近战攻击+临时攻击力，AOE+范围加成"""
        if attacker.player_id != self.player_id:
            return None
        if not self.is_savior:
            return None

        from models.equipment import WeaponRange
        mods = {}

        # 近战加成
        if weapon and weapon.weapon_range == WeaponRange.MELEE and self.temp_attack_bonus > 0:
            mods["bonus_damage"] = self.temp_attack_bonus

        # AOE加成
        if weapon and weapon.weapon_range == WeaponRange.AREA and self.aoe_bonus > 0:
            mods["bonus_damage"] = mods.get("bonus_damage", 0) + self.aoe_bonus

        return mods if mods else None

    # ============================================
    #  远程禁用
    # ============================================

    def is_remote_disabled(self):
        """救世主状态下禁用远程攻击"""
        return self.is_savior

    # ============================================
    #  T0 展示（无主动T0，但要考虑献予负世之诗）
    # ============================================

    def get_t0_option(self, player):
        """
        涟漪强化后解锁主动发动选项。
        未强化时返回 None（原始行为：纯被动触发）。
        """
        if player.player_id != self.player_id:
            return None
        if self.spent:
            return None
        if self.is_savior:
            return None
        if not self.can_active_start:
            return None
        if self.divinity <= 0:
            return None

        return {
            "name": "愿负世，照拂黎明（主动发动）",
            "description": (
                f"消耗所有火种（当前{self.divinity}点）进入救世主状态，"
                f"启动后立即获得1个额外行动回合"
            ),
        }

    def execute_t0(self, player):
        """主动发动救世主状态（涟漪强化后可用）"""
        if not self.can_active_start:
            return "❌ 无法主动发动（未受涟漪强化）", False
        if self.divinity <= 0:
            return "❌ 需要至少1点火种才能主动发动", False
        if self.is_savior:
            return "❌ 已经处于救世主状态", False

        # 进入救世主状态
        self._enter_savior_state(player, is_manual=True)

        # 标记额外行动回合（由 round_manager 在行动结束后检查）
        player.savior_extra_turn = True

        msg = (
            f"🌅 {player.name} 主动发动「愿负世，照拂黎明」！\n"
            f"   将在本次行动结束后获得1个额外行动回合"
        )
        return msg, True  # True = 消耗本次行动回合

    # ============================================
    #  描述
    # ============================================

    def describe_status(self):
        if self.spent:
            if self.is_savior:
                return "救世主状态中（即将结束）"
            return "已永久失效"

        parts = [f"火种：{self.divinity}/{self.MAX_DIVINITY}"]

        if self.ripple_enhanced:
            parts.append("🌊涟漪强化")
            if self.can_active_start:
                parts.append("可主动发动")

        if self.is_savior:
            parts.append(f"🌅救世主状态")
            parts.append(f"临时HP:{self.temp_hp}")
            parts.append(f"攻击加成:+{self.temp_attack_bonus}")
            parts.append(f"剩余{self.savior_duration}轮")

        return " | ".join(parts)

    def describe(self):
        return (f"【{self.name}】"
                f"\n  被攻击/被正面天赋作用 → +1火种（限定次数来源额外+1）"
                f"\n  上限{self.MAX_DIVINITY}。致命时消耗全部火种进入救世主状态。"
                f"\n  每点火种 = 0.5临时HP + 0.5攻击 + 1轮持续"
                f"\n  AOE加成（固定值）：消耗≤6时+0.5，>6时+1.0"
                f"\n  救世主期间禁远程+免死（再次致命则退出）"
                f"\n  状态结束时剩余临时HP转永久（上限3）")
