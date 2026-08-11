"""M9 G5 轮回培养与锚定脚本天赋（profile: m9-rfc，G5 合同 v0.4 + 诗篇 v0.1）。

- 四形态状态机：CYRENE 小昔涟（有限寿命）/ HOME 归家（非死亡）/ DEMIURGE 德谬歌
  （追忆封存，永不再涨）/ PAST 闭合退场（非死亡、不进往世层）；
- 追忆：R0 结算（战斗/亲历/损失/PP 事件/闲时），封存池 capped；
- AnchorScript 投影器（AnchorScriptProjector）：K 槽封闭语法 → 逐槽差分 →
  候选事件（DEFEAT/DESTROY/RELOCATE/ACQUIRE），首非法槽拒绝、不补前置；
- 逐槽监控（R4）：自然实现 / 再投影强制（DEFEAT 带 absolute_death）/
  因果改写（脚本失败）；
- 快照窄回溯 + 水晶花/完结条 arc（m9 评分通道，不读 v2exp finale 键）；
- 爱愿互斥：激活锚定不可献诗；有爱愿不可锚定；
- 诗篇共享入口：地火/负世为完整额外行动来源（三源白名单已建），彼岸复活置 SP2。
数值一律读 `m9_talents_extended.g5.*`（[待风洞]）。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from engine.balance import get as bget
from talents.g5.ripple import Ripple

FORM_CYRENE = "cyrene"
FORM_HOME = "home"
FORM_DEMIURGE = "demiurge"
FORM_PAST = "past"

SCRIPT_KINDS: Tuple[str, ...] = ("move", "find", "lock", "charge",
                                 "interact", "attack")


def _g5(key: str, default):
    return bget("m9_talents_extended", "g5", key, default=default)


@dataclass
class EventCandidate:
    """投影差分事件（合同 §6.3）：四类候选 + 责任登记。"""
    kind: str                      # DEFEAT / DESTROY / RELOCATE / ACQUIRE
    slot_index: int
    subject_id: str
    source_action: Tuple
    before: Any = None
    after: Any = None


class AnchorScriptProjector:
    """K 槽封闭语法投影器（合同 §5.2/§6）：只读投影、逐槽差分、首非法槽拒绝。"""

    def __init__(self, state: Any, g5: "Ripple9") -> None:
        self.state = state
        self.g5 = g5

    def validate_script(self, script: List[Tuple]) -> Optional[int]:
        """语法/合法性预检：返回首个非法槽 index（0-based），合法返回 None。"""
        if not script:
            return 0
        for i, action in enumerate(script):
            if not isinstance(action, (list, tuple)) or len(action) < 2:
                return i
            kind = action[0]
            if kind not in SCRIPT_KINDS:
                return i
            target = action[1]
            if not target or not isinstance(target, str):
                return i
            if kind == "attack" and len(action) < 3:
                return i  # attack(target, weapon_id)
            if kind == "interact" and len(action) < 3:
                return i  # interact(object_id, operation, ...)
        return None

    def project(self, script: List[Tuple], k: int) -> List[EventCandidate]:
        """逐槽投影：在只读状态副本上执行动作，产出差分候选事件。"""
        from engine.m9.combat import resolve_hit_probe
        candidates: List[EventCandidate] = []
        for i, action in enumerate(script[:k]):
            kind, target_id = action[0], action[1]
            target = self.state.get_player(target_id) if target_id else None
            if kind == "attack" and target is not None and target.is_alive():
                weapon_name = action[2]
                weapon = self.g5._weapon_by_name(weapon_name)
                if weapon is not None:
                    from controllers.ai.game_query import GameQuery
                    attr = GameQuery.get_weapon_attr(weapon).value
                    hit = resolve_hit_probe(target, int(weapon.get_effective_damage()),
                                            attr)
                    if hit.damage >= target.hp:
                        candidates.append(EventCandidate(
                            "DEFEAT", i, target_id, tuple(action),
                            before=("alive",), after=("dead",)))
                    elif hit.broken:
                        candidates.append(EventCandidate(
                            "DESTROY", i, hit.broken[0], tuple(action),
                            before=("intact",), after=("destroyed",)))
            elif kind == "move":
                candidates.append(EventCandidate(
                    "RELOCATE", i, self.g5.player_id, tuple(action),
                    before=("loc",), after=(target_id,)))
            elif kind == "interact":
                op = action[2] if len(action) > 2 else ""
                if op == "pickup":
                    candidates.append(EventCandidate(
                        "ACQUIRE", i, target_id, tuple(action),
                        before=(None,), after=(self.g5.player_id,)))
        return candidates


class Ripple9(Ripple):
    """M9 G5（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。"""

    name = "神代天赋-往世的涟漪"

    def __init__(self, player_id: str, game_state: Any) -> None:
        super().__init__(player_id, game_state)
        self.form = FORM_CYRENE
        self.incarnations = 0
        self.life_ticks = int(_g5("cyrene_life_ticks", 4))
        self.sealed_reminiscence = 0.0
        self._g5_established_round = None
        # 锚定状态（M9：K 槽脚本）
        self.active_anchor = False
        self.anchor_script: List[Tuple] = []
        self.anchor_k = 0
        self.anchor_slot_index = 0
        self.anchor_candidates: List[EventCandidate] = []
        self.anchor_snapshot = None
        self.anchor_established_round = None
        self.anchor_results: List[str] = []   # 未来闭合 / 因果被改写 / 锚定粉碎
        self.total_closures = 0               # 未来闭合次数（完结条 = 第 2 次）
        self.flower_arc_granted = False
        from engine.m9.talents.poems import PoeticRecital
        self.poems = PoeticRecital(self)

    def recite_poem(self, poem_name: str, target_pid: str) -> str:
        """献诗入口（共享入口预检 + 十四首执行器）。"""
        return self.poems.recite(
            self.state.get_player(self.player_id), poem_name, target_pid)

    # ════════════════════════════════════════════════════════
    #  四形态 / R0 结算
    # ════════════════════════════════════════════════════════

    def on_round_start(self, round_num):
        """R0：HOME 转世/诞生结算；DEMIURGE 闭合退场检查。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return super().on_round_start(round_num)
        me = self.state.get_player(self.player_id)
        if not me:
            return
        if self.form == FORM_HOME:
            if (self.incarnations < int(_g5("cyrene_max_incarnations", 3))
                    and self.sealed_reminiscence < float(
                        _g5("demiurge_birth_threshold", 12))):
                self._born_cyrene(me)
            else:
                self._born_demiurge(me)
        elif self.form == FORM_DEMIURGE:
            if not self.active_anchor and self.sealed_reminiscence < float(
                    _g5("anchor_min_k", 3)):
                self._enter_past(me)

    def _born_cyrene(self, me: Any) -> None:
        self.form = FORM_CYRENE
        self.incarnations += 1
        self.life_ticks = int(_g5("cyrene_life_ticks", 4))
        me.hp = min(me.max_hp, int(_g5("cyrene_hp", 8)))
        me.is_awake = True

    def _born_demiurge(self, me: Any) -> None:
        self.form = FORM_DEMIURGE
        me.hp = me.max_hp
        me.is_awake = True

    def _enter_past(self, me: Any) -> None:
        """闭合退场：非死亡、不进往世层、PP 冻结、物品保留。"""
        self.form = FORM_PAST
        me.is_awake = False
        pp = getattr(self.state, "m9_pp", None)
        if pp is not None:
            pp.freeze(self.player_id)

    def m9_on_lethal(self, target: Any, attacker: Any,
                     source_kind: Optional[str]) -> Optional[str]:
        """小昔涟普通致死 → 归家（非死亡、无击杀/T7/往世层）；德谬歌普通致死 = 真死。"""
        from engine.m9.combat import is_absolute_death_source
        if self.form == FORM_CYRENE and not is_absolute_death_source(source_kind):
            self._homecoming(target)
            return "g5_homecoming"
        return None

    def _homecoming(self, me: Any) -> None:
        """归家：离开地图、物品原地掉落、下一 R0 转世/诞生。"""
        self.form = FORM_HOME
        me.hp = 1
        me.location = "home"
        me.is_awake = False
        self.state.log_event("CYRENE_HOMECOMING", player=self.player_id)

    # ════════════════════════════════════════════════════════
    #  追忆（R0 结算，封存池 capped）
    # ════════════════════════════════════════════════════════

    def m9_on_combat_event(self, kind: str, personal: bool = False) -> None:
        """引擎事件喂入：combat / loss / pp_event / idle（每类每轮一次）。"""
        if self.form != FORM_CYRENE:
            return
        gain = 0.0
        if kind == "combat":
            gain = float(_g5("reminiscence_combat_gain", 1))
            if personal:
                gain += float(_g5("reminiscence_combat_personal_bonus", 1))
        elif kind == "loss":
            gain = float(_g5("reminiscence_loss_gain", 1))
        elif kind == "pp_event":
            gain = float(_g5("reminiscence_pp_event_gain", 1))
        else:
            gain = float(_g5("reminiscence_idle_gain", 0.5))
        cap = float(_g5("reminiscence_cap", 24))
        self.sealed_reminiscence = min(cap, self.sealed_reminiscence + gain)

    # ════════════════════════════════════════════════════════
    #  锚定（公演入口 + 逐槽监控）
    # ════════════════════════════════════════════════════════

    def get_t0_option(self, player: Any) -> Optional[dict]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return super().get_t0_option(player)
        if self.form != FORM_DEMIURGE:
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None or m9.get_sp(self.player_id) < 2:
            return None
        if self.active_anchor:
            return None
        return {"name": "锚定：填写基础行动脚本", "description": "公演 2 SP + K 追忆",
                "m9_kind": "g5_anchor"}

    def execute_t0(self, player: Any):
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return super().execute_t0(player)
        return "❌ 锚定脚本经接入层执行（execute_anchor）", False

    def execute_anchor(self, player: Any, script: List[Tuple]) -> Tuple[str, bool]:
        """锚定入口：预检（投影须产出 ≥1 候选）先于 SP/槽/K 追忆消费。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return "❌ M9 未启用", False
        if self.form != FORM_DEMIURGE:
            return "❌ 仅德谬歌可锚定", False
        if self.active_anchor:
            return "❌ 已有激活锚定", False
        if self._has_love_wish_on_me():
            return "❌ 存在爱愿，不可锚定", False
        m9 = getattr(self.state, "m9_system", None)
        round_num = getattr(self.state, "current_round", 1)
        k = len(script)
        if not (int(_g5("anchor_min_k", 3)) <= k <= int(_g5("anchor_max_k", 8))):
            return f"❌ K 须在 [anchor_min_k, anchor_max_k]（当前 {k}）", False
        projector = AnchorScriptProjector(self.state, self)
        bad = projector.validate_script(script)
        if bad is not None:
            return f"❌ 第 {bad + 1} 槽非法（不补前置、不选武器）", False
        candidates = projector.project(script, k)
        if not candidates:
            return "❌ 投影无候选事件，预检失败（不消费）", False
        if m9.get_sp(self.player_id) < 2:
            return "❌ SP 不足", False
        if self.sealed_reminiscence < k:
            return "❌ 追忆不足", False
        if m9.assign_public_slot(round_num) != self.player_id:
            if not m9.register_performance(self.player_id, round_num):
                return "❌ 公演位不足", False
        if m9.dispatch_public(self.player_id, round_num) is None:
            return "❌ 公演派发失败", False
        self.sealed_reminiscence -= k
        self.active_anchor = True
        self.anchor_script = list(script)
        self.anchor_k = k
        self.anchor_slot_index = 0
        self.anchor_candidates = candidates
        self.anchor_snapshot = copy.deepcopy(
            {"hp": getattr(player, "hp", 0),
             "location": getattr(player, "location", None)})
        self.anchor_established_round = round_num
        self.state.log_event("anchor_script_committed", player=self.player_id, k=k)
        return f"⚓ 锚定建立（K={k}，{len(candidates)} 个候选事件）", True

    def _has_love_wish_on_me(self) -> bool:
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if (p and p.talent and hasattr(p.talent, "has_love_wish")
                    and p.talent.has_love_wish(self.player_id)):
                return True
        return False

    def on_round_end(self, round_num):
        """R4 逐槽监控：推进槽位；自然实现 / 再投影强制 / 因果改写。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return super().on_round_end(round_num)
        self.poems.tick_love_wishes()
        if not self.active_anchor:
            return
        if self.anchor_established_round == round_num:
            return  # 建立轮 R4 不 tick
        self._monitor_slot()
        self.anchor_slot_index += 1
        if self.anchor_slot_index >= self.anchor_k:
            self._finish_anchor()

    def _monitor_slot(self) -> None:
        """当前槽候选：自然实现 → 跳过；否则再投影（实时状态）→ 强制差分；
        不可达 → 因果改写（脚本失败）。"""
        if self.anchor_slot_index >= len(self.anchor_candidates):
            return
        cand = self.anchor_candidates[self.anchor_slot_index]
        if self._naturally_realized(cand):
            self.state.log_event("MATERIALIZED_NATURALLY", player=self.player_id,
                                 kind=cand.kind, subject=cand.subject_id)
            return
        if self._reproject(cand):
            self.state.log_event("MATERIALIZED_BY_ANCHOR", player=self.player_id,
                                 kind=cand.kind, subject=cand.subject_id)
            return
        self.anchor_results.append("因果被改写")
        self._fail_anchor(rewritten=True)

    def _naturally_realized(self, cand: EventCandidate) -> bool:
        if cand.kind == "DEFEAT":
            p = self.state.get_player(cand.subject_id)
            return bool(p and not p.is_alive())
        if cand.kind == "RELOCATE":
            p = self.state.get_player(cand.subject_id)
            return bool(p and getattr(p, "location", None) == cand.after[0])
        return False

    def _reproject(self, cand: EventCandidate) -> bool:
        """再投影：同来源动作对实时状态重放；DEFEAT 强制 = absolute_death 差分。"""
        if cand.kind == "DEFEAT":
            p = self.state.get_player(cand.subject_id)
            if p is not None and p.is_alive():
                from engine.m9.combat import resolve_damage
                me = self.state.get_player(self.player_id)
                resolve_damage(
                    me, p, weapon=None, game_state=self.state,
                    raw_damage_override=9999,
                    damage_attribute_override="__无视__",
                    source_kind="g5_anchor")
                return True
        if cand.kind == "RELOCATE":
            p = self.state.get_player(cand.subject_id)
            if p is not None and p.is_alive():
                p.location = cand.after[0]
                return True
        return False

    def _finish_anchor(self) -> None:
        """收尾：全候选实现 → 未来闭合（窄回溯 + 水晶花）；否则失败。"""
        me = self.state.get_player(self.player_id)
        if not self.anchor_results:
            self.anchor_results.append("未来闭合")
            self.total_closures += 1
            self._rewind(me)
            self._grant_flower()
            if self.total_closures >= 2:
                pp = getattr(self.state, "m9_pp", None)
                if pp is not None:
                    pp.earn(self.player_id, 1)  # 完结条进展 PP
                self.state.log_event("g5_double_closure", player=self.player_id)
        self.active_anchor = False
        self.anchor_script = []
        self.anchor_candidates = []
        self.anchor_snapshot = None

    def _rewind(self, me: Any) -> None:
        """快照窄回溯：只回自身 HP/位置（不改耐久/弹药/credits/SP/追忆）。"""
        if self.anchor_snapshot:
            me.hp = self.anchor_snapshot.get("hp", me.hp)
            me.location = self.anchor_snapshot.get("location", me.location)

    def _grant_flower(self) -> None:
        if self.flower_arc_granted:
            return
        self.flower_arc_granted = True
        pp = getattr(self.state, "m9_pp", None)
        if pp is not None:
            pp.earn(self.player_id, int(_g5("crystal_flower_arc_count", 1)))
        self.state.log_event("crystal_flower", player=self.player_id)

    def _fail_anchor(self, rewritten: bool = False) -> None:
        self.active_anchor = False
        self.anchor_script = []
        self.anchor_candidates = []
        self.anchor_snapshot = None

    def _weapon_by_name(self, name: str):
        me = self.state.get_player(self.player_id)
        if me is None:
            return None
        for w in getattr(me, "weapons", []):
            if w and getattr(w, "name", "") == name:
                return w
        return None

    # ── 爱愿（复用 v2exp love_wish 结构；诗篇正文执行随阶段 8 接线）──
    def grant_love_wish(self, target_pid: str, ticks: int) -> None:
        self.love_wish[target_pid] = ticks

    def has_love_wish(self, target_pid: str) -> bool:
        return self.love_wish.get(target_pid, 0) > 0
