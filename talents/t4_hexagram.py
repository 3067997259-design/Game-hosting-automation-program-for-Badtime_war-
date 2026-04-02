"""
天赋4：六爻（原初）— 乾卦六爻重做版
充能制：开局1次，每4个全局轮次获得1次使用机会，最多存2次。
主动，T0启动，消耗行动回合。
对另一名玩家发起猜拳，6种结果对应乾卦六爻：

  双剪刀 → 潜龙勿用：天雷（1点无视克制伤害 + 击碎1层外甲）
  双石头 → 飞龙在天：偷甲（复制目标1层外甲给自己，击碎目标该甲）
  双布   → 元亨利贞：金身（免疫所有伤害和debuff直到下轮R1，无视属性克制伤害除外）
  剪刀vs石头 → 亢龙有悔：禁武（禁用目标1件武器2轮，仅有拳击则眩晕）
  剪刀vs布   → 或跃在渊：额外行动（2个连续额外行动回合）
  石头vs布   → 群龙无首：遁走（清锁定+隐身 + 强制目标移动到随机地点）
"""

from talents.base_talent import BaseTalent
from cli import display
from controllers.human import HumanController
from engine.prompt_manager import prompt_manager
from combat.damage_resolver import resolve_damage


class Hexagram(BaseTalent):
    name = "六爻"
    description = "每4轮充能1次(上限2)。消耗行动回合猜拳，乾卦六爻，6种不同效果。"
    tier = "原初"

    CHOICES = ["石头", "剪刀", "布"]

    # 卦象名映射
    HEXAGRAM_NAMES = {
        "both_scissors": "潜龙勿用",
        "both_rock": "飞龙在天",
        "both_paper": "元亨利贞",
        "scissors_rock": "亢龙有悔",
        "scissors_paper": "或跃在渊",
        "rock_paper": "群龙无首",
    }

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.charges = 1
        self.max_charges = 2
        self.round_counter = 0

        # 元亨利贞：金身免疫状态
        self.immunity_active = False      # 是否处于金身状态
        self.immunity_expire_round = -1   # 金身在哪个轮次的R0失效

        # 亢龙有悔：武器禁用追踪
        # 格式: [(player_id, weapon_name, expire_round), ...]
        self.disabled_weapons = []

        # 涟漪增强
        self.ripple_free_choices = 0

    # ============================================
    #  轮次钩子
    # ============================================

    def on_round_start(self, round_num):
        """每4轮充能+1；清理过期的金身和武器禁用"""
        # 充能
        self.round_counter += 1
        if self.round_counter >= 4:
            self.round_counter = 0
            if self.charges < self.max_charges:
                self.charges += 1
                me = self.state.get_player(self.player_id)
                if me:
                    display.show_info(
                        f"🔮 {me.name} 的六爻充能+1！当前：{self.charges}/{self.max_charges}")

        # 元亨利贞：到期失效
        if self.immunity_active and round_num >= self.immunity_expire_round:
            self.immunity_active = False
            me = self.state.get_player(self.player_id)
            if me:
                display.show_info(f"☯️ {me.name} 的「元亨利贞」金身效果消散。")

        # 亢龙有悔：清理过期的武器禁用
        still_active = []
        for pid, wname, expire_round in self.disabled_weapons:
            if round_num >= expire_round:
                # 解禁
                p = self.state.get_player(pid)
                if p:
                    for w in getattr(p, 'weapons', []):
                        if w and w.name == wname and getattr(w, '_hexagram_disabled', False):
                            w._hexagram_disabled = False
                    display.show_info(f"☯️ {p.name} 的「{wname}」解除封印。")
            else:
                still_active.append((pid, wname, expire_round))
        self.disabled_weapons = still_active

    # ============================================
    #  金身免疫接口（供 damage_resolver / round_manager 调用）
    # ============================================

    def is_immune_to_damage(self, damage_attribute_str=None):
        """
        检查是否免疫该伤害。
        元亨利贞：免疫所有伤害，无视属性克制的伤害除外。
        """
        if not self.immunity_active:
            return False
        # 无视属性克制的伤害可以穿透金身
        if damage_attribute_str == "无视属性克制":
            return False
        return True

    def is_immune_to_debuff(self, debuff_type=None):
        """
        检查是否免疫该debuff。
        元亨利贞：免疫石化、震荡、眩晕、G2拉人判定。
        不免疫：G3幻想乡拉人。
        """
        if not self.immunity_active:
            return False
        # G3幻想乡不免疫
        if debuff_type == "mythland_pull":
            return False
        return True

    # ============================================
    #  T0选项与执行
    # ============================================

    def get_t0_option(self, player):
        if self.charges <= 0:
            return None
        return {
            "name": "六爻",
            "description": f"与另一名玩家猜拳（充能：{self.charges}/{self.max_charges}）",
        }

    def execute_t0(self, player):
        if self.charges <= 0:
            return "❌ 六爻没有充能", False

        # 涟漪增强：自由选择效果
        free = getattr(self, 'ripple_free_choices', 0)
        if free > 0:
            for pid in self.state.player_order:
                p = self.state.get_player(pid)
                if p and p.talent and hasattr(p.talent, 'apply_hexagram_free_choice'):
                    effect_key = p.talent.apply_hexagram_free_choice(player, self)
                    if effect_key and effect_key is not True:
                        self.charges -= 1
                        METHOD_MAP = {
                            "both_scissors": self._both_scissors,
                            "both_rock": self._both_rock,
                            "both_paper": self._both_paper,
                            "scissors_rock": self._scissors_rock,
                            "scissors_paper": self._scissors_paper,
                            "rock_paper": self._rock_paper,
                        }
                        method = METHOD_MAP.get(effect_key)
                        if method:
                            # 需要目标的效果
                            if effect_key in ("both_scissors", "both_rock",
                                              "scissors_rock", "rock_paper"):
                                others = [p2 for p2 in self.state.alive_players()
                                          if p2.player_id != player.player_id]
                                if others:
                                    names = [p2.name for p2 in others]
                                    target_name = player.controller.choose(
                                        "选择目标：", names,
                                        context={"phase": "T0",
                                                 "situation": "hexagram_free_target"})
                                    target = next(
                                        p2 for p2 in others if p2.name == target_name)
                                    msg = method(player, target)
                                else:
                                    msg = method(player, None)
                            else:
                                msg = method(player, None)
                            return msg, True
                    break

        # 正常猜拳流程
        others = [p for p in self.state.alive_players()
                  if p.player_id != player.player_id]
        if not others:
            return "❌ 没有可选择的目标", False

        # 选择猜拳对手
        names = [p.name for p in others]
        target_name = player.controller.choose(
            "选择猜拳对手：", names,
            context={"phase": "T0", "situation": "hexagram_pick_opponent"})
        target = next(p for p in others if p.name == target_name)

        self.charges -= 1

        display.show_info(
            f"🔮 六爻发动！{player.name} 向 {target.name} 发起猜拳！")

        # 发动者出拳
        my_choice = player.controller.choose(
            f"{player.name}，请出拳：", self.CHOICES,
            context={"phase": "T0", "situation": "hexagram_my_choice"})

        # 交屏幕（仅人类）
        if isinstance(target.controller, HumanController):
            display.show_info(f"请将屏幕交给 {target.name}")
            input("  按回车继续...")

        # 对手出拳
        opp_choice = target.controller.choose(
            f"{target.name}，请出拳：", self.CHOICES,
            context={"phase": "T0", "situation": "hexagram_opp_choice"})

        display.show_info(
            f"🔮 {player.name} 出「{my_choice}」 vs {target.name} 出「{opp_choice}」")

        # 判定结果
        msg = self._resolve(player, target, my_choice, opp_choice)

        self.state.log_event("hexagram", player=player.player_id,
                             target=target.player_id,
                             my_choice=my_choice, opp_choice=opp_choice)
        return msg, True

    # ============================================
    #  猜拳结果分发
    # ============================================

    def _resolve(self, player, target, my, opp):
        """根据猜拳结果执行效果"""
        if my == opp:
            if my == "剪刀":
                return self._both_scissors(player, target)
            elif my == "石头":
                return self._both_rock(player, target)
            else:
                return self._both_paper(player, target)
        else:
            pair = frozenset([my, opp])
            if pair == frozenset(["剪刀", "石头"]):
                return self._scissors_rock(player, target)
            elif pair == frozenset(["剪刀", "布"]):
                return self._scissors_paper(player, target)
            else:
                return self._rock_paper(player, target)

    # ============================================
    #  潜龙勿用（双剪刀）：天雷增强
    #  1点无视克制伤害 + 击碎目标1层外甲（无视属性）
    # ============================================

    def _both_scissors(self, player, target):
        """潜龙勿用：天雷增强"""
        others = [p for p in self.state.alive_players()
                  if p.player_id != player.player_id]
        if not others:
            return "🔮 潜龙勿用→⚡天雷！但没有可攻击的目标。"

        # 选择天雷目标（可以和猜拳对手不同）
        names = [p.name for p in others]
        choice = player.controller.choose(
            "⚡ 潜龙勿用——天雷！选择承受伤害的玩家：", names,
            context={"phase": "T0", "situation": "hexagram_thunder_target"})
        thunder_target = next(p for p in others if p.name == choice)

        lines = [f"☯️ 潜龙勿用——⚡天雷降临！目标：{thunder_target.name}"]

        # 1. 造成1点无视克制伤害
        result = resolve_damage(
            attacker=player,
            target=thunder_target,
            weapon=None,
            game_state=self.state,
            raw_damage_override=1.0,
            damage_attribute_override="无视属性克制",
            ignore_counter=True,
            is_talent_attack=True,
        )

        lines.append(f"   ⚡ 对 {thunder_target.name} 造成 1.0 无视克制伤害")
        for detail in result.get("details", []):
            lines.append(f"   {detail}")

        # 2. 额外击碎1层外甲（无视属性，独立于伤害）
        if thunder_target.is_alive():
            from models.equipment import ArmorLayer
            outer_active = thunder_target.armor.get_active(ArmorLayer.OUTER)
            if outer_active:
                # 击碎优先级最高的外甲
                outer_active.sort(key=lambda a: a.priority, reverse=True)
                broken_piece = outer_active[0]
                broken_piece.is_broken = True
                broken_piece.current_hp = 0
                lines.append(
                    f"   💥 天雷击碎了 {thunder_target.name} 的外甲"
                    f"「{broken_piece.name}」（{broken_piece.attribute.value}）！")
            else:
                lines.append(f"   （{thunder_target.name} 没有外层护甲可击碎）")

        # 击杀判定
        if result.get("killed", False):
            player.kill_count += 1
            self.state.markers.on_player_death(thunder_target.player_id)
            if self.state.police_engine:
                self.state.police_engine.on_player_death(thunder_target.player_id)
            lines.append(f"   💀 {thunder_target.name} 被天雷击杀！")
        elif result.get("stunned", False):
            lines.append(f"   💫 {thunder_target.name} 进入眩晕！")

        return "\n".join(lines)

    # ============================================
    #  飞龙在天（双石头）：偷甲
    #  复制目标1层外甲给自己，同时击碎目标的那层甲
    # ============================================

    def _both_rock(self, player, target):
        """飞龙在天：偷甲"""
        from models.equipment import ArmorLayer, ArmorPiece

        # 如果没有有效target（涟漪自由选择时可能为None），让玩家选
        if target is None:
            others = [p for p in self.state.alive_players()
                      if p.player_id != player.player_id]
            if not others:
                return "🔮 飞龙在天→偷甲！但没有可选择的目标。"
            names = [p.name for p in others]
            choice = player.controller.choose(
                "☯️ 飞龙在天——选择偷甲目标：", names,
                context={"phase": "T0", "situation": "hexagram_steal_target"})
            target = next(p for p in others if p.name == choice)

        # 检查目标是否有外甲
        outer_active = target.armor.get_active(ArmorLayer.OUTER)
        if not outer_active:
            return (f"☯️ 飞龙在天——夺！\n"
                    f"   {target.name} 没有外层护甲，效果不生效。")

        # 选择要偷的外甲（如果有多件，让玩家选）
        if len(outer_active) == 1:
            stolen_piece = outer_active[0]
        else:
            armor_names = [f"{a.name}（{a.attribute.value}）" for a in outer_active]
            choice = player.controller.choose(
                "选择要夺取的护甲：", armor_names,
                context={"phase": "T0", "situation": "hexagram_steal_pick"})
            idx = armor_names.index(choice)
            stolen_piece = outer_active[idx]

        lines = [f"☯️ 飞龙在天——夺！"]

        # 击碎目标的甲
        stolen_name = stolen_piece.name
        stolen_attr = stolen_piece.attribute
        stolen_piece.is_broken = True
        stolen_piece.current_hp = 0
        lines.append(
            f"   💥 击碎了 {target.name} 的「{stolen_name}」"
            f"（{stolen_attr.value}）！")

        # 给自己复制一件同属性同名的甲
        new_armor = ArmorPiece(
            stolen_name, stolen_attr, ArmorLayer.OUTER, 1.0,
            priority=stolen_piece.priority,
            can_regen=stolen_piece.can_regen,
            special_tags=list(stolen_piece.special_tags),
        )
        success, reason = player.add_armor(new_armor)
        if success:
            lines.append(
                f"   🛡️ {player.name} 获得了「{stolen_name}」"
                f"（{stolen_attr.value}）！")
        else:
            lines.append(
                f"   ⚠️ {player.name} 无法装备偷来的护甲：{reason}")

        return "\n".join(lines)

    # ============================================
    #  元亨利贞（双布）：金身
    #  免疫所有伤害和debuff直到下轮R1开始
    #  无视属性克制的伤害除外（包括灼烧）
    #  可免疫病毒致死判定
    #  免疫石化/震荡/眩晕/G2拉人，不免疫G3幻想乡
    # ============================================

    def _both_paper(self, player, _target):
        """元亨利贞：金身"""
        self.immunity_active = True
        # 在下一个轮次的R0（on_round_start）中失效
        self.immunity_expire_round = self.state.current_round + 1

        lines = [
            f"☯️ 元亨利贞——大吉大利，利于坚守正道。",
            f"   🛡️ {player.name} 进入金身状态！",
            f"   免疫所有伤害和debuff，直到下个轮次开始。",
            f"   （无视属性克制的伤害仍可穿透）",
        ]
        return "\n".join(lines)

    # ============================================
    #  亢龙有悔（剪刀vs石头）：禁武
    #  禁用目标1件武器（随机），持续2轮
    #  如果目标只有拳击，则改为眩晕
    # ============================================

    def _scissors_rock(self, player, target):
        """亢龙有悔：禁武"""
        if target is None:
            others = [p for p in self.state.alive_players()
                      if p.player_id != player.player_id]
            if not others:
                return "🔮 亢龙有悔→禁武！但没有可选择的目标。"
            names = [p.name for p in others]
            choice = player.controller.choose(
                "☯️ 亢龙有悔——选择禁武目标：", names,
                context={"phase": "T0", "situation": "hexagram_disarm_target"})
            target = next(p for p in others if p.name == choice)

        import random

        # 获取目标的非拳击武器
        real_weapons = [w for w in getattr(target, 'weapons', [])
                        if w and getattr(w, 'name', '') != "拳击"
                        and not getattr(w, '_hexagram_disabled', False)]

        if not real_weapons:
            # 只有拳击 → 眩晕
            if not target.is_stunned:
                target.is_stunned = True
                self.state.markers.add(target.player_id, "STUNNED")
            return (f"☯️ 亢龙有悔——过刚则折！\n"
                    f"   {target.name} 没有可封印的武器，改为眩晕！\n"
                    f"   💫 {target.name} 进入眩晕状态！")

        # 随机选一件武器禁用
        weapon_to_disable = random.choice(real_weapons)
        weapon_to_disable._hexagram_disabled = True
        expire_round = self.state.current_round + 2
        self.disabled_weapons.append(
            (target.player_id, weapon_to_disable.name, expire_round))

        return (f"☯️ 亢龙有悔——过刚则折！\n"
                f"   🔒 {target.name} 的「{weapon_to_disable.name}」被封印！\n"
                f"   （持续2轮，第{expire_round}轮开始时解除）")

    # ============================================
    #  或跃在渊（剪刀vs布）：额外行动
    #  2个连续额外行动回合（不可再发六爻）
    # ============================================

    def _scissors_paper(self, player, _target):
        """或跃在渊：额外行动"""
        player.hexagram_extra_turn = 2
        return (f"☯️ 或跃在渊——蓄势待发！\n"
                f"   🎯 {player.name} 获得2个连续额外行动回合！\n"
                f"   （第1个补偿发动消耗，第2个是奖励。额外回合内不可再次发动六爻）")

    # ============================================
    #  群龙无首（石头vs布）：遁走
    #  清锁定+隐身 + 强制目标移动到随机地点（D6）
    # ============================================

    def _rock_paper(self, player, target):
        """群龙无首：遁走 + 强制目标位移"""
        from utils.dice import roll_d6
        from actions.move import ALL_LOCATIONS

        lines = [f"☯️ 群龙无首——天下大乱！"]

        # 1. 清锁定/探测（结界内不生效）
        in_barrier = False
        if self.state.active_barrier:
            if self.state.active_barrier.is_liuyao_blocked(player.player_id):
                in_barrier = True
                lines.append("   🌀 结界内，解除锁定/发现不生效！")

        if not in_barrier:
            lockers = self.state.markers.get_related(
                player.player_id, "LOCKED_BY")
            for lid in list(lockers):
                self.state.markers.remove_relation(
                    player.player_id, "LOCKED_BY", lid)
            detectors = self.state.markers.get_related(
                player.player_id, "DETECTED_BY")
            for did in list(detectors):
                self.state.markers.remove_relation(
                    player.player_id, "DETECTED_BY", did)
            lines.append(f"   🔓 {player.name} 清除所有锁定与探测！")

        # 2. 隐身
        player.is_invisible = True
        self.state.markers.add(player.player_id, "INVISIBLE")

        if in_barrier:
            # 结界内隐身也被破除
            player.is_invisible = False
            self.state.markers.remove(player.player_id, "INVISIBLE")
            lines.append("   🌀 结界内隐身被破除。")
        else:
            lines.append(f"   🫥 {player.name} 进入隐身！")

        # 3. 强制目标移动到随机地点（D6决定）
        if target is not None and target.is_alive():
            # 构建可用地点列表（排除目标当前位置）
            available_locs = [loc for loc in ALL_LOCATIONS
                              if loc != target.location]
            # 加入所有玩家的家（排除目标当前位置）
            for pid in self.state.player_order:
                home_loc = f"home_{pid}"
                if home_loc != target.location:
                    available_locs.append(home_loc)

            if available_locs:
                roll = roll_d6()
                dest_idx = (roll - 1) % len(available_locs)
                destination = available_locs[dest_idx]

                old_loc = target.location or "未知"
                target.location = destination
                # 清除目标的锁定/面对面关系
                self.state.markers.on_player_move(target.player_id)

                # 显示地点名
                from actions.move import get_location_display_name
                dest_display = get_location_display_name(
                    destination, self.state)
                lines.append(
                    f"   🎲 D6 = {roll} → {target.name} 被传送到"
                    f"「{dest_display}」！（从{old_loc}）")
            else:
                lines.append(f"   （{target.name} 无处可去）")
        else:
            lines.append("   （无有效目标可传送）")

        return "\n".join(lines)

    def describe_status(self):
        parts = [f"充能：{self.charges}/{self.max_charges}（开局1次，每4轮+1）"]
        if self.immunity_active:
            parts.append("☯️ 金身（元亨利贞）生效中")
        if self.disabled_weapons:
            for pid, wname, expire in self.disabled_weapons:
                p = self.state.get_player(pid)
                pname = p.name if p else pid
                parts.append(f"🔒 {pname}的{wname}被封印（第{expire}轮解除）")
        return " | ".join(parts)

