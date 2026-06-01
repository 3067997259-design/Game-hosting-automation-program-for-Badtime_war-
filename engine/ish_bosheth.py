"""engine/ish_bosheth.py
ish-bosheth 舞台结界状态管理器（G2 Reset 核心）

负责：
- 结界开启 / R4 衰减 / R0 废墟谢幕 / 统一清理
- 情绪状态机（adjust_emotion_up / down / ma_non_troppo）
- 旋律（Melody）链式伤害
- 曲目执行辅助
"""

from __future__ import annotations
import random
from typing import TYPE_CHECKING, List, Optional, Set

from cli import display
from engine.prompt_manager import prompt_manager
from models.chorus import ChorusUnit

if TYPE_CHECKING:
    from engine.game_state import GameState
    from models.player import Player

# ── 情绪常量 ──────────────────────────────────────────────────────
ACCAREZZEVOLE = "accarezzevole"   # 入戏
INDIFFERENZA  = "indifferenza"    # 抽离
STRAPPANDO    = "strappando"      # 反抗

EMOTION_ORDER = [STRAPPANDO, INDIFFERENZA, ACCAREZZEVOLE]  # 低→高

EMOTION_LABELS = {
    ACCAREZZEVOLE: "入戏 (Accarezzevole)",
    INDIFFERENZA:  "抽离 (Indifferenza)",
    STRAPPANDO:    "反抗 (Strappando)",
}

# ── 结束原因 ──────────────────────────────────────────────────────
END_BREAK       = "break"
END_EMPTY       = "empty"
END_CURTAIN     = "curtain"
END_MAX_DURATION = "max_duration"
END_FORCED      = "forced"
END_DEATH       = "death"


class IshBosheth:
    """舞台结界实例。同一时间最多一个。"""

    def __init__(self, g2_owner_id: str):
        self.g2_owner_id: str = g2_owner_id
        self.g2_home: str = f"home_{g2_owner_id}"

        self.regard: float = 0.0
        self.regard_cap: float = 8.0
        self.r4_count: int = 0
        self.phase: str = "active"   # "active" / "pending_curtain" / "ended"

        self.chorus_list: List[ChorusUnit] = []
        self.submerged_list: list = []    # 被冻结的警察单位
        self.seat_assignments: dict = {}  # pid/chorus_id → seat location name

        self.before_light: Optional[str] = None   # "riposato" / "dolente" / None

        self.melody_2_unlocked: bool = False
        self.melody_2_used: bool = False
        self.melody_3_unlocked: bool = False
        self.melody_3_used: bool = False

        self.participants: Set[str] = set()  # 在场真实玩家 pid

    # ================================================================
    #  展开
    # ================================================================
    def open(self, game_state: GameState, g2_player: Player):
        """展开分布式 ish-bosheth：G2 回家，其他玩家分配座位，空位填 Chorus。"""
        lines: list[str] = []

        # 1. G2 传送回家（舞台中心）
        g2_player.location = self.g2_home
        lines.append(f"  🏠 {g2_player.name} 的家成为舞台中心。")

        # 2. 收集参与者（排除 G3 结界内、排除已死亡）
        g3_inside: set[str] = set()
        if game_state.active_barrier:
            barrier = game_state.active_barrier
            if hasattr(barrier, 'inside_players'):
                g3_inside = set(barrier.inside_players)

        for pid in game_state.player_order:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            if pid in g3_inside:
                lines.append(f"  {p.name} 身处幻想乡结界，未参与舞台。")
                continue
            self.participants.add(pid)

        # 3. 解除隐身
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p and p.is_invisible:
                p.is_invisible = False
                lines.append(f"  {p.name} 的隐身被解除。")
        if g2_player.is_invisible:
            g2_player.is_invisible = False
            lines.append(f"  {g2_player.name} 的隐身被解除。")

        # 4. 清除即时关系
        all_pids_in_stage = list(self.participants) + [self.g2_owner_id]
        for pid in all_pids_in_stage:
            game_state.markers.clear_all_relations(pid)

        # 5. 星野架盾/持盾终止
        for pid in all_pids_in_stage:
            p = game_state.get_player(pid)
            if p and p.talent and hasattr(p.talent, 'shield_mode') and p.talent.shield_mode:
                if hasattr(p.talent, '_end_shield_mode'):
                    p.talent._end_shield_mode(p)
                    lines.append(f"  {p.name} 的架盾/持盾状态被终止。")

        # 6. 座位分配
        seat_lines = self._assign_seats(game_state, g2_player)
        lines.extend(seat_lines)

        # 6.5. Submerge 非玩家单位（原地冻结，不拉人）
        if hasattr(game_state, 'police') and game_state.police:
            for unit in game_state.police.units:
                if unit.is_alive() and unit.is_on_map():
                    unit.is_submerged = True
                    self.submerged_list.append(unit)
            if self.submerged_list:
                lines.append(prompt_manager.get_prompt(
                    "g2reset", "stage.submerge_count",
                    count=len(self.submerged_list)))

        # 7. liberamente_vivace
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p:
                p.stage_statuses = getattr(p, 'stage_statuses', set())
                p.stage_statuses.add("liberamente_vivace")
        g2_player.stage_statuses = getattr(g2_player, 'stage_statuses', set())
        g2_player.stage_statuses.add("liberamente_vivace")

        # 8. 真实观众选择情绪
        from controllers.human import HumanController
        for pid in self.participants:
            p = game_state.get_player(pid)
            if not p:
                continue
            if isinstance(p.controller, HumanController):
                display.show_info(
                    f"\n{'='*50}\n"
                    f"  🎭 ish-bosheth 展开！请将屏幕交给 {p.name}\n"
                    f"{'='*50}")
                input(f"  [仅 {p.name} 可看] 按回车选择初始情绪...")
            emotion = p.controller.choose(
                "选择你在 ish-bosheth 舞台中的初始情绪：",
                ["入戏 (Accarezzevole)", "抽离 (Indifferenza)", "反抗 (Strappando)"],
                context={"phase": "T0", "situation": "g2_emotion_choice"},
            )
            p.emotion = self._parse_emotion_choice(emotion)
            lines.append(f"  {p.name} 选择了 {EMOTION_LABELS.get(p.emotion, p.emotion)}。")

        # 9. ma non troppo 开场校正
        self.ma_non_troppo(game_state)

        # 11. 初始 Regard（旋律延后到 execute_t0 中座位展示之后触发）
        P = len(self.participants)       # 真实观众
        C = len(self.chorus_list)         # Chorus
        N = len(self.submerged_list)      # Submerged 非玩家单位
        self.regard = max(4.0, min(8.0, 3.0 + P + 0.5 * C + 0.5 * N))
        lines.append(f"  初始 Regard: {self.regard}/{self.regard_cap}")

        game_state.ish_bosheth = self
        return lines

    # ================================================================
    #  座位分配
    # ================================================================
    SEATS = {"商店", "魔法所", "警察局", "医院", "军事基地"}

    def _assign_seats(self, game_state, g2_player) -> list[str]:
        """将参与者分配到 5 个座位，空位填 Chorus。返回 display lines。"""
        lines: list[str] = []
        from controllers.chorus_controller import ChorusController

        seats = sorted(self.SEATS - {g2_player.location})  # G2 的家不算座位

        assigned: dict[str, list] = {}  # seat → [units]
        unassigned = []

        for pid in self.participants:
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            loc = p.location
            if loc and loc in seats and loc not in assigned:
                assigned[loc] = [p]
            else:
                unassigned.append(p)

        empty_seats = [s for s in seats if s not in assigned]
        for p in unassigned:
            old_loc = p.location
            if empty_seats:
                seat = random.choice(empty_seats)
                assigned[seat] = [p]
                empty_seats.remove(seat)
            else:
                seat = random.choice(seats)
                assigned.setdefault(seat, []).append(p)
            # 移动到分配座位（触发 on_player_move 清理旧关系）
            if p.location != seat:
                p.location = seat
                game_state.markers.on_player_move(p.player_id)

        # 空座位填充 Chorus（立即分配随机情绪）
        for seat in empty_seats:
            c = ChorusUnit()
            c.location = seat
            c.controller = ChorusController()
            c.emotion = random.choice(EMOTION_ORDER)
            self.chorus_list.append(c)
            game_state.markers.register_unit(c.player_id)
            game_state.register_chorus(c)
            assigned.setdefault(seat, []).append(c)

        # 记录座位分配（含情绪标签）
        for seat, units in assigned.items():
            names = []
            for u in units:
                pid = getattr(u, 'player_id', None)
                if pid:
                    self.seat_assignments[pid] = seat
                # Chorus 此时已有随机情绪，玩家情绪待选
                label = getattr(u, 'name', '?')
                if getattr(u, 'is_chorus', False) and u.emotion:
                    label += f"[{EMOTION_LABELS.get(u.emotion, '?')}]"
                names.append(label)
            lines.append(f"  🪑 {seat}: {', '.join(names)}")

        return lines

    # ================================================================
    #  R4 衰减
    # ================================================================
    def on_r4(self, game_state: GameState):
        if self.phase != "active":
            return

        # 空场检查
        remaining_real = [pid for pid in self.participants
                          if pid != self.g2_owner_id
                          and game_state.get_player(pid)
                          and game_state.get_player(pid).is_alive()]
        if not remaining_real:
            self.end_ish_bosheth(END_EMPTY, game_state)
            return

        self.ma_non_troppo(game_state)

        # Regard 衰减
        self.regard -= 1.0

        # 入戏贡献
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if p and p.is_alive() and getattr(p, 'emotion', None) == ACCAREZZEVOLE:
                self.regard += 0.5
        for c in self.chorus_list:
            if c.is_alive() and c.emotion == ACCAREZZEVOLE:
                self.regard += 0.25

        # 反抗消耗
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if p and p.is_alive() and getattr(p, 'emotion', None) == STRAPPANDO:
                self.regard -= 0.5
        for c in self.chorus_list:
            if c.is_alive() and c.emotion == STRAPPANDO:
                self.regard -= 0.25

        self.regard = max(0, min(self.regard, self.regard_cap))
        self.r4_count += 1

        # 旋律解锁
        if self.r4_count >= 3:
            self.melody_2_unlocked = True
        if self.r4_count >= 6:
            self.melody_3_unlocked = True

        # 谢幕检查
        if self.regard <= 0:
            self.phase = "pending_curtain"
            prompt_manager.show("g2reset", "stage.regard_zero")
        elif self.r4_count >= 8:
            self.phase = "pending_curtain"
            prompt_manager.show("g2reset", "stage.max_duration")

        # 清除上一轮授予的聚光灯 + Sognando 锁 + 临时增益
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p and "spotlight" in getattr(p, 'stage_statuses', set()):
                granted_r4 = getattr(p, '_spotlight_granted_r4', -1)
                if granted_r4 < self.r4_count:  # 本轮之前授予的
                    p.stage_statuses.discard("spotlight")
                    p.stage_statuses.discard("sognando_lock")
                    p.temp_hp_g2 = 0.0
                    p.temp_atk_g2 = 0.0
        for c in self.chorus_list:
            if "spotlight" in getattr(c, 'stage_statuses', set()):
                granted_r4 = getattr(c, '_spotlight_granted_r4', -1)
                if granted_r4 < self.r4_count:
                    c.stage_statuses.discard("spotlight")
                    c.stage_statuses.discard("sognando_lock")
                    c.temp_hp_g2 = 0.0
                    c.temp_atk_g2 = 0.0
        self.before_light = None

    # ================================================================
    #  R0 废墟谢幕
    # ================================================================
    def on_r0_curtain(self, game_state: GameState):
        """在 round_manager._phase_r0 中检查并调用。"""
        if self.phase != "pending_curtain":
            return

        # 1. 有聚光灯/安可的非G2观众受 0.5 无视属性克制伤害
        for pid in list(self.participants):
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            ss = getattr(p, 'stage_statuses', set())
            if "spotlight" in ss or getattr(p, 'encore_layers', 0) > 0:
                p.hp = round(max(0, p.hp - 0.5), 2)
                prompt_manager.show("g2reset", "stage.curtain_damage",
                                   player_name=p.name, hp=p.hp)

        # 2. 所有 Accarezzevole 下调一级
        for pid in list(self.participants):
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if p and p.is_alive() and getattr(p, 'emotion', None) == ACCAREZZEVOLE:
                adjust_emotion_down(p, game_state, self)
        for c in self.chorus_list:
            if c.is_alive() and c.emotion == ACCAREZZEVOLE:
                adjust_emotion_down(c, game_state, self)

        # 3/4. Strappando engage 检查
        has_strappando_engage = False
        g2p = game_state.get_player(self.g2_owner_id)
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if (p and p.is_alive()
                    and getattr(p, 'emotion', None) == STRAPPANDO
                    and game_state.markers.has_relation(
                        pid, "ENGAGED_WITH", self.g2_owner_id)):
                has_strappando_engage = True
                # D4+1 奖励标记
                p._g2_curtain_d4_bonus = True
                display.show_info(
                    f"  {p.name} 与 G2 面对面，获得下轮 D4+1。")

        if not has_strappando_engage and g2p and g2p.is_alive():
            game_state.markers.clear_all_relations(self.g2_owner_id)
            g2p.is_invisible = True
            prompt_manager.show("g2reset", "stage.g2_invisible", player_name=g2p.name)

        self.end_ish_bosheth(END_CURTAIN, game_state)

    # ================================================================
    #  统一清理
    # ================================================================
    def end_ish_bosheth(self, reason: str, game_state: GameState,
                        breaker_id: Optional[str] = None):
        g2p = game_state.get_player(self.g2_owner_id)

        display.show_info(f"\n{'='*50}")
        prompt_manager.show("g2reset", "stage.end_header", reason=reason)
        display.show_info(f"{'='*50}")

        # 破幕特殊处理：给破幕者 D4/D6 +1
        if reason == END_BREAK and breaker_id:
            breaker = game_state.get_player(breaker_id)
            if breaker:
                breaker._g2_curtain_d4_bonus = True
                prompt_manager.show("g2reset", "stage.break_reward",
                                   player_name=breaker.name)

        # 空场退场
        if reason == END_EMPTY and g2p and g2p.is_alive():
            game_state.markers.clear_all_relations(self.g2_owner_id)
            g2p.is_invisible = True
            prompt_manager.show("g2reset", "stage.g2_empty_leave",
                               player_name=g2p.name)

        # 通用清理：参与者
        for pid in list(self.participants):
            p = game_state.get_player(pid)
            if p:
                p.emotion = None
                if hasattr(p, 'stage_statuses'):
                    p.stage_statuses.clear()
                p.encore_layers = 0
                if hasattr(p, 'stage_entangle'):
                    p.stage_entangle.clear()
                p.temp_hp_g2 = 0.0
                p.temp_atk_g2 = 0.0

        # G2 发动者清理
        if g2p:
            if hasattr(g2p, 'stage_statuses'):
                g2p.stage_statuses.clear()
            g2p.emotion = None
            g2p.encore_layers = 0
            if hasattr(g2p, 'stage_entangle'):
                g2p.stage_entangle.clear()
            g2p.temp_hp_g2 = 0.0
            g2p.temp_atk_g2 = 0.0

        # Chorus 消散
        for c in self.chorus_list:
            game_state.unregister_chorus(c.player_id)
        self.chorus_list.clear()

        # Submerged 解除（原地解冻，无需位置恢复）
        for unit in self.submerged_list:
            unit.is_submerged = False
        self.submerged_list.clear()

        self.phase = "ended"
        game_state.ish_bosheth = None

    # ================================================================
    #  情绪状态机
    # ================================================================
    @staticmethod
    def _parse_emotion_choice(choice_str: str) -> str:
        if not choice_str:
            return INDIFFERENZA
        low = choice_str.lower()
        if "入戏" in low or "accarezzevole" in low:
            return ACCAREZZEVOLE
        if "反抗" in low or "strappando" in low:
            return STRAPPANDO
        return INDIFFERENZA

    # ================================================================
    #  ma non troppo 校正
    # ================================================================
    def ma_non_troppo(self, game_state: GameState):
        real_observers: list[Player] = []
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if p and p.is_alive():
                real_observers.append(p)

        R = len(real_observers)
        if R >= 3:
            emotions_present = set(p.emotion for p in real_observers if p.emotion)
            missing = {ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO} - emotions_present
            if missing:
                from collections import Counter
                emotion_counts = Counter(p.emotion for p in real_observers if p.emotion)
                if emotion_counts:
                    most_common_emotion = emotion_counts.most_common(1)[0][0]
                    candidates = [p for p in real_observers
                                  if p.emotion == most_common_emotion
                                  and "moderation_lock" not in getattr(p, 'stage_statuses', set())]
                    if candidates:
                        chosen = random.choice(candidates)
                        target_emotion = missing.pop()
                        old_emotion = chosen.emotion
                        chosen.emotion = target_emotion
                        chosen.stage_statuses = getattr(chosen, 'stage_statuses', set())
                        chosen.stage_statuses.add("moderation_lock")
                        prompt_manager.show("g2reset", "ma_non_troppo.corrected",
                                           player_name=chosen.name,
                                           old_emotion=EMOTION_LABELS.get(old_emotion, '?'),
                                           new_emotion=EMOTION_LABELS.get(target_emotion, '?'))
        elif R == 2:
            if real_observers[0].emotion and real_observers[0].emotion == real_observers[1].emotion:
                target = random.choice(real_observers)
                old = target.emotion
                idx = EMOTION_ORDER.index(old) if old in EMOTION_ORDER else 1
                new_idx = (idx + 1) % 3
                target.emotion = EMOTION_ORDER[new_idx]
                target.stage_statuses = getattr(target, 'stage_statuses', set())
                target.stage_statuses.add("moderation_lock")
                prompt_manager.show("g2reset", "ma_non_troppo.corrected",
                                   player_name=target.name,
                                   old_emotion=EMOTION_LABELS.get(old, '?'),
                                   new_emotion=EMOTION_LABELS.get(target.emotion, '?'))

        # Chorus 补位（确保 Chorus 中也覆盖缺失情绪）
        alive_chorus = [c for c in self.chorus_list if c.is_alive()]
        if alive_chorus:
            all_emotions = set()
            for p in real_observers:
                if p.emotion:
                    all_emotions.add(p.emotion)
            for c in alive_chorus:
                if c.emotion:
                    all_emotions.add(c.emotion)
            missing_c = {ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO} - all_emotions
            for em in missing_c:
                candidates_c = [c for c in alive_chorus if c.emotion != em]
                if candidates_c:
                    c = random.choice(candidates_c)
                    c.emotion = em

    # ================================================================
    #  曲目辅助（Phase 3 补充）
    # ================================================================
    def get_available_songs(self) -> list[dict]:
        """返回当前可用曲目列表。"""
        songs = []
        if self.regard >= 1:
            songs.append({
                "name": "追寻那道光",
                "cost": 1,
                "desc": "聚光灯+额外行动",
                "rhythms": self._get_rhythms_for_song("追寻那道光"),
            })
            songs.append({
                "name": "拼接遗憾",
                "cost": 1,
                "desc": "安可（阻止离场）",
                "rhythms": self._get_rhythms_for_song("拼接遗憾"),
            })
            songs.append({
                "name": "Before light",
                "cost": 1,
                "desc": "光色修改全场伤害",
                "rhythms": self._get_rhythms_for_song("Before light"),
            })
        # 旋律（不消耗 Regard）
        if self.melody_2_unlocked and not self.melody_2_used:
            songs.append({
                "name": "旋律·第二间章",
                "cost": 0,
                "desc": "链式伤害 1/1/0.5/0.5",
                "rhythms": [{"name": "第二间章", "cost": 0}],
            })
        if self.melody_3_unlocked and not self.melody_3_used:
            songs.append({
                "name": "旋律·第三间章",
                "cost": 0,
                "desc": "链式伤害 1/1/0.5/0.5",
                "rhythms": [{"name": "第三间章", "cost": 0}],
            })
        return songs

    def _get_rhythms_for_song(self, song_name: str) -> list[dict]:
        """返回曲目可用节奏列表（含全部 6 种节奏）。"""
        if song_name == "追寻那道光":
            rhythms = [{"name": "温柔 (Soave)", "cost": 1}]
            if self.regard >= 2:
                rhythms.append({"name": "追寻 (Sognando)", "cost": 2})
            return rhythms
        elif song_name == "Before light":
            rhythms = [{"name": "休息 (Riposato)", "cost": 1}]
            if self.regard >= 2:
                rhythms.append({"name": "悲伤 (Dolente)", "cost": 2})
            return rhythms
        elif song_name == "拼接遗憾":
            rhythms = [{"name": "平静 (Placido)", "cost": 1}]
            if self.regard >= 2:
                rhythms.append({"name": "遗憾 (Zeffiroso)", "cost": 2})
            return rhythms
        return []

    def get_legal_sing_targets(self, game_state: GameState,
                                song_name: str, rhythm_name: str) -> list:
        """返回合法听者列表。Sognando 可以选择 Strappando。"""
        targets = []
        is_sognando = "Sognando" in rhythm_name or "追寻" in rhythm_name
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            # Soave: 不能给 Strappando（Sognando 除外）
            if (song_name == "追寻那道光"
                    and "Soave" in rhythm_name
                    and not is_sognando):
                if getattr(p, 'emotion', None) == STRAPPANDO:
                    continue
            targets.append(p)
        # Chorus 也可以是目标
        for c in self.chorus_list:
            if not c.is_alive():
                continue
            if (song_name == "追寻那道光"
                    and "Soave" in rhythm_name
                    and not is_sognando):
                if c.emotion == STRAPPANDO:
                    continue
            targets.append(c)
        return targets

    # ================================================================
    #  旋律（Melody）
    # ================================================================
    def execute_melody(self, game_state: GameState, g2_player: Player):
        """旋律：G2 选一个座位 → 该座位所有人按序受 1/1/0.5/0.5 伤害。"""
        damage_sequence = [1.0, 1.0, 0.5, 0.5]
        occupied = self._get_occupied_seats(game_state)
        if not occupied:
            prompt_manager.show("g2reset", "melody.no_targets")
            return

        seat_names = sorted(occupied.keys())
        chosen = g2_player.controller.choose(
            "选择旋律目标座位：",
            seat_names,
            context={"situation": "g2_melody_seat"},
        )
        if chosen not in occupied:
            chosen = seat_names[0]

        targets = occupied[chosen]
        for i, (dmg, target) in enumerate(zip(damage_sequence, targets)):
            if not target.is_alive():
                continue
            from combat.damage_resolver import resolve_damage
            result = resolve_damage(
                g2_player, target, None, game_state,
                raw_damage_override=dmg,
                damage_attribute_override="无视属性克制",
                is_talent_attack=True,
                is_embrace_damage=True,
            )
            # 走 damage_resolver 的标准输出（和 T1 一刀缭断、T3 天星一致）
            prompt_manager.show("g2reset", "melody.hit",
                               index=i+1, target_name=target.name)
            for detail in result.get("details", []):
                display.show_info(f"   {detail}")

            if result.get("killed") and hasattr(game_state, 'police_engine') and game_state.police_engine:
                game_state.police_engine.check_and_record_crime(
                    g2_player.player_id, "伤害玩家")

            if target.is_alive() and hasattr(target, 'emotion') and target.emotion:
                adjust_emotion_down(target, game_state, self)

    def _get_occupied_seats(self, game_state) -> dict:
        """返回 {座位名: [存活单位列表]}，仅含有人（玩家或 Chorus）的座位。"""
        occupied: dict[str, list] = {}
        for seat in self.SEATS:
            units = []
            for pid in self.participants:
                if pid == self.g2_owner_id:
                    continue
                p = game_state.get_player(pid)
                if p and p.is_alive() and p.location == seat:
                    units.append(p)
            for c in self.chorus_list:
                if c.is_alive() and c.location == seat:
                    units.append(c)
            if units:
                occupied[seat] = units
        return occupied


# ══════════════════════════════════════════════════════════════════
#  情绪调整（模块级函数，供外部调用）
# ══════════════════════════════════════════════════════════════════

def adjust_emotion_up(unit, game_state=None, ish=None):
    """情绪上调一级。"""
    if not hasattr(unit, 'emotion') or not unit.emotion:
        return
    idx = EMOTION_ORDER.index(unit.emotion) if unit.emotion in EMOTION_ORDER else 1
    if idx < 2:
        unit.emotion = EMOTION_ORDER[idx + 1]


def adjust_emotion_down(unit, game_state=None, ish=None):
    """情绪下调一级。若已是 Strappando，触发断弦。"""
    if not hasattr(unit, 'emotion') or not unit.emotion:
        return
    idx = EMOTION_ORDER.index(unit.emotion) if unit.emotion in EMOTION_ORDER else 1
    if idx > 0:
        unit.emotion = EMOTION_ORDER[idx - 1]
    else:
        trigger_snap(unit, game_state, ish)


def trigger_snap(unit, game_state=None, ish=None):
    """断弦：0.5 无视属性克制伤害 + 失衡。"""
    unit.hp = round(max(0, unit.hp - 0.5), 2)
    if hasattr(unit, 'stage_statuses'):
        unit.stage_statuses.add("imbalance")
    prompt_manager.show("g2reset", "snap.triggered",
                       player_name=unit.name, hp=unit.hp)
