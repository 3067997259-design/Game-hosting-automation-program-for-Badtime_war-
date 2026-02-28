"""
神代天赋3：神话之外（遗世独立的幻想乡）

主动1次，T0启动，消耗行动回合。
拉自己 + 同地点至多1名玩家进入结界。

结界规则：
  - 立刻获得1个额外行动回合
  - 每轮猜拳决定唯一行动者
  - 可正常行动（移动/攻击/物品）但不可与地点交互
    （不可拿物品/学法术/手术/举报/补魔法护盾/补AT力场）
  - 隐身无效，强制相互可见+面对面
  - 六爻的"解除锁定/发现"在结界内不生效
  - 发动者免疫所有控制效果（眩晕等）

结界外：
  - 全局轮次完全暂停（D4/R0/R4/所有倒计时冻结）

解除条件：
  - 结界内仅剩1人存活 OR 经过5个结界内轮次

解除结算：
  - 所有存活玩家回到原地点，恢复常规流程
"""

from engine.action_turn import ActionTurnManager
from talents.base_talent import BaseTalent
from cli import display


class Mythland(BaseTalent):
    name = "神话之外"
    description = "主动1次：拉自己+至多1人进入结界，全局暂停，结界内独立轮次。"
    tier = "神代"

    # 不可在结界内执行的地点交互动作
    BLOCKED_ACTIONS = frozenset([
        "pick_up","move","learn_spell", "surgery", "report",
        "refill_shield", "refill_at_field", "buy",
        "interact", "location_action",
    ])

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)

        # 使用状态
        self.used = False

        # 结界状态
        self.active = False
        self.barrier_players = []         # 结界内玩家ID列表
        self.original_locations = {}      # {pid: 进入前的地点}
        self.barrier_round = 0
        self.max_barrier_rounds = 5
        self.barrier_location = None      # 结界发动时的地点

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

        # 检查同地点是否有其他存活玩家
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
        """获取同地点的其他存活玩家"""
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
        """
        启动结界。
        返回 (description, "talent")。
        结界循环由 run_barrier() 驱动。
        """
        self.used = True
        self.active = True
        self.barrier_round = 0
        self.barrier_location = player.location

        # 选择拉入对象
        target = self._choose_barrier_target(player)

        # 记录原始位置
        self.barrier_players = [self.player_id]
        self.original_locations = {self.player_id: player.location}

        if target:
            self.barrier_players.append(target.player_id)
            self.original_locations[target.player_id] = target.location

        # 设置全局结界标记
        self.state.active_barrier = self

        # 强制面对面 + 破除隐身
        self._setup_barrier_state()

        lines = [
            f"\n{'='*60}",
            f"  🌀 {player.name} 展开了「神话之外」！",
            f"  📍 结界位置：{self.barrier_location}",
            f"  👥 结界内玩家：{self._player_names()}",
            f"  ⏳ 最多 {self.max_barrier_rounds} 个结界轮次",
            f"  ⏸️  全局轮次已暂停！",
            f"{'='*60}",
        ]
        display.show_info("\n".join(lines))

        # 运行结界循环（同步阻塞，CLI游戏无需异步）
        self.run_barrier()

        # 结界结束后返回
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

        # 多个候选：让玩家选择
        display.show_info("\n选择要拉入结界的玩家（输入编号，0=不拉人）：")
        for i, o in enumerate(others, 1):
            display.show_info(f"  {i}. {o.name}")

        while True:
            choice = input("选择：").strip()
            if choice == "0":
                return None
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(others):
                    display.show_info(f"  选择拉入 {others[idx].name}！")
                    return others[idx]
            except ValueError:
                pass
            display.show_info("  无效输入，请重新选择。")

    def _setup_barrier_state(self):
        """进入结界时：破除隐身、建立面对面"""
        for pid in self.barrier_players:
            # 破除隐身
            if self.state.markers.has(pid, "INVISIBLE"):
                self.state.markers.remove(pid, "INVISIBLE")
                p = self.state.get_player(pid)
                name = p.name if p else pid
                display.show_info(f"  🌀 {name} 的隐身被结界破除！")

        # 如果有两人，建立面对面
        if len(self.barrier_players) == 2:
            p1, p2 = self.barrier_players
            self.state.markers.set_engaged(p1, p2)
            n1 = self.state.get_player(p1).name
            n2 = self.state.get_player(p2).name
            display.show_info(f"  🌀 {n1} 与 {n2} 强制进入面对面！")

    # ============================================
    #  结界主循环
    # ============================================

    def run_barrier(self):
        """
        结界主循环。同步执行，阻塞主引擎。
        流程：额外行动 → 循环（猜拳→行动→检查退出）
        """
        caster = self.state.get_player(self.player_id)

        # 额外行动回合（发动者立刻获得）
        display.show_info(
            f"\n🌀 {caster.name} 获得结界额外行动回合！")
        self._execute_barrier_action(caster)

        if self._check_exit():
            self._end_barrier()
            return

        # 结界轮次循环
        while self.barrier_round < self.max_barrier_rounds:
            self.barrier_round += 1
            display.show_info(
                f"\n{'─'*40}"
                f"\n🌀 结界轮次 {self.barrier_round}/{self.max_barrier_rounds}"
                f"\n{'─'*40}")

            # 确定行动者
            actor = self._determine_actor()
            if actor is None:
                # 只剩一人，结界结束
                break

            display.show_info(f"🌀 本轮行动者：{actor.name}")

            # 执行行动
            self._execute_barrier_action(actor)



            # 检查退出条件
            if self._check_exit():
                break

        self._end_barrier()

    # ============================================
    #  猜拳决定行动者
    # ============================================

    def _determine_actor(self):
        """猜拳决定唯一行动者"""
        alive = self._get_alive_barrier_players()
        if len(alive) <= 1:
            return alive[0] if alive else None

        # 只有2个人猜拳
        p1 = self.state.get_player(alive[0])
        p2 = self.state.get_player(alive[1])

        RPS_MAP = {"石头": 0, "剪刀": 1, "布": 2}
        RPS_NAMES = ["石头", "剪刀", "布"]
        # 胜负：(a - b) % 3 == 1 → a赢, == 2 → b赢, == 0 → 平

        display.show_info(
            f"\n✊✌️✋ 猜拳！{p1.name} vs {p2.name}")

        while True:
            c1 = self._get_rps_choice(p1)
            c2 = self._get_rps_choice(p2)

            display.show_info(
                f"  {p1.name}：{RPS_NAMES[c1]}"
                f"  vs  {p2.name}：{RPS_NAMES[c2]}")

            diff = (c1 - c2) % 3
            if diff == 1:
                display.show_info(f"  → {p1.name} 胜出！")
                return p1
            elif diff == 2:
                display.show_info(f"  → {p2.name} 胜出！")
                return p2
            else:
                display.show_info(f"  → 平局！重新猜拳。")

    def _get_rps_choice(self, player):
        """获取一个玩家的猜拳选择"""
        display.show_info(
            f"  {player.name} 请选择（1=石头 2=剪刀 3=布）：")
        while True:
            choice = input(f"  [{player.name}] > ").strip()
            if choice in ("1", "2", "3"):
                return int(choice) - 1
            display.show_info("  请输入 1、2 或 3。")

    # ============================================
    #  结界内行动回合
    # ============================================

    def _execute_barrier_action(self, actor):
        """在结界内执行一次行动回合"""
        # 发动者免疫控制：清除所有控制状态
        if actor.player_id == self.player_id:
            if actor.is_stunned:
                actor.is_stunned = False
                self.state.markers.on_stun_recover(actor.player_id)
                display.show_info(f"  🌀 {actor.name} 免疫控制，眩晕解除！")
            if hasattr(actor, 'is_shocked') and actor.is_shocked:
                actor.is_shocked = False
                self.state.markers.on_shock_recover(actor.player_id)
                display.show_info(f"  🌀 {actor.name} 免疫控制，震荡解除！")
            if hasattr(actor, 'is_petrified') and actor.is_petrified:
                actor.is_petrified = False
                self.state.markers.on_petrify_recover(actor.player_id)
                display.show_info(f"  🌀 {actor.name} 免疫控制，石化解除！")

        # 检查非发动者是否能行动
        if actor.player_id != self.player_id:
            if actor.is_stunned:
                display.show_info(f"  💫 {actor.name} 处于眩晕状态，跳过行动。")
                return
            if hasattr(actor, 'is_shocked') and actor.is_shocked:
                display.show_info(f"  ⚡ {actor.name} 处于震荡状态，跳过行动。")
                return
            if hasattr(actor, 'is_petrified') and actor.is_petrified:
                display.show_info(f"  🗿 {actor.name} 处于石化状态，跳过行动。")
                return

        # 调用简化行动回合
        atm = ActionTurnManager(self.state)
        atm.execute_single_action(actor)
    def _enforce_barrier_face_to_face(self):
        """每轮结束后重新强制面对面"""
        alive = self._get_alive_barrier_players()
        if len(alive) != 2:
            return
        p1, p2 = alive
        # 检查是否同地点（结界内可以移动）
        pl1 = self.state.get_player(p1)
        pl2 = self.state.get_player(p2)
        if pl1.location == pl2.location:
            if not self.state.markers.has_relation(p1, "ENGAGED_WITH", p2):
                self.state.markers.set_engaged(p1, p2)
                display.show_info(
                    f"  🌀 {pl1.name} 与 {pl2.name} 重新进入面对面。")

    # ============================================
    #  退出条件检查
    # ============================================

    def _check_exit(self):
        """检查结界是否应该结束"""
        alive = self._get_alive_barrier_players()

        # 条件1：仅剩1人存活
        if len(alive) <= 1:
            if alive:
                name = self.state.get_player(alive[0]).name
                display.show_info(f"🌀 结界内仅剩 {name} 存活，结界即将解除。")
            else:
                display.show_info(f"🌀 结界内无人存活，结界即将解除。")
            return True

        # 条件2：达到最大轮次
        if self.barrier_round >= self.max_barrier_rounds:
            display.show_info(
                f"🌀 结界已持续 {self.max_barrier_rounds} 轮，结界即将解除。")
            return True

        return False

    # ============================================
    #  结界结束
    # ============================================

    def _end_barrier(self):
        """结界解除，恢复常规流程"""
        lines = [
            f"\n{'='*60}",
            f"  🌀 「神话之外」结界解除！",
        ]

        # 存活玩家回到原地点
        alive = self._get_alive_barrier_players()
        for pid in alive:
            p = self.state.get_player(pid)
            original = self.original_locations.get(pid)
            if p and original:
                p.location = original
                lines.append(
                    f"  📍 {p.name} 返回原地点：{original}")

        # 清除面对面（结界产生的）
        if len(self.barrier_players) == 2:
            p1, p2 = self.barrier_players
            self.state.markers.disengage(p1, p2)
            lines.append(f"  📎 结界内面对面关系解除。")

        lines.extend([
            f"  ▶️  全局轮次恢复！",
            f"{'='*60}",
        ])
        display.show_info("\n".join(lines))

        # 重置状态
        self.active = False
        self.barrier_players = []
        self.original_locations = {}
        self.barrier_round = 0
        self.barrier_location = None

        # 清除全局标记
        self.state.active_barrier = None

    # ============================================
    #  辅助方法
    # ============================================

    def _get_alive_barrier_players(self):
        """获取结界内存活玩家ID列表"""
        alive = []
        for pid in self.barrier_players:
            p = self.state.get_player(pid)
            if p and p.is_alive():
                alive.append(pid)
        return alive

    def _player_names(self):
        """结界内玩家名字列表"""
        names = []
        for pid in self.barrier_players:
            p = self.state.get_player(pid)
            names.append(p.name if p else pid)
        return "、".join(names)

    # ============================================
    #  查询接口（供validator调用）
    # ============================================

    def is_in_barrier(self, player_id):
        """某玩家是否在结界内"""
        if not self.active:
            return False
        return player_id in self.barrier_players

    def is_action_blocked(self, action_type):
        """某行动类型是否被结界禁止"""
        if not self.active:
            return False, ""
        if action_type in self.BLOCKED_ACTIONS:
            return True, "结界内无法与地点交互！"
        return False, ""

    def is_liuyao_blocked(self, player_id):
        """六爻的解除锁定/发现是否被结界屏蔽"""
        if not self.active:
            return False
        return player_id in self.barrier_players

    def is_caster_immune_to_control(self, player_id):
        """发动者是否免疫控制"""
        if not self.active:
            return False
        return player_id == self.player_id

    def is_stealth_blocked_in_barrier(self, player_id):
        """结界内隐身是否无效"""
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
