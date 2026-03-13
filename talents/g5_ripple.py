"""
神代天赋5：往世的涟漪 + Controller 接入

追忆系统：R0每轮+1或+2层，满24层可发动。
方式一（锚定）：锚定事件，5轮监控破坏性行动，成功则事件自动实现。
方式二（献诗）：对目标玩家根据天赋类型施加特殊增强。
仅能使用一次。
"""

import copy
import random
from talents.base_talent import BaseTalent
from combat.damage_resolver import resolve_damage
from cli import display
from controllers.human import HumanController
from engine.prompt_manager import prompt_manager

try:
    from engine.action_turn import ActionTurnManager
except Exception:
    ActionTurnManager = None


class Ripple(BaseTalent):
    name = "往世的涟漪"
    description = "追忆满24层后发动：锚定命运或献诗增强。仅一次。"
    tier = "神代"

    POEM_MAP = {
        "一刀缭断": "游侠",
        "你给路打油": "游侠",
        "天星": "群星",
        "朝阳好市民": "律法",
        "不良少年": "诡计",
        "六爻": "阴阳",
        "死者苏生": "彼岸",
        "火萤Ⅳ型-完全燃烧": "纷争",
        "请一直，注视着我": "追光",
        "愿负世，照拂黎明": "负世",
        "往世的涟漪": "命运",
    }

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.reminiscence = 0
        self.max_reminiscence = 24
        self.acted_last_round = False
        self.only_extra_turn = False
        self.used = False
        self.anchor_active = False
        self.anchor_type = None
        self.anchor_target_id = None
        self.anchor_detail = ""
        self.anchor_path = []
        self.anchor_fate = 0
        self.anchor_variance = 0
        self.anchor_rounds_left = 0
        self.anchor_destructive_count = 0
        self.anchor_caster_backup = None
        self.anchor_target_snapshot = None
        self.anchor_revealed_step = None
        self.was_paused_by_barrier = False  # 新增：记录是否被结界暂停

    # ================================================================
    #  辅助：判断是否有人类玩家（用于 DM 判定自动化）
    # ================================================================

    def _has_human_players(self):
        """如果全场没有人类玩家，DM 判定自动处理"""
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and isinstance(p.controller, HumanController):
                return True
        return False

    def _get_caster(self):
        """获取发动者 Player 对象"""
        return self.state.get_player(self.player_id)

    # ================================================================
    #  追忆积累（R0）
    # ================================================================

    def on_round_start(self, round_num):
        if self.used:
            return
        if self.anchor_active:
            return
        if round_num <= 1:
            return

        if not self.acted_last_round or self.only_extra_turn:
            gain = 2
        else:
            gain = 1

        old = self.reminiscence
        self.reminiscence = min(self.max_reminiscence, self.reminiscence + gain)

        me = self._get_caster()
        name = me.name if me else self.player_id
        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.reminiscence_gain",
                default="🌊 {name} 获得 {gain} 层追忆（{old}→{reminiscence}/{max_reminiscence}）"
            ).format(
                name=name, gain=gain, old=old, 
                reminiscence=self.reminiscence, max_reminiscence=self.max_reminiscence
            )
        )

        if self.reminiscence >= self.max_reminiscence:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.reminiscence_full",
                    default="🌊✨ {name} 的追忆已满！可以发动「往世的涟漪」！"
                ).format(name=name)
            )

        self.acted_last_round = False
        self.only_extra_turn = False

    def on_turn_end(self, player, action_type):
        if player.player_id != self.player_id:
            return
        if action_type and action_type not in ("shock_recover", "petrify_skip"):
            if hasattr(player, 'hexagram_extra_turn') and player.hexagram_extra_turn:
                self.only_extra_turn = True
            else:
                self.acted_last_round = True
                self.only_extra_turn = False

    # ================================================================
    #  T0选项
    # ================================================================

    def get_t0_option(self, player):
        if player.player_id != self.player_id:
            return None
        if self.used:
            return None
        if self.anchor_active:
            return None
        if self.reminiscence < self.max_reminiscence:
            return None

        return {
            "name": "往世的涟漪",
            "description": (
                f"追忆已满（{self.reminiscence}/{self.max_reminiscence}\n"
                f"  方式一：锚定命运（不消耗行动回合）\n"
                f"  方式二：献诗增强（消耗行动回合）"),
        }

    def execute_t0(self, player):
        # ══ CONTROLLER 改动 1：选方式 ══
        choice = player.controller.choose(
            "选择涟漪的发动方式：",
            ["方式一：锚定命运（不消耗行动回合）",
             "方式二：献诗增强（消耗行动回合）",
             "取消"],
            context={"phase": "T0", "situation": "ripple_choose_method"}
        )
        # ══ CONTROLLER 改动 1 结束 ══

        if "锚定" in choice:
            msg = self._execute_anchor(player)
            if msg is None:
                return prompt_manager.get_prompt(
                    "talent", "g5ripple.anchor_not_established",
                    default="锚定未成立，追忆已返还。"
                ), False
            return msg, False

        elif "献诗" in choice:
            msg = self._execute_poem(player)
            return msg, True

        else:
            return prompt_manager.get_prompt(
                "talent", "g5ripple.cancel_activation",
                default="取消发动。"
            ), False

    # ================================================================
    #  方式一：锚定命运
    # ================================================================

    def _execute_anchor(self, player):
        lines = [
            f"\n{'='*60}",
            prompt_manager.get_prompt(
                "talent", "g5ripple.anchor_activation",
                default="  🌊 {player_name} 发动「往世的涟漪」——锚定命运！"
            ).format(player_name=player.name),
            f"{'='*60}",
        ]
        display.show_info("\n".join(lines))

        # ══ CONTROLLER 改动 2：选锚定事件类型 ══
        event_type = player.controller.choose(
            "选择锚定事件类型：",
            ["击杀目标玩家",
             "破坏目标护甲层",
             "获取指定物品或权能",
             "到达指定地点",
             "取消"],
            context={"phase": "T0", "situation": "ripple_anchor_type"}
        )
        # ══ CONTROLLER 改动 2 结束 ══

        if "取消" in event_type:
            return None
        if "击杀" in event_type:
            return self._anchor_kill(player)
        elif "护甲" in event_type:
            return self._anchor_break_armor(player)
        elif "获取" in event_type:
            return self._anchor_acquire(player)
        elif "到达" in event_type:
            return self._anchor_arrive(player)
        return None

    # ---------- 击杀类锚定 ----------

    def _anchor_kill(self, player):
        others = [p for p in self.state.alive_players()
                  if p.player_id != player.player_id]
        if not others:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.no_targets",
                    default="没有可锚定的目标。"
                )
            )
            return None

        # ══ CONTROLLER 改动 3：选击杀目标 ══
        names = [p.name for p in others]
        target_name = player.controller.choose(
            "选择击杀目标：", names + ["取消"],
            context={"phase": "T0", "situation": "ripple_anchor_kill_target"}
        )
        # ══ CONTROLLER 改动 3 结束 ══

        if target_name == "取消":
            return None
        target = next(p for p in others if p.name == target_name)

        self.anchor_type = "kill"
        self.anchor_target_id = target.player_id
        self.anchor_detail = f"击杀 {target.name}"

        return self._anchor_dm_validation(player, target)

    # ---------- 破坏护甲类锚定 ----------

    def _anchor_break_armor(self, player):
        others = [p for p in self.state.alive_players()
                  if p.player_id != player.player_id]
        if not others:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.no_targets",
                    default="没有可锚定的目标。"
                )
            )
            return None

        # ══ CONTROLLER 改动 4：选目标玩家 ══
        names = [p.name for p in others]
        target_name = player.controller.choose(
            "选择目标玩家：", names + ["取消"],
            context={"phase": "T0", "situation": "ripple_anchor_armor_target"}
        )
        # ══ CONTROLLER 改动 4 结束 ══

        if target_name == "取消":
            return None
        target = next(p for p in others if p.name == target_name)

        armor_desc = target.armor.describe() if hasattr(target, 'armor') else "无护甲"
        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.target_armor_display",
                default="{target_name} 当前护甲：{armor_desc}"
            ).format(target_name=target.name, armor_desc=armor_desc)
        )

        # ══ CONTROLLER 改动 5：选护甲层名称 ══
        # 尝试从目标护甲获取可选列表
        armor_options = self._get_target_armor_names(target)
        if armor_options:
            armor_name = player.controller.choose(
                "选择要破坏的护甲层：", armor_options + ["取消"],
                context={"phase": "T0", "situation": "ripple_anchor_armor_pick"}
            )
            if armor_name == "取消":
                return None
        else:
            # 无法列举 → 人类手动输入，AI 自动选第一层
            if isinstance(player.controller, HumanController):
                armor_name = input(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.input_armor_name",
                        default="输入要破坏的护甲层名称："
                    )
                ).strip()
                if not armor_name:
                    return None
            else:
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.ai_no_armor_name",
                        default="AI 无法确定护甲层名称，锚定取消。"
                    )
                )
                return None
        # ══ CONTROLLER 改动 5 结束 ══

        self.anchor_type = "break_armor"
        self.anchor_target_id = target.player_id
        self.anchor_detail = f"破坏 {target.name} 的 {armor_name}"

        return self._anchor_dm_validation(player, target)

    def _get_target_armor_names(self, target):
        """尝试获取目标的护甲层名称列表"""
        names = []
        if not hasattr(target, 'armor'):
            return names
        armor = target.armor
        if hasattr(armor, 'layers'):
            for layer in armor.layers:
                if layer and hasattr(layer, 'name'):
                    names.append(layer.name)
        elif hasattr(armor, 'describe_layers'):
            desc = armor.describe_layers()
            if desc:
                names = [d.strip() for d in desc.split(',') if d.strip()]
        return names

    # ---------- 获取类锚定 ----------

    def _anchor_acquire(self, player):
        # ══ CONTROLLER 改动 6：输入物品名 ══
        # 给出常见物品列表供选择
        common_items = [
            "凭证", "小刀", "磨刀石", "盾牌", "陶瓷护甲",
            "魔法护盾", "AT力场", "隐身衣", "热成像仪",
            "魔法弹幕", "远程魔法弹幕", "电磁步枪", "导弹",
            "高斯步枪", "防毒面具", "通行证",
        ]
        item_name = player.controller.choose(
            prompt_manager.get_prompt(
                "talent", "g5ripple.select_item",
                default="选择要获取的物品或权能："
            ),
            common_items + ["取消"],
            context={"phase": "T0", "situation": "ripple_anchor_acquire_item"}
        )
        if item_name == "取消":
            return None
        # ══ CONTROLLER 改动 6 结束 ══

        self.anchor_type = "acquire"
        self.anchor_target_id = None
        self.anchor_detail = f"获取 {item_name}"

        # ══ DM 判定 1：DM 确认可行性（全 AI 时自动通过）══
        if self._has_human_players():
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.dm_confirm_acquisition",
                    default="\n📋 DM请确认：{player_name} 是否可以在5回合内获取「{item_name}」？"
                ).format(player_name=player.name, item_name=item_name)
            )
            confirm = display.prompt_choice(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.dm_confirm",
                    default="DM确认："
                ),
                [
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.feasible",
                        default="可行"
                    ),
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.infeasible",
                        default="不可行"
                    )
                ]
            )
            if confirm == prompt_manager.get_prompt("talent", "g5ripple.infeasible", default="不可行"):
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.dm_reject",
                        default="DM判定不可行，追忆返还。"
                    )
                )
                return None
        else:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.auto_dm_feasible",
                    default="  📋 [自动DM] 判定获取「{item_name}」可行。"
                ).format(item_name=item_name)
            )
        # ══ DM 判定 1 结束 ══

        return self._anchor_start_simple(player)

    # ---------- 到达类锚定 ----------

    def _anchor_arrive(self, player):
        from actions.move import get_all_valid_locations
        locations = get_all_valid_locations(self.state)

        # ══ CONTROLLER 改动 7：选地点 ══
        loc = player.controller.choose(
            prompt_manager.get_prompt(
                "talent", "g5ripple.select_location",
                default="选择要到达的地点："
            ),
            locations + ["取消"],
            context={"phase": "T0", "situation": "ripple_anchor_arrive_loc"}
        )
        # ══ CONTROLLER 改动 7 结束 ══

        if loc == "取消":
            return None

        self.anchor_type = "arrive"
        self.anchor_target_id = None
        self.anchor_detail = f"到达 {loc}"

        return self._anchor_start_simple(player)

    # ---------- DM验证 + 命定/变数计算（击杀/护甲类） ----------

    def _anchor_dm_validation(self, player, target):
        from engine.anchor_resolver import verify_anchor

        if self.anchor_type == "kill":
            result = verify_anchor(self.state, player, "kill", target=target)
        elif self.anchor_type == "break_armor":
            result = verify_anchor(
                self.state, player, "break_armor",
                target=target, armor_description=self.anchor_detail)
        elif self.anchor_type == "acquire":
            result = verify_anchor(
                self.state, player, "acquire", item_name=self.anchor_detail)
        elif self.anchor_type == "arrive":
            result = verify_anchor(
                self.state, player, "arrive", target_location=self.anchor_detail)
        else:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.unknown_anchor_type",
                    default="❌ 未知锚定类型：{anchor_type}"
                ).format(anchor_type=self.anchor_type)
            )
            return None

        display.show_info(
            f"\n{'─'*50}"
            f"\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.anchor_event",
                default="📋 锚定事件：{anchor_detail}"
            ).format(anchor_detail=self.anchor_detail)
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.dummy_system_result",
                default="📋 假人系统自动验证结果："
            )
        )

        if not result.feasible:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.infeasible_reason",
                    default="   ❌ 不可行：{reason}"
                ).format(reason=result.reason)
                + f"\n{'─'*50}"
            )
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.reminiscence_returned",
                    default="追忆已返还。"
                )
            )
            return None

        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.feasible_check",
                default="   ✅ 可行"
            )
        )
        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.fate_variance",
                default="   命数 = {fate}，变数 = {variance}"
            ).format(fate=result.fate, variance=result.variance)
        )
        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.path_steps",
                default="   路径："
            )
        )
        for step in result.path_description:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.path_step",
                    default="     {step}"
                ).format(step=step)
            )
        display.show_info(f"{'─'*50}")

        # ══ DM 判定 2：DM 最终确认（全 AI 时自动采用系统结果）══
        if self._has_human_players():
            confirm = display.prompt_choice(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.dm_confirm_choices",
                    default="DM确认："
                ),
                [
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.confirm_anchor",
                        default="确认，按此结果开始锚定"
                    ),
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.modify_fate",
                        default="手动修改命数"
                    ),
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.reject_anchor",
                        default="不可行，返还追忆"
                    ),
                ])

            if prompt_manager.get_prompt("talent", "g5ripple.reject_anchor", default="不可行，返还追忆") in confirm:
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.dm_reject",
                        default="DM判定不可行，追忆返还。"
                    )
                )
                return None

            if prompt_manager.get_prompt("talent", "g5ripple.modify_fate", default="手动修改命数") in confirm:
                while True:
                    fate_str = input(
                        prompt_manager.get_prompt(
                            "talent", "g5ripple.dm_input_fate",
                            default="DM请输入修正后的「命数」（1-5）："
                        )
                    ).strip()
                    try:
                        fate = int(fate_str)
                        if 1 <= fate <= 5:
                            break
                    except ValueError:
                        pass
                    display.show_info(
                        prompt_manager.get_prompt(
                            "talent", "g5ripple.input_1_to_5",
                            default="请输入1-5的整数。"
                        )
                    )
                self.anchor_fate = fate
                self.anchor_variance = 5 - fate
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.fate_modified",
                        default="已修正：命数 = {fate}，变数 = {variance}"
                    ).format(fate=fate, variance=self.anchor_variance)
                )

                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.dm_input_path",
                        default="DM请输入锚定路径（共{fate}步，每步一行，输入空行结束）："
                    ).format(fate=fate)
                )
                path = []
                for i in range(fate):
                    step = input(
                        prompt_manager.get_prompt(
                            "talent", "g5ripple.path_step_input",
                            default="  第{step_num}步："
                        ).format(step_num=i+1)
                    ).strip()
                    if not step:
                        break
                    path.append(step)
                self.anchor_path = path
            else:
                self.anchor_fate = result.fate
                self.anchor_variance = result.variance
                self.anchor_path = result.path_description
        else:
            # 全 AI 模式：自动采用系统结果
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.auto_dm_adopt",
                    default="  📋 [自动DM] 采用系统验证结果。"
                )
            )
            self.anchor_fate = result.fate
            self.anchor_variance = result.variance
            self.anchor_path = result.path_description
        # ══ DM 判定 2 结束 ══

        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.anchor_confirmed",
                default="\n⚓ 锚定确认！"
            )
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.fate_display",
                default="   命数 = {fate}"
            ).format(fate=self.anchor_fate)
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.variance_display",
                default="   变数 = {variance}"
            ).format(variance=self.anchor_variance)
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.path_length",
                default="   路径共 {length} 步"
            ).format(length=len(self.anchor_path))
        )

        self.anchor_caster_backup = self._create_player_backup(player)

        self.anchor_target_snapshot = {
            'hp': target.hp,
            'is_stunned': target.is_stunned,
            'is_invisible': getattr(target, 'is_invisible', False),
            'is_shocked': getattr(target, 'is_shocked', False),
            'armor_summary': self._get_armor_summary(target),
            'weapon_names': ([w.name for w in target.weapons]
                            if hasattr(target, 'weapons') else []),
            'money': getattr(target, 'money', 0),
        }

        if self.anchor_path:
            d6 = random.randint(1, 6)
            step_idx = min(d6 - 1, len(self.anchor_path) - 1)
            self.anchor_revealed_step = self.anchor_path[step_idx]
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.d6_reveal",
                    default="🎲 D6 = {d6} → 公布路径步骤：「{revealed_step}」"
                ).format(d6=d6, revealed_step=self.anchor_revealed_step)
            )
        else:
            self.anchor_revealed_step = prompt_manager.get_prompt(
                "talent", "g5ripple.no_path",
                default="（无路径）"
            )

        self._anchor_start_combat(player, target)

        return prompt_manager.get_prompt(
            "talent", "g5ripple.anchor_fate_started",
            default="锚定命运已启动：{anchor_detail}"
        ).format(anchor_detail=self.anchor_detail)

    # ---------- 获取/到达类简化启动 ----------

    def _anchor_start_simple(self, player):
        self.anchor_fate = 0
        self.anchor_variance = 5
        self.anchor_path = []
        self.anchor_caster_backup = self._create_player_backup(player)
        self.anchor_target_snapshot = None

        self.used = True
        self.reminiscence = 0
        self.anchor_active = True
        self.anchor_rounds_left = 5
        self.anchor_destructive_count = 0

        lines = [
            f"\n{'='*60}",
            prompt_manager.get_prompt(
                "talent", "g5ripple.anchor_established_simple",
                default="  🌊 锚定成立！事件：{anchor_detail}"
            ).format(anchor_detail=self.anchor_detail),
            prompt_manager.get_prompt(
                "talent", "g5ripple.anchor_countdown",
                default="  ⏳ 5轮后若 {player_name} 存活，事件自动实现。"
            ).format(player_name=player.name),
            prompt_manager.get_prompt(
                "talent", "g5ripple.state_backed_up",
                default="  📸 状态已备份。"
            ),
            f"{'='*60}",
        ]
        display.show_info("\n".join(lines))
        return "\n".join(lines)

    # ---------- 击杀/护甲类启动 ----------

    def _anchor_start_combat(self, player, target):
        self.used = True
        self.reminiscence = 0
        self.anchor_active = True
        self.anchor_rounds_left = 5
        self.anchor_destructive_count = 0

        display.show_info(self._format_anchor_start_msg(player, target))

    def _format_anchor_start_msg(self, player, target):
        lines = [
            f"\n{'='*60}",
            prompt_manager.get_prompt(
                "talent", "g5ripple.anchor_established",
                default="  🌊 锚定成立！"
            ),
            prompt_manager.get_prompt(
                "talent", "g5ripple.anchor_event_detail",
                default="  📋 事件：{anchor_detail}"
            ).format(anchor_detail=self.anchor_detail),
            prompt_manager.get_prompt(
                "talent", "g5ripple.anchor_target",
                default="  🎯 目标：{target_name}"
            ).format(target_name=target.name),
            prompt_manager.get_prompt(
                "talent", "g5ripple.fate_variance_display",
                default="  📊 命定 = {fate}，变数 = {variance}"
            ).format(fate=self.anchor_fate, variance=self.anchor_variance),
            prompt_manager.get_prompt(
                "talent", "g5ripple.revealed_step_display",
                default="  🎲 公布步骤：「{revealed_step}」"
            ).format(revealed_step=self.anchor_revealed_step),
            prompt_manager.get_prompt(
                "talent", "g5ripple.monitoring_period",
                default="  ⏳ 监控期：5轮"
            ),
            prompt_manager.get_prompt(
                "talent", "g5ripple.state_backed_up_player",
                default="  📸 {player_name} 状态已备份"
            ).format(player_name=player.name),
            f"{'='*60}",
        ]
        return "\n".join(lines)

    # ================================================================
    #  锚定：每轮结束处理（R4）
    # ================================================================

    def on_round_end(self, round_num):
        if not self.anchor_active:
            return

        # 检查是否被结界暂停
        if self.is_anchor_paused():
            self.was_paused_by_barrier = True
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.paused_by_barrier",
                    default="🌊⏸️ 锚定倒计时因幻想乡结界暂停（剩余{remaining_rounds}轮）"
                ).format(remaining_rounds=self.anchor_rounds_left)
            )
            return
        
        # 如果之前被暂停，现在结界已结束，显示恢复信息
        if self.was_paused_by_barrier:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.resumed_after_barrier",
                    default="🌊▶️ 结界已结束，锚定监控恢复（剩余{remaining_rounds}轮）"
                ).format(remaining_rounds=self.anchor_rounds_left)
            )
            self.was_paused_by_barrier = False

        me = self._get_caster()

        if not me or not me.is_alive():
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.caster_death_during_anchor",
                    default="\n🌊💀 {caster_name} 在锚定期间死亡！\n   锚定立即失败，且无法回溯复活。"
                ).format(caster_name=me.name if me else "发动者")
            )
            self._anchor_fail(me, can_revert=False)
            return

        self.anchor_rounds_left -= 1

        if self.anchor_type in ("acquire", "arrive"):
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.simple_anchor_remaining",
                    default="🌊 锚定剩余 {remaining_rounds} 轮（{anchor_detail}）"
                ).format(remaining_rounds=self.anchor_rounds_left, anchor_detail=self.anchor_detail)
            )
            if self.anchor_rounds_left <= 0:
                self._anchor_resolve_simple()
            return

        target = self.state.get_player(self.anchor_target_id)

        if self._check_external_completion(target):
            return

        display.show_info(
            f"\n{'─'*50}"
            f"\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.monitoring_header",
                default="🌊 锚定监控 —— 剩余 {remaining_rounds} 轮"
            ).format(remaining_rounds=self.anchor_rounds_left)
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.monitoring_event",
                default="   事件：{anchor_detail}"
            ).format(anchor_detail=self.anchor_detail)
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.monitoring_destructive_count",
                default="   当前破坏性行动：{current}/{variance}"
            ).format(current=self.anchor_destructive_count, variance=self.anchor_variance)
            + f"\n{'─'*50}"
        )

        if target and target.is_alive():
            self._ask_dm_destructive_action(target)

        if self.anchor_rounds_left <= 0:
            self._anchor_resolve_combat(target)

    def _ask_dm_destructive_action(self, target):
        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.destructive_action_question",
                default="\n📋 DM请判定：{target_name} 本轮是否执行了破坏性行动？"
            ).format(target_name=target.name)
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.destructive_action_list",
                default="   破坏性行动包括：\n   - 移动到与发动者不同地点\n   - 获得/更换克制发动者武器属性的护甲\n   - 进入隐身（且发动者无探测手段）\n   - 被攻击致眩晕或HP降至0.5\n   每轮最多计1次。"
            )
        )

        # ══ DM 判定 3：破坏性行动（全 AI 时自动判定）══
        if self._has_human_players():
            choice = display.prompt_choice(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.destructive_action_choice_prompt",
                    default="{target_name} 本轮破坏性行动判定："
                ).format(target_name=target.name),
                [
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.has_destructive_action",
                        default="有破坏性行动"
                    ),
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.no_destructive_action",
                        default="无破坏性行动"
                    )
                ]
            )
        else:
            # 全 AI 自动判定：检查目标是否移动、护甲变化等
            choice = self._auto_judge_destructive(target)
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.auto_dm_judgment",
                    default="  📋 [自动DM] 判定：{judgment}"
                ).format(judgment=choice)
            )
        # ══ DM 判定 3 结束 ══

        if prompt_manager.get_prompt("talent", "g5ripple.has_destructive_action", default="有破坏性行动") in choice:
            self.anchor_destructive_count += 1
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.destructive_action_incremented",
                    default="  ⚠️ 破坏性行动+1！当前：{current}/{variance}"
                ).format(current=self.anchor_destructive_count, variance=self.anchor_variance)
            )

    def _auto_judge_destructive(self, target):
        """全 AI 模式下自动判定破坏性行动"""
        me = self._get_caster()
        if not me:
            return prompt_manager.get_prompt("talent", "g5ripple.no_destructive_action", default="无破坏性行动")

        # 简单启发式：目标和发动者不在同地点 → 有
        if target.location != me.location:
            return prompt_manager.get_prompt("talent", "g5ripple.has_destructive_action", default="有破坏性行动")
        # 目标隐身且发动者无探测 → 有
        if getattr(target, 'is_invisible', False):
            if not getattr(me, 'has_detection', False):
                return prompt_manager.get_prompt("talent", "g5ripple.has_destructive_action", default="有破坏性行动")
        # 目标快照对比：护甲变化 → 有
        if self.anchor_target_snapshot:
            current_armor = self._get_armor_summary(target)
            if current_armor != self.anchor_target_snapshot.get('armor_summary', []):
                return prompt_manager.get_prompt("talent", "g5ripple.has_destructive_action", default="有破坏性行动")
        return prompt_manager.get_prompt("talent", "g5ripple.no_destructive_action", default="无破坏性行动")

    def _check_external_completion(self, target):
        if self.anchor_type == "kill":
            if not target or not target.is_alive():
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.external_kill_completion",
                        default="\n🌊❌ 锚定目标 {target_name} 已被其他单位击杀！锚定失败。"
                    ).format(target_name=target.name if target else "?")
                )
                me = self._get_caster()
                self._anchor_fail(me, can_revert=True)
                return True

        elif self.anchor_type == "break_armor":
            if target and target.is_alive():
                # ══ DM 判定 4：外部破坏（全 AI 时自动判定）══
                if self._has_human_players():
                    display.show_info(
                        prompt_manager.get_prompt(
                            "talent", "g5ripple.external_armor_damage_question",
                            default="\n📋 DM请确认：{target_name} 的被锚定护甲是否已被其他单位破坏？"
                        ).format(target_name=target.name)
                    )
                    ext = display.prompt_choice(
                        prompt_manager.get_prompt(
                            "talent", "g5ripple.external_damage_judgment_prompt",
                            default="判定："
                        ),
                        [
                            prompt_manager.get_prompt(
                                "talent", "g5ripple.not_externally_damaged",
                                default="未被外部破坏"
                            ),
                            prompt_manager.get_prompt(
                                "talent", "g5ripple.externally_damaged",
                                default="已被外部破坏或超出"
                            )
                        ]
                    )
                else:
                    # 自动判定：对比快照
                    current_armor = self._get_armor_summary(target)
                    snap_armor = (self.anchor_target_snapshot.get('armor_summary', [])
                                  if self.anchor_target_snapshot else [])
                    if current_armor != snap_armor:
                        ext = prompt_manager.get_prompt("talent", "g5ripple.externally_damaged", default="已被外部破坏或超出")
                    else:
                        ext = prompt_manager.get_prompt("talent", "g5ripple.not_externally_damaged", default="未被外部破坏")
                    display.show_info(
                        prompt_manager.get_prompt(
                            "talent", "g5ripple.auto_dm_judgment",
                            default="  📋 [自动DM] 判定：{judgment}"
                        ).format(judgment=ext)
                    )
                # ══ DM 判定 4 结束 ══

                if prompt_manager.get_prompt("talent", "g5ripple.externally_damaged", default="已被外部破坏或超出") in ext:
                    display.show_info(
                        prompt_manager.get_prompt(
                            "talent", "g5ripple.external_condition_met",
                            default="🌊❌ 锚定条件已被外部达成，锚定失败。"
                        )
                    )
                    me = self._get_caster()
                    self._anchor_fail(me, can_revert=True)
                    return True

        return False

    # ================================================================
    #  锚定结算
    # ================================================================

    def _anchor_resolve_simple(self):
        me = self._get_caster()
        if me and me.is_alive():
            display.show_info(
                f"\n{'='*60}"
                f"\n" + prompt_manager.get_prompt(
                    "talent", "g5ripple.simple_anchor_success",
                    default="  🌊✅ 锚定成功！事件自动实现：{anchor_detail}"
                ).format(anchor_detail=self.anchor_detail)
                + "\n" + prompt_manager.get_prompt(
                    "talent", "g5ripple.state_restored_from_backup",
                    default="  📸 {caster_name} 状态回溯至锚定前备份。"
                ).format(caster_name=me.name)
                + f"\n{'='*60}"
            )
            self._auto_resolve_event(me)
            self._restore_player_backup(me, self.anchor_caster_backup)
        else:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.caster_death_anchor_fail",
                    default="\n🌊❌ 发动者已死亡，锚定失败。"
                )
            )
        self._anchor_cleanup()

    def _anchor_resolve_combat(self, target):
        me = self._get_caster()

        adjusted_count = self.anchor_destructive_count
        if (self.anchor_destructive_count > 1
                and target and target.is_alive()
                and self.anchor_target_snapshot):
            if self._target_state_unchanged(target):
                adjusted_count = max(0, adjusted_count - 2)
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.state_unchanged_adjustment",
                        default="\n📋 {target_name} 执行了{count}次破坏性行动，但最终状态（除位置）未改变。\n   破坏性行动计数 -2：{original} → {adjusted}"
                    ).format(
                        target_name=target.name,
                        count=self.anchor_destructive_count,
                        original=self.anchor_destructive_count,
                        adjusted=adjusted_count
                    )
                )

        display.show_info(
            f"\n{'='*60}"
            f"\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.anchor_settlement_header",
                default="🌊 锚定结算！"
            )
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.settlement_event",
                default="   事件：{anchor_detail}"
            ).format(anchor_detail=self.anchor_detail)
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.settlement_variance",
                default="   变数：{variance}"
            ).format(variance=self.anchor_variance)
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.settlement_destructive_adjusted",
                default="   破坏性行动（调整后）：{adjusted_count}"
            ).format(adjusted_count=adjusted_count)
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.settlement_judgment",
                default="   判定：{result}"
            ).format(result=prompt_manager.get_prompt(
                "talent", "g5ripple.success" if adjusted_count <= self.anchor_variance else "g5ripple.failure",
                default="成功" if adjusted_count <= self.anchor_variance else "失败"
            ))
        )

        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.full_path_header",
                default="\n📜 锚定完整路径："
            )
        )
        for i, step in enumerate(self.anchor_path, 1):
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.path_step_numbered",
                    default="   第{step_num}步：{step}"
                ).format(step_num=i, step=step)
            )

        if adjusted_count <= self.anchor_variance:
            display.show_info(
                f"\n" + prompt_manager.get_prompt(
                    "talent", "g5ripple.combat_anchor_success",
                    default="  🌊✅ 锚定成功！事件自动实现：{anchor_detail}"
                ).format(anchor_detail=self.anchor_detail)
                + "\n" + prompt_manager.get_prompt(
                    "talent", "g5ripple.state_restored_from_backup",
                    default="  📸 {caster_name} 状态回溯至锚定前备份。"
                ).format(caster_name=me.name)
                + f"\n{'='*60}"
            )
            self._auto_resolve_event(me)
            self._restore_player_backup(me, self.anchor_caster_backup)
        else:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.combat_anchor_failure",
                    default="\n  🌊❌ 锚定失败！"
                )
            )
            self._anchor_fail(me, can_revert=True)
            return

        self._anchor_cleanup()

    def _anchor_fail(self, me, can_revert=True):
        # ══ CONTROLLER 改动 8：锚定失败选择 ══
        if can_revert and me and me.is_alive() and self.anchor_caster_backup:
            choice = me.controller.choose(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.anchor_fail_choice_prompt",
                    default="{caster_name}，锚定失败。选择："
                ).format(caster_name=me.name),
                [
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.return_to_past",
                        default="回到过去（回档至备份状态）"
                    ),
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.stay_in_present",
                        default="留在当下（不做额外操作）"
                    )
                ],
                context={"phase": "anchor_fail", "situation": "ripple_anchor_fail"}
            )
            if prompt_manager.get_prompt("talent", "g5ripple.return_to_past", default="回到过去（回档至备份状态）") in choice:
                self._restore_player_backup(me, self.anchor_caster_backup)
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.state_restored_to_past",
                        default="🌊 {caster_name} 回溯至锚定前状态。\n   HP: {hp} | 位置: {location}"
                    ).format(caster_name=me.name, hp=me.hp, location=me.location)
                )
            else:
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.stayed_in_present",
                        default="🌊 {caster_name} 选择留在当下。"
                    ).format(caster_name=me.name)
                )
        else:
            if not can_revert:
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.caster_death_no_revert",
                        default="🌊 发动者死亡，无法回溯。"
                    )
                )
        # ══ CONTROLLER 改动 8 结束 ══

        self._anchor_cleanup()

    # ================================================================
    #  锚定：事件自动实现
    # ================================================================

    def _auto_resolve_event(self, caster):
        if self.anchor_type == "kill":
            target = self.state.get_player(self.anchor_target_id)
            if target and target.is_alive():
                target.hp = 0
                self.state.markers.on_player_death(target.player_id)
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.kill_event_implemented",
                        default="💀 锚定事件实现：{target_name} 被命运击杀！"
                    ).format(target_name=target.name)
                )
                display.show_death(target.name, "锚定命运")

        elif self.anchor_type == "break_armor":
            target = self.state.get_player(self.anchor_target_id)
            if target:
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.break_armor_event_implemented",
                        default="🛡️💥 锚定事件实现：{target_name} 的被锚定护甲被命运破坏！"
                    ).format(target_name=target.name)
                )
                display.show_info(
                    prompt_manager.get_prompt(
                        "talent", "g5ripple.manual_armor_removal",
                        default="📋 DM请手动移除 {target_name} 的对应护甲层。"
                    ).format(target_name=target.name)
                )

        elif self.anchor_type == "acquire":
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.acquire_event_implemented",
                    default="📦 锚定事件实现：{caster_name} 获得「{item_detail}」！"
                ).format(caster_name=caster.name, item_detail=self.anchor_detail)
            )
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.manual_item_addition",
                    default="📋 DM请手动为 {caster_name} 添加对应物品/权能。"
                ).format(caster_name=caster.name)
            )

        elif self.anchor_type == "arrive":
            loc = self.anchor_detail.replace("到达 ", "")
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.arrive_event_implemented",
                    default="📍 锚定事件实现：{caster_name} 到达 {location}！"
                ).format(caster_name=caster.name, location=loc)
            )

    # ================================================================
    #  锚定：目标状态对比
    # ================================================================

    def _target_state_unchanged(self, target):
        snap = self.anchor_target_snapshot
        if not snap:
            return False
        if target.hp != snap['hp']:
            return False
        if target.is_stunned != snap['is_stunned']:
            return False
        if target.is_invisible != snap.get('is_invisible', False):
            return False
        if hasattr(target, 'is_shocked') and target.is_shocked != snap.get('is_shocked', False):
            return False
        current_armor = self._get_armor_summary(target)
        if current_armor != snap.get('armor_summary', []):
            return False
        current_weapons = [w.name for w in target.weapons]
        if current_weapons != snap.get('weapon_names', []):
            return False
        if hasattr(target, 'money') and target.money != snap.get('money', 0):
            return False
        return True

    def _get_armor_summary(self, player):
        if hasattr(player, 'armor') and hasattr(player.armor, 'layers'):
            return [layer.name for layer in player.armor.layers if layer]
        return []

    # ================================================================
    #  状态备份与恢复
    # ================================================================

    def _create_player_backup(self, player):
        backup = {
            'hp': player.hp,
            'max_hp': player.max_hp,
            'location': player.location,
            'is_stunned': player.is_stunned,
            'is_invisible': getattr(player, 'is_invisible', False),
            'is_shocked': getattr(player, 'is_shocked', False),
            'is_petrified': getattr(player, 'is_petrified', False),
            'is_awake': player.is_awake,
            'money': getattr(player, 'money', 0),
            'kill_count': getattr(player, 'kill_count', 0),
            'armor_summary': self._get_armor_summary(player),
            'weapon_names': [w.name for w in player.weapons],
        }
        try:
            backup['weapons'] = copy.deepcopy(player.weapons)
        except Exception:
            backup['weapons'] = None
        try:
            backup['armor'] = copy.deepcopy(player.armor)
        except Exception:
            backup['armor'] = None
        try:
            backup['inventory'] = copy.deepcopy(
                getattr(player, 'inventory', []))
        except Exception:
            backup['inventory'] = []

        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.backup_created",
                default="📸 备份 {player_name}： HP={hp} 位置={location} 护甲={armor_summary}"
            ).format(
                player_name=player.name,
                hp=backup['hp'],
                location=backup['location'],
                armor_summary=backup['armor_summary']
            )
        )
        return backup

    def _restore_player_backup(self, player, backup):
        if not backup:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.no_backup_data",
                    default="⚠️ 无备份数据，无法恢复。"
                )
            )
            return
        player.hp = backup['hp']
        player.max_hp = backup['max_hp']
        player.location = backup['location']
        player.is_stunned = backup['is_stunned']
        player.is_awake = backup['is_awake']
        player.is_invisible = backup.get('is_invisible', False)
        if hasattr(player, 'is_shocked'):
            player.is_shocked = backup.get('is_shocked', False)
        if hasattr(player, 'is_petrified'):
            player.is_petrified = backup.get('is_petrified', False)
        if hasattr(player, 'money'):
            player.money = backup.get('money', 0)
        if hasattr(player, 'kill_count'):
            player.kill_count = backup.get('kill_count', 0)
        if backup.get('weapons') is not None:
            player.weapons = copy.deepcopy(backup['weapons'])
        if backup.get('armor') is not None:
            player.armor = copy.deepcopy(backup['armor'])
        if backup.get('inventory') is not None:
            player.inventory = copy.deepcopy(backup['inventory'])
        self.state.markers.on_player_move(player.player_id)
        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.backup_restored",
                default="📸 {player_name} 状态已恢复至备份： HP={hp} 位置={location} 护甲={armor_summary}"
            ).format(
                player_name=player.name,
                hp=player.hp,
                location=player.location,
                armor_summary=self._get_armor_summary(player)
            )
        )

    # ================================================================
    #  锚定清理
    # ================================================================

    def _anchor_cleanup(self):
        self.anchor_active = False
        self.anchor_type = None
        self.anchor_target_id = None
        self.anchor_detail = ""
        self.anchor_path = []
        self.anchor_fate = 0
        self.anchor_variance = 0
        self.anchor_rounds_left = 0
        self.anchor_destructive_count = 0
        self.anchor_caster_backup = None
        self.anchor_target_snapshot = None
        self.anchor_revealed_step = None
        self.was_paused_by_barrier = False

    # ================================================================
    #  方式二：献诗增强
    # ================================================================

    def _execute_poem(self, player):
        self.used = True
        self.reminiscence = 0

        others = [p for p in self.state.alive_players()
                  if p.player_id != player.player_id and p.talent]
        all_targets = others + [player] if player.talent else others

        if not all_targets:
            return prompt_manager.get_prompt(
                "talent", "g5ripple.no_poem_targets",
                default="❌ 没有可献诗的目标。"
            )

        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.poem_selection_header",
                default="\n🌊 选择献诗目标："
            )
        )
        
        for i, p in enumerate(all_targets, 1):
            talent_name = p.talent.name if p.talent else "无天赋"
            poem_name = self.POEM_MAP.get(talent_name, "未知")
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.poem_target_format",
                    default="  {index}. {player_name}（{talent_name}）→ 献予「{poem_name}」之诗"
                ).format(
                    index=i,
                    player_name=p.name,
                    talent_name=talent_name,
                    poem_name=poem_name
                )
            )

        # ══ CONTROLLER 改动 9：选献诗目标 ══
        names = [p.name for p in all_targets]
        target_name = player.controller.choose(
            "选择目标：", names + ["取消"],
            context={"phase": "T0", "situation": "ripple_poem_target"}
        )
        # ══ CONTROLLER 改动 9 结束 ══

        if target_name == "取消":
            self.used = False
            self.reminiscence = self.max_reminiscence
            return prompt_manager.get_prompt(
                "talent", "g5ripple.cancel_poem",
                default="取消献诗。"
            )

        target = next(p for p in all_targets if p.name == target_name)
        talent_name = target.talent.name if target.talent else ""
        poem_type = self.POEM_MAP.get(talent_name)

        if not poem_type:
            self.used = False
            self.reminiscence = self.max_reminiscence
            return prompt_manager.get_prompt(
                "talent", "g5ripple.talent_not_in_poem_list",
                default="❌ {target_name} 的天赋不在献诗列表中。"
            ).format(target_name=target.name)

        return self._dispatch_poem(player, target, poem_type)

    def _dispatch_poem(self, caster, target, poem_type):
        separator = '=' * 60
        header = prompt_manager.get_prompt(
            "talent", "g5ripple.poem_header",
            default="\n{separator}\n  🌊🎶 献予「{poem_type}」之诗！\n  目标：{target_name}\n{separator}\n"
        ).format(separator=separator, poem_type=poem_type, target_name=target.name)

        if poem_type == "游侠":
            msg = self._poem_ranger(target)
        elif poem_type == "群星":
            msg = self._poem_stars(target)
        elif poem_type == "律法":
            msg = self._poem_law(target)
        elif poem_type == "诡计":
            msg = self._poem_trick(caster, target)
        elif poem_type == "阴阳":
            msg = self._poem_yinyang(target)
        elif poem_type == "彼岸":
            msg = self._poem_shore(target)
        elif poem_type == "纷争":
            msg = self._poem_strife(caster, target)
        elif poem_type == "追光":
            msg = self._poem_light(target)
        elif poem_type == "负世":
            msg = self._poem_bear(target)
        elif poem_type == "命运":
            msg = self._poem_destiny(caster)
        else:
            msg = prompt_manager.get_prompt(
                "talent", "g5ripple.poem_unknown",
                default="❌ 未知诗名。"
            )

        return header + msg

    # ---------- 各献诗效果 ----------

    def _poem_ranger(self, target):
        talent = target.talent
        if talent.name == "一刀缭断":
            if hasattr(talent, 'max_uses'):
                talent.max_uses += 1
                if hasattr(talent, 'uses_left'):
                    talent.uses_left += 1
                return prompt_manager.get_prompt(
                    "talent", "g5ripple.poem_ranger_oneslash",
                    default="⚔️ {target_name} 的「一刀缭断」可用次数+1！当前：{uses_left}/{max_uses}"
                ).format(
                    target_name=target.name,
                    uses_left=talent.uses_left,
                    max_uses=talent.max_uses
                )
            return prompt_manager.get_prompt(
                "talent", "g5ripple.poem_ranger_oneslash_fallback",
                default="⚔️ {target_name} 的一刀缭断已增强！+1次数。"
            ).format(target_name=target.name)
        elif talent.name == "你给路打油":
            if hasattr(talent, 'reset_all_triggers'):
                talent.reset_all_triggers()
            if hasattr(talent, 'max_global_triggers'):
                talent.max_global_triggers += 2
            return prompt_manager.get_prompt(
                "talent", "g5ripple.poem_ranger_oiltheroad",
                default="🛤️ {target_name} 的「你给路打油」所有地点触发重置，全局上限+2！"
            ).format(target_name=target.name)
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_ranger_default",
            default="效果已生效。"
        )

    def _poem_stars(self, target):
        talent = target.talent
        talent.ripple_enhanced = True
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_stars",
            default="⭐ {target_name} 的「天星」被涟漪增强！\n   天星落下后额外2次×0.5无视属性伤害\n   石化不再因被攻击自动解除"
        ).format(target_name=target.name)

    def _poem_law(self, target):
        lines = []
        
        # 使用警察系统的统一方法来处理律法之诗效果
        if hasattr(self.state, 'police_engine') and self.state.police_engine:
            msg = self.state.police_engine.process_poem_law_effect(target.player_id)
            lines.append(msg)
        else:
            # 备用逻辑：如果警察引擎不可用
            if not getattr(target, 'is_police', False):
                if hasattr(target, 'crime_records'):
                    target.crime_records = []
                target.is_police = True
                lines.append(prompt_manager.get_prompt(
                    "talent", "g5ripple.poem_law_police_granted",
                    default="👮 {target_name} 犯罪记录清除，获得警察岗位！"
                ).format(target_name=target.name))
            elif getattr(target, 'is_captain', False):
                if hasattr(target, 'prestige'):
                    target.prestige += 2
                    lines.append(prompt_manager.get_prompt(
                        "talent", "g5ripple.poem_law_prestige_increased",
                        default="👮 {target_name} 的威信+2！当前：{prestige}"
                    ).format(target_name=target.name, prestige=target.prestige))
                else:
                    lines.append(prompt_manager.get_prompt(
                        "talent", "g5ripple.poem_law_prestige_manual",
                        default="👮 DM请手动为 {target_name} 的威信+2。"
                    ).format(target_name=target.name))
            else:
                # 竞选进度+2
                if self.state.police_engine:
                    pe = self.state.police_engine
                    # 直接使用警察引擎的竞选进度系统
                    progress_key = "captain_election"
                    current = target.progress.get(progress_key, 0)
                    current += 2
                    target.progress[progress_key] = current
                    
                    # 检查是否立即上任
                    required = 3
                    if target.talent and hasattr(target.talent, 'get_election_rounds_reduction'):
                        reduction = target.talent.get_election_rounds_reduction()
                        required = max(1, required - reduction)
                    
                    if current >= required:
                        # 竞选成功
                        del target.progress[progress_key]
                        if hasattr(pe, 'police') and hasattr(pe.police, 'captain_id'):
                            pe.police.captain_id = target.player_id
                            pe.police.authority = 3
                        target.is_captain = True
                        if hasattr(self.state, 'markers'):
                            self.state.markers.add(target.player_id, "IS_CAPTAIN")
                        lines.append(prompt_manager.get_prompt(
                            "talent", "g5ripple.poem_law_election_success",
                            default="👑 {target_name} 立即成为警队队长！威信：3"
                        ).format(target_name=target.name))
                    else:
                        lines.append(prompt_manager.get_prompt(
                            "talent", "g5ripple.poem_law_election_progress",
                            default="🏛️ {target_name} 竞选进度+2！当前：{current}/{required}"
                        ).format(target_name=target.name, current=current, required=required))
        
        return "\n".join(lines) if lines else prompt_manager.get_prompt(
            "talent", "g5ripple.poem_law_default",
            default="效果已生效。"
        )

    def _poem_trick(self, caster, target):
        display.show_info(prompt_manager.get_prompt(
            "talent", "g5ripple.poem_trick_immediate_action",
            default="🃏 {target_name} 获得一次立刻行动！"
        ).format(target_name=target.name))
        
        from engine.action_turn import ActionTurnManager
        atm = ActionTurnManager(self.state)
        atm.execute_single_action(target)
        
        talent = target.talent
        if hasattr(talent, 'trigger_count'):
            talent.trigger_count = 0
        if hasattr(talent, 'used_this_round'):
            talent.used_this_round = False
        
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_trick_completion",
            default="🃏 {target_name} 完成立刻行动！\n   「不良少年」天赋累计触发已重置。"
        ).format(target_name=target.name)

    def _poem_yinyang(self, target):
        talent = target.talent
        if hasattr(talent, 'charges'):
            talent.charges += 1
        if hasattr(talent, 'max_charges'):
            talent.max_charges = max(talent.max_charges, talent.charges)
        talent.ripple_free_choices = getattr(
            talent, 'ripple_free_choices', 0) + 2
        
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_yinyang",
            default="☯️ {target_name} 的「六爻」增强！\n   充能+1（当前{charges}）\n   下{free_choices}次发动可指定效果"
        ).format(
            target_name=target.name,
            charges=talent.charges,
            free_choices=talent.ripple_free_choices
        )

    def _poem_shore(self, target):
        talent = target.talent
        talent.ripple_enhanced = True
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_shore",
            default="💀✨ {target_name} 的「死者苏生」增强！\n   复活后可获得全游戏任意一件物品或法术\n   （不含扩展/天赋物品，不含抽象权能）"
        ).format(target_name=target.name)

    def _poem_strife(self, caster, target):
        """纷争之诗：为火萤Ⅳ型-完全燃烧的持有者增强"""
        display.show_info(prompt_manager.get_prompt(
            "talent", "g5ripple.poem_strife_immediate_action",
            default="🔥 {target_name} 获得一次立刻行动！"
        ).format(target_name=target.name))
        
        from engine.action_turn import ActionTurnManager
        atm = ActionTurnManager(self.state)
        atm.execute_single_action(target)
        
        # 调用目标天赋的grant_ardent_wish方法
        if hasattr(target.talent, 'grant_ardent_wish'):
            target.talent.grant_ardent_wish()
            return prompt_manager.get_prompt(
                "talent", "g5ripple.poem_strife_completion",
                default="🔥 {target_name} 完成立刻行动！\n   获得特殊物品「炽愿」\n   （可抵扣1次debuff效果结算）"
            ).format(target_name=target.name)
        else:
            # 如果目标天赋没有grant_ardent_wish方法，尝试直接设置字段
            target.talent.has_ardent_wish = True
            return prompt_manager.get_prompt(
                "talent", "g5ripple.poem_strife_completion",
                default="🔥 {target_name} 完成立刻行动！\n   获得特殊物品「炽愿」\n   （可抵扣1次debuff效果结算）"
            ).format(target_name=target.name)

    def _poem_light(self, target):
        talent = target.talent
        if hasattr(talent, 'enhance_by_ripple'):
            talent.enhance_by_ripple()
            return prompt_manager.get_prompt(
                "talent", "g5ripple.poem_light_enhanced",
                default="👁️ {target_name} 的「请一直，注视着我」增强！\n   持续时间-1轮 | 易伤+1.0 | 可用次数+1"
            ).format(target_name=target.name)
        else:
            talent.ripple_enhanced = True
            return prompt_manager.get_prompt(
                "talent", "g5ripple.poem_light_fallback",
                default="👁️ {target_name} 的全息影像已增强！\n   DM请手动调整效果。"
            ).format(target_name=target.name)

    def _poem_bear(self, target):  
        talent = target.talent  
  
        # g5 基础：给予 2 点神性  
        if hasattr(talent, 'gain_divinity'):  
            talent.gain_divinity(2, "涟漪方式2-基础奖励")  
        elif hasattr(talent, 'divinity'):  
            talent.divinity += 2  
  
        # 调用 g4 的增强方法（额外 2 点神性 + 解锁主动 + 被动奖励）  
        if hasattr(talent, 'enhance_by_ripple'):  
            talent.enhance_by_ripple()  
        else:  
            # 兼容：直接设置属性  
            talent.ripple_enhanced = True  
            talent.can_active_start = True  
            talent.passive_bonus_divinity = 2  
  
        current_div = getattr(talent, 'divinity', '?')  
        return prompt_manager.get_prompt(  
            "talent", "g5ripple.poem_bear",  
            default=(  
                "🌅 {target_name} 的「愿负世」增强！\n"  
                "   额外+2神性（当前：{divinity}）\n"  
                "   新增：可花1回合主动启动（启动后获1额外行动）\n"  
                "   被动触发时再+2神性"  
            )  
        ).format(target_name=target.name, divinity=current_div)
    
    def _poem_destiny(self, caster):
        """
        献予「命运」之诗（自身）
        选4个单体单位，各受1点伤害（科技/普通/魔法/无视属性克制各一次）
        """
        DAMAGE_TYPES = ["科技", "普通", "魔法", "无视属性克制"]
        damage_assignments = []

        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.poem_destiny_header",
                default="\n🌊 献予「命运」之诗！\n   选择4个单体单位（可重复），分别承受：\n   科技/普通/魔法/无视属性克制 各1点伤害\n   四种类型必须各使用一次。"
            )
        )

        all_alive = [p for p in self.state.alive_players()]
        if not all_alive:
            return prompt_manager.get_prompt(
                "talent", "g5ripple.poem_destiny_no_targets",
                default="❌ 没有存活的目标。"
            )

        names = [p.name for p in all_alive]

        # ══ CONTROLLER 改动 10：选伤害目标 ×4 ══
        for dtype in DAMAGE_TYPES:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.poem_destiny_damage_selection",
                    default="\n   选择承受「{damage_type}」1点伤害的目标："
                ).format(damage_type=dtype)
            )
            
            target_name = caster.controller.choose(
                f"「{dtype}」伤害目标：", names,
                context={
                    "phase": "T0",
                    "situation": "ripple_destiny_damage",
                    "damage_type": dtype,
                }
            )
            target = next(p for p in all_alive if p.name == target_name)
            damage_assignments.append((target, dtype))
        # ══ CONTROLLER 改动 10 结束 ══

        # 执行伤害
        lines = [prompt_manager.get_prompt(
            "talent", "g5ripple.poem_destiny_settlement_header",
            default="\n🌊 命运之诗——伤害结算："
        )]

        for target, dtype in damage_assignments:
            if not target.is_alive():
                lines.append(prompt_manager.get_prompt(
                    "talent", "g5ripple.poem_destiny_target_dead",
                    default="   → {target_name}（{damage_type}）：目标已死亡，跳过。"
                ).format(target_name=target.name, damage_type=dtype))
                continue

            old_hp = target.hp
            result = None

            # 对所有伤害类型都使用 resolve_damage
            result = resolve_damage(
                attacker=caster,
                target=target,
                weapon=None,
                game_state=self.state,
                raw_damage_override=1.0,
                damage_attribute_override=dtype,
            )
            
            lines.append(prompt_manager.get_prompt(
                "talent", "g5ripple.poem_destiny_damage_result",
                default="   → {target_name}（{damage_type}）： HP {old_hp} → {new_hp}"
            ).format(
                target_name=target.name,
                damage_type=dtype,
                old_hp=old_hp,
                new_hp=target.hp
            ))
            
            for detail in result.get("details", []):
                lines.append(f"      {detail}")

            killed = result.get("killed", False)
            stunned = result.get("stunned", False)

            if killed:
                self.state.markers.on_player_death(target.player_id)
                lines.append(prompt_manager.get_prompt(
                    "talent", "g5ripple.poem_destiny_killed",
                    default="   💀 {target_name} 被命运之诗击杀！"
                ).format(target_name=target.name))
                display.show_death(target.name, "命运之诗")
            elif stunned:
                if not target.is_stunned:
                    target.is_stunned = True
                if not self.state.markers.has(target.player_id, "STUNNED"):
                    self.state.markers.add(target.player_id, "STUNNED")
                lines.append(prompt_manager.get_prompt(
                    "talent", "g5ripple.poem_destiny_stunned",
                    default="   💫 {target_name} 进入眩晕！"
                ).format(target_name=target.name))

        result_msg = "\n".join(lines)
        display.show_info(result_msg)
        return result_msg

    # ================================================================
    #  锚定期间：发动者死亡检查
    # ================================================================

    def on_player_death_check(self, player):
        if not self.anchor_active:
            return
        if player.player_id != self.player_id:
            return

        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.death_check_during_anchor",
                default="\n🌊💀 {player_name} 在锚定期间死亡！\n   锚定立即失败，无法回溯复活。"
            ).format(player_name=player.name)
        )
        self._anchor_fail(player, can_revert=False)

    # ================================================================
    #  锚定期间暂停检查
    # ================================================================

    def is_anchor_paused(self):
        if not self.anchor_active:
            return False
        if (hasattr(self.state, 'active_barrier')
                and self.state.active_barrier
                and self.state.active_barrier.active):
            return True
        return False

    # ================================================================
    #  结界结束钩子：当结界结束时调用此方法
    # ================================================================

    def on_barrier_end(self):
        """当幻想乡结界结束时调用，确保锚定恢复监控"""
        if not self.anchor_active:
            return
        
        # 如果锚定之前被暂停，现在恢复监控
        if self.was_paused_by_barrier:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.barrier_end_resume",
                    default="🌊▶️ 结界已结束，{caster_name} 的锚定监控恢复（剩余{remaining_rounds}轮）"
                ).format(
                    caster_name=self._get_caster().name,
                    remaining_rounds=self.anchor_rounds_left
                )
            )
            # 标记为不再暂停，下一轮 on_round_end 会正常处理
            self.was_paused_by_barrier = False

    # ================================================================
    #  六爻献诗：自由选择效果
    # ================================================================

    def apply_hexagram_free_choice(self, player, talent):
        free = getattr(talent, 'ripple_free_choices', 0)
        if free <= 0:
            return False

        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.hexagram_free_choice_header",
                default="\n☯️ 涟漪增强：跳过猜拳判定！\n   {player_name} 可直接指定效果（剩余自由选择：{remaining}次）"
            ).format(player_name=player.name, remaining=free)
        )

        effects = prompt_manager.get_prompt(
            "talent", "g5ripple.hexagram_effects",
            default=[
                "双剪刀→天雷（对任意1人造成1点伤害）",
                "双石头→获得任意护甲",
                "双布→进入隐身",
                "剪刀vs石头→所有蓄力武器立刻蓄力",
                "剪刀vs布→获得额外行动回合",
                "石头vs布→清除锁定/探测+隐身"
            ]
        )

        # ══ CONTROLLER 改动 11：选六爻效果 ══
        choice = player.controller.choose(
            "选择要触发的效果：", effects,
            context={"phase": "T0", "situation": "ripple_hexagram_free_choice"}
        )
        # ══ CONTROLLER 改动 11 结束 ══

        talent.ripple_free_choices -= 1

        if "天雷" in choice:
            return "both_scissors"
        elif "护甲" in choice:
            return "both_rock"
        elif "双布" in choice:
            return "both_paper"
        elif "蓄力" in choice:
            return "scissors_rock"
        elif "额外行动" in choice:
            return "scissors_paper"
        elif "清除锁定" in choice:
            return "rock_paper"

        return False

    # ================================================================
    #  描述
    # ================================================================

    def describe_status(self):
        parts = []
        if self.anchor_active:
            parts.append(f"🌊锚定中：{self.anchor_detail}")
            parts.append(f"剩余{self.anchor_rounds_left}轮")
            parts.append(
                f"破坏{self.anchor_destructive_count}/{self.anchor_variance}")
        elif self.used:
            parts.append("已使用")
        else:
            parts.append(f"追忆：{self.reminiscence}/{self.max_reminiscence}")
            if self.reminiscence >= self.max_reminiscence:
                parts.append("✨可发动")
        return " | ".join(parts)

    def describe(self):
        return (
            f"【{self.name}】"
            f"\n  追忆：{self.reminiscence}/{self.max_reminiscence}"
            f"（R0每轮+1或+2）"
            f"\n  满24层后选择："
            f"\n    方式一：锚定命运（不消耗行动回合）"
            f"\n    方式二：献诗增强（消耗行动回合）"
            f"\n  仅能使用一次。")
