"""
PoliceMind —— 统一警察策略（非黑箱、可独立测试）

设计原则：
1. 所有方法接收明确参数，不依赖隐式状态
2. 每个方法有单一职责，方法名即意图
3. 决策依据用 NamedTuple/dataclass 显式表达
4. 详细的调试日志输出，便于观察AI"在想什么"

解决问题：
- 警察逻辑以前散落在 PoliceMixin、CombatMixin、EvaluationMixin、controller.py
- 现在集中在一个类中，所有警察相关决策经过同一个入口
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple, Dict, Any, Set

from controllers.ai.constants import (
    EFFECTIVE_AGAINST, POLICE_AOE_WEAPONS,
    debug_ai_basic,
)
from controllers.ai.minds.base import BaseMind, MindAssessment
from controllers.ai.strategies.base_strategy import DecisionPhase
from engine.experiments import is_enabled as _is_exp_enabled
from engine.m9.text import m9_text


class PoliceStance(Enum):
    """AI对警察体系的态度"""
    BUILD = "build"       # 主动建设警察（加入、竞选队长）
    RESIST = "resist"     # 对抗警察（获取AOE、击杀队长）
    IGNORE = "ignore"     # 警察不影响我（无队长或警察已瘫痪）


@dataclass
class PoliceSituation:
    """警察态势快照 —— 所有警察相关决策的输入"""
    # 警察系统是否存在
    police_exists: bool = False
    # 队长信息
    captain_id: Optional[str] = None
    i_am_captain: bool = False
    foreign_captain_exists: bool = False  # 有非自己的队长
    # 警察单位统计
    total_units: int = 0
    alive_units: int = 0
    active_units: int = 0
    # 对"我"的威胁
    active_units_at_my_location: int = 0        # 同地点活跃警察数
    i_am_report_target: bool = False            # 我被举报
    report_phase: str = "idle"                  # idle / reported / dispatched
    # 对"其他目标"的保护
    protected_target_count: int = 0              # 受警察保护的目标数
    all_targets_protected: bool = False          # 所有存活目标都受保护
    # 威信
    authority: int = 0
    # 推荐态度
    recommended_stance: PoliceStance = PoliceStance.IGNORE
    # 人类可读的决策理由
    reasoning: str = ""


class PoliceMind(BaseMind):
    """统一警察策略。

    使用方式：
        mind = PoliceMind(debug_name="AI_张三")
        situation = mind.assess(player, state, police_cache, threat_scores, my_location)
    """

    # ════════════════════════════════════════════════════════════
    #  核心：警察态势评估（替代散落的 _is_pursued / _is_stuck 等）
    # ════════════════════════════════════════════════════════════

    def assess(
        self,
        player: Any,
        state: Any,
        police_cache: Dict[str, Any],
        threat_scores: Dict[str, float],
        my_location: str,
        strategy: Any = None,
        snapshot: Optional[Any] = None,
        assessment: Optional[Any] = None,
    ) -> PoliceSituation:
        """评估警察态势，返回结构化的 PoliceSituation。

        这是所有警察决策的入口。调用一次，得到完整评估。
        """
        sit = PoliceSituation()

        # ── 快照化派生输出（2026-08-12）：M9 警务摘要写入 AssessmentLayer ──
        self._snapshot_derived(snapshot, assessment)

        # ── M9 警务修正（2026-08-12）：m9_police 存在 → 构造兼容 police_cache ──
        # 使 M9 局的警察评估真正生效（此前 M9 cache 无 has_police 键导致早退）。
        # 深层保护判定（_decide_stance 内 v2exp 引擎读法）属后续批次。
        m9 = snapshot.m9 if snapshot is not None else None
        if m9 is not None and police_cache.get("m9_police") is not None:
            if not m9.police_disabled or m9.police_cases > 0 \
                    or m9.police_captain is not None:
                roster = police_cache.get("roster", [])
                police_cache = {
                    **police_cache,
                    "has_police": True,
                    "captain_id": m9.police_captain,
                    "is_captain": m9.police_captain == self._player_id(player),
                    "units": roster,
                    "alive_count": sum(1 for u in roster
                                       if u.get("alive")),
                    "active_count": sum(1 for u in roster
                                        if u.get("alive")),
                    "report_target": m9.police_wanted,
                    "report_phase": (
                        "reported" if m9.police_wanted else "idle"),
                    "authority": 0,
                }

        # ── 基础信息 ──
        sit.police_exists = police_cache.get("has_police", False)
        if not sit.police_exists:
            sit.reasoning = "警察系统未激活"
            return sit

        sit.captain_id = police_cache.get("captain_id")
        sit.i_am_captain = police_cache.get("is_captain", False)
        sit.foreign_captain_exists = (
            sit.captain_id is not None
            and sit.captain_id != self._player_id(player)
        )
        sit.authority = police_cache.get("authority", 0)
        sit.total_units = len(police_cache.get("units", []))
        sit.alive_units = police_cache.get("alive_count", 0)
        sit.active_units = police_cache.get("active_count", 0)

        # ── 对"我"的直接威胁 ──
        sit.i_am_report_target = (
            police_cache.get("report_target") == self._player_id(player)
        )
        sit.report_phase = police_cache.get("report_phase", "idle")
        sit.active_units_at_my_location = self._count_active_units_at(
            police_cache, my_location
        )

        # ── 对目标的保护程度 ──
        pe = getattr(state, 'police_engine', None)
        if pe:
            alive_enemies = self._query.get_alive_enemy_ids(state, self._player_id(player))
            protected = [
                eid for eid in alive_enemies
                if pe.is_protected_by_police(eid)
            ]
            sit.protected_target_count = len(protected)
            sit.all_targets_protected = (
                len(protected) > 0 and len(protected) == len(alive_enemies)
            )

        # ── 判定推荐态度 ──
        sit.recommended_stance, sit.reasoning = self._decide_stance(
            player, sit, threat_scores, police_cache, strategy, state
        )

        self._log_assessment(sit)
        return sit

    # ── 快照化派生输出（2026-08-12）──
    def _snapshot_derived(self, snapshot, assessment):
        """M9 警务摘要写入 AssessmentLayer.notes（行为不变，纯派生）。"""
        if snapshot is None or assessment is None:
            return
        m9 = snapshot.m9
        if m9 is None:
            return
        assessment.notes["m9_police_state"] = m9_text(
            "ai.police_mind.m9_police_state",
            cases=m9.police_cases,
            wanted=m9.police_wanted or m9_text("ai.police_mind.none"),
            captain=m9.police_captain or m9_text("ai.police_mind.none"),
            disabled=m9.police_disabled,
        )
        if m9.barrier_active:
            assessment.notes["barrier_location"] = (
                m9.barrier_location or "?")

    def _decide_stance(
        self,
        player: Any,
        sit: PoliceSituation,
        threat_scores: Dict[str, float],
        police_cache: Dict[str, Any],
        strategy: Any = None,
        state: Any = None,
    ) -> Tuple[PoliceStance, str]:
        """判定AI应该对警察采取什么态度。优先使用Strategy的建议。"""

        # Strategy覆盖（最高优先级）
        if strategy and state and hasattr(strategy, 'get_police_stance'):
            stance_str = strategy.get_police_stance(player, state)
            if stance_str == "build":
                return PoliceStance.BUILD, "Strategy: 人格偏好警察建设"
            elif stance_str == "resist":
                return PoliceStance.RESIST, "Strategy: 人格要求对抗警察"
            elif stance_str == "ignore":
                return PoliceStance.IGNORE, "Strategy: 人格忽略警察"

        # 我是队长 → BUILD（管理警察体系）
        if sit.i_am_captain:
            return PoliceStance.BUILD, "我是队长，主导警察体系"

        # 有外部队长 + 我被警察追击 → RESIST（最高优先级）
        if sit.foreign_captain_exists:
            pursued = (
                sit.i_am_report_target
                and sit.report_phase in ("reported", "dispatched")
            )
            active_nearby = sit.active_units_at_my_location > 0
            if pursued or active_nearby:
                return PoliceStance.RESIST, (
                    f"外部队长存在且警察威胁直接："
                    f"举报={'是' if pursued else '否'}，"
                    f"同地点活跃警察={sit.active_units_at_my_location}"
                )

        # 外部队长存在但我没被追 → 警察体系成型，但未直接威胁我
        # 仍然建议 RESIST（因为警察成型后几乎无法应对）
        if sit.foreign_captain_exists and sit.alive_units >= 2:
            return PoliceStance.RESIST, (
                f"外部队长存在且警察体系已成型"
                f"（{sit.alive_units}存活单位），"
                f"被动等待只会更被动"
            )

        # 所有目标受保护但我没有AOE → RESIST（需要获取武器）
        if sit.all_targets_protected:
            return PoliceStance.RESIST, (
                f"所有{ sit.protected_target_count }个目标均受警察保护，"
                f"必须获取AOE武器突破"
            )

        # 无队长，没有直接威胁 → IGNORE
        if not sit.foreign_captain_exists:
            return PoliceStance.IGNORE, "无外部队长，警察体系未构成威胁"

        return PoliceStance.IGNORE, "警察体系不足以影响当前决策"

    # ════════════════════════════════════════════════════════════
    #  AOE 武器策略：如何穿透警察保护
    # ════════════════════════════════════════════════════════════

    def can_damage_through_protection(
        self,
        player: Any,
        target: Any,
        state: Any,
        talent_adjusted_damage: float,
        outer_armor_attrs: List[Any],
        inner_armor_attrs: List[Any],
        aoe_weapon_names: List[str],
        player_weapons: List[Any],
        learned_spells: Set[str],
    ) -> Tuple[bool, str]:
        """检查能否对受警察保护的目标造成伤害。

        Returns:
            (can_damage: bool, reason: str)
            reason 说明为什么能/不能——这是非黑箱化的关键
        """
        pe = getattr(state, 'police_engine', None)
        if not pe:
            return True, "无警察引擎"
        if not pe.is_protected_by_police(target.player_id):
            return True, "目标不受警察保护"

        threshold = pe.get_protection_threshold(target.player_id)

        if _is_exp_enabled("m8_ai"):
            # D1：净伤为基——AOE 净伤>0 可绕过保护；单发净伤超阈值可硬穿（无硬克制）
            for aoe_name in aoe_weapon_names:
                aoe_w = self._find_weapon_by_name(player_weapons, aoe_name)
                if aoe_w is None:
                    from models.equipment import make_weapon
                    aoe_w = make_weapon(aoe_name)
                if aoe_w is None:
                    continue
                if self._needs_charge(aoe_w) and not self._is_charged(aoe_w):
                    continue  # 未蓄力
                if self._query.net_damage(player, aoe_w, target) > 0:
                    return True, f"AOE武器 {aoe_name} 净伤可无视警察保护攻击"
            best = max(
                (self._query.net_damage(player, w, target)
                 for w in getattr(player, 'weapons', []) if w),
                default=0.0,
            )
            if best > threshold:
                return True, f"净伤({best:.1f})超过警察保护阈值({threshold})，可硬穿"
            return False, f"净伤({best:.1f})不足穿透警察保护阈值({threshold})"

        # 路径1：伤害超过保护阈值（硬穿）
        if talent_adjusted_damage > threshold:
            return True, (
                f"伤害({talent_adjusted_damage:.1f})超过警察保护阈值({threshold})，可硬穿"
            )

        # 路径2：有有效AOE武器（AOE无视保护）
        target_attrs = set(outer_armor_attrs) if outer_armor_attrs else set(inner_armor_attrs)

        for aoe_name in aoe_weapon_names:
            aoe_attr = self._get_aoe_weapon_attr(
                aoe_name, player_weapons, learned_spells
            )
            if aoe_attr is None:
                continue
            effective_set = EFFECTIVE_AGAINST.get(aoe_attr, set())
            if target_attrs and not any(a in effective_set for a in target_attrs):
                continue  # 属性被克制
            # 检查是否需要蓄力
            aoe_w = self._find_weapon_by_name(player_weapons, aoe_name)
            if aoe_w and self._needs_charge(aoe_w) and not self._is_charged(aoe_w):
                continue  # 未蓄力
            return True, f"AOE武器 {aoe_name} 可无视警察保护攻击"

        # 路径3：有AOE但被克制
        if aoe_weapon_names and target_attrs:
            return False, (
                f"AOE武器属性被目标护甲克制（目标属性={target_attrs}），无法穿透"
            )

        # 路径4：完全没有AOE
        if not aoe_weapon_names:
            return False, (
                f"无AOE武器且伤害({talent_adjusted_damage:.1f})不足穿透阈值({threshold})"
            )

        return False, "无法穿透警察保护"

    def get_aoe_acquisition_commands(
        self,
        player: Any,
        state: Any,
        available: List[str],
        target_armor_attrs: Set[Any],
        my_location: str,
        has_pass: bool,
        learned_spells: Set[str],
    ) -> List[str]:
        """生成获取有效AOE武器的命令序列。

        决策逻辑：
        1. 目标有ORDINARY护甲 → 必须TECH AOE（电磁步枪），去军事基地
        2. 否则 → MAGIC AOE也行（地震/地动山摇），去人少的地方
        """
        from utils.attribute import Attribute

        commands: List[str] = []
        need_tech_aoe = any(a == Attribute.ORDINARY for a in target_armor_attrs)

        if need_tech_aoe:
            # 必须有科技AOE（电磁步枪）→ 军事基地
            has_emr = any(
                w.name == "电磁步枪"
                for w in getattr(player, 'weapons', []) if w
            )
            if has_emr:
                emr = next(
                    (w for w in player.weapons if w and w.name == "电磁步枪"), None
                )
                if emr and not getattr(emr, 'is_charged', False):
                    if "special" in available:
                        debug_ai_basic(self._debug_name,
                            "PoliceMind: 有电磁步枪但未蓄力 → 蓄力")
                        commands.append("special 蓄力电磁步枪")
                # 已蓄力 → 不需要额外命令，交给攻击逻辑
            elif my_location == "军事基地" and "interact" in available:
                if not has_pass:
                    debug_ai_basic(self._debug_name,
                        "PoliceMind: 在军事基地但无通行证 → 先办证")
                    commands.append("interact 通行证")
                else:
                    debug_ai_basic(self._debug_name,
                        "PoliceMind: 在军事基地 → 拿电磁步枪")
                    commands.append("interact 电磁步枪")
            elif "move" in available:
                debug_ai_basic(self._debug_name,
                    "PoliceMind: 需要科技AOE → 前往军事基地")
                commands.append("move 军事基地")
        else:
            # 魔法AOE也可行 → 选人少的地点
            enemies_magic = self._query.count_players_at("魔法所", state, self._player_id(player))
            enemies_military = self._query.count_players_at("军事基地", state, self._player_id(player))

            if enemies_magic <= enemies_military:
                # 去魔法所
                if my_location == "魔法所" and "interact" in available:
                    if "地动山摇" not in learned_spells and "地震" in learned_spells:
                        debug_ai_basic(self._debug_name,
                            "PoliceMind: 在魔法所 → 升级到地动山摇")
                        commands.append("interact 地动山摇")
                    elif "地震" not in learned_spells:
                        debug_ai_basic(self._debug_name,
                            "PoliceMind: 在魔法所 → 学地震")
                        commands.append("interact 地震")
                elif "move" in available:
                    debug_ai_basic(self._debug_name,
                        "PoliceMind: 需要魔法AOE → 前往魔法所")
                    commands.append("move 魔法所")
            else:
                # 去军事基地
                if my_location == "军事基地" and "interact" in available:
                    debug_ai_basic(self._debug_name,
                        "PoliceMind: 在军事基地 → 拿电磁步枪")
                    commands.append("interact 电磁步枪")
                elif "move" in available:
                    debug_ai_basic(self._debug_name,
                        "PoliceMind: 需要AOE → 前往军事基地")
                    commands.append("move 军事基地")

        return commands

    # ════════════════════════════════════════════════════════════
    #  工具方法
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def has_any_aoe(player: Any) -> bool:
        """检查玩家是否有任何AOE武器（已学法术也算）"""
        for w in getattr(player, 'weapons', []):
            name = w.name if hasattr(w, 'name') else str(w)
            if name in POLICE_AOE_WEAPONS:
                return True
        learned = getattr(player, 'learned_spells', set())
        return "地震" in learned or "地动山摇" in learned


    @staticmethod
    def _player_id(player: Any) -> Optional[str]:
        return getattr(player, 'player_id', None)

    @staticmethod
    def _count_active_units_at(police_cache: Dict, location: str) -> int:
        count = 0
        for unit in police_cache.get("units", []):
            if (unit.get("is_active") and unit.get("is_alive")
                    and unit.get("location") == location):
                count += 1
        return count

    @staticmethod
    def _get_aoe_weapon_attr(
        aoe_name: str, weapons: List[Any], learned_spells: Set[str]
    ) -> Optional[Any]:
        """获取AOE武器的属性。法术类从make_weapon获取。"""
        from utils.attribute import Attribute
        from models.equipment import make_weapon as _make_weapon

        w = PoliceMind._find_weapon_by_name(weapons, aoe_name)
        if w:
            return getattr(w, 'attribute', None)
        # 法术：通过make_weapon创建临时实例获取属性
        if aoe_name in learned_spells:
            temp = _make_weapon(aoe_name)
            if temp:
                return getattr(temp, 'attribute', None)
        return None

    @staticmethod
    def _find_weapon_by_name(weapons: List[Any], name: str) -> Optional[Any]:
        for w in weapons:
            if w and getattr(w, 'name', '') == name:
                return w
        return None

    @staticmethod
    def _needs_charge(weapon: Any) -> bool:
        return bool(getattr(weapon, 'requires_charge', False)
                    and getattr(weapon, 'charge_mandatory', True))

    @staticmethod
    def _is_charged(weapon: Any) -> bool:
        return bool(getattr(weapon, 'is_charged', False))

    # ════════════════════════════════════════════════════════════
    #  调试输出
    # ════════════════════════════════════════════════════════════

    def _log_assessment(self, sit: PoliceSituation) -> None:
        """输出结构化的警察态势评估日志（调试级别1即可见）"""
        if not sit.police_exists:
            return
        parts = [
            f"PoliceMind评估: 态度={sit.recommended_stance.value}",
            f"队长={'自己' if sit.i_am_captain else '外部' if sit.foreign_captain_exists else '无'}",
            f"存活警察={sit.alive_units}/{sit.total_units}",
            f"活跃警察={sit.active_units}",
        ]
        if sit.i_am_report_target:
            parts.append(f"被举报(阶段={sit.report_phase})")
        if sit.active_units_at_my_location > 0:
            parts.append(f"同地点活跃警察={sit.active_units_at_my_location}")
        if sit.all_targets_protected:
            parts.append(f"所有{sit.protected_target_count}个目标受保护")
        parts.append(f"理由: {sit.reasoning}")
        debug_ai_basic(self._debug_name, " | ".join(parts))
