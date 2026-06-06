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

        # 诊断：通知所有 controller 记录轮次快照
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and hasattr(p.controller, '_diag') and p.controller._diag:
                p.controller._diag.record_round_snapshot(
                    self.state.current_round, self.state
                )

    # ============================================
    # R0: 轮次开始结算
    # ============================================
    def _phase_r0(self):
        self.state.current_phase = "r0_start"
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p:
                p._armor_gained_this_round = False

        # ish-bosheth 废墟谢幕（pending_curtain → 清理）
        if (self.state.ish_bosheth
                and self.state.ish_bosheth.phase == "pending_curtain"):
            self.state.ish_bosheth.on_r0_curtain(self.state)

        # v2.0: duet 模式按钮刷新
        ish = self.state.ish_bosheth
        if ish and ish.phase == "duet" and not ish.duet_curtain_triggered:
            ish._spawn_duet_buttons(self.state)

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

        # Chorus 参与 D4
        if self.state.ish_bosheth and self.state.ish_bosheth.phase in ("active", "duet"):
            for c in self.state.ish_bosheth.chorus_list:
                if c.is_alive() and c.location:
                    base_roll = roll_d4()
                    final = min(base_roll, 4)  # Chorus 无 D4 加成
                    raw[c.player_id] = base_roll
                    bonuses[c.player_id] = 0
                    results[c.player_id] = final
                    if final > max_val:
                        max_val = final

        # v0.6 ish-bosheth/duet 模式：G2 固定 D4=0，所有人按 D4 排序，全部行动
        if self.state.ish_bosheth and self.state.ish_bosheth.phase in ("active", "duet"):
            g2_pid = self.state.ish_bosheth.g2_owner_id
            raw[g2_pid] = 0
            bonuses[g2_pid] = 0
            results[g2_pid] = 0

        self.state.d4_results = raw
        self.state.d4_bonuses = bonuses

        # ish-bosheth/duet 活跃 → 所有参与者按 D4 排序，全部获得行动权
        if self.state.ish_bosheth and self.state.ish_bosheth.phase in ("active", "duet"):
            sorted_pids = sorted(results.keys(),
                                 key=lambda pid: results[pid], reverse=True)
            self.state.round_winners = sorted_pids
        else:
            winners = [pid for pid, val in results.items() if val == max_val]
            self.state.round_winners = winners

        # 构建 pid → name 映射（含 Chorus）
        pid_to_name = {}
        for pid in raw:
            p = self.state.get_player(pid)
            if p:
                pid_to_name[pid] = p.name
            elif self.state.ish_bosheth:
                for c in self.state.ish_bosheth.chorus_list:
                    if c.player_id == pid:
                        pid_to_name[pid] = c.name
                        break
        display_names = {pid_to_name[pid]: raw[pid] for pid in raw}
        bonus_names = {pid_to_name[pid]: bonuses[pid] for pid in raw}
        winner_names = [pid_to_name[pid] for pid in self.state.round_winners]
        display.show_d4_results(display_names, bonus_names, winner_names)

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
                p = self.state.get_player(pid)
                base_roll = roll_d6()
                bonus = p.get_d6_bonus()
                r = max(base_roll + bonus, 1)
                rolls[pid] = r
                if bonus != 0:
                    display.show_info(f"  {p.name}: D6 = {base_roll} {'+' if bonus >= 0 else ''}{bonus} → {r}")
                else:
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

        # v0.6 ish-bosheth: G2 保底最优先行动
        ish = self.state.ish_bosheth
        if ish and ish.phase in ("active", "duet"):
            g2_pid = ish.g2_owner_id
            if g2_pid in action_queue:
                action_queue.remove(g2_pid)
            action_queue.insert(0, g2_pid)
            display.show_info(f"🎵 G2 优先演唱回合")

        i = 0
        while i < len(action_queue):
            actor_id = action_queue[i]
            actor = self.state.get_player(actor_id)
            if not actor or not actor.is_alive():
                i += 1
                continue

            # 执行行动回合
            action_type = self.turn_manager.execute_action_turn(actor)

            # 犯罪检测（攻击和特殊行动都可能包含攻击）
            self._check_attack_crime(actor)

            # 更新行动记录
            actor.last_action_type = action_type
            non_action_types = ("status", "help",
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
            if getattr(actor, 'hexagram_extra_turn', 0) > 0:
                # V1.92: 支持连续多个额外行动回合
                turns_to_add = actor.hexagram_extra_turn
                actor.hexagram_extra_turn = 0
                for t_idx in range(turns_to_add):
                    action_queue.insert(i + 1 + t_idx, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的六爻额外行动回合已插入（{turns_to_add}个）！")

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

            # === G2 聚光灯额外行动回合 ===
            spotlight_target = getattr(actor, '_g2_spotlight_target_id', None)
            if spotlight_target:
                actor._g2_spotlight_target_id = None
                action_queue.insert(i + 1, spotlight_target)
                target = self.state.get_player(spotlight_target)
                tname = target.name if target else spotlight_target
                display.show_info(
                    f"📌 {tname} 的聚光灯额外行动回合已插入！")

            # === G2 聚光合影额外回合 ===
            photo_invitee = getattr(actor, '_photo_invitee_id', None)
            if photo_invitee:
                actor._photo_invitee_id = None
                action_queue.insert(i + 1, photo_invitee)
                invitee = self.state.get_player(photo_invitee)
                tname2 = invitee.name if invitee else photo_invitee
                display.show_info(
                    f"📸 {tname2} 的合影额外行动回合已插入！")

            # === 星野临战-Archer 起床额外回合 ===
            if getattr(actor, 'hoshino_wakeup_extra_turn', False):
                actor.hoshino_wakeup_extra_turn = False
                action_queue.insert(i + 1, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的额外行动回合已插入！（临战-Archer起床加成）")

            # === 剪刀手一突·警觉额外回合（扫描所有玩家，支持被动方即时插入）===
            for pid in self.state.player_order:
                p = self.state.get_player(pid)
                if p and p.is_alive() and getattr(p, 'vigilance_extra_turn', False):
                    p.vigilance_extra_turn = False
                    action_queue.insert(i + 1, p.player_id)
                    display.show_info(
                        f"📌 {p.name} 的额外行动回合已插入！（警觉）")

            # 检查胜利
            if self.state.check_victory():
                return

            i += 1

        # v2.0: duet 按钮清理
        ish = self.state.ish_bosheth
        if ish and ish.phase == "duet":
            ish._despawn_duet_buttons(self.state)

        # 未行动保底
        initial_count = len(self.state.player_order)
        alive_count = len([pid for pid in self.state.player_order
                   if self.state.get_player(pid) and self.state.get_player(pid).is_alive()])
        for pid in self.state.player_order:
            player = self.state.get_player(pid)
            if not player or not player.is_alive():
                continue
            if not player.acted_this_round:
                # 开局>3人且仅剩2人时，保底失效
                if initial_count > 3 and alive_count <= 2:
                    player.no_action_streak = 0
                else:
                    player.no_action_streak += 1

    # 已知造成伤害的天赋攻击事件类型（非标准 "attack" 事件）
    _TALENT_DAMAGE_EVENT_TYPES = frozenset({
        "oneslash_attack",   # T1 一刀缭断
        "star_attack",       # T3 天星
        "firefly_kill",      # G1 火萤击杀
        "firefly_supernova", # G1 超新星过载
    })

    def _check_attack_crime(self, attacker):
        """攻击后犯罪检测（含天赋钩子及各类天赋攻击事件）"""
        for event in reversed(self.state.event_log):
            # 匹配标准攻击事件（attacker字段）或天赋攻击事件（player字段）
            is_my_event = (
                (event.get("attacker") == attacker.player_id)
                or (event.get("player") == attacker.player_id)
            )
            if not is_my_event:
                continue
            if event.get("round") != self.state.current_round:
                continue

            # 判定是否造成了伤害/击杀
            caused_damage = False
            etype = event.get("type")
            if etype == "attack":
                caused_damage = event.get("result", {}).get("success", False)
            elif etype in self._TALENT_DAMAGE_EVENT_TYPES:
                caused_damage = True  # 这些天赋必然造成伤害
            elif etype == "ripple_poem":
                # 只有「爱与记忆」之诗造成伤害，其他诗篇是 buff
                caused_damage = (event.get("poem_type") == "爱与记忆")
            else:
                # 其他事件：检查 killed/damage/final_damage 字段
                caused_damage = (
                    event.get("killed", False)
                    or event.get("damage", 0) > 0
                    or event.get("final_damage", 0) > 0
                )

            if caused_damage:
                # 天赋犯罪检查
                if attacker.talent:
                    crime_result = attacker.talent.on_crime_check(
                        attacker.player_id, "伤害玩家")
                    if crime_result:
                        if crime_result.get("immune"):
                            return  # 免罪
                        if crime_result.get("extra_turn"):
                            msg = crime_result.get("message", "")
                            if msg:
                                display.show_info(msg)
                            attacker.crime_extra_turn = True

                self.police_engine.check_and_record_crime(
                    attacker.player_id, "伤害玩家")
            break

    @staticmethod
    def notify_all_talents_of_death(game_state, victim_id, killer_id=None):
        """
        通知所有存活玩家的天赋：有玩家死亡。
        用于星野色彩计数等需要全局死亡通知的机制。
        """
        for pid in game_state.player_order:
            p = game_state.get_player(pid)
            if p and p.is_alive() and p.talent:
                if hasattr(p.talent, '_on_any_player_death'):
                    p.talent._on_any_player_death(victim_id, killer_id)

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

        # R4-1.5: 火萤灼烧结算（与警察同时机）
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.talent:
                if hasattr(p.talent, 'process_burn_damage'):
                    p.talent.process_burn_damage(self.state.current_round)
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
                        # 六爻·元亨利贞：免疫病毒致死（最先检查，避免浪费死亡预防能力）
                        if p.talent and hasattr(p.talent, 'is_immune_to_damage') and p.talent.is_immune_to_damage("病毒"):
                            display.show_info(f"☯️ {p.name} 的「元亨利贞」免疫了病毒致死判定！")
                            continue
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
                            RoundManager.notify_all_talents_of_death(
                                self.state, p.player_id, killer_id=None)
        if self.state.check_victory():
            return

        # R4-2.5: ish-bosheth R4 衰减
        if (self.state.ish_bosheth
                and self.state.ish_bosheth.phase in ("active", "duet")):
            self.state.ish_bosheth.on_r4(self.state)
        if self.state.check_victory():
            return

        # R4-3: 天赋轮次结束钩子
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.talent:
                p.talent.on_round_end(self.state.current_round)
        # R4-4: 星野架盾 cost 扣除（README: "位于R4所有检查之后"）
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.talent and hasattr(p.talent, '_r4_shield_cost_check'):
                p.talent._r4_shield_cost_check()
        if self.state.check_victory():
            return
