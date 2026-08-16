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
from engine.m9.text import m9_text
from talents.g5.ripple import Ripple

FORM_CYRENE = "cyrene"
FORM_HOME = "home"
FORM_DEMIURGE = "demiurge"
FORM_PAST = "past"

CYRENE_END_HOME = "homecoming"
CYRENE_END_DEMIURGE = "demiurge"
CYRENE_END_DEATH = "death"

SCRIPT_KINDS: Tuple[str, ...] = ("move", "find", "lock", "charge",
                                 "interact", "attack")


def _g5(key: str, default):
    return bget("m9_talents_extended", "g5", key, default=default)


def _pp(key: str, default):
    return bget("m9_system", "pp", key, default=default)


def build_anchor_fallback_script(player: Any, state: Any) -> List[Tuple]:
    """构造「真实预言」兜底脚本（引擎/AI 共用）：可落空预言 + move 槽垫至 anchor_min_k。

    可落空预言构造顺序：① 以最强武器攻击最脆弱存活对手（须投影出 DEFEAT/DESTROY
    候选）；② 拾取地面遗落物中尚未持有的对象（ACQUIRE 候选）。两者皆不可构造时
    返回空列表——锚定会被张力规则拒绝，调用方应直接放弃锚定。
    """
    from controllers.ai.game_query import GameQuery
    from engine.m9.combat import resolve_hit_probe
    min_k = max(1, int(_g5("anchor_min_k", 3)))
    script: List[Tuple] = []

    # ① 攻击预言：最强武器 × 最脆弱存活对手
    best = None
    for w in getattr(player, "weapons", []) or []:
        if not w:
            continue
        dmg = float(GameQuery.get_weapon_damage(w))
        if best is None or dmg > best[1]:
            best = (w, dmg)
    if best is not None:
        attr = GameQuery.get_weapon_attr(best[0]).value
        living = [
            other for pid in getattr(state, "player_order", [])
            if (other := state.get_player(pid)) is not None
            and pid != getattr(player, "player_id", None) and other.is_alive()
        ]
        living.sort(key=lambda p: float(getattr(p, "hp", 1e9)))
        for target in living:
            hit = resolve_hit_probe(target, int(best[1]), attr)
            if hit.damage >= float(getattr(target, "hp", 0)) or hit.broken:
                # 攻击槽必须是真实可执行预言：远程先补 lock、近战先补 find，
                # 否则锚定者只能靠因果改写拿花。
                from models.equipment import WeaponRange
                weapon_range = getattr(best[0], "weapon_range", None)
                if weapon_range == WeaponRange.RANGED:
                    script.append(("lock", target.player_id))
                elif weapon_range == WeaponRange.MELEE:
                    script.append(("find", target.player_id))
                script.append(("attack", target.player_id,
                               getattr(best[0], "name", "")))
                break

    # ② 拾取预言：地面遗落物中尚未持有的对象
    if not script:
        for pile in (getattr(state, "ground_loot", {}) or {}).values():
            if not isinstance(pile, dict):
                continue
            for key in ("weapons", "items", "armor"):
                for entry in list(pile.get(key, []) or []):
                    if not isinstance(entry, dict):
                        continue
                    name = entry.get("name")
                    if name and not Ripple9._holds_object_named(player, name):
                        script.append(("interact", name, "pickup"))
                        break
                if script:
                    break
            if script:
                break
    if not script:
        return []

    # ③ move 槽垫至 anchor_min_k（排除当前位置与被毁地点）
    current = getattr(player, "location", None)
    destroyed = getattr(state, "m9_destroyed_locations", set()) or set()
    pads = [("move", loc) for loc in Ripple9._DEFAULT_ANCHOR_LOCATIONS
            if loc != current and loc not in destroyed]
    script.extend(pads[: max(0, min_k - len(script))])
    if len(script) < min_k:
        return []  # 可用地点不足，脚本无法达到 K → 放弃锚定
    return script[: int(_g5("anchor_max_k", 8))]


@dataclass
class EventCandidate:
    """投影差分事件（合同 §6.3）：四类候选 + 责任登记。"""
    kind: str                      # DEFEAT / DESTROY / RELOCATE / ACQUIRE
    slot_index: int
    subject_id: str
    source_action: Tuple
    before: Any = None
    after: Any = None
    realized: bool = False         # 窗口期内已自然实现（合同 §7.2 曾发生即锁定）


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
        self._cyrene_established_round: Optional[int] = None
        self._cyrene_life_expired = False
        self._ordinary_max_hp: Optional[float] = None
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
        self.flowers_granted = 0
        self._double_closure_granted = False
        # 追忆喂入：两层每轮各至多一次（DOC-046）
        self._combat_any_fed_round = -1
        self._combat_personal_fed_round = -1
        # 微澜（W4：1 SP 信息型即演；每个完整公演后重开一次）
        self._ripple_used_this_cycle = False
        from engine.m9.talents.poems import PoeticRecital
        self.poems = PoeticRecital(self)

    def describe_status(self) -> str:
        """M9 状态口径：形态/世数/追忆预算/锚定进度/闭合次数。"""
        if self.form == FORM_CYRENE:
            label = m9_text("talents.g5.form_label_cyrene")
            parts = [
                label,
                m9_text("talents.g5.status_incarnation",
                        incarnation=int(self.incarnations)),
                m9_text("talents.g5.status_life_ticks",
                        ticks=int(getattr(self, 'life_ticks', 0) or 0)),
            ]
        else:
            label = m9_text("talents.g5.form_label_demiurge")
            parts = [
                label,
                m9_text("talents.g5.status_reminiscence",
                        reminiscence=f"{float(getattr(self, 'sealed_reminiscence', 0) or 0):g}"),
                m9_text("talents.g5.status_closures",
                        count=int(getattr(self, 'total_closures', 0) or 0)),
            ]
        if getattr(self, "active_anchor", False):
            k = int(getattr(self, "anchor_k", 0) or 0)
            slot = int(getattr(self, "anchor_slot_index", 0) or 0)
            parts.append(m9_text("talents.g5.status_anchor", slot=slot, k=k))
        return " | ".join(parts)

    def on_register(self) -> None:
        """M9 真实注册：开局小昔涟是第一世，且身体上限为 8。"""
        super().on_register()
        me = self.state.get_player(self.player_id)
        if me is None:
            return
        self._ordinary_max_hp = float(getattr(me, "max_hp", 20))
        self.incarnations = 1
        self._establish_cyrene_body(me, increment=False, enter_map=False)

    # ════════════════════════════════════════════════════════
    #  微澜（W4 裁决：1 SP 信息型即演）
    # ════════════════════════════════════════════════════════

    def ripple_available(self) -> bool:
        """微澜重开闸：每个完整公演后重开一次；激活锚定监控期不可用。"""
        return not self._ripple_used_this_cycle and not self.active_anchor

    def open_ripple_after_public(self) -> None:
        """德谬歌完成一次完整公演后重开微澜（round_manager 收尾调用）。"""
        self._ripple_used_this_cycle = False

    def _do_ripple(self, player: Any, m9: Any) -> Tuple[str, bool]:
        """微澜：1 SP 信息型即演——揭示一名可感知单位的当前位置与装备，
        并对其无视隐身/闪避直到 G5 下一个实际结算的 ActionGrant 结束。"""
        round_num = getattr(self.state, "current_round", 1)
        candidates = [a for a in self.state.iter_actors()
                      if a.is_alive() and getattr(a, "location", None)
                      and getattr(a, "player_id", None) != self.player_id]
        if not candidates:
            return m9_text("talents.g5.err_no_ripple_target"), False
        if m9.dispatch_improvise(self.player_id, round_num) is None:
            return m9_text("talents.g5.err_sp_insufficient_cancel"), False
        ctrl = getattr(player, "controller", None)
        picked = None
        if ctrl is not None:
            try:
                picked = ctrl.choose(m9_text("talents.g5.choose_ripple_target_prompt"),
                                     [a.name for a in candidates])
            except Exception:
                picked = None
        target = next((a for a in candidates if a.name == picked), candidates[0])
        self._ripple_used_this_cycle = True
        target._m9_ripple_ignore_stealth_from = self.player_id
        equipped = [getattr(w, "name", "?") for w in getattr(target, "weapons", []) if w]
        items = [getattr(i, "name", "?") for i in getattr(target, "items", []) if i]
        loc = getattr(target, "location", "?")
        equipped_s = '、'.join(equipped) if equipped else m9_text("talents.g5.none")
        items_s = '、'.join(items) if items else m9_text("talents.g5.none")
        self.state.log_event(
            "g5_ripple", player=self.player_id, target=target.player_id,
            location=loc, equipped=equipped, items=items)
        return m9_text("talents.g5.ripple_result", target=target.name, loc=loc,
                       equipped=equipped_s, items=items_s), True

    def _do_poem(self, player: Any) -> Tuple[str, bool]:
        """献诗：T0 选诗篇 + 选目标 → 共享入口（2 SP 公演 + 追忆）。"""
        from engine.m9.talents.poems import POEM_TARGETS
        ctrl = getattr(player, "controller", None)
        poem_list = list(POEM_TARGETS)
        poem_name = poem_list[0]
        if ctrl is not None:
            try:
                poem_name = ctrl.choose(m9_text("talents.g5.choose_poem_prompt"),
                                       poem_list)
            except Exception:
                poem_name = poem_list[0]
        if poem_name not in POEM_TARGETS:
            return m9_text("talents.g5.err_unknown_poem", poem=poem_name), False
        slot = POEM_TARGETS[poem_name]
        # 目标菜单本身只暴露持有对应槽位的合法玩家，避免 AI
        # 选中错槽位后才在 recite 内失败并消费决策机会。
        others = [
            pid for pid in self.state.player_order
            if (self.state.get_player(pid) is not None
                and self.state.get_player(pid).is_alive()
                and str(getattr(self.state.get_player(pid),
                                "talent_slot_id", "")) == slot)
        ]
        target_pid = self.player_id
        if slot != "G5":
            if not others:
                return m9_text("talents.g5.err_no_poem_target", slot=slot), False
            names = [self.state.get_player(pid).name for pid in others]
            picked = None
            if ctrl is not None:
                try:
                    picked = ctrl.choose(
                        m9_text("talents.g5.choose_poem_target_prompt",
                                poem=poem_name, slot=slot), names)
                except Exception:
                    picked = None
            matched = next((pid for pid in others
                            if self.state.get_player(pid).name == picked), None)
            if matched is not None:
                target_pid = matched
        msg = self.poems.recite(
            self.state.get_player(self.player_id), poem_name, target_pid)
        if msg.startswith("❌"):
            return msg, False
        return msg, True

    def feed_combat_round(self, personal: bool = False) -> None:
        """引擎喂入（R3 攻击结算后）：任意有效战斗每轮 +1；小昔涟亲历再 +1。

        两层独立按轮去重（DOC-046）：地图任意有效战斗取得
        `reminiscence_combat_gain`；小昔涟是有效攻击者或承受有效伤害目标时
        再叠加 `reminiscence_combat_personal_bonus`。仅小昔涟形态记账。
        """
        if self.form != FORM_CYRENE:
            return
        round_num = getattr(self.state, "current_round", 0)
        gain = 0.0
        if self._combat_any_fed_round != round_num:
            self._combat_any_fed_round = round_num
            gain += float(_g5("reminiscence_combat_gain", 1))
        if personal and self._combat_personal_fed_round != round_num:
            self._combat_personal_fed_round = round_num
            gain += float(_g5("reminiscence_combat_personal_bonus", 1))
        if gain > 0:
            cap = float(_g5("reminiscence_cap", 24))
            self.sealed_reminiscence = min(
                cap, self.sealed_reminiscence + gain)

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
        if not m9_enabled(self.state):
            return super().on_round_start(round_num)
        me = self.state.get_player(self.player_id)
        if not me:
            return
        if self.form == FORM_HOME:
            from engine import world_clock
            if world_clock.current_phase(self.state) == world_clock.DUSK:
                self._born_demiurge(me)
                self.state.log_event(
                    "G5_DUSK_FORCED_DEMIURGE", player=self.player_id,
                    trigger="r0_home")
            elif (self.incarnations < int(_g5("cyrene_max_incarnations", 3))
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
        self._establish_cyrene_body(me, increment=True, enter_map=True)

    def _establish_cyrene_body(self, me: Any, *, increment: bool,
                               enter_map: bool) -> None:
        self.form = FORM_CYRENE
        if increment:
            self.incarnations += 1
        self.life_ticks = int(_g5("cyrene_life_ticks", 4))
        self._cyrene_life_expired = False
        self._cyrene_established_round = int(
            getattr(self.state, "current_round", 0) or 0)
        if self._ordinary_max_hp is None:
            self._ordinary_max_hp = float(getattr(me, "max_hp", 20))
        cyrene_hp = float(_g5("cyrene_hp", 8))
        me.max_hp = cyrene_hp
        me.hp = cyrene_hp
        me._loot_dropped = False
        if enter_map:
            me.location = f"home_{self.player_id}"
            me.is_awake = True
            self._reset_body_state(me)

    def _born_demiurge(self, me: Any) -> None:
        self.form = FORM_DEMIURGE
        self._cyrene_established_round = None
        if self._ordinary_max_hp is None:
            self._ordinary_max_hp = float(getattr(me, "max_hp", 20))
        me.max_hp = self._ordinary_max_hp
        me.hp = me.max_hp
        me.location = f"home_{self.player_id}"
        me.is_awake = True
        me._loot_dropped = False
        self._reset_body_state(me)

    def _reset_body_state(self, me: Any) -> None:
        """跨肉身状态清理：只保留共通身份资源，不携带锁定/石化/燃烧。"""
        self.state.markers.on_player_death(self.player_id)
        self.state.markers.on_player_wake_up(self.player_id)
        petrify = getattr(self.state, "m9_petrify", None)
        if petrify is not None:
            petrify.remove_by_id(self.player_id)
        me.is_stunned = False
        me.is_shocked = False
        me.is_invisible = False
        me.is_petrified = False
        me.burn_stacks = 0

    def _enter_past(self, me: Any) -> None:
        """闭合退场：非死亡、不进往世层，世界物品留场。"""
        old_location = getattr(me, "location", None)
        self.state.drop_world_items_on_homecoming(me, old_location)
        self._release_body_modules(me)
        self.state.markers.on_player_death(self.player_id)
        self.form = FORM_PAST
        me.is_awake = False
        me.location = None
        me._m9_exit_round = int(getattr(self.state, "current_round", 0) or 0)
        m9 = getattr(self.state, "m9_system", None)
        if m9 is not None:
            m9.queue.remove_permanently(self.player_id)
        scoring = getattr(self.state, "m9_scoring", None)
        if scoring is not None:
            scoring.mark_retreat(self.player_id)
        pp = getattr(self.state, "m9_pp", None)
        if pp is not None:
            pp.freeze(self.player_id)

    def is_retreated(self) -> bool:
        return self.form == FORM_PAST

    def m9_on_lethal(self, target: Any, attacker: Any,
                     source_kind: Optional[str]) -> Optional[str]:
        """小昔涟致死按世界阶段/残局裁决归家、强制毕业或正常死亡。"""
        from engine.m9.combat import is_absolute_death_source
        if self.form == FORM_CYRENE and not is_absolute_death_source(source_kind):
            mode = self._cyrene_end_mode()
            if mode == CYRENE_END_DEMIURGE:
                self._force_demiurge(target, trigger="lethal")
                return "g5_demiurge_birth"
            if mode == CYRENE_END_HOME:
                self._homecoming(target)
                return "g5_homecoming"
            # 终焉或双人残局：不作天赋替代，继续走 T7/保险/真死亡。
            return None
        return None

    def _eligible_player_count(self) -> int:
        """统计仍有胜负资格的玩家身份，不含影身、警察、无人机和 Chorus。"""
        count = 0
        for pid in self.state.player_order:
            player = self.state.players.get(pid)
            if player is None:
                continue
            talent = getattr(player, "talent", None)
            form = getattr(talent, "form", None)
            if form == FORM_PAST or (
                    talent is not None
                    and getattr(talent, "is_retreated", lambda: False)()):
                continue
            # 致死裁决发生时小昔涟 HP 已为 0；仍应按受击前身份计入残局人数。
            if player.is_alive() or pid == self.player_id:
                count += 1
        return count

    def _cyrene_end_mode(self) -> str:
        """统一裁决普通致死与寿命归零，终焉/双人残局优先于黄昏。"""
        from engine import world_clock
        phase = world_clock.current_phase(self.state)
        if (phase == world_clock.APOCALYPSE
                or self._eligible_player_count() <= 2):
            return CYRENE_END_DEATH
        if phase == world_clock.DUSK:
            return CYRENE_END_DEMIURGE
        return CYRENE_END_HOME

    def _force_demiurge(self, me: Any, *, trigger: str) -> None:
        """小昔涟本世结束后立即毕业；保留追忆，不赠行动或最低预算。"""
        old_location = getattr(me, "location", None)
        self.m9_on_combat_event("loss", personal=True)
        self.state.drop_world_items_on_homecoming(me, old_location)
        self._release_body_modules(me)
        self.state.markers.on_player_death(self.player_id)
        petrify = getattr(self.state, "m9_petrify", None)
        if petrify is not None:
            petrify.remove_by_id(self.player_id)
        m9 = getattr(self.state, "m9_system", None)
        if m9 is not None:
            m9.queue.remove_permanently(self.player_id)
        self._born_demiurge(me)
        self.state.log_event(
            "G5_DUSK_FORCED_DEMIURGE", player=self.player_id,
            trigger=trigger, location=old_location,
            reminiscence=self.sealed_reminiscence)

    def _homecoming(self, me: Any) -> None:
        """归家：离开地图、物品原地掉落、下一 R0 转世/诞生。"""
        old_location = getattr(me, "location", None)
        self.m9_on_combat_event("loss", personal=True)
        self.state.drop_world_items_on_homecoming(me, old_location)
        self._release_body_modules(me)
        self.state.markers.on_player_death(self.player_id)
        petrify = getattr(self.state, "m9_petrify", None)
        if petrify is not None:
            petrify.remove_by_id(self.player_id)
        self.form = FORM_HOME
        me.hp = 1
        me.location = None
        me.is_awake = False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is not None:
            m9.queue.remove_permanently(self.player_id)
        self.state.log_event(
            "CYRENE_HOMECOMING", player=self.player_id,
            location=old_location)

    def _release_body_modules(self, me: Any) -> None:
        try:
            from engine.bow_modules import release_on_death
            release_on_death(me, self.state)
        except (AttributeError, ImportError):
            pass

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
        if not m9_enabled(self.state):
            return super().get_t0_option(player)
        if self.form != FORM_DEMIURGE:
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        if self.active_anchor:
            return None
        sp = m9.get_sp(self.player_id)
        round_num = getattr(self.state, "current_round", 1)
        phase = getattr(self.state, "current_phase", "")
        seated = m9._public_holder_by_round.get(round_num) == self.player_id
        public_ready = sp >= 2 and (phase != "r3_actions" or seated)
        if public_ready:
            return {"name": m9_text("talents.g5.t0_anchor_or_poem_name"),
                    "description": m9_text("talents.g5.t0_anchor_or_poem_description"),
                    "m9_kind": "g5_anchor_or_poem"}
        if sp >= 1 and self.ripple_available():
            return {"name": m9_text("talents.g5.t0_ripple_name"),
                    "description": m9_text("talents.g5.t0_ripple_description"),
                    "m9_kind": "g5_ripple"}
        return None

    def execute_t0(self, player: Any):
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().execute_t0(player)
        if self.form != FORM_DEMIURGE:
            return m9_text("talents.g5.err_only_demiurge_can_perform"), False
        if self.active_anchor:
            return m9_text("talents.g5.err_anchor_already_active"), False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return m9_text("talents.g5.err_m9_not_mounted"), False
        sp = m9.get_sp(self.player_id)
        round_num = getattr(self.state, "current_round", 1)
        public_ready = sp >= 2 \
            and m9.assign_public_slot(round_num) == self.player_id
        options: List[str] = []
        if public_ready:
            options += [m9_text("talents.g5.option_anchor"),
                        m9_text("talents.g5.option_poem")]
        if sp >= 1 and self.ripple_available():
            options.append(m9_text("talents.g5.option_ripple"))
        if not options:
            return m9_text("talents.g5.err_no_performance_option"), False
        ctrl = getattr(player, "controller", None)
        try:
            want = ctrl.choose(m9_text("talents.g5.choose_performance_prompt"),
                               options) if ctrl else options[0]
        except Exception:
            want = options[0]
        if want == "微澜":
            return self._do_ripple(player, m9)
        if want == "献诗":
            return self._do_poem(player)
        script = self._collect_anchor_script(player)
        return self.execute_anchor(player, script)

    # 兜底锚定脚本地点表（固定普通地点；投影只校验语法，move 恒有候选）
    _DEFAULT_ANCHOR_LOCATIONS: Tuple[str, ...] = (
        "公园", "医院", "军事基地", "魔法所", "警察局")

    def _collect_anchor_script(self, player: Any) -> List[Tuple]:
        """从 controller 收集 K 槽锚定脚本（接入层，合同 §5.2「玩家自写」）。

        控制器未实现 ``choose_anchor_script`` 或返回非法值时，回退到
        ``build_anchor_fallback_script`` 的「真实预言」脚本（含可落空预言）；
        无法构造可落空预言时返回空列表，锚定会被张力规则拒绝。
        """
        ctrl = getattr(player, "controller", None)
        if ctrl is not None and hasattr(ctrl, "choose_anchor_script"):
            try:
                script = ctrl.choose_anchor_script(player, self.state)
                if isinstance(script, list) and script:
                    return list(script)
            except Exception:
                pass
        return build_anchor_fallback_script(player, self.state)

    def execute_anchor(self, player: Any, script: List[Tuple]) -> Tuple[str, bool]:
        """锚定入口：预检（投影须产出 ≥1 候选）先于 SP/槽/K 追忆消费。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return m9_text("talents.g5.err_m9_disabled"), False
        if self.form != FORM_DEMIURGE:
            return m9_text("talents.g5.err_only_demiurge_can_anchor"), False
        if self.active_anchor:
            return m9_text("talents.g5.err_anchor_already_active"), False
        if self._has_love_wish_on_me():
            return m9_text("talents.g5.err_love_wish_blocks_anchor"), False
        m9 = getattr(self.state, "m9_system", None)
        round_num = getattr(self.state, "current_round", 1)
        k = len(script)
        if not (int(_g5("anchor_min_k", 3)) <= k <= int(_g5("anchor_max_k", 8))):
            return m9_text("talents.g5.err_anchor_k_range", k=k), False
        projector = AnchorScriptProjector(self.state, self)
        bad = projector.validate_script(script)
        if bad is not None:
            return m9_text("talents.g5.err_anchor_slot_illegal",
                           slot=bad + 1), False
        candidates = projector.project(script, k)
        if not candidates:
            return m9_text("talents.g5.err_anchor_no_candidates"), False
        # 张力规则（裁决：合同 §6.4 附带）：未来必须包含至少一个可能落空的预言
        # （DEFEAT/DESTROY/ACQUIRE 依赖目标状态与后续事件，可落空）；
        # 全 RELOCATE 脚本（move 可被强制实现）毫无意义，不给 arc。
        if not any(c.kind in ("DEFEAT", "DESTROY", "ACQUIRE") for c in candidates):
            return m9_text("talents.g5.err_anchor_no_falsifiable"), False
        if m9.get_sp(self.player_id) < 2:
            return m9_text("talents.g5.err_sp_insufficient"), False
        if self.sealed_reminiscence < k:
            return m9_text("talents.g5.err_reminiscence_insufficient"), False
        if m9.assign_public_slot(round_num) != self.player_id:
            return m9_text("talents.g5.err_public_seat_insufficient"), False
        if m9.dispatch_public(self.player_id, round_num) is None:
            return m9_text("talents.g5.err_public_dispatch_failed"), False
        self.sealed_reminiscence -= k
        # 每段脚本独立结算；上一段的“未来闭合/因果改写”不得
        # 成为下一段脚本的状态门。
        self.anchor_results = []
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
        return m9_text("talents.g5.anchor_established",
                       k=k, count=len(candidates)), True

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
        if not m9_enabled(self.state):
            return super().on_round_end(round_num)
        self.poems.tick_love_wishes()
        if self.form == FORM_CYRENE and not self._cyrene_life_expired:
            if self._cyrene_established_round != round_num:
                self.life_ticks -= 1
                if self.life_ticks <= 0:
                    me = self.state.get_player(self.player_id)
                    if me is not None:
                        mode = self._cyrene_end_mode()
                        if mode == CYRENE_END_DEMIURGE:
                            self._force_demiurge(me, trigger="life_expiry")
                        elif mode == CYRENE_END_HOME:
                            self._homecoming(me)
                        else:
                            from engine.m9.combat import (
                                adjudicate_and_finalize_death,
                            )
                            # 正常死亡链可能被 T7/保险救回；同一世的寿命到期只裁决一次，
                            # 不能让已归零的计时器在后续每个 R4 重复杀死获救者。
                            self._cyrene_life_expired = True
                            me.hp = 0
                            adjudicate_and_finalize_death(
                                self.state, me, source_kind="g5_life_expiry",
                                cause=m9_text("talents.g5.cause_life_expiry"))
                    return
        if not self.active_anchor:
            return
        if self.anchor_established_round == round_num:
            return  # 建立轮 R4 不 tick
        self._latch_natural_realizations()
        self._monitor_slot()
        self.anchor_slot_index += 1
        if self.anchor_slot_index >= self.anchor_k:
            self._finish_anchor()

    def _latch_natural_realizations(self) -> None:
        """窗口期扫描（合同 §7.2）：任一未决候选在监控期内自然实现即锁定，
        不要求由 G5 完成、不因后来状态变化抹除。"""
        for cand in self.anchor_candidates:
            if cand.realized or cand.slot_index < self.anchor_slot_index:
                continue
            if self._naturally_realized(cand):
                cand.realized = True
                self.state.log_event(
                    "MATERIALIZED_NATURALLY", player=self.player_id,
                    kind=cand.kind, subject=cand.subject_id)

    def _monitor_slot(self) -> None:
        """当前槽候选：已自然实现 → 跳过；否则再投影（实时状态）→ 强制差分；
        不可达 → 因果改写（脚本失败）。"""
        if self.anchor_slot_index >= len(self.anchor_candidates):
            return
        cand = self.anchor_candidates[self.anchor_slot_index]
        if cand.realized:
            return
        if self._reproject(cand):
            self.state.log_event("MATERIALIZED_BY_ANCHOR", player=self.player_id,
                                 kind=cand.kind, subject=cand.subject_id)
            return
        self.anchor_results.append(m9_text("talents.g5.anchor_result_rewritten"))
        self._fail_anchor(rewritten=True)

    def _naturally_realized(self, cand: EventCandidate) -> bool:
        since = int(self.anchor_established_round or 0)
        if cand.kind == "DEFEAT":
            p = self.state.get_player(cand.subject_id)
            if p is not None and not p.is_alive():
                return True
            return any(
                isinstance(ev, dict) and ev.get("type") == "death"
                and ev.get("player") == cand.subject_id
                and int(ev.get("round", 0) or 0) >= since
                for ev in getattr(self.state, "event_log", []))
        if cand.kind == "RELOCATE":
            # 监控期内曾进入终点即锁定（不要求截止时仍在终点）
            dest = cand.after[0]
            return any(
                isinstance(ev, dict) and ev.get("type") == "move"
                and ev.get("player") == cand.subject_id
                and ev.get("to_loc") == dest
                and int(ev.get("round", 0) or 0) >= since
                for ev in getattr(self.state, "event_log", []))
        if cand.kind == "DESTROY":
            owner = self.state.get_player(cand.source_action[1])
            piece = self._armor_piece_named(owner, cand.subject_id)
            return bool(owner and piece is None)  # 该对象已不存在/摧毁
        if cand.kind == "ACQUIRE":
            me = self.state.get_player(self.player_id)
            return bool(me and self._holds_object_named(me, cand.subject_id))
        return False

    def _source_weapon(self, cand: EventCandidate) -> Optional[Any]:
        """再投影外部帧：来源武器必须仍由 G5 持有（丢失/被缴 → 不可再投影）。"""
        me = self.state.get_player(self.player_id)
        if me is None:
            return None
        action = cand.source_action
        if len(action) < 3 or action[0] != "attack":
            return None
        return self._weapon_by_name(action[2])

    def _source_attack_reachable(self, cand: EventCandidate) -> bool:
        """同来源攻击的真实合法性门（裁决：与 action_enumerator 同语义）——
        近战须同地点且已交战；远程须已锁定目标；范围须同地点。
        再投影 DEFEAT/DESTROY 是“重放来源动作”，不可达即预言落空 → 因果改写。"""
        me = self.state.get_player(self.player_id)
        target = self.state.get_player(cand.source_action[1])
        weapon = self._source_weapon(cand)
        if me is None or target is None or weapon is None:
            return False
        markers = getattr(self.state, "markers", None)
        if markers is None:
            return False
        same_loc = getattr(target, "location", None) == \
            getattr(me, "location", None)
        from controllers.ai.game_query import GameQuery
        rng = GameQuery.get_weapon_range(weapon)
        if rng == "melee":
            return same_loc and markers.has_relation(
                me.player_id, "ENGAGED_WITH", target.player_id)
        if rng == "ranged":
            return markers.has_relation(
                target.player_id, "LOCKED_BY", me.player_id)
        return same_loc  # area

    def _probe_source_attack(self, cand: EventCandidate, target: Any):
        """同来源动作对实时状态的攻击探针；失败返回 None。"""
        weapon = self._source_weapon(cand)
        if weapon is None or target is None or not target.is_alive():
            return None
        from engine.m9.combat import resolve_hit_probe
        from controllers.ai.game_query import GameQuery
        raw = max(0, int(round(GameQuery.get_weapon_damage(weapon))))
        attr = GameQuery.get_weapon_attr(weapon).value
        return resolve_hit_probe(target, raw, attr)

    def _reproject(self, cand: EventCandidate) -> bool:
        """再投影：同来源动作对实时状态重放（合同 §7.3 混合状态帧）。

        DEFEAT/DESTROY 用真实武器对实时 HP/护甲重探针——只有同一动作仍产生
        同一事件才强制差分（DEFEAT 带 absolute_death）；武器丢失、目标痊愈/
        换甲/修甲 → 失败 → 因果改写。RELOCATE 的目的地被毁 → 失败。
        """
        if cand.kind == "DEFEAT":
            p = self.state.get_player(cand.subject_id)
            if p is not None and p.is_alive():
                if not self._source_attack_reachable(cand):
                    return False  # 不可达：预言落空 → 因果改写
                hit = self._probe_source_attack(cand, p)
                if hit is not None and hit.damage >= getattr(p, "hp", 0):
                    me = self.state.get_player(self.player_id)
                    from engine.m9.combat import resolve_damage
                    result = resolve_damage(
                        me, p, weapon=self._source_weapon(cand),
                        game_state=self.state, source_kind="g5_anchor")
                    # 命中掷骰/擦伤可能让目标幸存：只有真实致死才算未来实现
                    if bool(result.get("killed")) or not p.is_alive():
                        return True
        if cand.kind == "DESTROY":
            owner = self.state.get_player(cand.source_action[1])
            piece = self._armor_piece_named(owner, cand.subject_id)
            if owner is not None and piece is not None:
                if not self._source_attack_reachable(cand):
                    return False  # 不可达：预言落空 → 因果改写
                hit = self._probe_source_attack(cand, owner)
                if hit is not None and cand.subject_id in hit.broken:
                    owner.armor.remove_piece(piece)
                    return True
        if cand.kind == "RELOCATE":
            p = self.state.get_player(cand.subject_id)
            if p is not None and p.is_alive():
                dest = cand.after[0]
                destroyed = getattr(self.state, "m9_destroyed_locations",
                                    set()) or set()
                if dest in destroyed:
                    return False  # 目的地永久封闭 → 改写
                p.location = dest
                return True
        if cand.kind == "ACQUIRE":
            me = self.state.get_player(self.player_id)
            if me is not None and self._transfer_ground_object(me, cand.subject_id):
                return True
        return False

    @staticmethod
    def _armor_piece_named(owner: Any, piece_name: str) -> Optional[Any]:
        """在 owner 的活跃护甲中按名称找一件（DESTROY 候选对象）。"""
        if owner is None:
            return None
        armor = getattr(owner, "armor", None)
        if armor is None:
            return None
        for piece in armor.get_all_active():
            if getattr(piece, "name", "") == piece_name:
                return piece
        return None

    @staticmethod
    def _holds_object_named(me: Any, object_name: str) -> bool:
        """G5 是否已持有该名称的武器/护甲/物品（ACQUIRE 自然实现）。"""
        if me is None:
            return False
        for w in getattr(me, "weapons", []) or []:
            if w and getattr(w, "name", "") == object_name:
                return True
        armor = getattr(me, "armor", None)
        if armor is not None:
            for piece in armor.get_all_active():
                if getattr(piece, "name", "") == object_name:
                    return True
        for it in getattr(me, "items", []) or []:
            if it and getattr(it, "name", "") == object_name:
                return True
        return False

    def _transfer_ground_object(self, me: Any, object_name: str) -> bool:
        """把地面遗落物中该名称的可转移对象交给 G5（ACQUIRE 强制差分）。"""
        from models.equipment import ArmorLayer
        piles = getattr(self.state, "ground_loot", {}) or {}
        for pile in piles.values():
            if not isinstance(pile, dict):
                continue
            for key in ("weapons", "items", "armor"):
                for entry in list(pile.get(key, []) or []):
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("name") != object_name:
                        continue
                    obj = entry.get("object")
                    if obj is None:
                        continue
                    pile[key].remove(entry)
                    if key == "weapons":
                        me.weapons.append(obj)
                    elif key == "items":
                        me.items.append(obj)
                    else:
                        target = me.armor.inner \
                            if getattr(obj, "layer", None) == ArmorLayer.INNER \
                            else me.armor.outer
                        target.append(obj)
                    return True
        return False

    def _finish_anchor(self) -> None:
        """收尾：全候选实现 → 未来闭合（窄回溯 + 水晶花）；否则失败。"""
        me = self.state.get_player(self.player_id)
        if not self.anchor_results:
            self.anchor_results.append(m9_text("talents.g5.anchor_result_closure"))
            self.total_closures += 1
            self._rewind(me)
            self._grant_flower()
            if self.total_closures >= 2 and not self._double_closure_granted:
                # 第二次未来闭合：三章制完结条的第三章事件（arc RFC v0.1）；
                # arc/PP 统一由 game_state.m9_arc 扫描授予，本处只登记事实。
                self._double_closure_granted = True
                if getattr(self.state, "m9_arc", None) is None:
                    # 无 ledger 的隔离夹具/旧测试兼容：维持旧私有挂接
                    scoring = getattr(self.state, "m9_scoring", None)
                    if scoring is not None and hasattr(scoring, "add_arc"):
                        scoring.add_arc(
                            self.player_id,
                            int(_g5("double_anchor_arc_count", 1)))
                    pp = getattr(self.state, "m9_pp", None)
                    if pp is not None:
                        pp.earn(self.player_id, int(_pp("arc_progress", 1)))
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
        self.flower_arc_granted = True
        self.flowers_granted += 1
        if getattr(self.state, "m9_arc", None) is None:
            # 无 ledger 的隔离夹具/旧测试兼容：维持旧私有挂接
            scoring = getattr(self.state, "m9_scoring", None)
            if scoring is not None and hasattr(scoring, "add_arc"):
                scoring.add_arc(self.player_id,
                                int(_g5("crystal_flower_arc_count", 1)))
        # 水晶花只登记事件事实；arc/PP 由 game_state.m9_arc 按章节表授予。
        self.state.log_event("crystal_flower", player=self.player_id)

    def _fail_anchor(self, rewritten: bool = False) -> None:
        if rewritten:
            self._grant_flower()
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

    def break_love_wish(self, target_pid: str) -> bool:
        """G5 主动伤害爱愿持有者时，在公共伤害预检层先破愿。"""
        return self.love_wish.pop(target_pid, None) is not None
