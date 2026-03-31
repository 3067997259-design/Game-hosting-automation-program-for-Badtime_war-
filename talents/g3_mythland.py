"""
神代天赋3：神话之外（遗世独立的幻想乡）+ Controller 接入

主动1次，T0启动，消耗行动回合。
拉自己 + 同地点至多1名玩家进入结界。

结界规则：
  - 立刻获得1个额外行动回合
  - 每轮猜拳决定唯一行动者
  - 可正常行动（移动/攻击/物品）但不可与地点交互
  - 隐身无效，强制相互可见+面对面
  - 六爻的"解除锁定/发现"在结界内不生效
  - 发动者免疫所有控制效果（眩晕等）

结界外：
  - 全局轮次完全暂停

解除条件：
  - 结界内仅剩1人存活 OR 经过5个结界内轮次

解除结算：
  - 所有存活玩家回到原地点，恢复常规流程
"""

from engine.action_turn import ActionTurnManager
from talents.base_talent import BaseTalent
from cli import display
from engine.prompt_manager import prompt_manager
from controllers.human import HumanController


class Mythland(BaseTalent):
    name = "神话之外"
    description = "主动1次：拉自己+至多1人进入结界，全局暂停，结界内独立轮次。"
    tier = "神代"

    BLOCKED_ACTIONS = frozenset([
        "pick_up", "move", "learn_spell", "surgery", "report",
        "refill_shield", "refill_at_field", "buy",
        "interact", "location_action",
    ])

    RPS_NAMES = ["石头", "剪刀", "布"]

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.used = False
        self.active = False
        self.barrier_players = []
        self.original_locations = {}
        self.barrier_round = 0
        self.max_barrier_rounds = 5
        self.barrier_location = None
        self.poem_eternity_enhanced = False  # 涟漪献诗增强：被拉入者禁用主动天赋
        self._target_first_action_done = False  # 追踪被拉入者是否已完成第一次行动

    # ============================================
    #  T0选项
    # ============================================

    def get_t0_option(self, player):
        if player.player_id != self.player_id:
            return None
        if self.used:
            return None
        if self.active:
            return None

        others = self._get_same_location_targets(player)
        if others:
            names = ", ".join(o.name for o in others)
            return {
                "name": self.name,
                "description": f"拉自己+至多1人进入结界。同地点可选目标：{names}"
            }
        else:
            return {
                "name": self.name,
                "description": "独自进入结界"
            }

    def _get_same_location_targets(self, player):
        targets = []
        for pid in self.state.player_order:
            if pid == self.player_id:
                continue
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.location == player.location:
                targets.append(p)
        return targets

    # ============================================
    #  T0执行：启动结界
    # ============================================

    def execute_t0(self, player):
        self.used = True
        self.active = True
        self.barrier_round = 0
        self.barrier_location = player.location

        target = self._choose_barrier_target(player)

        self.barrier_players = [self.player_id]
        self.original_locations = {self.player_id: player.location}

        if target:
            self.barrier_players.append(target.player_id)
            self.original_locations[target.player_id] = target.location

        self.state.active_barrier = self
        self._setup_barrier_state()

        barrier_text = prompt_manager.get_prompt(
            "talent", "mythland.barrier_start",
            default=f"""
═══════════════════════════════════════════════════════════════
  🌀 {player.name} 展开了「神话之外」！
  📍 结界位置：{self.barrier_location}
  👥 结界内玩家：{self._player_names()}
  ⏳ 最多 {self.max_barrier_rounds} 个结界轮次
  ⏸️  全局轮次已暂停！
═══════════════════════════════════════════════════════════════"""
        ).format(
            player_name=player.name,
            location=self.barrier_location,
            players=self._player_names(),
            max_rounds=self.max_barrier_rounds
        )
        display.show_info(barrier_text)

        self.run_barrier()

        return f"「神话之外」结界已结束。", "talent"

    def _choose_barrier_target(self, player):
        """让发动者选择拉入的目标（0或1人）"""
        others = self._get_same_location_targets(player)
        if not others:
            display.show_info("  同地点无其他玩家，独自进入结界。")
            return None

        if len(others) == 1:
            display.show_info(f"  同地点只有 {others[0].name}，自动拉入。")
            return others[0]

        # ══ CONTROLLER 改动 1：选择拉入目标 ══
        names = [o.name for o in others]
        options = names + ["不拉人"]
        choice = player.controller.choose(
            "选择要拉入结界的玩家：", options,
            context={"phase": "T0", "situation": "mythland_pick_target"}
        )

        if choice == "不拉人":
            return None

        target = next((o for o in others if o.name == choice), None)
        if target:
            display.show_info(f"  选择拉入 {target.name}！")
        return target
        # ══ CONTROLLER 改动 1 结束 ══

    def _setup_barrier_state(self):
        for pid in self.barrier_players:
            if self.state.markers.has(pid, "INVISIBLE"):
                self.state.markers.remove(pid, "INVISIBLE")
                p = self.state.get_player(pid)
                name = p.name if p else pid
                stealth_removed = prompt_manager.get_prompt(
                    "talent", "mythland.stealth_removed",
                    default=f"  🌀 {name} 的隐身被结界破除！"
                ).format(player_name=name)
                display.show_info(stealth_removed)

        if len(self.barrier_players) == 2:
            p1, p2 = self.barrier_players
            self.state.markers.set_engaged(p1, p2)
            n1 = self.state.get_player(p1).name
            n2 = self.state.get_player(p2).name
            forced_engaged = prompt_manager.get_prompt(
                "talent", "mythland.forced_engaged",
                default=f"  🌀 {n1} 与 {n2} 强制进入面对面！"
            ).format(player1_name=n1, player2_name=n2)
            display.show_info(forced_engaged)

    # ============================================
    #  结界主循环
    # ============================================

    def run_barrier(self):
        caster = self.state.get_player(self.player_id)

        extra_turn = prompt_manager.get_prompt(
            "talent", "mythland.extra_turn",
            default=f"🌀 {caster.name} 获得结界额外行动回合！"
        ).format(player_name=caster.name)
        display.show_info(extra_turn)
        self._execute_barrier_action(caster)

        if self._check_exit():
            self._end_barrier()
            return

        while self.barrier_round < self.max_barrier_rounds:
            self.barrier_round += 1
            barrier_round = prompt_manager.get_prompt(
                "talent", "mythland.barrier_round",
                default=f"""
──────────────────────────────────────────────────
🌀 结界轮次 {self.barrier_round}/{self.max_barrier_rounds}
──────────────────────────────────────────────────"""
            ).format(current=self.barrier_round, max=self.max_barrier_rounds)
            display.show_info(barrier_round)

            actor = self._determine_actor()
            if actor is None:
                break

            barrier_actor = prompt_manager.get_prompt(
                "talent", "mythland.barrier_actor",
                default=f"🌀 本轮行动者：{actor.name}"
            ).format(actor_name=actor.name)
            display.show_info(barrier_actor)
            self._execute_barrier_action(actor)

            if self._check_exit():
                break

        self._end_barrier()

    # ============================================
    #  猜拳决定行动者
    # ============================================

    def _determine_actor(self):
        alive = self._get_alive_barrier_players()
        if len(alive) <= 1:
            return alive[0] if alive else None

        p1 = self.state.get_player(alive[0])
        p2 = self.state.get_player(alive[1])

        rps_header = prompt_manager.get_prompt(
            "talent", "mythland.rps_header",
            default=f"✊✌️✋ 猜拳！{p1.name} vs {p2.name}"
        ).format(player1=p1.name, player2=p2.name)
        display.show_info(rps_header)

        while True:
            c1 = self._get_rps_choice(p1)
            c2 = self._get_rps_choice(p2)

            rps_choice1 = prompt_manager.get_prompt(
                "talent", "mythland.rps_choice",
                default=f"  {p1.name}：{self.RPS_NAMES[c1]}"
            ).format(player_name=p1.name, choice=self.RPS_NAMES[c1])
            rps_choice2 = prompt_manager.get_prompt(
                "talent", "mythland.rps_choice",
                default=f"  vs  {p2.name}：{self.RPS_NAMES[c2]}"
            ).format(player_name=p2.name, choice=self.RPS_NAMES[c2])
            display.show_info(f"{rps_choice1} {rps_choice2}")

            diff = (c1 - c2) % 3
            if diff == 1:
                rps_winner = prompt_manager.get_prompt(
                    "talent", "mythland.rps_winner",
                    default=f"  → {p1.name} 胜出！"
                ).format(winner_name=p1.name)
                display.show_info(rps_winner)
                return p1
            elif diff == 2:
                rps_winner = prompt_manager.get_prompt(
                    "talent", "mythland.rps_winner",
                    default=f"  → {p2.name} 胜出！"
                ).format(winner_name=p2.name)
                display.show_info(rps_winner)
                return p2
            else:
                rps_tie = prompt_manager.get_prompt(
                    "talent", "mythland.rps_tie",
                    default="  → 平局！重新猜拳。"
                )
                display.show_info(rps_tie)

    def _get_rps_choice(self, player):
        """获取一个玩家的猜拳选择，走 controller"""

        # ══ CONTROLLER 改动 2：猜拳走 controller ══
        options = ["石头", "剪刀", "布"]
        choice = player.controller.choose(
            f"{player.name} 请出拳：", options,
            context={"phase": "barrier_rps", "situation": "mythland_rps"}
        )
        return options.index(choice)
        # ══ CONTROLLER 改动 2 结束 ══

    # ============================================
    #  结界内行动回合
    # ============================================

    def _execute_barrier_action(self, actor):
        if actor.player_id == self.player_id:
            if actor.is_stunned:
                actor.is_stunned = False
                self.state.markers.on_stun_recover(actor.player_id)
                immune_control = prompt_manager.get_prompt(
                    "talent", "mythland.immune_control",
                    default=f"  🌀 {actor.name} 免疫控制，眩晕解除！"
                ).format(player_name=actor.name, control_type="眩晕")
                display.show_info(immune_control)
            if hasattr(actor, 'is_shocked') and actor.is_shocked:
                actor.is_shocked = False
                self.state.markers.on_shock_recover(actor.player_id)
                immune_control = prompt_manager.get_prompt(
                    "talent", "mythland.immune_control",
                    default=f"  🌀 {actor.name} 免疫控制，震荡解除！"
                ).format(player_name=actor.name, control_type="震荡")
                display.show_info(immune_control)
            if hasattr(actor, 'is_petrified') and actor.is_petrified:
                actor.is_petrified = False
                self.state.markers.on_petrify_recover(actor.player_id)
                immune_control = prompt_manager.get_prompt(
                    "talent", "mythland.immune_control",
                    default=f"  🌀 {actor.name} 免疫控制，石化解除！"
                ).format(player_name=actor.name, control_type="石化")
                display.show_info(immune_control)

        if actor.player_id != self.player_id:
            if actor.is_stunned:
                skip_stunned = prompt_manager.get_prompt(
                    "talent", "mythland.skip_stunned",
                    default=f"  💫 {actor.name} 处于眩晕状态，跳过行动。"
                ).format(player_name=actor.name)
                display.show_info(skip_stunned)
                return
            if hasattr(actor, 'is_shocked') and actor.is_shocked:
                skip_shocked = prompt_manager.get_prompt(
                    "talent", "mythland.skip_shocked",
                    default=f"  ⚡ {actor.name} 处于震荡状态，跳过行动。"
                ).format(player_name=actor.name)
                display.show_info(skip_shocked)
                return
            if hasattr(actor, 'is_petrified') and actor.is_petrified:
                skip_petrified = prompt_manager.get_prompt(
                    "talent", "mythland.skip_petrified",
                    default=f"  🗿 {actor.name} 处于石化状态，跳过行动。"
                ).format(player_name=actor.name)
                display.show_info(skip_petrified)
                return

        # 永恒之诗增强：被拉入者禁用主动天赋（V1.92: 改为基础效果，无需涟漪强化）
        talent_blocked = False
        forfeit_only = False  # 涟漪增强：第一次行动只能是forfeit

        if actor.player_id != self.player_id and actor.talent:
            # 基础效果：被拉入者无法发动主动天赋
            actor._eternity_blocked = True
            talent_blocked = True
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g3mythland.talent_blocked",
                    default="  🌀 {actor_name} 被拉入幻想乡，无法发动主动天赋！"
                ).format(actor_name=actor.name)
            )

            # 涟漪增强效果：被拉入者第一次行动只能是forfeit
            if getattr(self, 'poem_eternity_forfeit_only', False):
                # 检查是否是第一次行动
                if not self._target_first_action_done:
                    forfeit_only = True
                    display.show_info(
                        prompt_manager.get_prompt(
                            "talent", "g3mythland.forfeit_only",
                            default="  🌀✨ 永恒之诗生效：{actor_name} 第一次行动只能是放弃！"
                        ).format(actor_name=actor.name)
                    )

        atm = ActionTurnManager(self.state)
        # 如果forfeit_only为True，直接执行forfeit，跳过行动选择
        if forfeit_only:
            from actions import forfeit as forfeit_action
            msg = forfeit_action.execute(actor, self.state)
            display.show_info(msg)
            # 标记第一次行动已完成
            self._target_first_action_done = True
        else:
            atm.execute_single_action(actor)
            # 如果是target的第一次行动，标记完成
            if actor.player_id != self.player_id and not self._target_first_action_done:
                self._target_first_action_done = True

        if talent_blocked:
            actor._eternity_blocked = False

    def _enforce_barrier_face_to_face(self):
        alive = self._get_alive_barrier_players()
        if len(alive) != 2:
            return
        p1, p2 = alive
        pl1 = self.state.get_player(p1)
        pl2 = self.state.get_player(p2)
        if pl1.location == pl2.location:
            if not self.state.markers.has_relation(p1, "ENGAGED_WITH", p2):
                self.state.markers.set_engaged(p1, p2)
                forced_engaged = prompt_manager.get_prompt(
                    "talent", "mythland.forced_engaged",
                    default=f"  🌀 {pl1.name} 与 {pl2.name} 重新进入面对面。"
                ).format(player1_name=pl1.name, player2_name=pl2.name)
                display.show_info(forced_engaged)

    # ============================================
    #  退出条件检查
    # ============================================

    def _check_exit(self):
        alive = self._get_alive_barrier_players()

        if len(alive) <= 1:
            if alive:
                name = self.state.get_player(alive[0]).name
                display.show_info(f"🌀 结界内仅剩 {name} 存活，结界即将解除。")
            else:
                display.show_info(f"🌀 结界内无人存活，结界即将解除。")
            return True

        if self.barrier_round >= self.max_barrier_rounds:
            display.show_info(
                f"🌀 结界已持续 {self.max_barrier_rounds} 轮，结界即将解除。")
            return True

        return False

    # ============================================
    #  结界结束
    # ============================================

    def _end_barrier(self):
        alive = self._get_alive_barrier_players()

        # 构建玩家返回信息
        return_info = []
        for pid in alive:
            p = self.state.get_player(pid)
            original = self.original_locations.get(pid)
            if p and original:
                return_info.append(f"  📍 {p.name} 返回原地点：{original}")

        barrier_end = prompt_manager.get_prompt(
            "talent", "mythland.barrier_end",
            default=f"""
═══════════════════════════════════════════════════════════════
  🌀 「神话之外」结界解除！
{chr(10).join(return_info) if return_info else ''}
  📎 结界内面对面关系解除。
  ▶️  全局轮次恢复！
═══════════════════════════════════════════════════════════════"""
        ).format(
            player_name="玩家" if not alive else self.state.get_player(alive[0]).name,
            location=self.original_locations.get(alive[0] if alive else "", "未知")
        )
        display.show_info(barrier_end)

        # ══ 修复：通知所有涟漪天赋结界已结束 ══
        self._notify_ripple_barrier_end()
        # ══ 修复结束 ══

        self.active = False
        self.barrier_players = []
        self.original_locations = {}
        self.barrier_round = 0
        self.barrier_location = None
        self._target_first_action_done = False
        self.state.active_barrier = None

    def _notify_ripple_barrier_end(self):
        """通知所有涟漪天赋结界已结束"""
        try:
            # 导入涟漪类以进行类型检查
            from talents.g5_ripple import Ripple
            for pid in self.state.player_order:
                p = self.state.get_player(pid)
                if p and p.talent and isinstance(p.talent, Ripple):
                    # 调用涟漪天赋的结界结束钩子
                    p.talent.on_barrier_end()
        except ImportError:
            # 如果无法导入涟漪类，跳过通知
            pass
        except Exception as e:
            # 避免通知失败导致结界结束崩溃
            error_msg = prompt_manager.get_prompt(
                "error", "action_failed",
                default=f"⚠️ 通知涟漪天赋结界结束时出错：{e}"
            ).format(reason=str(e))
            display.show_info(error_msg)

    # ============================================
    #  辅助方法
    # ============================================

    def _get_alive_barrier_players(self):
        alive = []
        for pid in self.barrier_players:
            p = self.state.get_player(pid)
            if p and p.is_alive():
                alive.append(pid)
        return alive

    def _player_names(self):
        names = []
        for pid in self.barrier_players:
            p = self.state.get_player(pid)
            names.append(p.name if p else pid)
        return "、".join(names)

    # ============================================
    #  查询接口
    # ============================================

    def is_in_barrier(self, player_id):
        if not self.active:
            return False
        return player_id in self.barrier_players

    def is_action_blocked(self, action_type):
        if not self.active:
            return False, ""
        if action_type in self.BLOCKED_ACTIONS:
            return True, "结界内无法与地点交互！"
        return False, ""

    def is_liuyao_blocked(self, player_id):
        if not self.active:
            return False
        return player_id in self.barrier_players

    def is_caster_immune_to_control(self, player_id):
        if not self.active:
            return False
        return player_id == self.player_id

    def is_stealth_blocked_in_barrier(self, player_id):
        if not self.active:
            return False
        return player_id in self.barrier_players

    # ============================================
    #  描述
    # ============================================

    def describe_status(self):
        parts = []
        if self.active:
            parts.append(f"🌀结界展开中")
            parts.append(f"轮次{self.barrier_round}/{self.max_barrier_rounds}")
            parts.append(f"内部：{self._player_names()}")
        elif self.used:
            parts.append("已使用")
        else:
            parts.append("可用")
        return " | ".join(parts)

    def describe(self):
        return (
            f"【{self.name}】"
            f"\n  主动1次：拉自己+至多1人进入结界"
            f"\n  立刻获得1额外行动回合"
            f"\n  每轮猜拳决定行动者 | 不可地点交互 | 隐身无效"
            f"\n  六爻解锁/发现不生效 | 发动者免疫控制"
            f"\n  全局暂停 | 最多5轮或仅剩1人存活时结束")
