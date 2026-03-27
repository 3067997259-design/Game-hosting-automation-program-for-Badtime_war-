"""轮次调度器（Phase 4 完整版）：天赋钩子+响应窗口+额外行动回合"""

from utils.dice import roll_d4, roll_d6
from cli import display
from engine.action_turn import ActionTurnManager
from engine.police_system import PoliceEngine


class RoundManager:
    def __init__(self, game_state):
        self.state = game_state
        self.turn_manager = ActionTurnManager(game_state)
        self.police_engine = PoliceEngine(game_state)
        self.state.police_engine = self.police_engine

    def run_game_loop(self):
        while not self.state.game_over:
            self.run_one_round()
            winner_id = self.state.check_victory()
            if winner_id:
                self.state.game_over = True
                self.state.winner = winner_id
                if winner_id == "nobody":
                    display.show_info("所有玩家都已死亡……无人获胜。")
                else:
                    winner = self.state.get_player(winner_id)
                    display.show_victory(winner.name if winner else winner_id)
                return

            # 最大轮数安全网
            if self.state.is_max_rounds_reached():
                self.state.game_over = True
                self.state.winner = "nobody"
                display.show_info(
                    f"⚠️ 达到最大轮数限制（{self.state.max_rounds}轮），游戏判定平局。")
                return

    def run_one_round(self):
        self.state.current_round += 1
        display.show_round_header(self.state.current_round)
        for p in self.state.players.values():
            p.acted_this_round = False

        self._phase_r0()
        self._phase_r1()
        self._phase_r2()
        self._phase_r3()
        self._phase_r4()

    # ============================================
    # R0: 轮次开始结算
    # ============================================
    def _phase_r0(self):
        self.state.current_phase = "r0_start"
        # 天赋轮次开始钩子
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.talent:
                p.talent.on_round_start(self.state.current_round)

    # ============================================
    # R1: D4 争夺行动权
    # ============================================
    def _phase_r1(self):
        self.state.current_phase = "r1_d4"
        display.show_phase("🎲 D4 争夺行动权")

        self.state.d4_results.clear()
        self.state.d4_bonuses.clear()
        self.state.round_winners.clear()

        max_val = 0
        results = {}
        raw = {}
        bonuses = {}

        for pid in self.state.player_order:
            player = self.state.get_player(pid)
            if not player or not player.is_alive():
                continue
            base_roll = roll_d4()
            bonus = player.get_d4_bonus()
            final = min(base_roll + bonus, 4)
            raw[pid] = base_roll
            bonuses[pid] = bonus
            results[pid] = final
            if final > max_val:
                max_val = final

        self.state.d4_results = raw
        self.state.d4_bonuses = bonuses
        winners = [pid for pid, val in results.items() if val == max_val]
        self.state.round_winners = winners

        display.show_d4_results(
            {self.state.get_player(pid).name: raw[pid] for pid in raw},
            {self.state.get_player(pid).name: bonuses[pid] for pid in bonuses},
            [self.state.get_player(pid).name for pid in winners]
        )

    # ============================================
    # R2: 先后手判定
    # ============================================
    def _phase_r2(self):
        self.state.current_phase = "r2_priority"
        if len(self.state.round_winners) <= 1:
            return
        conflict = set()
        loc_groups = {}
        for pid in self.state.round_winners:
            p = self.state.get_player(pid)
            if p and p.location:
                loc_groups.setdefault(p.location, []).append(pid)
        for loc, pids in loc_groups.items():
            if len(pids) > 1:
                conflict.update(pids)

        if conflict:
            display.show_phase("⚔️ 先后手判定（D6）")
            rolls = {}
            for pid in conflict:
                r = roll_d6()
                rolls[pid] = r
                p = self.state.get_player(pid)
                display.show_info(f"  {p.name}: D6 = {r}")
            sorted_conflict = sorted(conflict, key=lambda x: rolls[x], reverse=True)
            non_conflict = [w for w in self.state.round_winners if w not in conflict]
            self.state.round_winners = sorted_conflict + non_conflict
        else:
            order_map = {pid: i for i, pid in enumerate(self.state.player_order)}
            self.state.round_winners.sort(key=lambda pid: order_map.get(pid, 99))

    # ============================================
    # R3: 行动阶段
    # ============================================
    def _phase_r3(self):
        self.state.current_phase = "r3_actions"
        display.show_phase("⚔️ 行动阶段")

        if not self.state.round_winners:
            display.show_info("本轮无人获得行动权。")
            return

        # 构建行动队列（可在运行中插入额外行动回合）
        action_queue = list(self.state.round_winners)

        i = 0
        while i < len(action_queue):
            actor_id = action_queue[i]
            actor = self.state.get_player(actor_id)
            if not actor or not actor.is_alive():
                i += 1
                continue

            # 执行行动回合
            action_type = self.turn_manager.execute_action_turn(actor)

            # 犯罪检测
            if action_type == "attack":
                self._check_attack_crime(actor)

            # 更新行动记录
            actor.last_action_type = action_type
            non_action_types = ("forfeit", "status", "help",
                                "police_status", "allstatus", "shock_recover")
            if action_type not in non_action_types:
                actor.acted_this_round = True
                actor.no_action_streak = 0
                actor.total_action_turns += 1

            # === 响应窗口（你给路打油）===
            if hasattr(self.state, 'response_window'):
                triggered, responder = self.state.response_window.process_after_action(
                    actor, action_type)
                if triggered and responder:
                    # 插入额外行动回合：在当前位置之后
                    action_queue.insert(i + 1, responder.player_id)
                    display.show_info(
                        f"📌 {responder.name} 的额外行动回合已插入！")

            # === 六爻额外回合（剪刀vs布）===
            if actor.hexagram_extra_turn:
                actor.hexagram_extra_turn = False
                action_queue.insert(i + 1, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的六爻额外行动回合已插入！")

            # === 犯罪触发的额外回合（不良少年等）===
            if getattr(actor, 'crime_extra_turn', False):
                actor.crime_extra_turn = False
                action_queue.insert(i + 1, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的额外行动回合已插入！")
            # === 愿负世主动发动的额外回合 ===
            if getattr(actor, 'savior_extra_turn', False):
                actor.savior_extra_turn = False
                action_queue.insert(i + 1, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的额外行动回合已插入！（主动发动）")

            # 检查胜利
            if self.state.check_victory():
                return

            i += 1

        # 未行动保底
        for pid in self.state.player_order:
            player = self.state.get_player(pid)
            if not player or not player.is_alive():
                continue
            if not player.acted_this_round:
                player.no_action_streak += 1

    def _check_attack_crime(self, attacker):
        """攻击后犯罪检测（含天赋钩子）"""
        for event in reversed(self.state.event_log):
            if (event.get("type") == "attack"
                    and event.get("attacker") == attacker.player_id
                    and event.get("round") == self.state.current_round):
                result = event.get("result", {})
                if result.get("success"):
                    # 天赋犯罪检查
                    if attacker.talent:
                        crime_result = attacker.talent.on_crime_check(
                            attacker.player_id, "伤害玩家")
                        if crime_result:
                            if crime_result.get("immune"):
                                return  # 免罪
                            if crime_result.get("extra_turn"):
                                # 不良少年额外行动
                                msg = crime_result.get("message", "")
                                if msg:
                                    display.show_info(msg)
                                # 标记需要插入额外回合
                                attacker.crime_extra_turn = True

                    self.police_engine.check_and_record_crime(
                        attacker.player_id, "伤害玩家")
                break

    # ============================================
    # R4: 轮次结束结算
    # ============================================
    def _phase_r4(self):
        self.state.current_phase = "r4_end"
        display.show_phase("📋 轮次结束结算")

        # R4-1: 警察执法
        police_msgs = self.police_engine.process_end_of_round()
        if police_msgs:
            display.show_police_enforcement(police_msgs)
        if self.state.check_victory():
            return

        # R4-2: 病毒
        if self.state.virus.is_active:
            is_lethal = self.state.virus.tick()
            display.show_virus_status(self.state)
            if is_lethal:
                dead = self.state.virus.get_dead_players(
                    list(self.state.players.values()))
                if dead:
                    display.show_virus_deaths(dead)
                    for p in dead:
                        # 天赋死亡检查
                        prevented = False
                        if p.talent:
                            dr = p.talent.on_death_check(p, None)
                            if dr and dr.get("prevent_death"):
                                p.hp = dr.get("new_hp", 0.5)
                                prevented = True
                        if not prevented:
                            # 其他玩家天赋检查（死者苏生）
                            for pid2 in self.state.player_order:
                                p2 = self.state.get_player(pid2)
                                if p2 and p2.talent and p2.player_id != p.player_id:
                                    dr = p2.talent.on_death_check(p, None)
                                    if dr and dr.get("prevent_death"):
                                        p.hp = dr.get("new_hp", 0.5)
                                        prevented = True
                                        break
                        if not prevented:
                            p.hp = 0
                            self.state.markers.on_player_death(p.player_id)
                            if self.state.police_engine:
                                self.state.police_engine.on_player_death(p.player_id)
                            self.state.log_event("death", player=p.player_id, cause="virus")
        if self.state.check_victory():
            return

        # R4-3: 天赋轮次结束钩子
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.talent:
                p.talent.on_round_end(self.state.current_round)
