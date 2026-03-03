"""
神代天赋1：火萤Ⅳ型-完全燃烧

效果A 强化（常驻）：
  - 造成的所有伤害 +100%
  - 受到的所有伤害 -50%
  - HP降至0.5时不进入眩晕，下一行动回合开始时(T0)自动恢复HP至1

效果B 后期代价debuff：
  - debuff开始轮次 = 10 + (开局玩家人数-2)*3 + 累计击杀数*5
  - 特殊延迟：前10轮累计行动回合<6次 → 延迟到累计行动达5次那轮
  - 从debuff轮开始，每轮R0：
    1. 有外层护甲/护盾 → 摧毁一件
    2. 没有外层 → 扣1点内层护甲
    3. 没有任何护甲 → 跳过（不致死）
"""

from talents.base_talent import BaseTalent, PromptLevel
from models.equipment import ArmorLayer
from engine.prompt_manager import prompt_manager
from engine.debug_config import debug_ai


class G1MythFire(BaseTalent):
    name = "火萤Ⅳ型-完全燃烧"
    description = "常驻：伤害+100%/受伤-50%/0.5不眩晕。后期debuff扣护甲。"
    tier = "神代"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)

        # 统计
        self.kill_count = 0
        self.action_turn_count = 0

        # debuff
        self.debuff_started = False
        self.debuff_start_round = None  # 延迟计算，首次R0时算
        self.initial_player_count = 0

        # 炽愿（涟漪献诗给的抵扣道具）
        self.has_ardent_wish = False

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
        """debuff判定与执行"""
        # 首次计算debuff开始轮次
        if self.debuff_start_round is None:
            self.debuff_start_round = self._calc_debuff_start_round()

        # 特殊延迟条款检查
        effective_start = self._get_effective_start_round(round_num)
        if round_num < effective_start:
            return

        # debuff开始
        if not self.debuff_started:
            self.debuff_started = True
            prompt_manager.show("talent", "g1mythfire.debuff_start",
                               player_name=me.name, round_num=round_num,
                               level=PromptLevel.IMPORTANT)

        # 炽愿抵扣
        if self.has_ardent_wish:
            self.has_ardent_wish = False
            prompt_manager.show("talent", "g1mythfire.ardent_wish_consume",
                               player_name=me.name,
                               level=PromptLevel.NORMAL)
            return

        # 执行debuff：扣护甲
        self._execute_debuff(me, round_num)

    def _calc_debuff_start_round(self):
        """计算debuff开始轮次（基础公式）"""
        n = self.initial_player_count
        k = self.kill_count
        return 10 + (n - 2) * 3 + k * 5

    def _get_effective_start_round(self, current_round):
        """考虑特殊延迟条款后的实际开始轮次"""
        base = self._calc_debuff_start_round()

        # 特殊延迟：前10轮累计行动<6次 → 延迟到累计行动达5次那轮
        if current_round <= 10 and self.action_turn_count < 6:
            # 还没达标，继续延迟
            return max(base, current_round + 1)  # 至少比当前轮大

        # 如果前10轮内行动不足6次，debuff被延迟
        # 延迟到累计行动达5次那轮（通过on_turn_end追踪）
        return base

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
    #  行动回合结束：统计行动次数
    # ============================================

    def on_turn_end(self, player, action_type):
        if player.player_id != self.player_id:
            return
        # 放弃行动不计
        if action_type == "forfeit":
            return
        self.action_turn_count += 1

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
        prompt_manager.show("talent", "g1mythfire.damage_reduction",
                           attacker_name=attacker.name, target_name=target.name,
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
        """击杀时更新计数，重算debuff起始轮"""
        if killer.player_id != self.player_id:
            return
        self.kill_count += 1
        # 击杀会推迟debuff
        self.debuff_start_round = self._calc_debuff_start_round()
        prompt_manager.show("talent", "g1mythfire.kill_record",
                           killer_name=killer.name, victim_name=victim.name,
                           kill_count=self.kill_count, debuff_round=self.debuff_start_round,
                           level=PromptLevel.IMPORTANT)

    # ============================================
    #  炽愿（涟漪献诗给的）
    # ============================================

    def grant_ardent_wish(self):
        """获得炽愿道具（抵扣1次debuff）"""
        self.has_ardent_wish = True
        me = self.state.get_player(self.player_id)
        name = me.name if me else self.player_id
        prompt_manager.show("talent", "g1mythfire.ardent_wish_gain",
                           player_name=name,
                           level=PromptLevel.IMPORTANT)

    # ============================================
    #  描述
    # ============================================

    def get_t0_option(self, player):
        """火萤没有主动T0选项"""
        return None

    def describe_status(self):
        parts = [
            f"击杀：{self.kill_count}",
            f"行动回合数：{self.action_turn_count}",
        ]
        if self.debuff_start_round is not None:
            parts.append(f"debuff起始轮：{self.debuff_start_round}")
        if self.debuff_started:
            parts.append("⚠️ debuff已激活")
        if self.has_ardent_wish:
            parts.append("持有「炽愿」")
        return " | ".join(parts)

    def describe(self):
        return (
            f"【{self.name}】"
            f"\n  常驻：伤害+100% | 受伤-50% | 0.5血不眩晕(T0自愈)"
            f"\n  debuff = 10+(人数-2)×3+击杀×5 轮后每轮R0扣护甲"
            f"\n  前10轮行动<6次则延迟")