"""
神代天赋1：火萤IV型-完全燃烧（V1.92）

效果A 强化（常驻）：
  - 造成的所有伤害 +100%
  - 受到的所有伤害 -50%
  - HP降至0.5时不进入眩晕，下一行动回合开始时(T0)自动恢复HP至1

效果B 后期代价debuff：
  - debuff开始轮次 = 15 + (开局玩家人数-2)*3
  - 前期延迟：前15轮累计行动 < 5+(人数-2)*2 次 → 延迟
  - 后期延缓：每3轮窗口内行动<1次 → 该次debuff跳过
  - 每2轮结算1次debuff：
    1. 有外层护甲/护盾 → 摧毁一件
    2. 没有外层 → 扣1点内层护甲
    3. 没有任何护甲 → 跳过（不致死）
  - 炽愿（增强版）：每层可抵扣2次debuff（需有护甲可扣时生效）
    + 受攻击时充当额外生命值（每层0.5HP）

效果C 超新星过载（V1.92新增）：
  - 首次debuff生效时 / 击杀时授予1次超新星过载（不叠加）
  - 移动时自动触发：对目的地所有单位造成1.0无视克制伤害
  - 击杀可再次授予超新星；发动后debuff开始轮次后延3轮
"""

from talents.base_talent import BaseTalent, PromptLevel
from models.equipment import ArmorLayer
from engine.prompt_manager import prompt_manager
from engine.debug_config import debug_ai


class G1MythFire(BaseTalent):
    name = "火萤IV型-完全燃烧"
    description = "常驻：伤害+100%/受伤-50%/0.5不眩晕。后期debuff扣护甲。"
    tier = "神代"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)

        # 统计
        self.kill_count = 0
        self.action_turn_count = 0  # 累计行动次数（全局）

        # debuff
        self.debuff_started = False
        self.debuff_start_round = None
        self.initial_player_count = 0
        self.debuff_settle_toggle = False  # True = 本轮结算，False = 跳过

        # 后续延迟：每3轮窗口追踪
        self.window_action_count = 0  # 当前3轮窗口内的行动次数
        self.window_start_round = None  # 当前窗口起始轮次（debuff开始后才启用）

        # 炽愿（涟漪献诗给的抵扣道具）— 增强版
        self.ardent_wish_charges = 0  # 炽愿层数
        self.ardent_wish_debuff_uses = 0  # 当前活跃炽愿的剩余debuff抵扣次数

        self.has_supernova = False  # 超新星过载是否就绪
        self.supernova_charges = 0  # 超新星过载次数（不可叠加，最多1）
        self.supernova_used_this_move = False # 本行动回合是否已使用过超新星过载（重置条件：每轮R0）

        self.debuff_tick_count = 0  # 炽愿抵扣结算计数

    # ============================================
    #  注册
    # ============================================

    def on_register(self):
        """记录开局玩家数"""
        self.initial_player_count = len(self.state.player_order)

    # ============================================
    #  R0：debuff判定 + 0.5血自愈
    # ============================================

    def on_round_start(self, round_num):
        me = self.state.get_player(self.player_id)
        if not me or not me.is_alive():
            return

        # 0.5血自动恢复（T0也做一次，这里做R0兜底）
        # 注意：手册说"下一次行动回合开始时恢复"，放在T0更准确
        # R0这里只处理debuff

        self._process_debuff(me, round_num)

    def _process_debuff(self, me, round_num):
        self._update_window(round_num)

        if self.debuff_start_round is None:
            self.debuff_start_round = self._calc_debuff_start_round()

        effective_start = self._get_effective_start_round(round_num)
        if round_num < effective_start:
            return

        # debuff 首次启动
        if not self.debuff_started:
            self.debuff_started = True
            self._debuff_last_settled_round = round_num  # 记录上次结算轮次
            prompt_manager.show("talent", "g1mythfire.debuff_start",
                            player_name=me.name, round_num=round_num,
                            level=PromptLevel.IMPORTANT)
            # V1.92: 首次 debuff 生效时授予超新星
            self._grant_supernova(me)
            # 首次启动轮立刻结算
            self._try_debuff_settle(me, round_num)
            return

        # V1.92: 每 2 轮结算 1 次
        self._try_debuff_settle(me, round_num)

    def _try_debuff_settle(self, me, round_num):
        """每 2 轮结算一次 debuff"""
        if not hasattr(self, '_debuff_last_settled_round'):
            self._debuff_last_settled_round = round_num

        rounds_since = round_num - self._debuff_last_settled_round
        if rounds_since > 0 and rounds_since % 2 != 0:
            return  # 非结算轮，跳过

        self._debuff_last_settled_round = round_num

        # 炽愿抵扣（每2轮的频率门控已由上方 _debuff_last_settled_round 统一处理）
        if self.ardent_wish_charges > 0:
            self.debuff_tick_count += 1
            has_armor = bool(
                me.armor.get_active(ArmorLayer.OUTER) or
                me.armor.get_active(ArmorLayer.INNER)
            )
            if has_armor:
                if self.ardent_wish_debuff_uses <= 0:
                    self.ardent_wish_debuff_uses = 2
                self.ardent_wish_debuff_uses -= 1
                if self.ardent_wish_debuff_uses <= 0:
                    self.ardent_wish_charges -= 1
                prompt_manager.show("talent", "g1mythfire.ardent_wish_consume",
                                player_name=me.name,
                                remaining=self.ardent_wish_debuff_uses,
                                level=PromptLevel.NORMAL)
                return

        self._execute_debuff(me, round_num)

    def _calc_debuff_start_round(self):
        n = self.initial_player_count
        return 15 + (n - 2) * 3

    def _get_effective_start_round(self, current_round):
        """考虑特殊延迟条款后的实际开始轮次（含超新星后延）"""
        base = self.debuff_start_round  # 使用实例属性（可能被超新星 +3 修改过）

        # === 前15轮延迟保护 ===
        n = self.initial_player_count
        early_threshold = 5 + (n - 2) * 2  # 4人局=9次
        if current_round <= 15 and self.action_turn_count < early_threshold:
            return max(base, current_round + 1)

        # === 后续延迟：每3轮窗口内行动<1次则延缓 ===
        if current_round > 15 and current_round >= base:
            if self._is_window_delay_active(current_round):
                return current_round + 1  # 延缓到下一轮

        return base

    def _is_window_delay_active(self, current_round):
        """检查当前3轮窗口内行动是否不足1次"""
        if self.window_start_round is None:
            return False
        # 窗口内行动不足1次 → 延缓
        return self.window_action_count < 1

    def _execute_debuff(self, me, round_num):
        """执行一次debuff扣除"""
        # 优先扣外层
        outer_active = me.armor.get_active(ArmorLayer.OUTER)
        if outer_active:
            victim = outer_active[0]
            me.armor.remove_piece(victim)
            prompt_manager.show("talent", "g1mythfire.debuff_outer",
                               player_name=me.name, armor_name=victim.name,
                               level=PromptLevel.NORMAL)
            return

        # 没有外层 → 扣内层
        inner_active = me.armor.get_active(ArmorLayer.INNER)
        if inner_active:
            victim = inner_active[0]
            old_hp = victim.current_hp
            victim.current_hp = max(0, victim.current_hp - 1.0)
            if victim.current_hp <= 0:
                me.armor.remove_piece(victim)
                prompt_manager.show("talent", "g1mythfire.debuff_inner_destroy",
                                   player_name=me.name, armor_name=victim.name,
                                   level=PromptLevel.NORMAL)
            else:
                prompt_manager.show("talent", "g1mythfire.debuff_inner_damage",
                                   player_name=me.name, armor_name=victim.name,
                                   old_hp=old_hp, new_hp=victim.current_hp,
                                   level=PromptLevel.NORMAL)
            return

        # 没有任何护甲 → 跳过
        prompt_manager.show("talent", "g1mythfire.debuff_no_armor",
                           player_name=me.name,
                           level=PromptLevel.NORMAL)

    def _grant_supernova(self, me):
        """授予 1 次超新星过载（不叠加）"""
        if not self.has_supernova:
            self.has_supernova = True
            prompt_manager.show("talent", "g1mythfire.supernova_granted",
                            player_name=me.name,
                            level=PromptLevel.IMPORTANT)

    def receive_damage_to_temp_hp(self, remaining_damage):
        """炽愿额外生命值：每层炽愿 = 0.5 HP，吸收穿透护甲后的伤害"""
        if self.ardent_wish_charges <= 0 or remaining_damage <= 0:
            return remaining_damage

        ardent_hp = self.ardent_wish_charges * 0.5
        absorbed = min(remaining_damage, ardent_hp)
        remaining_damage -= absorbed

        # 消耗对应的炽愿层数（每层0.5 HP）
        import math
        charges_consumed = math.ceil(absorbed / 0.5)
        charges_consumed = min(charges_consumed, self.ardent_wish_charges)
        self.ardent_wish_charges -= charges_consumed

        # 如果炽愿耗尽，重置debuff抵扣计数
        if self.ardent_wish_charges <= 0:
            self.ardent_wish_debuff_uses = 0

        me = self.state.get_player(self.player_id)
        name = me.name if me else self.player_id
        prompt_manager.show("talent", "g1mythfire.ardent_wish_absorb",
                        player_name=name,
                        absorbed=absorbed,
                        remaining_charges=self.ardent_wish_charges,
                        level=PromptLevel.NORMAL)

        return remaining_damage

    # ============================================
    #  T0：0.5血自动恢复
    # ============================================

    def on_turn_start(self, player):
        """T0：如果HP=0.5，自动恢复到1"""
        if player.player_id != self.player_id:
            return None
        if not player.is_alive():
            return None

        if player.hp <= 0.5 and player.hp > 0:
            old_hp = player.hp
            player.hp = min(1.0, player.max_hp)
            # 同时确保不是眩晕状态
            if player.is_stunned:
                player.is_stunned = False
                self.state.markers.on_stun_recover(player.player_id)
            prompt_manager.show("talent", "g1mythfire.auto_heal",
                               player_name=player.name,
                               old_hp=old_hp, new_hp=player.hp,
                               level=PromptLevel.IMPORTANT)

        return None  # 不消耗回合，正常继续

    # ============================================
    #  3轮行动窗口追踪（后续延迟用）
    # ============================================

    def _update_window(self, round_num):
        """更新3轮行动窗口"""
        if not self.debuff_started:
            return
        if self.window_start_round is None:
            self.window_start_round = round_num
            self.window_action_count = 0
        # 每3轮重置窗口
        elif round_num - self.window_start_round >= 3:
            self.window_start_round = round_num
            self.window_action_count = 0

    # ============================================
    #  行动回合结束：统计行动次数
    # ============================================

    def on_turn_end(self, player, action_type):
        if player.player_id != self.player_id:
            return
        if action_type == "forfeit":
            return
        self.action_turn_count += 1
        # 3轮窗口内行动计数
        if self.debuff_started and self.window_start_round is not None:
            self.window_action_count += 1

    # ============================================
    #  伤害修正：输出+100%
    # ============================================

    def modify_outgoing_damage(self, attacker, target, weapon, base_damage):
        if attacker.player_id != self.player_id:
            return None
        # 伤害翻倍通过 damage_multiplier 实现
        # 这里返回一个标记让 resolve_damage 知道要乘2
        return {"damage_multiplier_override": 2.0}

    # ============================================
    #  伤害修正：受伤-50%
    # ============================================

    def modify_incoming_damage(self, target, attacker, weapon, raw_damage):
        """
        受到的伤害减半。
        由 damage_resolver 在计算最终伤害时调用。
        返回修正后的伤害值。
        """
        if target.player_id != self.player_id:
            return raw_damage
        reduced = raw_damage * 0.5
        attacker_name = attacker.name if attacker else "环境"
        prompt_manager.show("talent", "g1mythfire.damage_reduction",
                           attacker_name=attacker_name, target_name=target.name,
                           original_damage=raw_damage, reduced_damage=reduced,
                           level=PromptLevel.NORMAL)
        return reduced

    # ============================================
    #  眩晕免疫：HP=0.5时不眩晕
    # ============================================

    def prevent_stun(self, player):
        """
        当HP降至0.5时是否阻止眩晕。
        由 damage_resolver 在眩晕判定时调用。
        """
        if player.player_id != self.player_id:
            return False
        return True  # 火萤持有者不眩晕

    # ============================================
    #  击杀记录
    # ============================================

    def on_kill(self, killer, victim):
        if killer.player_id != self.player_id:
            return
        self.kill_count += 1
        # V1.92: 击杀不再推迟debuff，改为授予超新星
        self._grant_supernova(killer)
        prompt_manager.show("talent", "g1mythfire.kill_record",
                        killer_name=killer.name, victim_name=victim.name,
                        kill_count=self.kill_count,
                        debuff_round=self.debuff_start_round,
                        level=PromptLevel.IMPORTANT)

    # ============================================
    #  炽愿（涟漪献诗给的）
    # ============================================

    def grant_ardent_wish(self):
        """获得1层炽愿（增强版：抵扣2次debuff + 0.5额外生命值）"""
        self.ardent_wish_charges += 1
        me = self.state.get_player(self.player_id)
        name = me.name if me else self.player_id
        prompt_manager.show("talent", "g1mythfire.ardent_wish_gain",
                        player_name=name,
                        charges=self.ardent_wish_charges,
                        debuff_uses=self.ardent_wish_debuff_uses,
                        level=PromptLevel.IMPORTANT)


    def trigger_supernova(self, player, destination, game_state):
        """DHGDR-超新星过载：对目的地所有单位造成1点无视属性克制伤害"""
        from combat.damage_resolver import resolve_location_damage
        from cli import display

        self.has_supernova = False
        display.show_info(
            f"\n{'='*50}\n"
            f"  🌟💥 DHGDR-超新星过载！\n"
            f"  {player.name} 如火流星从天而降，席卷{destination}！\n"
            f"{'='*50}")

        results_dict = resolve_location_damage(
            attacker=player, location=destination,
            game_state=game_state, raw_damage=1.0,
            ignore_counter=True, exclude_self=True,
            damage_attribute_override="无视属性克制")

        # 分别处理玩家和警察结果（数据结构不同，不能 flatten）
        for r in results_dict.get("players", []):
            t = r["target"]
            res = r["result"]
            display.show_info(f"  → {t.name} 受到 1.0 伤害（无视克制）")
            if res.get("killed"):
                player.kill_count += 1
                game_state.markers.on_player_death(t.player_id)
                if game_state.police_engine:
                    game_state.police_engine.on_player_death(t.player_id)
                display.show_info(f"  💀 {t.name} 被超新星击杀！")
                # 击杀再给超新星
                self._grant_supernova(player)

        for u in results_dict.get("police", []):
            # police 列表中的元素是 unit 对象（非字典）
            if u.is_alive():
                display.show_info(f"  → 警察{u.unit_id} 受到 1.0 伤害")
            else:
                display.show_info(f"  → 警察{u.unit_id} 被超新星击杀！")

        # V1.92: 超新星发动后 debuff 开始轮次后延3轮
        if self.debuff_start_round is not None:
            self.debuff_start_round += 3
            display.show_info(f"  🔥 debuff 开始轮次后延至第 {self.debuff_start_round} 轮")

    # ============================================
    #  描述
    # ============================================

    def get_t0_option(self, player):
        """火萤没有主动T0选项"""
        return None

    def describe_status(self):
        parts = [f"击杀：{self.kill_count}", f"行动回合数：{self.action_turn_count}"]
        if self.debuff_start_round is not None:
            parts.append(f"debuff起始轮：{self.debuff_start_round}")
        if self.debuff_started:
            parts.append("debuff已激活")
            parts.append(f"结算计数：{self.debuff_tick_count}")
        if self.has_supernova:
            parts.append("🌟超新星过载就绪")
        if self.ardent_wish_charges > 0:
            parts.append(f"炽愿×{self.ardent_wish_charges}")
        return " | ".join(parts)

    def describe(self):
        return (
            f"【{self.name}】"
            f"\n  常驻：伤害+100% | 受伤-50% | 0.5血不眩晕(T0自愈)"
            f"\n  debuff = 15+(人数-2)×3 轮后每2轮R0扣护甲"
            f"\n  前15轮行动不足则延迟 | 后续每3轮行动<1次则延缓"
            f"\n  超新星过载：首次debuff/击杀时获得，移动时对目的地全体1.0伤害"
            f"\n  炽愿：每层抵扣2次debuff + 0.5额外生命值")

