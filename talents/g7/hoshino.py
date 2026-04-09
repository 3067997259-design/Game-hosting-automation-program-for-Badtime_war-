"""
Hoshino —— 神代天赋7「大叔我啊，剪短发了」主类

光环系统 + 装备融合 + 战术指令宏 + 正面/背面 + 色彩反转/Terror。
"""

from talents.base_talent import BaseTalent
from talents.g7.halo_mixin import HaloMixin
from talents.g7.fusion_mixin import FusionMixin
from talents.g7.tactical_mixin import TacticalMixin
from talents.g7.facing_mixin import FacingMixin
from talents.g7.terror_mixin import TerrorMixin
from cli import display
from engine.prompt_manager import prompt_manager
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
        self.ammo = []  # list of {"attribute": str}，每个条目代表一发子弹
        self.max_ammo = 8
        # 盾牌模式 + 正面/背面
        self.shield_mode = None      # "架盾" / "持盾" / None
        self.shield_snapshot_hp = 0  # 架盾时铁之荷鲁斯的护甲值快照（用于正面伤害过滤阈值）
        self.shield_guard_mode = "block_leaving"  # "block_leaving" / "block_entering"
        self.front_players = set()
        self.back_players = set()
        # 战术道具 + 药物
        self.tactical_items = []     # 最多2
        self.medicines = []
        self.adrenaline_used = False
        self._adrenaline_d4_rounds = 0  # 肾上腺素 D4+3 剩余轮数
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

    def on_register(self):
        """选择初始形态"""
        me = self.state.get_player(self.player_id)
        forms = [
            "水着-shielder（起床获盾牌，架盾免cost）",
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

    def on_wakeup(self, player, game_state):
        """起床时根据形态给予加成"""
        from engine.prompt_manager import prompt_manager

        msgs = []

        # 通用：起床自带凭证（军人优待）
        if player.vouchers < 1:
            player.vouchers = max(player.vouchers, 1)
            msgs.append(prompt_manager.get_prompt("talent", "g7hoshino.wakeup_voucher",
                default="📋 军人优待：自动获得1张购买凭证"))

        # 形态加成
        if self.form == "水着-shielder":
            from models.equipment import make_armor
            armor = make_armor("盾牌")
            if armor:
                success, reason = player.add_armor(armor)
                if success:
                    msgs.append(prompt_manager.get_prompt("talent", "g7hoshino.wakeup_shield",
                        default="🛡️ {player_name} 起床获得盾牌！（水着-shielder）").format(
                        player_name=player.name))
        elif self.form == "临战-Archer":
            player.hoshino_wakeup_extra_turn = True
            msgs.append(prompt_manager.get_prompt("talent", "g7hoshino.wakeup_extra_turn",
                default="🏹 {player_name} 起床获得额外行动回合！（临战-Archer）").format(
                player_name=player.name))
        elif self.form == "临战-shielder":
            restored = self._halo_restore_one()
            if restored:
                msgs.append(prompt_manager.get_prompt("talent", "g7hoshino.wakeup_halo",
                    default="✨ {player_name} 起床恢复1层光环！（临战-shielder）").format(
                    player_name=player.name))

        return "\n".join(msgs) if msgs else None

    def on_round_start(self, round_num):
        """R0: cost回满 + 光环tick + 融合检查 + 战术解锁检查"""
        # 闪光弹/烟雾弹过期清理（即使 Hoshino 已死也必须执行，否则致盲永久化）
        if hasattr(self.state, '_hoshino_smoke_zones'):
            expired = [loc for loc, expire_round in self.state._hoshino_smoke_zones.items()
                      if round_num > expire_round]
            for loc in expired:
                del self.state._hoshino_smoke_zones[loc]

        # 清理致盲效果
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and hasattr(p, '_hoshino_blind_expire_round'):
                if round_num > p._hoshino_blind_expire_round:
                    p._hoshino_blinded = False
                    if hasattr(p, '_hoshino_blind_snapshot'):
                        del p._hoshino_blind_snapshot  # 释放快照内存
                    if hasattr(p, '_hoshino_blind_markers_simple'):
                        del p._hoshino_blind_markers_simple
                    if hasattr(p, '_hoshino_blind_markers_relations'):
                        del p._hoshino_blind_markers_relations
                    del p._hoshino_blind_expire_round

        me = self.state.get_player(self.player_id)
        if not me or not me.is_alive():
            return

        # 肾上腺素 D4+3 递减（在 cost 重置之前）
        if getattr(self, '_adrenaline_d4_rounds', 0) > 0:
            self._adrenaline_d4_rounds -= 1

        # Terror 状态下不回满 cost
        if not self.is_terror:
            self.cost = self.max_cost
            # 肾上腺素下回合效果
            if getattr(self, '_adrenaline_next_round', False):
                self._adrenaline_next_round = False
                self._adrenaline_d4_rounds = 1  # D4+3 本轮生效
                self.cost = self.max_cost + 5  # cost 额外 +5（本回合为10）
                # 光环全恢复
                for h in self.halos:
                    h['active'] = True
                    h['recovering'] = False
                    h['cooldown_remaining'] = 0
                from cli import display
                display.show_info(prompt_manager.get_prompt("talent", "g7hoshino.adrenaline_effect",
                    default="💉 肾上腺素生效！Cost={cost}，光环全恢复！").format(cost=self.cost))

        # 光环恢复 tick
        self._halo_tick()

        # 装备融合检查
        self._check_fusion(me)

    def on_round_end(self, round_num):
        """R4: 架盾cost扣除（位于R4所有检查之后）"""
        # 注意：这个方法由 round_manager.py 的 R4-3 天赋轮次结束钩子调用
        # 但 README 说架盾cost扣除要在"R4所有检查之后"
        # 所以实际的扣除逻辑在 _r4_shield_cost_check 中，
        # 由 round_manager.py 在 R4-3 之后单独调用
        pass

    def on_turn_start(self, player):
        """T0: 自我怀疑/Terror处理 + 色彩≥6选择 + 架盾控制检查"""
        if not player.is_alive():
            return

        # 架盾/持盾状态下，眩晕/震荡立刻解除盾牌状态
        if self.shield_mode and self._should_end_shield(player):
            msg = prompt_manager.get_prompt("talent", "g7hoshino.shield_end_control",
                                          player_name=player.name, shield_mode=self.shield_mode)
            display.show_info(msg)
            self._end_shield_mode(player)

        # 自我怀疑 → 跳过回合 → 反转为 Terror
        if self.self_doubt_pending:
            self.self_doubt_pending = False
            self._enter_terror(player)
            return {"consume_turn": True, "message": prompt_manager.get_prompt(
                "talent", "g7hoshino.self_doubt_terror", player_name=player.name)}

        # 色彩≥6 时提供选择是否进入自我怀疑
        if not self.color_is_null and not self.is_terror and self.color >= 6:
            choice = player.controller.choose(
                f"是因为你在，她才会死在沙漠里的。她的死都是你的错……你还在试图相信那个可笑的自己吗？",
                ["是因为我……一切都是因为我……", "不，不是这样的……"],
                context={"phase": "T0", "situation": "hoshino_self_doubt_choice"}
            )
            if "是因为我……一切都是因为我……" in choice:
                self.self_doubt_pending = True
                msg = prompt_manager.get_prompt("talent", "g7hoshino.self_doubt_enter",
                                             player_name=player.name)
                display.show_info(msg)

    def get_t0_option(self, player):
        """T0选项：战术指令宏入口（已移至 special Hoshino）"""
        return None

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
        """Terror下无视复活，无视任何条件死亡"""
        if self.is_terror:
            # Terror 形态下 HP 归零 → 无视任何条件死亡
            return None  # 不阻止死亡
        return None

    def on_d4_bonus(self, player):
        """肾上腺素注射后，下一轮 D4+3"""
        if player.player_id == self.player_id:
            if getattr(self, '_adrenaline_d4_rounds', 0) > 0:
                return 3
        return 0

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
                total_ammo = len(self.ammo)
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