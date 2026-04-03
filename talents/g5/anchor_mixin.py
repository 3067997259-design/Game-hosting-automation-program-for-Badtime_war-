"""
AnchorMixin —— 锚定系统（方式一）
从 talents/g5_ripple.py 提取，包含：
  - 锚定启动（四种类型：击杀/护甲/获取/到达）
  - DM验证 + 命定/变数计算
  - 每轮结束监控（破坏性行动判定）
  - 锚定结算（成功/失败）
  - 事件自动实现
  - 状态备份与恢复
  - 锚定清理

Bug 修复：
  - _auto_judge_destructive: 移动判定改为对比轮初位置（而非与发动者位置比较）
  - _auto_judge_destructive: 护甲判定改为对比轮初护甲（而非锚定成立时快照）
  - _auto_judge_destructive: 新增发动者被眩晕/震荡/石化检测
  - _auto_judge_destructive: 被锚定者 move 到发动者当前所在地点不视作破坏性行动

新增字段（需在 Ripple.__init__ 中初始化）：
  - _target_round_start_location
  - _target_round_start_armor
  - _caster_round_start_stunned
  - _caster_round_start_shocked
  - _caster_round_start_petrified
"""

import copy
import random
from typing import Any, Optional, List
from cli import display
from controllers.human import HumanController
from engine.prompt_manager import prompt_manager


class AnchorMixin:
    """锚定系统 Mixin，由 Ripple 类多继承使用。"""

    # ---- 类型声明（运行时由 Ripple.__init__ 初始化）----
    state: Any
    player_id: str
    used: bool
    reminiscence: float
    max_reminiscence: float
    anchor_active: bool
    anchor_type: Optional[str]
    anchor_target_id: Optional[str]
    anchor_detail: str
    anchor_path: list
    anchor_fate: int
    anchor_variance: int
    anchor_rounds_left: int
    anchor_destructive_count: int
    anchor_caster_backup: Optional[dict]
    anchor_target_snapshot: Optional[dict]
    anchor_revealed_step: Optional[str]
    was_paused_by_barrier: bool
    # 轮初快照（新增）—— 可为 None（锚定未激活或目标不存在时）
    _target_round_start_location: Any
    _target_round_start_armor: Optional[List]
    _caster_round_start_stunned: bool
    _caster_round_start_shocked: bool
    _caster_round_start_petrified: bool

    # ================================================================
    #  辅助方法（由主类 Ripple 提供，此处声明供类型检查）
    # ================================================================

    def _get_caster(self): ...
    def _has_human_players(self) -> bool: ...
    def _consume_use(self, cost: int = 12) -> None: ...

    # ================================================================
    #  方式一：锚定命运 —— 入口
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

    # ================================================================
    #  四种锚定类型
    # ================================================================

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
        armor_options = self._get_target_armor_names(target)
        if armor_options:
            armor_name = player.controller.choose(
                "选择要破坏的护甲层：", armor_options + ["取消"],
                context={"phase": "T0", "situation": "ripple_anchor_armor_pick"}
            )
            if armor_name == "取消":
                return None
        else:
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

    def _anchor_acquire(self, player):
        # ══ CONTROLLER 改动 6：输入物品名 ══
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

    # ================================================================
    #  DM验证 + 命定/变数计算（击杀/护甲类）
    # ================================================================

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

    # ================================================================
    #  获取/到达类简化启动
    # ================================================================

    def _anchor_start_simple(self, player):
        """获取/到达类锚定：即时生效（V1.92+改动）"""
        self._consume_use()

        # 即时实现事件
        self._anchor_resolve_simple_immediate(player)

        lines = [
            f"\n{'='*60}",
            f"  🌊 锚定成立并即时实现！事件：{self.anchor_detail}",
            f"  📸 状态已备份（失败时可回溯）。",
            f"{'='*60}",
        ]
        display.show_info("\n".join(lines))
        return "\n".join(lines)

    def _anchor_resolve_simple_immediate(self, player):
        """即时实现获取/到达锚定事件"""
        if self.anchor_type == "acquire":
            item_name = self.anchor_detail.replace("获取 ", "")
            self._grant_item_to_player(player, item_name)
        elif self.anchor_type == "arrive":
            loc_name = self.anchor_detail.replace("到达 ", "")
            old_loc = getattr(player, 'location', None)
            player.location = loc_name
            display.show_info(f"  ✅ {player.name} 立即到达了「{loc_name}」！（从{old_loc}）")
        # 不进入锚定监控期，不设置 anchor_active = True
        # 备份状态以防需要回溯（保留向后兼容）
        self.anchor_caster_backup = self._create_player_backup(player)

    def _grant_item_to_player(self, player, item_name):
        """根据物品名称，使用工厂函数创建并添加到玩家身上"""
        from models.equipment import make_weapon, make_armor, make_item

        weapon = make_weapon(item_name)
        if weapon:
            player.add_weapon(weapon)
            display.show_info(f"  ✅ {player.name} 立即获得了武器「{item_name}」！")
            return

        armor = make_armor(item_name)
        if armor:
            success, msg = player.add_armor(armor)
            if success:
                display.show_info(f"  ✅ {player.name} 立即获得了护甲「{item_name}」！")
            else:
                display.show_info(f"  ⚠️ {player.name} 获取护甲「{item_name}」失败：{msg}")
            return

        item = make_item(item_name)
        if item:
            player.add_item(item)
            display.show_info(f"  ✅ {player.name} 立即获得了物品「{item_name}」！")
            return

        # 工厂无法识别的物品，提示DM手动处理
        display.show_info(f"  ⚠️ 无法自动创建「{item_name}」，请DM手动为 {player.name} 添加。")

    # ================================================================
    #  击杀/护甲类启动
    # ================================================================

    def _anchor_start_combat(self, player, target):
        self._consume_use()
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

    def _anchor_on_round_end(self, round_num):
        """锚定专用的轮末处理，由主类 on_round_end 调用"""
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

    # ================================================================
    #  DM判定：破坏性行动
    # ================================================================

    def _ask_dm_destructive_action(self, target):
        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.destructive_action_question",
                default="\n📋 DM请判定：{target_name} 本轮是否执行了破坏性行动？"
            ).format(target_name=target.name)
            + "\n" + prompt_manager.get_prompt(
                "talent", "g5ripple.destructive_action_list",
                default="   破坏性行动包括：\n"
                        "   - 移动到与发动者不同地点（移动到发动者所在地点除外）\n"
                        "   - 获得/更换克制发动者武器属性的护甲\n"
                        "   - 进入隐身（且发动者无探测手段）\n"
                        "   - 使发动者陷入眩晕/震荡/石化\n"
                        "   每轮最多计1次。"
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
            # 全 AI 自动判定
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

    # ════════════════════════════════════════════════════════════════
    #  ★ 修复后的自动判定逻辑 ★
    # ════════════════════════════════════════════════════════════════

    def _auto_judge_destructive(self, target):
        """
        全 AI 模式下自动判定破坏性行动（已修复）

        修复内容：
        1. 移动判定：对比目标轮初位置 vs 当前位置（而非与发动者位置比较）
        2. 新规则：被锚定者 move 到发动者当前所在地点时，不视作破坏性行动
        3. 护甲判定：对比轮初护甲 vs 当前护甲（而非锚定成立时的快照）
        4. 新增：检测目标是否使发动者本轮新陷入眩晕/震荡/石化
        """
        me = self._get_caster()
        if not me:
            return prompt_manager.get_prompt(
                "talent", "g5ripple.no_destructive_action",
                default="无破坏性行动"
            )

        HAS = prompt_manager.get_prompt(
            "talent", "g5ripple.has_destructive_action",
            default="有破坏性行动"
        )
        NO = prompt_manager.get_prompt(
            "talent", "g5ripple.no_destructive_action",
            default="无破坏性行动"
        )

        # ---- 1. 目标本轮是否移动了 ----
        # 对比轮初快照位置，而非与发动者位置比较
        if self._target_round_start_location is not None:
            if target.location != self._target_round_start_location:
                # 目标确实移动了，但如果移动到了发动者当前所在地点，则不算破坏性行动
                if target.location != me.location:
                    return HAS
                # else: 移动到了发动者所在地点，不视作破坏性行动

        # ---- 2. 目标本轮是否获得/更换了护甲 ----
        # 对比轮初护甲快照，而非锚定成立时的快照
        if self._target_round_start_armor is not None:
            current_armor = self._get_armor_summary(target)
            if current_armor != self._target_round_start_armor:
                return HAS

        # ---- 3. 目标是否使发动者本轮新陷入眩晕/震荡/石化 ----
        if not self._caster_round_start_stunned and me.is_stunned:
            return HAS
        if not self._caster_round_start_shocked and getattr(me, 'is_shocked', False):
            return HAS
        if not self._caster_round_start_petrified and getattr(me, 'is_petrified', False):
            return HAS

        # ---- 4. 目标隐身且发动者无探测 ----
        if getattr(target, 'is_invisible', False):
            if not getattr(me, 'has_detection', False):
                return HAS

        return NO

    # ════════════════════════════════════════════════════════════════
    #  轮初快照（供 _auto_judge_destructive 使用）
    # ════════════════════════════════════════════════════════════════

    def _anchor_save_round_snapshots(self):
        """
        在每轮开始时调用，保存目标和发动者的轮初状态。
        由主类 on_round_start 在追忆积累之后调用。
        """
        if not self.anchor_active:
            return

        # 保存目标轮初位置和护甲
        if self.anchor_target_id:
            target = self.state.get_player(self.anchor_target_id)
            if target:
                self._target_round_start_location = target.location
                self._target_round_start_armor = self._get_armor_summary(target)
            else:
                self._target_round_start_location = None
                self._target_round_start_armor = None
        else:
            self._target_round_start_location = None
            self._target_round_start_armor = None

        # 保存发动者轮初状态
        me = self._get_caster()
        if me:
            self._caster_round_start_stunned = me.is_stunned
            self._caster_round_start_shocked = getattr(me, 'is_shocked', False)
            self._caster_round_start_petrified = getattr(me, 'is_petrified', False)
        else:
            self._caster_round_start_stunned = False
            self._caster_round_start_shocked = False
            self._caster_round_start_petrified = False

    # ================================================================
    #  外部完成检查
    # ================================================================

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
                    # 自动判定：对比锚定成立时的快照（这里用初始快照是正确的）
                    current_armor = self._get_armor_summary(target)
                    snap_armor = (self.anchor_target_snapshot.get('armor_summary', [])
                                  if self.anchor_target_snapshot else [])
                    if current_armor != snap_armor:
                        ext = prompt_manager.get_prompt(
                            "talent", "g5ripple.externally_damaged",
                            default="已被外部破坏或超出"
                        )
                    else:
                        ext = prompt_manager.get_prompt(
                            "talent", "g5ripple.not_externally_damaged",
                            default="未被外部破坏"
                        )
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
            # V1.92: 应用「一页永恒的善见天」D4加成
            self._apply_anchor_d4_bonus()
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
        if not me:
            self._anchor_cleanup()
            return

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
            # V1.92: 应用「一页永恒的善见天」D4加成
            self._apply_anchor_d4_bonus()
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

    # ================================================================
    #  锚定失败
    # ================================================================

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
            if prompt_manager.get_prompt(
                "talent", "g5ripple.return_to_past",
                default="回到过去（回档至备份状态）"
            ) in choice:
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
                if self.state.police_engine:
                    self.state.police_engine.on_player_death(target.player_id)
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
    #  锚定清理（V1.92: 增加轮初快照字段清理）
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
        # V1.92: 清理轮初快照
        self._target_round_start_location = None
        self._target_round_start_armor = None
        self._caster_round_start_stunned = False
        self._caster_round_start_shocked = False
        self._caster_round_start_petrified = False

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
    #  结界结束钩子
    # ================================================================

    def on_barrier_end(self):
        """当幻想乡结界结束时调用，确保锚定恢复监控"""
        if not self.anchor_active:
            return

        if self.was_paused_by_barrier:
            me = self._get_caster()
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.barrier_end_resume",
                    default="🌊▶️ 结界已结束，{caster_name} 的锚定监控恢复（剩余{remaining_rounds}轮）"
                ).format(
                    caster_name=me.name if me else self.player_id,
                    remaining_rounds=self.anchor_rounds_left
                )
            )
            self.was_paused_by_barrier = False