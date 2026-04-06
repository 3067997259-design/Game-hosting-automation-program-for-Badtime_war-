"""
PoemMixin —— 献诗系统（方式二）

包含：
  - _execute_poem: 献诗入口（选目标、验证、分发）
  - _dispatch_poem: 按诗名分发到12个效果方法
  - 12个 _poem_xxx 方法
  - apply_hexagram_free_choice: 六爻献诗自由选择
"""

from typing import Any
from cli import display
from engine.prompt_manager import prompt_manager
from combat.damage_resolver import resolve_damage


class PoemMixin:
    """献诗系统 Mixin，由 Ripple 主类继承。"""

    # 类型声明（运行时由 Ripple.__init__ 初始化）
    POEM_MAP: dict
    state: Any
    player_id: str
    used: bool
    reminiscence: float
    max_reminiscence: float
    total_uses: int
    poem_use_counts: dict
    destiny_use_count: int

    # 辅助方法（由主类 Ripple 提供）
    def _consume_use(self, cost: int = 12) -> None: ...
    def get_destiny_cost(self) -> int: ...

    # ================================================================
    #  献诗入口
    # ================================================================

    def _execute_poem(self, player):
        """
        V1.92 改动：
        - 不再在方法开头直接 self.used=True / self.reminiscence=0
        - 改为在确认选择后调用 self._consume_use()
        - 同一首诗可重复使用
        """
        others = [p for p in self.state.alive_players()
                  if p.player_id != player.player_id and p.talent]
        all_targets = others + [player] if player.talent else others

        if not all_targets:
            return prompt_manager.get_prompt(
                "talent", "g5ripple.no_poem_targets",
                default="❌ 没有可献诗的目标。"
            ), False  # 无目标不消耗行动

        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.poem_selection_header",
                default="\n🌊 选择献诗目标："
            )
        )

        for i, p in enumerate(all_targets, 1):
            talent_name = p.talent.name if p.talent else "无天赋"
            poem_name = self.POEM_MAP.get(talent_name, "未知")
            use_count = self.poem_use_counts.get(poem_name, 0)
            used_mark = f"（已使用{use_count}次）" if use_count > 0 else ""
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.poem_target_format",
                    default="  {index}. {player_name}（{talent_name}）→ 献予「{poem_name}」之诗{used_mark}"
                ).format(
                    index=i,
                    player_name=p.name,
                    talent_name=talent_name,
                    poem_name=poem_name,
                    used_mark=used_mark,
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
            return prompt_manager.get_prompt(
                "talent", "g5ripple.cancel_poem",
                default="取消献诗。"
            ), False  # 取消不消耗行动

        target = next(p for p in all_targets if p.name == target_name)
        talent_name = target.talent.name if target.talent else ""
        poem_type = self.POEM_MAP.get(talent_name)

        if not poem_type:
            return prompt_manager.get_prompt(
                "talent", "g5ripple.talent_not_in_poem_list",
                default="❌ {target_name} 的天赋不在献诗列表中。"
            ).format(target_name=target.name), False  # 失败不消耗行动

        if poem_type == "爱与记忆":
            cost = self.get_destiny_cost()
            if self.reminiscence < cost:
                return f"❌ 爱与记忆之诗需要 {cost} 层追忆（当前 {self.reminiscence} 层）", False
            self._consume_use(cost)
            self.destiny_use_count += 1
        else:
            self._consume_use(12)

        self.poem_use_counts[poem_type] = self.poem_use_counts.get(poem_type, 0) + 1

        from combat.damage_resolver import notify_positive_talent_effect
        caster = self.state.get_player(self.player_id)
        notify_positive_talent_effect(caster, target)
        # 施加爱愿（爱与记忆之诗是自我目标，不施加）
        if poem_type != "爱与记忆" and target.player_id != self.player_id:
            self.grant_love_wish(target.player_id)

        return self._dispatch_poem(player, target, poem_type), True  # 成功消耗行动

    # ================================================================
    #  分发
    # ================================================================

    def _dispatch_poem(self, caster, target, poem_type):
        separator = '=' * 60
        header = prompt_manager.get_prompt(
            "talent", "g5ripple.poem_header",
            default="\n{separator}\n  🌊🎶 献予「{poem_type}」之诗！\n  目标：{target_name}\n{separator}\n"
        ).format(separator=separator, poem_type=poem_type, target_name=target.name)

        if poem_type == "游侠":
            msg = self._poem_ranger(target)
        elif poem_type == "隐者":
            msg = self._poem_hermit(target)
        elif poem_type == "永恒":
            msg = self._poem_eternity(target)
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
        elif poem_type == "飞萤":
            msg = self._poem_strife(caster, target)
        elif poem_type == "追光":
            msg = self._poem_light(target)
        elif poem_type == "负世":
            msg = self._poem_bear(target)
        elif poem_type == "欢愉":
            msg = self._poem_joy(caster, target)
        elif poem_type == "守夜人":
            msg = self._poem_nightwatch(caster, target)
        elif poem_type == "爱与记忆":
            msg = self._poem_destiny(caster)
        else:
            msg = prompt_manager.get_prompt(
                "talent", "g5ripple.poem_unknown",
                default="❌ 未知诗名。"
            )

        return header + msg

    # ================================================================
    #  各献诗效果
    # ================================================================

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
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_ranger_default",
            default="效果已生效。"
        )

    def _poem_hermit(self, target):
        """献予「隐者」之诗：你给路打油增强"""
        talent = target.talent
        if hasattr(talent, 'reset_all_triggers'):
            talent.reset_all_triggers()
        if hasattr(talent, 'max_global_triggers'):
            talent.max_global_triggers += 2
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_ranger_oiltheroad",
            default="🛤️ {target_name} 的「你给路打油」所有地点触发重置，全局上限+2！"
        ).format(target_name=target.name)

    def _poem_eternity(self, target):
        """献予「永恒」之诗：神话之外增强——发动次数+1，被拉入者第一次行动只能是forfeit"""
        talent = target.talent
        # 发动次数+1
        if hasattr(talent, 'used'):
            talent.used = False
        if hasattr(talent, 'max_uses'):
            talent.max_uses += 1
        # 标记涟漪增强效果：被拉入者第一次行动只能是forfeit
        talent.poem_eternity_forfeit_only = True
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_eternity",
            default="🌀 {target_name} 的「神话之外」被涟漪增强！\n  发动次数+1 | 被拉入幻想乡的玩家第一次行动只能是放弃"
        ).format(target_name=target.name)

    def _poem_stars(self, target):
        """献予「群星」之诗：天星增强（可叠加）"""
        talent = target.talent
        if hasattr(talent, 'ripple_enhance_count'):
            talent.ripple_enhance_count = getattr(talent, 'ripple_enhance_count', 0) + 1
        else:
            talent.ripple_enhanced = True
            talent.ripple_enhance_count = 1
            talent.ripple_petrify_lock = True

        talent.ripple_bounce_count = getattr(talent, 'ripple_bounce_count', 0) + 2

        bounce_count = talent.ripple_bounce_count
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_stars",
            default="⭐ {target_name} 的「天星」被涟漪增强（{count}层）！\n   天星落下后额外{bounce}次×0.5无视属性弹射伤害\n   石化不再因被攻击自动解除"
        ).format(target_name=target.name, count=talent.ripple_enhance_count, bounce=bounce_count)

    def _poem_law(self, target):
        """
        献予律法之诗 — 完整规则。

        分支逻辑（按优先级）：
        1. 无队长 且 无存活警察 → 立刻上任队长 + 召唤新单位（解除永久禁用）
        2. 目标不是警察 → 清除犯罪记录 + 赋予警察岗位
        3. 目标是警察但不是队长，且队长空缺 → 立刻成为队长
        4. 目标已是队长：
           a. 有存活警察 → 威信+2 + 指定1个警察单位立刻行动
           b. 无存活警察 → 召唤新单位（解除永久禁用）
        """
        lines = []
        pe = getattr(self.state, 'police_engine', None)
        police = getattr(self.state, 'police', None)

        if not pe or not police:
            # 备用逻辑：警察引擎不可用，仅做基础处理
            if not getattr(target, 'is_police', False):
                target.is_police = True
                lines.append(prompt_manager.get_prompt(
                    "talent", "g5ripple.poem_law_police_granted",
                    default="👮 {target_name} 犯罪记录清除，获得警察岗位！"
                ).format(target_name=target.name))
            return "\n".join(lines) if lines else prompt_manager.get_prompt(
                "talent", "g5ripple.poem_law_default", default="效果已生效。")

        # ---- 公共前置：清除犯罪记录 ----
        if target.player_id in police.crime_records:
            police.crime_records[target.player_id] = set()
        if hasattr(target, 'is_criminal'):
            target.is_criminal = False

        has_captain = police.has_captain()
        has_alive_police = police.any_alive()
        is_captain = (getattr(target, 'is_captain', False)
                      and police.captain_id == target.player_id)
        is_police = getattr(target, 'is_police', False)

        # ============================================================
        #  分支1：无队长 且 无存活警察单位
        # ============================================================
        if not has_captain and not has_alive_police:
            if not is_police:
                target.is_police = True
                self.state.markers.add(target.player_id, "IS_POLICE")
                lines.append(f"👮 {target.name} 犯罪记录清除，获得警察岗位！")

            police.captain_id = target.player_id
            police.authority = 3
            target.is_captain = True
            self.state.markers.add(target.player_id, "IS_CAPTAIN")
            lines.append(f"👑 {target.name} 立即成为警队队长！威信：3")

            if police.permanently_disabled:
                police.permanently_disabled = False
                lines.append("🏙️ 警察局永久禁用已解除！")

            pe._on_captain_elected()
            lines.append("🚔 队长上任，3个警察单位已在警察局就位！")

        # ============================================================
        #  分支2：目标不是警察
        # ============================================================
        elif not is_police:
            target.is_police = True
            self.state.markers.add(target.player_id, "IS_POLICE")
            lines.append(prompt_manager.get_prompt(
                "talent", "g5ripple.poem_law_police_granted",
                default="👮 {target_name} 犯罪记录清除，获得警察岗位！"
            ).format(target_name=target.name))

        # ============================================================
        #  分支3：目标是警察但不是队长，且队长空缺
        # ============================================================
        elif is_police and not is_captain and not has_captain:
            police.captain_id = target.player_id
            police.authority = 3
            target.is_captain = True
            self.state.markers.add(target.player_id, "IS_CAPTAIN")
            pe._on_captain_elected()
            lines.append(f"👑 {target.name} 立即成为警队队长！威信：3")

        # ============================================================
        #  分支4：目标已是队长
        # ============================================================
        elif is_captain:
            if has_alive_police:
                # 4a：有存活警察 → 威信+2 + 指定1个警察单位立刻行动
                police.authority += 2
                lines.append(f"👑 {target.name} 威信+2！当前：{police.authority}")

                alive_units = police.alive_units()
                if alive_units:
                    unit_ids = [u.unit_id for u in alive_units]
                    lines.append(
                        f"🏙️ 朝阳好市民效果：可指定1个警察单位立刻行动！"
                        f"可选：{', '.join(unit_ids)}"
                    )

                    chosen_id = target.controller.choose(
                        "选择立刻行动的警察单位：",
                        unit_ids,
                        context={"phase": "T0", "situation": "poem_law_extra_action"}
                    )
                    if chosen_id not in unit_ids:
                        chosen_id = unit_ids[0]
                        lines.append(f"⚠️ 选择的单位无效，自动使用 {chosen_id}")

                    display.show_info(
                        f"🚔 {chosen_id} 获得一次立刻行动！"
                        f"请输入命令（police move/equip/attack {chosen_id} ...）"
                    )
                    raw_cmd = target.controller.get_command(
                        player=target,
                        game_state=self.state,
                        available_actions=["police move", "police equip", "police attack"],
                        context={
                            "phase": "T0",
                            "situation": "poem_law_police_action",
                            "police_id": chosen_id,
                        }
                    )

                    from cli.parser import parse
                    from cli.validator import validate_police_command
                    parsed = parse(raw_cmd, target.player_id)
                    if parsed and parsed.get("action") == "police_command":
                        parsed["police_id"] = chosen_id
                        valid, reason = validate_police_command(target, parsed, self.state)
                        if valid:
                            from actions.police_command import execute as police_cmd_exec
                            result = police_cmd_exec(target, parsed, self.state)
                            if isinstance(result, tuple):
                                result_msg, _ = result
                            else:
                                result_msg = str(result) if result else "⚠️ 命令执行失败"
                            lines.append(result_msg)
                        else:
                            lines.append(
                                f"⚠️ 命令验证失败：{reason}，{chosen_id} 的额外行动跳过。"
                            )
                    else:
                        lines.append(f"⚠️ 无法解析命令，{chosen_id} 的额外行动跳过。")

                    if not target.is_captain:
                        lines.append("⚠️ 额外行动导致威信归零，队长身份已解除！")

            else:
                # 4b：无存活警察 → 召唤新单位 + 解除永久禁用
                police.units = [u for u in police.units if u.is_alive()]
                msg = pe.summon_police_unit(target.location)
                lines.append(msg)
                lines.append("🏙️ 朝阳好市民效果：警察系统恢复运作！")

        # ============================================================
        #  分支5：目标是警察但不是队长，且已有队长
        # ============================================================
        else:
            lines.append(f"👮 {target.name} 的犯罪记录已清除。")

        return "\n".join(lines) if lines else prompt_manager.get_prompt(
            "talent", "g5ripple.poem_law_default", default="效果已生效。")

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
        """献予「彼岸」之诗：死者苏生增强（可叠加）"""
        talent = target.talent
        talent.ripple_enhance_count = getattr(talent, 'ripple_enhance_count', 0) + 1
        talent.ripple_enhanced = True
        talent.ripple_item_count = getattr(talent, 'ripple_item_count', 0) + 1
        count = talent.ripple_item_count
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_shore",
            default="💀✨ {target_name} 的「死者苏生」增强（{count}层）！\n   复活后可获得{count}件全游戏任意物品或法术\n   （不含扩展/天赋物品，不含抽象权能）"
        ).format(target_name=target.name, count=count)

    def _poem_strife(self, caster, target):
        """飞萤之诗：立刻行动（免费超新星）+ 2层炽愿"""
        # Set free supernova flag BEFORE the action
        if hasattr(target.talent, 'supernova_free_use'):
            target.talent.supernova_free_use = True
        else:
            target.talent.supernova_free_use = True  # dynamically add attribute

        display.show_info(prompt_manager.get_prompt(
            "talent", "g5ripple.poem_strife_immediate_action",
            default="🔥 {target_name} 获得一次立刻行动！（超新星过载本次不消耗次数）"
        ).format(target_name=target.name))

        from engine.action_turn import ActionTurnManager
        atm = ActionTurnManager(self.state)
        atm.execute_single_action(target)

        # Reset free supernova flag after action
        target.talent.supernova_free_use = False

        # Grant 2 ardent wishes
        if hasattr(target.talent, 'grant_ardent_wish'):
            target.talent.grant_ardent_wish()  # +1
            target.talent.grant_ardent_wish()  # +1 = total 2
        else:
            # Fallback
            if hasattr(target.talent, 'ardent_wish_charges'):
                target.talent.ardent_wish_charges += 2
            else:
                target.talent.ardent_wish_charges = 2

        charges = getattr(target.talent, 'ardent_wish_charges', 0)
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_strife_completion",
            default="🔥 {target_name} 完成立刻行动！\n"
                    "   获得2个「炽愿」（当前{charges}层）\n"
                    "   （每层抵扣1次debuff + 0.5额外生命值）"
        ).format(target_name=target.name, charges=charges)

    def _poem_light(self, target):
        """献予「追光」之诗：全息影像增强（可叠加）

        enhance_by_ripple() 已处理：max_uses+1, ripple_extra_vulnerability+0.5, enhanced=True
        此处只负责调用并返回提示信息，不再重复修改属性。
        """
        talent = target.talent
        if hasattr(talent, 'enhance_by_ripple'):
            talent.enhance_by_ripple()
        else:
            talent.ripple_enhanced = True

        if hasattr(talent, 'max_uses'):
            vuln = 0.5 + getattr(talent, 'ripple_extra_vulnerability', 0.0)
            return prompt_manager.get_prompt(
                "talent", "g5ripple.poem_light_enhanced",
                default="✨{target_name} 的「请一直，注视着我」增强！\n   易伤+{vuln} | 可用次数+1（当前{uses}次）"
            ).format(target_name=target.name, vuln=vuln, uses=talent.max_uses)
        else:
            return prompt_manager.get_prompt(
                "talent", "g5ripple.poem_light_fallback",
                default="✨{target_name} 的全息影像已增强！\n"
            ).format(target_name=target.name)

    def _poem_bear(self, target):
        """献予「负世」之诗：愿负世增强（可重复）"""
        talent = target.talent

        # 基础：给予 2 点火种（每次都给）
        if hasattr(talent, 'gain_divinity'):
            talent.gain_divinity(2, "涟漪方式2-基础奖励")
        elif hasattr(talent, 'divinity'):
            talent.divinity += 2

        # 增强效果
        if hasattr(talent, 'enhance_by_ripple'):
            # First time: full enhance (unlock active, set passive bonus)
            # Subsequent: only give divinity + increment passive bonus
            if not talent.ripple_enhanced:
                talent.enhance_by_ripple()
            else:
                # Already enhanced: just give extra divinity + increment passive bonus
                talent.gain_divinity(2, "献予负世之诗-额外奖励(重复)")
                talent.passive_bonus_divinity = getattr(talent, 'passive_bonus_divinity', 0) + 1
        else:
            # Fallback for non-savior talents
            talent.ripple_enhanced = True
            talent.can_active_start = True
            talent.passive_bonus_divinity = getattr(talent, 'passive_bonus_divinity', 0) + 1

        current_div = getattr(talent, 'divinity', '?')
        passive = getattr(talent, 'passive_bonus_divinity', 0)
        return prompt_manager.get_prompt(
            "talent", "g5ripple.poem_bear",
            default=(
                "🌅 {target_name} 的「愿负世」增强！\n"
                "   额外+2火种（当前：{divinity}）\n"
                "   被动触发时额外+{passive}火种"
            )
        ).format(target_name=target.name, divinity=current_div, passive=passive)

    def _poem_destiny(self, caster):
        """
        「爱与记忆」之诗（自身）
        V1.92+: 段数随使用次数成长。
        基础段数由开局人数决定（2人=2段, 4人=3段, 6人+=4段）。
        每多发动一次，段数+1。达到4段后，额外段数为真伤。
        消耗递增：min(24, 12 + 3 × 已用次数)（已在 _execute_poem 中处理）
        """
        ALL_DAMAGE_TYPES = ["科技", "普通", "魔法", "无视属性克制"]

        # Base stages from player count
        initial_count = len(self.state.player_order)
        base_n = min(4, max(2, initial_count // 2 + 1))

        # Stage growth: each additional use adds 1 stage
        # destiny_use_count was already incremented in _execute_poem BEFORE dispatch
        # So destiny_use_count is now the current use number (1-indexed)
        extra_stages = max(0, self.destiny_use_count - 1)  # first use = 0 extra
        total_stages = base_n + extra_stages

        # Split into normal stages (up to 4) and true damage stages (beyond 4)
        normal_n = min(4, total_stages)
        true_damage_n = max(0, total_stages - 4)

        DAMAGE_TYPES = ALL_DAMAGE_TYPES[:normal_n]
        # Add true damage stages
        for i in range(true_damage_n):
            DAMAGE_TYPES.append("真伤")

        damage_assignments = []

        cost = self.get_destiny_cost()
        display.show_info(
            f"\n🌊 献予「爱与记忆」之诗！（第{self.destiny_use_count}次，消耗{cost}层追忆）\n"
            f"   选择{total_stages}个单体单位（可重复），分别承受：\n"
            f"   {'/'.join(DAMAGE_TYPES)} 各1点伤害"
        )

        all_alive = [p for p in self.state.alive_players()]
        if not all_alive:
            return prompt_manager.get_prompt(
                "talent", "g5ripple.poem_destiny_no_targets",
                default="❌ 没有存活的目标。"
            )

        names = [p.name for p in all_alive]

        # ══ CONTROLLER 改动 10：选伤害目标 ×n ══
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
            default="\n🌊 爱与记忆之诗——伤害结算："
        )]

        for target, dtype in damage_assignments:
            if not target.is_alive():
                lines.append(prompt_manager.get_prompt(
                    "talent", "g5ripple.poem_destiny_target_dead",
                    default="   → {target_name}（{damage_type}）：目标已死亡，跳过。"
                ).format(target_name=target.name, damage_type=dtype))
                continue

            old_hp = target.hp

            # 真伤与普通伤害统一走 resolve_damage，与天星/超新星一致
            # "真伤"使用"无视属性克制"属性 + ignore_counter，正常经过护甲/天赋钩子
            damage_attr = "无视属性克制" if dtype == "真伤" else dtype
            result = resolve_damage(
                attacker=caster, target=target, weapon=None,
                game_state=self.state,
                raw_damage_override=1.0,
                damage_attribute_override=damage_attr,
                ignore_counter=True,
                is_talent_attack=True,
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
                if self.state.police_engine:
                    self.state.police_engine.on_player_death(target.player_id)
                lines.append(prompt_manager.get_prompt(
                    "talent", "g5ripple.poem_destiny_killed",
                    default="   💀 {target_name} 被爱与记忆之诗击杀！"
                ).format(target_name=target.name))
                display.show_death(target.name, "爱与记忆之诗")
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
        return result_msg

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
                default="\n☯️ 涟漪增强：跳过猜拳判定！\n"
                        "   {player_name} 可直接指定效果（剩余自由选择：{remaining}次）"
            ).format(player_name=player.name, remaining=free)
        )

        effects = prompt_manager.get_prompt(
    "talent", "g5ripple.hexagram_effects",
    default=[
        "双剪刀→天雷（对任意1人造成1点伤害，无视单体保护）",
        "双石头→获得任意武器",
        "双布→获得任意护甲",
        "剪刀vs石头→所有蓄力武器立刻蓄力（没有则获得一把）",
        "剪刀vs布→获得2个连续额外行动回合",
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
        elif "武器" in choice:
            return "both_rock"
        elif "护甲" in choice:
            return "both_paper"
        elif "蓄力" in choice:
            return "scissors_rock"
        elif "额外行动" in choice:
            return "scissors_paper"
        elif "清除锁定" in choice:
            return "rock_paper"

        return False

    # ================================================================
    #  献予「欢愉」之诗（神代6：要有笑声！）
    # ================================================================

    def _poem_joy(self, caster, target):
        """
        献予「欢愉」之诗：
        - 被选中的玩家立刻获得1次「插入式笑话」
        - 后续「插入式笑话」需要的forfeit数减少2
        README 第 2162 行
        """
        talent = target.talent
        if not talent or talent.name != "要有笑声！":
            return "❌ 目标天赋不是「要有笑声！」"

        # 立刻获得1次插入式笑话
        if hasattr(talent, 'cutaway_charges'):
            talent.cutaway_charges = getattr(talent, 'cutaway_charges', 0) + 1
        else:
            talent.cutaway_charges = 1

        # forfeit需求减少2
        if hasattr(talent, 'forfeit_threshold'):
            talent.forfeit_threshold = max(0, talent.forfeit_threshold - 2)
        else:
            talent.forfeit_reduction = getattr(talent, 'forfeit_reduction', 0) + 2

        charges = getattr(talent, 'cutaway_charges', 1)
        return (
            f"🎭 {target.name} 的「要有笑声！」增强！\n"
            f"   立刻获得1次「插入式笑话」（当前: {charges}次）\n"
            f"   后续「插入式笑话」所需forfeit数减少2"
        )

    # ================================================================
    #  献予「守夜人」之诗（神代7：大叔我啊，剪短发了）
    # ================================================================

    def _poem_nightwatch(self, caster, target):
        """
        献予「守夜人」之诗（特殊：需承受者选择是否接受）
        README 第 2164-2174 行

        若选择接受：
        - 色彩值永久赋为null
        - 若已在Terror：解除Terror，每1.5点额外生命值转化为1点永久额外生命值（向下取整），
          额外扣除3点（不致死，不足的话有多少扣多少），恢复护甲值为3的铁之荷鲁斯，
          恢复所有战术指令/药物/战术装备的可用性（需自己回去拿）
        """
        import math
        talent = target.talent
        if not talent or not hasattr(talent, 'color_is_null'):
            return "❌ 目标天赋不是「大叔我啊，剪短发了」"

        # 需承受者选择是否接受
        choice = target.controller.choose(
            "「向走向过去的少女说出你的愿望吧，她的未来，就是你的过去」\n"
            "「至少，她会给你一个改写痛苦现实的机会」\n"
            "是否接受献予「守夜人」之诗？",
            ["接受", "拒绝"],
            context={"phase": "T0", "situation": "poem_nightwatch_choice"}
        )

        if "拒绝" in choice:
            return "🌙 守夜人拒绝了涟漪的馈赠。"

        msg_parts = ["🌙 献予「守夜人」之诗生效！"]

        # 色彩值永久赋为null
        talent.color_is_null = True
        talent.color = 0
        msg_parts.append("   色彩值永久归null")

        if talent.is_terror:
            # Terror 解除
            talent.is_terror = False

            # 强制锁定ID解除 — 恢复原名
            # 注意：原名可能已丢失，用 player_id 作为 fallback
            if target.name == "星野-Terror":
                target.name = f"星野_{target.player_id}"
            msg_parts.append(f"   Terror 状态解除！ID恢复为 {target.name}")

            # 每1.5点剩余额外生命值转化为1点永久额外生命值（向下取整）
            permanent_extra = math.floor(talent.terror_extra_hp / 1.5)
            talent.terror_extra_hp = 0
            msg_parts.append(f"   额外生命值转化：{permanent_extra}点永久额外HP")

            # 额外扣除3点（不致死，不足2点的话有多少扣多少）
            deduct = min(permanent_extra, 3)
            permanent_extra -= deduct
            msg_parts.append(f"   扣除{deduct}点 → 剩余{permanent_extra}点永久额外HP")

            # 将永久额外HP存储到talent上（用于 receive_damage_to_temp_hp）
            talent.terror_extra_hp = float(permanent_extra)

            # 恢复护甲值为3的铁之荷鲁斯
            talent.iron_horus_hp = 3
            talent.iron_horus_max_hp = 3
            talent.fusion_shield_done = True
            msg_parts.append("   铁之荷鲁斯恢复（护甲值: 3）")

            # 恢复所有战术指令、药物和战术装备的可用性（需自己回去拿）
            if talent.fusion_weapon_done:
                talent.tactical_unlocked = True
            msg_parts.append("   战术指令可用性恢复（道具/药物需自行获取）")

        return "\n".join(msg_parts)
