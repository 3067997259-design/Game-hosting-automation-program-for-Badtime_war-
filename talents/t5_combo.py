"""
天赋5：Combo（原初）

v1（m7_talents 关）：连续 3 个全局轮次获得行动权 → 下一轮 D4=4/D6=6 + 1HP/1ATK 奖励关。
m7（m7_talents 开）：回归音游本体（§7.6.1）。系统每隔 N 轮发一张**有序谱面**
（1-3 个音符 = 动作大类@拍点轮，提前公开），按拍命中动作大类，判 Perfect/Good/Miss，
结算档位（FC/Clear/残/无）给「手感火热」buff，全连(FC)额外授予一次追加行动。
数值全经 balance.json talents.t5（m7 门控，[待风洞]）。
"""

from __future__ import annotations
import random
from typing import Optional, List, Dict, Any

from talents.base_talent import BaseTalent
from talents.talent_balance import m7_enabled, talent_num
from engine import experiments
from engine.prompt_manager import prompt_manager
from cli import display


class Combo(BaseTalent):
    name = "combo"
    description = "音游谱面：按拍命中动作大类，全连(FC)进入手感火热"
    tier = "原初"

    # 动作 action_type → 谱面大类（其余 wake/lock/find/forfeit/status… → None，不满足音符）
    _ACTION_CATEGORY = {
        "attack": "attack", "shoot": "attack", "hook": "attack",
        "move": "move",
        "interact": "interact",
        "special": "special",
        "report": "police", "assemble": "police", "recruit": "police",
        "election": "police", "designate": "police",
        "police_command": "police", "track_guide": "police",
    }
    _CATEGORY_LABEL = {
        "move": "移动", "attack": "攻击", "interact": "交互",
        "special": "特殊", "police": "警务",
    }
    _BASE_POOL = ("move", "attack", "interact", "special")

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        # ---- v1 旧 combo 状态（m7 关时使用，保持字节不变）----
        self.consecutive_actions = 0
        self.trigger_threshold = 3      # 献诗可临时改为 2
        self._d4_force = False
        self._d6_force = False
        self._bonus_round_active = False
        self._bonus_hp_applied = False
        # ---- m7 谱面状态 ----
        self.current_chart: List[Dict[str, Any]] = []   # [{category, beat, result}]
        self.chart_active = False
        self.next_chart_round: Optional[int] = None
        self.fever_atk = 0
        self.fever_until_round = -1

    # ============================================================
    #  轮次钩子
    # ============================================================
    def on_round_start(self, round_num):
        if m7_enabled():
            self._m7_on_round_start(round_num)
            return
        # ---- v1：奖励关应用 +1 HP ----
        if not self._d4_force:
            return
        player = self.state.get_player(self.player_id)
        if not player or not player.is_alive():
            return
        if getattr(player, '_mythland_talent_suppressed', False):
            return
        self._bonus_round_active = True
        if not self._bonus_hp_applied:
            player.max_hp = min(2.0, player.max_hp + 1.0)
            player.hp = min(player.max_hp, player.hp + 1.0)
            self._bonus_hp_applied = True
            display.show_info(prompt_manager.get_prompt(
                "talent", "t5combo.bonus_round",
                default="🔥 Combo！{player_name} 本轮获得 +1 HP 和 +1 攻击力！"
            ).format(player_name=player.name))

    def on_round_end(self, round_num):
        if m7_enabled():
            self._m7_on_round_end(round_num)
            return
        # ---- v1：追踪连续行动，触发下一轮奖励 ----
        player = self.state.get_player(self.player_id)
        if not player or not player.is_alive():
            return
        self._bonus_hp_applied = False
        self._bonus_round_active = False
        self._d4_force = False
        self._d6_force = False
        if getattr(player, '_mythland_talent_suppressed', False):
            self.consecutive_actions = 0
            return
        if player.acted_this_round:
            self.consecutive_actions += 1
        else:
            self.consecutive_actions = 0
        if self.consecutive_actions >= self.trigger_threshold:
            self.consecutive_actions = 0
            self._d4_force = True
            self._d6_force = True
            display.show_info(prompt_manager.get_prompt(
                "talent", "t5combo.streak_trigger",
                default="🔥 {player_name} 连续行动{threshold}轮！下一轮 D4 必为 4，D6 必为 6！"
            ).format(player_name=player.name, threshold=self.trigger_threshold))
            self.state.log_event("combo_trigger", player=self.player_id,
                                 threshold=self.trigger_threshold)
            if self.trigger_threshold != 3:
                self.trigger_threshold = 3

    # ============================================================
    #  m7 谱面系统
    # ============================================================
    def _m7_on_round_start(self, round_num):
        player = self.state.get_player(self.player_id)
        if not player or not player.is_alive():
            return
        cadence = int(talent_num("t5", "chart_cadence_rounds", v1=4))
        # 幻想乡压制：谱面作废、手感火热清空、推迟下次发谱
        if getattr(player, '_mythland_talent_suppressed', False):
            self.chart_active = False
            self.current_chart = []
            self.fever_atk = 0
            self.fever_until_round = -1
            self.next_chart_round = round_num + cadence
            return
        if self.next_chart_round is None:
            self.next_chart_round = round_num + cadence
        if not self.chart_active and round_num >= self.next_chart_round:
            self._generate_chart(player, round_num)

    def _generate_chart(self, player, round_num):
        weights = talent_num("t5", "chart_len_weights", v1=[40, 40, 20])
        spacing = max(1, int(talent_num("t5", "beat_spacing", v1=1)))
        pool = list(self._BASE_POOL)
        if self._has_police_action(player):
            pool.append("police")
        length = random.choices([1, 2, 3], weights=list(weights))[0]
        self.current_chart = [
            {"category": random.choice(pool),
             "beat": round_num + k * spacing,
             "result": None}
            for k in range(length)
        ]
        self.chart_active = True
        chart_str = "、".join(
            f"R{n['beat']}:{self._CATEGORY_LABEL.get(n['category'], n['category'])}"
            for n in self.current_chart)
        display.show_info(prompt_manager.get_prompt(
            "talent", "t5combo.chart_issued",
            default="🎵 {player_name} 的谱面：{chart}"
        ).format(player_name=player.name, chart=chart_str))
        self.state.log_event("combo_chart", player=self.player_id,
                             length=length,
                             categories=[n["category"] for n in self.current_chart])

    def _has_police_action(self, player) -> bool:
        """T5 当前是否有 police 大类动作可做（队长/警员，或场上有犯人可举报）。"""
        police = getattr(self.state, "police", None)
        if police is None:
            return False
        if getattr(player, "is_police", False):
            return True
        for pid in self.state.player_order:
            try:
                if police.is_criminal(pid):
                    return True
            except Exception:
                continue
        return False

    def _m7_on_round_end(self, round_num):
        player = self.state.get_player(self.player_id)
        if not player or not player.is_alive():
            return
        if not self.chart_active:
            return
        if getattr(player, '_mythland_talent_suppressed', False):
            return
        # 关窗：窗口 [beat-1, beat+1] 已过（本轮 >= beat+1）仍未判 → Miss
        for note in self.current_chart:
            if note["result"] is None and round_num >= note["beat"] + 1:
                note["result"] = "miss"
        if all(n["result"] is not None for n in self.current_chart):
            self._resolve_chart(player, round_num)

    def _resolve_chart(self, player, round_num):
        perfect = sum(1 for n in self.current_chart if n["result"] == "perfect")
        good = sum(1 for n in self.current_chart if n["result"] == "good")
        miss = sum(1 for n in self.current_chart if n["result"] == "miss")
        if miss == 0 and good == 0:
            tier = "fc"        # 全 Perfect = Full Combo
        elif miss == 0:
            tier = "clear"     # 0 Miss（含 Good）
        elif perfect + good > 0:
            tier = "partial"   # 有 Miss 有命中 = 残（仅剧情分，无 buff）
        else:
            tier = "none"      # 全 Miss = 无（失败无惩罚）

        # 手感火热 buff（fc/clear 两档）
        if tier in ("fc", "clear"):
            atk = int(talent_num("t5", "fever_atk_" + tier,
                                 v1=(2 if tier == "fc" else 1)))
            dur = int(talent_num("t5", "fever_duration_" + tier,
                                 v1=(3 if tier == "fc" else 2)))
            heal = talent_num("t5", "heal_" + tier, v1=(3 if tier == "fc" else 2))
            self.fever_atk = atk
            self.fever_until_round = round_num + dur
            player.hp = min(player.max_hp, player.hp + heal)
        if tier == "fc":
            self.grant_extra_turn(player, 1)

        # 剧情分：按 Perfect 数累加（m6_scoring 门控，不走会去重的 finale.mark）
        if experiments.is_enabled("m6_scoring") and perfect > 0:
            pts = talent_num("t5", "score_per_perfect", v1=2) * perfect
            if tier == "fc":
                pts += talent_num("t5", "fc_bonus_score", v1=5)
            player.story_score = getattr(player, "story_score", 0) + pts

        display.show_info(prompt_manager.get_prompt(
            "talent", "t5combo.resolve_" + tier,
            default="🎵 {player_name} 谱面结算：P{p}/G{g}/M{m}"
        ).format(player_name=player.name, p=perfect, g=good, m=miss))
        self.state.log_event("combo_resolve", player=self.player_id,
                             tier=tier, perfect=perfect, good=good, miss=miss)

        self.current_chart = []
        self.chart_active = False
        self.next_chart_round = round_num + int(
            talent_num("t5", "chart_cadence_rounds", v1=4))

    # ============================================================
    #  行动回合钩子（m7：按拍判定音符）
    # ============================================================
    def on_turn_end(self, player, action_type):
        if not m7_enabled() or not self.chart_active:
            return
        if player.player_id != self.player_id:
            return
        if getattr(player, '_mythland_talent_suppressed', False):
            return
        category = self._ACTION_CATEGORY.get(action_type)
        if category is None:
            return
        r = self.state.current_round
        for note in self.current_chart:
            if note["result"] is not None or note["category"] != category:
                continue
            if not (note["beat"] - 1 <= r <= note["beat"] + 1):
                continue
            if r == note["beat"]:
                note["result"] = "perfect"
                key, default = "t5combo.note_perfect", "🎶 Perfect！（{category}）"
            elif r < note["beat"]:
                note["result"] = "good"
                key, default = "t5combo.note_good_early", "🎵 Good，快了！（{category}）"
            else:
                note["result"] = "good"
                key, default = "t5combo.note_good_late", "🎵 Good，慢了！（{category}）"
            display.show_info(prompt_manager.get_prompt(
                "talent", key, default=default
            ).format(category=self._CATEGORY_LABEL.get(category, category)))
            return  # 一次行动至多判一个音符

    # ============================================================
    #  D4/D6 钩子（仅 v1 生效；m7 下 _d4_force 恒 False → 返回 0）
    # ============================================================
    def on_d4_bonus(self, player):
        if player.player_id == self.player_id and self._d4_force:
            return 3
        return 0

    def on_d6_bonus(self, player):
        if player.player_id == self.player_id and self._d6_force:
            return 5
        return 0

    # ============================================================
    #  战斗钩子
    # ============================================================
    def modify_outgoing_damage(self, attacker, target, weapon, base_damage):
        if attacker.player_id != self.player_id:
            return None
        if getattr(attacker, '_mythland_talent_suppressed', False):
            return None
        if m7_enabled():
            if (self.fever_atk > 0
                    and self.state.current_round <= self.fever_until_round):
                return {"bonus_damage": self.fever_atk}
            return None
        # v1：奖励回合 +1 攻击力
        if self._bonus_round_active:
            return {"bonus_damage": 1}
        return None

    # ============================================================
    #  献诗支持（G5 暂缓；保留 v1 行为不破坏，m7 路径不依赖）
    # ============================================================
    def activate_poem_bonus(self, player):
        """献诗：立刻给予奖励状态（+1 HP, +1 ATK）。"""
        if not self._bonus_hp_applied:
            player.max_hp += 1.0
            player.hp += 1.0
            self._bonus_hp_applied = True
            self._bonus_round_active = True

    def deactivate_poem_bonus(self, player):
        """献诗：移除奖励状态。"""
        if self._bonus_hp_applied:
            player.max_hp = max(1.0, player.max_hp - 1.0)
            if player.hp > player.max_hp:
                player.hp = player.max_hp
            self._bonus_hp_applied = False
            self._bonus_round_active = False

    # ============================================================
    #  状态描述
    # ============================================================
    def describe_status(self):
        if m7_enabled():
            if self.chart_active:
                marks = {"perfect": "✓P", "good": "✓G", "miss": "✗", None: "·"}
                parts = [
                    f"R{n['beat']}:{self._CATEGORY_LABEL.get(n['category'], n['category'])}"
                    f"{marks[n['result']]}"
                    for n in self.current_chart]
                status = "🎵 谱面：" + " ".join(parts)
            elif self.next_chart_round is not None:
                status = f"🎵 下次谱面 @R{self.next_chart_round}"
            else:
                status = "🎵 待发谱"
            if self.fever_atk > 0 and self.state.current_round <= self.fever_until_round:
                status += f" | 🔥 手感火热(+{self.fever_atk}ATK→R{self.fever_until_round})"
            return status
        # v1
        parts = [f"连续行动：{self.consecutive_actions}/{self.trigger_threshold}"]
        if self._d4_force:
            parts.append("⚡ 下一轮必定行动+奖励")
        if self._bonus_round_active:
            parts.append("🔥 奖励回合中（+1HP/+1ATK）")
        return " | ".join(parts)
