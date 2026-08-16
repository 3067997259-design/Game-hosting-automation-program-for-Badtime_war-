"""M9 通用剧情分（arc）：三章制完结条 ChapterRegistry / ChapterLedger。

合同：`docs/m9/current/m9_arc_universal_rfc_v0.1.md`（profile: m9-rfc）。

公共规则（已冻结）：
- 每名玩家每局 arc 上限 `m9_system.scoring_m9.arc_cap`（默认 3）；
- 一章一事：一次事件至多点亮一章；
- 顺序解锁：第一章（登台）→ 第二章（高光）→ 第三章（谢幕）；
- 第一章 = 本局第一次真实公演（SlotOutcome.performance_performed 且
  ActionSystem.performance_kind == "public"）；
- 第二章/第三章按 `CHAPTER_REGISTRY` 的事件谓词逐槽位判定；
- 每章授予 `arc_count +1` 与 `m9_system.pp.arc_progress` PP。

本模块只被 `m9-rfc` profile 引用；`v2exp` 不 import 本模块。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from engine.balance import get as bget

# ── 章节键（顺序解锁；debut 由 ActionSystem 实况挂钩授予）──
CHAPTER_DEBUT = "debut"
CHAPTER_SPOTLIGHT = "spotlight"
CHAPTER_CURTAIN = "curtain"
CHAPTER_ORDER: Tuple[str, ...] = (CHAPTER_DEBUT, CHAPTER_SPOTLIGHT, CHAPTER_CURTAIN)

# 影身 actor id 前缀（G2 代理 actor 的击杀归光身章节）
SHADOW_PREFIX = "G2:shadow@"


def _arc_cap() -> int:
    """每名玩家每局 arc 上限（公共规则）。"""
    return int(bget("m9_system", "scoring_m9", "arc_cap", default=3))


def _arc_progress_pp() -> int:
    """每点亮一章授予的 PP（B4 §3.2 完结条进展）。"""
    return int(bget("m9_system", "pp", "arc_progress", default=1))


def _owner_of_actor(actor_id: Optional[str]) -> Optional[str]:
    """G2 影身等代理 actor 的章节归所属玩家。"""
    if not isinstance(actor_id, str) or not actor_id.startswith(SHADOW_PREFIX):
        return actor_id
    return actor_id[len(SHADOW_PREFIX):]


def _event_round(event: Dict[str, Any]) -> int:
    return int(event.get("round", 0) or 0)


def _death_source(event: Dict[str, Any]) -> str:
    return str(event.get("source_kind", "") or event.get("cause", "") or "")


def _hexagram_is_hojump(event: Dict[str, Any]) -> bool:
    """剪刀对布 = 或跃在渊（T4 RFC；天机指定被禁，故只可能来自随机出拳）。"""
    if event.get("specified"):
        return str(event.get("specified", "")) == "hojump"
    pair = {event.get("my_choice"), event.get("opp_choice")}
    return pair == {"剪刀", "布"}


class ChapterLedger:
    """三章制完结条台账：事件扫描 + 实况挂钩 + 幂等授予。"""

    def __init__(self, game_state: Any = None) -> None:
        self._state = game_state
        self._cursor = 0                      # event_log 扫描游标
        self._chapters: Dict[str, Set[str]] = {}
        self._public_rounds: Dict[str, Set[int]] = {}
        self._full_extra_rounds: Dict[str, Set[int]] = {}
        self._hunt_rounds: Dict[str, Set[int]] = {}
        self._revive_round: Dict[str, int] = {}
        self._borrow_rounds: Dict[str, Set[int]] = {}
        self._g3_windows: Dict[str, List[Optional[int]]] = {}
        self._g4_enter_seen: Set[str] = set()
        self._pending_enforcement: Dict[str, Tuple[int, str]] = {}
        self._star_attack_toggle: Dict[str, bool] = {}
        self._t3_curtain_toggle: Dict[str, bool] = {}
        self._g2_shadow_kill_pending: Set[str] = set()

    # ── 接线 ──
    def attach_state(self, game_state: Any) -> None:
        self._state = game_state

    def reset(self) -> None:
        self.__init__(self._state)

    # ── 查询（BasicAI / ActionSystem 只读）──
    def has_chapter(self, pid: str, key: str) -> bool:
        return key in self._chapters.get(pid, set())

    def has_debut(self, pid: str) -> bool:
        return self.has_chapter(pid, CHAPTER_DEBUT)

    def chapters_of(self, pid: str) -> Set[str]:
        return set(self._chapters.get(pid, set()))

    # ── 实况挂钩（ActionSystem 调用）──
    def on_public_performance(self, pid: str, global_round: int) -> None:
        """第一章·登台：第一次真实公演（resolve_slot 后立即授予）。"""
        owner = _owner_of_actor(pid)
        if owner is None:
            return
        self._public_rounds.setdefault(owner, set()).add(global_round)
        self._grant(owner, CHAPTER_DEBUT)
        # G2 影身击杀先于登台：spotlight 此前顺序解锁失败，登台后补授。
        if owner in self._g2_shadow_kill_pending:
            self._g2_shadow_kill_pending.discard(owner)
            self._grant_ordered(owner, CHAPTER_SPOTLIGHT)

    def mark_full_extra_round(self, pid: str, global_round: int) -> None:
        owner = _owner_of_actor(pid)
        if owner is not None:
            self._full_extra_rounds.setdefault(owner, set()).add(global_round)

    # ── 扫描与授予 ──
    def scan(self, game_state: Any) -> None:
        """扫描新事件并按序尝试第二/三章；终局前可重复调用（幂等）。"""
        self.attach_state(game_state)
        events = list(getattr(game_state, "event_log", []) or [])
        new_events = events[self._cursor:]
        self._cursor = len(events)
        for event in new_events:
            if not isinstance(event, dict):
                continue
            self._apply_event(game_state, event)
        self._apply_state_predicates(game_state)

    def _apply_event(self, game_state: Any, event: Dict[str, Any]) -> None:
        etype = str(event.get("type", ""))
        pid = _owner_of_actor(event.get("player"))
        killer = _owner_of_actor(event.get("killer"))
        rnd = _event_round(event)

        # G2 影身首次击杀（第二章）；光身第二章也用同一条 death 事件。
        if etype == "death":
            for candidate in {killer}:
                self._try_spotlight_from_death(game_state, candidate, event)
            victim = _owner_of_actor(event.get("player"))
            if victim:
                self._try_curtain_from_death(game_state, victim, event)
            # T6 警力执法当轮死亡 → 队长第三章（pending_enforcement 匹配）
            self._try_enforcement_curtain(game_state, event)
            # T7 复仇归来（第三章）
            if killer:
                self._try_revenge_curtain(game_state, killer, rnd)
            return

        # ── 各槽位事件谓词 ──
        if etype == "star_attack" and pid:
            if int(event.get("hits", 0) or 0) >= 2 \
                    and self._star_attack_should_grant(game_state, pid):
                self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
            return
        if etype == "hexagram_cast" and pid and _hexagram_is_hojump(event):
            self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
            return
        if etype in ("m9_captain", "hotline") and pid:
            self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
            return
        if etype == "resurrection_trigger" and pid:
            self._revive_round[pid] = rnd
            self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
            return
        if etype == "g0_relic_effect" and pid:
            self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
            return
        if etype == "firefly_supernova" and pid:
            if int(event.get("hits", 0) or 0) >= 2:
                self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
            return
        if etype in ("g1_propagation_death", "location_destroyed") and pid:
            self._grant_ordered(pid, CHAPTER_CURTAIN)
            return
        if etype == "g2_last_song_heard" and pid:
            self._grant_ordered(pid, CHAPTER_CURTAIN)
            return
        if etype == "m9_g3_expand" and pid:
            self._g3_windows[pid] = [rnd, None]
            return
        if etype == "m9_g3_collapse" and pid:
            window = self._g3_windows.setdefault(pid, [None, None])
            window[1] = rnd
            if bool(event.get("terminal", False)):
                self._grant_ordered(pid, CHAPTER_CURTAIN)
            return
        if etype == "g4_savior_enter" and pid:
            self._g4_enter_seen.add(pid)
            return
        if etype == "g4_savior_exit" and pid and pid in self._g4_enter_seen:
            self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
            return
        if etype == "g4_judgment_completed" and pid:
            self._grant_ordered(pid, CHAPTER_CURTAIN)
            return
        if etype == "crystal_flower" and pid:
            self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
            return
        if etype == "g5_double_closure" and pid:
            self._grant_ordered(pid, CHAPTER_CURTAIN)
            return
        if etype == "g6_borrow_core" and pid:
            self._borrow_rounds.setdefault(pid, set()).add(rnd)
            self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
            return
        if etype == "t2_hunt_reaction" and pid:
            self._hunt_rounds.setdefault(pid, set()).add(rnd)
            return
        if etype == "m9_police_enforcement":
            captain = _owner_of_actor(event.get("captain"))
            target = event.get("target")
            if captain and target:
                self._pending_enforcement[captain] = (rnd, str(target))

    def _star_attack_should_grant(self, game_state: Any, pid: str) -> bool:
        """T3 星落高光章减半（2026-09 风洞 R7 机制压顶）：

        只有 T3 本人每两次「命中 ≥2 的星落」记一次第二章；其他来源
        （G6 借用）不受影响。确定性交替门：第 1/3/5… 次给章。
        """
        if self._slot_of(game_state, pid) != "T3":
            return True
        want = not self._star_attack_toggle.get(pid, False)
        self._star_attack_toggle[pid] = want
        return want

    def _apply_state_predicates(self, game_state: Any) -> None:
        """扫描期状态谓词：G7 Terror 进入（谢幕章）。"""
        for p in getattr(game_state, "players", {}).values():
            if p is None:
                continue
            talent = getattr(p, "talent", None)
            if talent is not None and bool(getattr(talent, "is_terror", False)):
                self._grant_ordered(p.player_id, CHAPTER_CURTAIN)

    # ── 死亡事件派生谓词 ──
    def _try_spotlight_from_death(self, game_state: Any, pid: Optional[str],
                                  event: Dict[str, Any]) -> None:
        if not pid:
            return
        src = _death_source(event)
        rnd = _event_round(event)
        slot = self._slot_of(game_state, pid)
        if slot == "T1" and src == "t1_core_slash":
            self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
        elif slot == "T2" and src == "t2_core_attack":
            if rnd in self._hunt_rounds.get(pid, set()):
                self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
        elif slot == "T3" and src == "t3_starfall":
            # R7 机制压顶：T3 星落击杀的谢幕章同样隔次记章（与高光章交替门并行）。
            want = not self._t3_curtain_toggle.get(pid, False)
            self._t3_curtain_toggle[pid] = want
            if want:
                self._grant_ordered(pid, CHAPTER_CURTAIN)
        elif slot == "G0" and src == "g0_crossfire":
            self._grant_ordered(pid, CHAPTER_CURTAIN)
        elif slot == "G2" and str(event.get("killer", "")).startswith(
                SHADOW_PREFIX):
            if not self._grant_ordered(pid, CHAPTER_SPOTLIGHT):
                self._g2_shadow_kill_pending.add(pid)
        elif slot == "G3":
            window = self._g3_windows.get(pid)
            if window and window[0] is not None \
                    and (window[1] is None or window[0] <= rnd <= window[1]):
                self._grant_ordered(pid, CHAPTER_SPOTLIGHT)
        elif slot == "G7":
            # 第一版近似：任意击杀记高光；宏内击杀待战斗来源标识后收紧。
            self._grant_ordered(pid, CHAPTER_SPOTLIGHT)

    def _try_curtain_from_death(self, game_state: Any, victim_pid: Optional[str],
                                event: Dict[str, Any]) -> None:
        """击杀者视角的谢幕章判定（victim 与 killer 同名候选处理）。"""
        killer = _owner_of_actor(event.get("killer"))
        if not killer:
            return
        src = _death_source(event)
        rnd = _event_round(event)
        slot = self._slot_of(game_state, killer)
        if slot == "T1" and src == "t1_core_slash" \
                and rnd in self._public_rounds.get(killer, set()):
            self._grant_ordered(killer, CHAPTER_CURTAIN)
        elif slot == "T2" and src == "t2_core_attack" \
                and rnd in self._public_rounds.get(killer, set()):
            self._grant_ordered(killer, CHAPTER_CURTAIN)
        elif slot == "T4" and rnd in self._full_extra_rounds.get(killer, set()):
            self._grant_ordered(killer, CHAPTER_CURTAIN)
        elif slot == "G6" and rnd in self._borrow_rounds.get(killer, set()):
            self._grant_ordered(killer, CHAPTER_CURTAIN)

    def _try_revenge_curtain(self, game_state: Any, killer: str, rnd: int) -> None:
        slot = self._slot_of(game_state, killer)
        if slot == "T7" and rnd > self._revive_round.get(killer, -1):
            self._grant_ordered(killer, CHAPTER_CURTAIN)

    def _try_enforcement_curtain(self, game_state: Any, event: Dict[str, Any]) -> None:
        victim = _owner_of_actor(event.get("player"))
        rnd = _event_round(event)
        for captain, (ernd, target) in list(self._pending_enforcement.items()):
            if ernd == rnd and target == victim:
                self._grant_ordered(captain, CHAPTER_CURTAIN)

    # ── 槽位与授予 ──
    def _slot_of(self, game_state: Any, pid: str) -> Optional[str]:
        player = None
        if hasattr(game_state, "get_player"):
            player = game_state.get_player(pid)
        if player is None:
            players = getattr(game_state, "players", {}) or {}
            player = players.get(pid)
        if player is None:
            return None
        return str(getattr(player, "talent_slot_id", "") or "")

    def _grant_ordered(self, pid: str, key: str) -> bool:
        """顺序解锁：目标章节的前一章节必须已点亮。"""
        index = CHAPTER_ORDER.index(key)
        if index > 0 and not self.has_chapter(pid, CHAPTER_ORDER[index - 1]):
            return False
        return self._grant(pid, key)

    def _grant(self, pid: str, key: str) -> bool:
        """幂等授予：写章节标记、arc_count 与 arc_progress PP。"""
        if not pid or key not in CHAPTER_ORDER:
            return False
        chapters = self._chapters.setdefault(pid, set())
        if key in chapters or len(chapters) >= _arc_cap():
            return False
        chapters.add(key)
        state = self._state
        if state is not None:
            scoring = getattr(state, "m9_scoring", None)
            if scoring is not None and hasattr(scoring, "add_arc"):
                try:
                    scoring.add_arc(pid, 1)
                except Exception:
                    pass
            pp = getattr(state, "m9_pp", None)
            if pp is not None and hasattr(pp, "earn"):
                try:
                    pp.earn(pid, _arc_progress_pp())
                except Exception:
                    pass
        return True
