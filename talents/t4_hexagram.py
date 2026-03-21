"""
天赋4：六爻（原初）+ Controller 接入
充能制：每5个全局轮次获得1次使用机会，最多存2次。
主动，T0启动，消耗行动回合。
对另一名玩家发起猜拳，6种结果分别不同效果。
"""

from talents.base_talent import BaseTalent
from cli import display
from controllers.human import HumanController
from engine.prompt_manager import prompt_manager
from combat.damage_resolver import resolve_damage


class Hexagram(BaseTalent):
    name = "六爻"
    description = "每5轮充能1次(上限2)。消耗行动回合猜拳，6种不同效果。"
    tier = "原初"

    CHOICES = ["石头", "剪刀", "布"]

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.charges = 0
        self.max_charges = 2
        self.round_counter = 0

    def on_round_start(self, round_num):
        """每5轮充能+1"""
        self.round_counter += 1
        if self.round_counter >= 5:
            self.round_counter = 0
            if self.charges < self.max_charges:
                self.charges += 1
                me = self.state.get_player(self.player_id)
                if me:
                    charge_msg = prompt_manager.get_prompt(
                        "talent", "t4hexagram.charge_gain",
                        default=f"🔮 {me.name} 的六爻充能+1！当前：{self.charges}/{self.max_charges}"
                    ).format(player_name=me.name, current=self.charges, max=self.max_charges)
                    display.show_info(charge_msg)

    def get_t0_option(self, player):
        if self.charges <= 0:
            return None
        return {
            "name": "六爻",
            "description": f"与另一名玩家猜拳（充能：{self.charges}/{self.max_charges}）",
        }

    def execute_t0(self, player):
        if self.charges <= 0:
            error_msg = prompt_manager.get_prompt(
                "error", "action_failed",
                default="❌ 六爻没有充能",
                reason="六爻没有充能"
            )
            return error_msg, False

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
                            msg = method(player)
                            return msg, True
                    break

        # 正常猜拳流程
        others = [p for p in self.state.alive_players()
                  if p.player_id != player.player_id]
        if not others:
            error_msg = prompt_manager.get_prompt(
                "error", "action_failed",
                default="❌ 没有可选择的目标",
                reason="没有可选择的目标"
            )
            return error_msg, False

        # ══ CONTROLLER 改动 1：选择猜拳对手 ══
        names = [p.name for p in others]
        target_name = player.controller.choose(
            "选择猜拳对手：", names,
            context={"phase": "T0", "situation": "hexagram_pick_opponent"}
        )
        target = next(p for p in others if p.name == target_name)
        # ══ CONTROLLER 改动 1 结束 ══

        self.charges -= 1

        activation_msg = prompt_manager.get_prompt(
            "talent", "t4hexagram.activation",
            default=f"🔮 六爻发动！{player.name} 向 {target.name} 发起猜拳！"
        ).format(player_name=player.name, target_name=target.name)
        display.show_info(activation_msg)

        # ══ CONTROLLER 改动 2：发动者出拳 ══
        my_choice = player.controller.choose(
            f"{player.name}，请出拳：", self.CHOICES,
            context={"phase": "T0", "situation": "hexagram_my_choice"}
        )
        # ══ CONTROLLER 改动 2 结束 ══

        # ══ CONTROLLER 改动 3：交屏幕（仅人类）══
        if isinstance(target.controller, HumanController):
            display.show_info(f"请将屏幕交给 {target.name}")
            input("  按回车继续...")
        # ══ CONTROLLER 改动 3 结束 ══

        # ══ CONTROLLER 改动 4：对手出拳（走对手的 controller）══
        opp_choice = target.controller.choose(
            f"{target.name}，请出拳：", self.CHOICES,
            context={"phase": "T0", "situation": "hexagram_opp_choice"}
        )
        # ══ CONTROLLER 改动 4 结束 ══

        choice_msg = prompt_manager.get_prompt(
            "talent", "t4hexagram.choices_display",
            default=f"🔮 {player.name} 出「{my_choice}」 vs {target.name} 出「{opp_choice}」"
        ).format(player_name=player.name, my_choice=my_choice,
                target_name=target.name, opp_choice=opp_choice)
        display.show_info(choice_msg)

        # 判定结果
        msg = self._resolve(player, target, my_choice, opp_choice)

        self.state.log_event("hexagram", player=player.player_id,
                             target=target.player_id,
                             my_choice=my_choice, opp_choice=opp_choice)

        return msg, True

    def _resolve(self, player, target, my, opp):
        """根据猜拳结果执行效果"""
        if my == opp:
            if my == "剪刀":
                return self._both_scissors(player)
            elif my == "石头":
                return self._both_rock(player)
            else:
                return self._both_paper(player)
        else:
            pair = frozenset([my, opp])
            if pair == frozenset(["剪刀", "石头"]):
                return self._scissors_rock(player)
            elif pair == frozenset(["剪刀", "布"]):
                return self._scissors_paper(player)
            else:
                return self._rock_paper(player)

    def _both_scissors(self, player):
        """双方剪刀：天雷，对任意1名玩家造成1点伤害（无视克制+无视单体保护）"""
        others = [p for p in self.state.alive_players()
                  if p.player_id != player.player_id]
        if not others:
            return prompt_manager.get_prompt(
                "talent", "t4hexagram.thunder_no_target",
                default="🔮 双剪刀→天雷！但没有可攻击的目标。"
            )

        # ══ CONTROLLER 改动 5：天雷选目标 ══
        names = [p.name for p in others]
        choice = player.controller.choose(
            "天雷！选择承受伤害的玩家：", names,
            context={"phase": "T0", "situation": "hexagram_thunder_target"}
        )
        target = next(p for p in others if p.name == choice)
        # ══ CONTROLLER 改动 5 结束 ══

        # 使用resolve_damage处理伤害
        result = resolve_damage(
            attacker=player,
            target=target,
            weapon=None,
            game_state=self.state,
            raw_damage_override=1.0,
            damage_attribute_override="无视属性克制",
            ignore_counter=True,
        )

        thunder_msg = prompt_manager.get_prompt(
            "talent", "t4hexagram.thunder_damage",
            default=f"🔮 双剪刀→⚡天雷！对 {{target_name}} 造成 1.0 伤害（无视克制+无视保护）"
        ).format(target_name=target.name)

        # 构建结果消息
        lines = [thunder_msg]
        for detail in result.get("details", []):
            lines.append(f"   {detail}")

        if result.get("killed", False):
            player.kill_count += 1
            self.state.markers.on_player_death(target.player_id)
            kill_msg = prompt_manager.get_prompt(
                "talent", "t4hexagram.thunder_kill",
                default=f"   💀 {{target_name}} 被天雷击杀！"
            ).format(target_name=target.name)
            lines.append(kill_msg)
        elif result.get("stunned", False):
            stun_msg = prompt_manager.get_prompt(
                "talent", "t4hexagram.thunder_stun",
                default=f"   💫 {{target_name}} 进入眩晕！"
            ).format(target_name=target.name)
            lines.append(stun_msg)

        return "\n".join(lines)

    def _both_rock(self, player):
        """双方石头：获得任意一种当前游戏中存在的护甲"""
        from models.equipment import make_armor, ArmorLayer
        from utils.attribute import Attribute

        available = []
        for name in ["盾牌", "陶瓷护甲", "魔法护盾", "AT力场",
                      "晶化皮肤", "额外心脏", "不老泉"]:
            armor = make_armor(name)
            if armor:
                success, _ = player.armor.check_can_equip(armor)
                if success:
                    available.append(name)

        if not available:
            return prompt_manager.get_prompt(
                "talent", "t4hexagram.armor_no_available",
                default="🔮 双石头→获得护甲！但你已经没有可装备的护甲槽了。天赋发动失效。"
            )

        # ══ CONTROLLER 改动 6：选护甲 ══
        choice = player.controller.choose(
            "选择获得的护甲：", available,
            context={"phase": "T0", "situation": "hexagram_pick_armor"}
        )
        # ══ CONTROLLER 改动 6 结束 ══

        armor = make_armor(choice)
        success, reason = player.add_armor(armor)
        if success:
            return prompt_manager.get_prompt(
                "talent", "t4hexagram.armor_gained",
                default=f"🔮 双石头→🛡️ {{player_name}} 获得了「{{armor_name}}」！"
            ).format(player_name=player.name, armor_name=choice)
        else:
            return prompt_manager.get_prompt(
                "talent", "t4hexagram.armor_failed",
                default=f"🔮 双石头→获得护甲失败：{{reason}}"
            ).format(reason=reason)

    def _both_paper(self, player):
        """双方布：进入隐身"""
        player.is_invisible = True
        self.state.markers.add(player.player_id, "INVISIBLE")
        return prompt_manager.get_prompt(
            "talent", "t4hexagram.invisible_gained",
            default=f"🔮 双布→🫥 {{player_name}} 进入隐身状态！"
        ).format(player_name=player.name)

    def _scissors_rock(self, player):
        """一方剪刀一方石头：所有需蓄力武器立刻蓄力完成"""
        charged = []
        for w in player.weapons:
            if w.requires_charge and not w.is_charged:
                w.is_charged = True
                charged.append(w.name)
        if charged:
            return prompt_manager.get_prompt(
                "talent", "t4hexagram.charge_completed",
                default=f"🔮 剪刀vs石头→⚡ 蓄力完成：{{weapons_list}}"
            ).format(weapons_list=", ".join(charged))
        return prompt_manager.get_prompt(
            "talent", "t4hexagram.charge_no_weapons",
            default="🔮 剪刀vs石头→蓄力完成！但你没有需要蓄力的武器。"
        )

    def _scissors_paper(self, player):
        """一方剪刀一方布：获得1个额外行动回合（不可再发六爻）"""
        player.hexagram_extra_turn = True
        return prompt_manager.get_prompt(
            "talent", "t4hexagram.extra_turn_gained",
            default=f"🔮 剪刀vs布→🎯 {{player_name}} 获得1个额外行动回合！\n   （额外回合内不可再次发动六爻）"
        ).format(player_name=player.name)

    def _rock_paper(self, player):
        """一方石头一方布：清除所有被发现+被锁定，进入隐身"""
        in_barrier = False
        if self.state.active_barrier:
            if self.state.active_barrier.is_liuyao_blocked(player.player_id):
                in_barrier = True
                barrier_msg = prompt_manager.get_prompt(
                    "talent", "t4hexagram.barrier_blocked",
                    default="🌀 结界内六爻的解除锁定/发现不生效！"
                )
                display.show_info(barrier_msg)

        if not in_barrier:
            lockers = self.state.markers.get_related(player.player_id, "LOCKED_BY")
            for lid in list(lockers):
                self.state.markers.remove_relation(player.player_id, "LOCKED_BY", lid)

            detectors = self.state.markers.get_related(player.player_id, "DETECTED_BY")
            for did in list(detectors):
                self.state.markers.remove_relation(player.player_id, "DETECTED_BY", did)

        player.is_invisible = True
        self.state.markers.add(player.player_id, "INVISIBLE")

        if in_barrier:
            player.is_invisible = False
            self.state.markers.remove(player.player_id, "INVISIBLE")
            return prompt_manager.get_prompt(
                "talent", "t4hexagram.invalid_in_barrier",
                default="🔮 石头vs布→结界内解除锁定/发现被屏蔽，隐身被结界破除。\n   效果完全无效。"
            )

        return prompt_manager.get_prompt(
            "talent", "t4hexagram.lock_clear_invisible",
            default=f"🔮 石头vs布→🫥 {{player_name}} 清除所有锁定与探测，进入隐身！"
        ).format(player_name=player.name)

    def describe_status(self):
        return f"充能：{self.charges}/{self.max_charges}（每5轮+1）"