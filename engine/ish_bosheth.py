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

    def __init__(self, g2_owner_id: str, anchor_location: str):
        self.g2_owner_id: str = g2_owner_id
        self.anchor_location: str = anchor_location

        self.regard: float = 0.0
        self.regard_cap: float = 8.0
        self.r4_count: int = 0
        self.phase: str = "active"   # "active" / "pending_curtain" / "ended"

        self.chorus_list: List[ChorusUnit] = []
        self.submerged_list: list = []

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
        """展开 ish-bosheth。调用方（g2_hologram.execute_t0）会把返回的
        IshBosheth 存入 game_state.ish_bosheth。
        """
        self.anchor_location = g2_player.location
        lines: list[str] = []

        # 1. 收集参与者（排除 G3 结界内的）
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
                lines.append(f"  {p.name} 身处幻想乡结界，未被拉入。")
                continue
            self.participants.add(pid)

        # 2. 强制起床
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p and not p.is_awake:
                p.is_awake = True
                p.location = p.location or self.anchor_location
                lines.append(f"  {p.name} 被强制唤醒！")

        # 3. 解除隐身
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p and p.is_invisible:
                p.is_invisible = False
                lines.append(f"  {p.name} 的隐身被解除。")
        g2p = game_state.get_player(self.g2_owner_id)
        if g2p and g2p.is_invisible:
            g2p.is_invisible = False
            lines.append(f"  {g2p.name} 的隐身被解除。")

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

        # 6. Submerge 非玩家单位
        if hasattr(game_state, 'police') and game_state.police:
            for unit in game_state.police.units:
                if unit.is_alive() and unit.is_on_map():
                    unit._pre_submerge_location = unit.location
                    unit.location = None
                    self.submerged_list.append(unit)
            if self.submerged_list:
                lines.append(f"  {len(self.submerged_list)} 个非玩家单位被压制 (Submerged)。")

        # 7. 生成 Chorus
        real_in_stage = len(self.participants) + 1  # +1 for G2 owner
        chorus_count = max(0, 6 - real_in_stage)
        for i in range(chorus_count):
            c = ChorusUnit()
            c.location = self.anchor_location
            self.chorus_list.append(c)
        if self.chorus_list:
            lines.append(f"  生成 {len(self.chorus_list)} 个 Chorus 单位。")

        # 8. liberamente_vivace
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p:
                p.stage_statuses = getattr(p, 'stage_statuses', set())
                p.stage_statuses.add("liberamente_vivace")
        if g2p:
            g2p.stage_statuses = getattr(g2p, 'stage_statuses', set())
            g2p.stage_statuses.add("liberamente_vivace")

        # 9. 真实观众选择情绪
        for pid in self.participants:
            p = game_state.get_player(pid)
            if not p:
                continue
            emotion = p.controller.choose(
                "选择你在 ish-bosheth 舞台中的初始态度：",
                ["入戏 (Accarezzevole)", "抽离 (Indifferenza)", "反抗 (Strappando)"],
                context={"phase": "T0", "situation": "g2_emotion_choice"},
            )
            p.emotion = self._parse_emotion_choice(emotion)
            lines.append(f"  {p.name} 选择了 {EMOTION_LABELS.get(p.emotion, p.emotion)}。")

        # 10. Chorus 随机情绪
        for c in self.chorus_list:
            c.emotion = random.choice(EMOTION_ORDER)

        # 11. ma non troppo 开场校正
        self.ma_non_troppo(game_state)

        # 12. 第一音节留给 Phase 4 实现（旋律）
        # self.execute_melody(game_state, g2_player)  # Phase 4 补

        # 13. 初始 Regard
        P = len(self.participants)  # 真实观众（不含 G2）
        C = len(self.chorus_list)
        N = len(self.submerged_list)
        self.regard = max(4.0, min(8.0, 3.0 + P + 0.5 * C + 0.5 * N))
        lines.append(f"  初始 Regard: {self.regard}/{self.regard_cap}")

        game_state.ish_bosheth = self
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

        self.regard = min(self.regard, self.regard_cap)
        self.r4_count += 1

        # 旋律解锁
        if self.r4_count >= 3:
            self.melody_2_unlocked = True
        if self.r4_count >= 6:
            self.melody_3_unlocked = True

        # 谢幕检查
        if self.regard <= 0:
            self.phase = "pending_curtain"
            display.show_info("🎭 Regard 归零，ish-bosheth 进入待谢幕状态。")
        elif self.r4_count >= 8:
            self.phase = "pending_curtain"
            display.show_info("🎭 已持续 8 轮，ish-bosheth 进入待谢幕状态。")

        # 清除到期聚光灯 / 临时 HP/ATK / before_light
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p:
                if "spotlight" in getattr(p, 'stage_statuses', set()):
                    p.stage_statuses.discard("spotlight")
                    p.temp_hp_g2 = 0.0
                    p.temp_atk_g2 = 0.0
        for c in self.chorus_list:
            if "spotlight" in c.stage_statuses:
                c.stage_statuses.discard("spotlight")
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
                display.show_info(
                    f"  🎭 {p.name} 受到废墟谢幕伤害 0.5 → HP: {p.hp}")

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
            display.show_info(f"  {g2p.name} 无人阻挡，清除标记并隐身。")

        self.end_ish_bosheth(END_CURTAIN, game_state)

    # ================================================================
    #  统一清理
    # ================================================================
    def end_ish_bosheth(self, reason: str, game_state: GameState,
                        breaker_id: Optional[str] = None):
        g2p = game_state.get_player(self.g2_owner_id)

        display.show_info(f"\n{'='*50}")
        display.show_info(f"  🎭 ish-bosheth 结束 (原因: {reason})")
        display.show_info(f"{'='*50}")

        # 破幕特殊处理：给破幕者 D4/D6 +1
        if reason == END_BREAK and breaker_id:
            breaker = game_state.get_player(breaker_id)
            if breaker:
                breaker._g2_curtain_d4_bonus = True
                display.show_info(f"  🎭 {breaker.name} 获得下轮 D4+1 奖励！")

        # 空场退场
        if reason == END_EMPTY and g2p and g2p.is_alive():
            game_state.markers.clear_all_relations(self.g2_owner_id)
            g2p.is_invisible = True
            display.show_info(f"  {g2p.name} 空场退场，清除标记并隐身。")

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
        self.chorus_list.clear()

        # Submerged 解除
        for unit in self.submerged_list:
            if hasattr(unit, '_pre_submerge_location'):
                unit.location = unit._pre_submerge_location
                del unit._pre_submerge_location
        self.submerged_list.clear()

        # 位置恢复：仍在场的真实玩家落回锚点
        for pid in list(self.participants):
            p = game_state.get_player(pid)
            if p and p.is_alive():
                p.location = self.anchor_location

        self.phase = "ended"
        game_state.ish_bosheth = None

    # ================================================================
    #  情绪状态机
    # ================================================================
    @staticmethod
    def _parse_emotion_choice(choice_str: str) -> str:
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
                        display.show_info(
                            f"  🎭 ma non troppo: {chosen.name} "
                            f"{EMOTION_LABELS.get(old_emotion, '?')} → "
                            f"{EMOTION_LABELS.get(target_emotion, '?')}")
        elif R == 2:
            if real_observers[0].emotion and real_observers[0].emotion == real_observers[1].emotion:
                target = random.choice(real_observers)
                old = target.emotion
                idx = EMOTION_ORDER.index(old) if old in EMOTION_ORDER else 1
                new_idx = (idx + 1) % 3
                target.emotion = EMOTION_ORDER[new_idx]
                target.stage_statuses = getattr(target, 'stage_statuses', set())
                target.stage_statuses.add("moderation_lock")
                display.show_info(
                    f"  🎭 ma non troppo: {target.name} "
                    f"{EMOTION_LABELS.get(old, '?')} → "
                    f"{EMOTION_LABELS.get(target.emotion, '?')}")

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
        """返回曲目可用节奏列表（MVP 只实现 Soave + Riposato）。"""
        if song_name == "追寻那道光":
            rhythms = [{"name": "温柔 (Soave)", "cost": 1}]
            # Sognando 等更强节奏留 Phase 后续
            return rhythms
        elif song_name == "Before light":
            rhythms = [{"name": "休息 (Riposato)", "cost": 1}]
            # Dolente 留后续
            return rhythms
        elif song_name == "拼接遗憾":
            rhythms = [{"name": "平静 (Placido)", "cost": 1}]
            return rhythms
        return []

    def get_legal_sing_targets(self, game_state: GameState,
                                song_name: str, rhythm_name: str) -> list:
        """返回合法听者列表。"""
        targets = []
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            # Soave: 不能给 Strappando
            if song_name == "追寻那道光" and "Soave" in rhythm_name:
                if getattr(p, 'emotion', None) == STRAPPANDO:
                    continue
            targets.append(p)
        # Chorus 也可以是目标
        for c in self.chorus_list:
            if not c.is_alive():
                continue
            if song_name == "追寻那道光" and "Soave" in rhythm_name:
                if c.emotion == STRAPPANDO:
                    continue
            targets.append(c)
        return targets

    # ================================================================
    #  旋律（Melody）
    # ================================================================
    def execute_melody(self, game_state: GameState, g2_player: Player):
        """执行旋律链式伤害。"""
        damage_sequence = [1.0, 1.0, 0.5, 0.5]
        hit_units: set = set()

        legal = self._get_melody_targets(game_state, hit_units)
        if not legal:
            display.show_info("  旋律：无合法目标。")
            return

        target_names = [t.name for t in legal]
        chosen_name = g2_player.controller.choose(
            "选择旋律初始目标：",
            target_names,
            context={"situation": "g2_melody_target"},
        )
        current_target = next((t for t in legal if t.name == chosen_name), legal[0])

        for i, dmg in enumerate(damage_sequence):
            if not current_target.is_alive():
                break
            # 伤害
            from combat.damage_resolver import resolve_damage
            result = resolve_damage(
                g2_player, current_target, None, game_state,
                raw_damage_override=dmg,
                damage_attribute_override="无视属性克制",
                is_talent_attack=True,
            )
            display.show_info(
                f"  🎵 旋律 #{i+1}: {current_target.name} 受到 {dmg} 伤害 → HP: {current_target.hp}")

            # 情绪下调
            if current_target.is_alive() and hasattr(current_target, 'emotion') and current_target.emotion:
                adjust_emotion_down(current_target, game_state, self)

            hit_units.add(current_target.player_id)

            # 下一个传播目标
            if i < len(damage_sequence) - 1:
                legal_next = self._get_melody_targets(game_state, hit_units)
                if not legal_next:
                    break
                next_names = [t.name for t in legal_next]
                chosen_next = g2_player.controller.choose(
                    "选择旋律传播目标：",
                    next_names,
                    context={"situation": "g2_melody_propagate"},
                )
                current_target = next(
                    (t for t in legal_next if t.name == chosen_next),
                    legal_next[0])

    def _get_melody_targets(self, game_state: GameState,
                            already_hit: set) -> list:
        """获取旋律合法目标（排除已命中、G2自身）。"""
        targets = []
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            if pid in already_hit:
                continue
            p = game_state.get_player(pid)
            if p and p.is_alive():
                targets.append(p)
        for c in self.chorus_list:
            if c.player_id in already_hit:
                continue
            if c.is_alive():
                targets.append(c)
        return targets


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
    display.show_info(
        f"  🎻 断弦！{unit.name} 受到 0.5 伤害 → HP: {unit.hp}，进入失衡。")
