"""
rl/expert_stack.py
──────────────────
M9 专家栈：多人分层部署的注册表 + 确定性路由 + 每专家独立 LSTM 状态。

定位（docs/m9/rl/m9_rl_next_steps.md §3，P0）：
  把"三人分层系统毕业"落成仓库内可执行事实。本模块只做三件事——
  1. EXPERT_CHECKPOINTS：十个课程专家 checkpoint 的唯一注册表
     （opening/postkill 两个默认分流 + 八个专项专家）；
  2. route()：基于公开游戏状态快照（GameFacts）的确定性路由纯函数，
     优先级先命中先赢，每步返回 (expert, reason) 保证可归因；
  3. ExpertStackModel：predict 侧路由器，伪装成单模型塞进
     rl/winrate_eval.evaluate_policy（评估单一信源不变）；各专家维护
     独立递归状态，episode 边界统一重置。

路由阈值是 v1 初始值，依据对应课程场景的布置事实（rl/env.py
_apply_course_scenario）；毕业评估校准后必须回写
docs/m9/rl/m9_rl_next_steps.md 的注册表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

PROFILE = "m9-rfc"

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ═════════════════════════════════════════════════════════════════════════════
#  专家注册表（单一信源；docs/m9/rl/m9_rl_next_steps.md §3 的表格是它的摘要）
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ExpertSpec:
    """单个课程专家的注册项。"""

    key: str
    path: str           # 相对仓库根的 checkpoint 路径
    role: str           # 职责一句话（与文档注册表保持一致）


EXPERT_CHECKPOINTS: Dict[str, ExpertSpec] = {
    spec.key: spec
    for spec in (
        ExpertSpec(
            "opening",
            "checkpoints/codex_m9_course_full_basicai_economy_ppo_s142/final.zip",
            "首杀前通用开局（默认）",
        ),
        ExpertSpec(
            "postkill",
            "checkpoints/codex_m9_course_real_postkill_dagger_s148/final.zip",
            "首杀后经济续战（默认）",
        ),
        ExpertSpec(
            "pp_finish",
            "checkpoints/codex_m9_course_pp_damage_s121/final.zip",
            "PP 斩杀",
        ),
        ExpertSpec(
            "survival_heal",
            "checkpoints/codex_m9_course_survival_dual_s124/final.zip",
            "治疗/购甲生存",
        ),
        ExpertSpec(
            "armor_repair",
            "checkpoints/codex_m9_course_survival_repair_bc_s131/final.zip",
            "修甲",
        ),
        ExpertSpec(
            "sharpen",
            "checkpoints/codex_m9_course_equipment_sharpen_bc_s135/final.zip",
            "磨刀",
        ),
        ExpertSpec(
            "detection",
            "checkpoints/codex_m9_course_visibility_thermal_bc_s136/final.zip",
            "隐身对策/探测",
        ),
        ExpertSpec(
            "attribute",
            "checkpoints/codex_m9_course_attribute_detection_bc_s144/final.zip",
            "属性克制选择",
        ),
        ExpertSpec(
            "fire_antiarmor",
            "checkpoints/codex_m9_course_fire_antiarmor_bc_s152/final.zip",
            "火矢反甲",
        ),
        ExpertSpec(
            "police",
            "checkpoints/codex_m9_course_police_election50k_s78/final.zip",
            "警察竞选/指挥",
        ),
    )
}

DEFAULT_EXPERT_KEY = "opening"

# 路由优先级：先命中先赢（police 最高，默认分流最低）。
ROUTE_PRIORITY: Tuple[str, ...] = (
    "police",
    "pp_finish",
    "fire_antiarmor",
    "survival_heal",
    "armor_repair",
    "sharpen",
    "detection",
    "attribute",
)


# ═════════════════════════════════════════════════════════════════════════════
#  路由阈值（v1 初始值；依据课程场景布置事实，毕业评估后校准并回写文档）
# ═════════════════════════════════════════════════════════════════════════════

# pp_damage 课程事实：5 HP 靶正好比小刀基础伤害多 1，先花 1 PP 取得下一击 +1。
PP_FINISH_MIN_PP = 1
PP_FINISH_HP_MARGIN = 1
# survival 课程从 4 HP 起教；8 为满血 20 的生存警戒线（待校准）。
SURVIVAL_HEAL_HP_MAX = 8
# v1.1：生存专家只在可执行治疗的地点接管（医院=治疗，商店=购甲）；
# 低血量但在别处时由默认专家负责移动，防止"低血锁死"拖满轮次。
SURVIVAL_LOCATIONS: Tuple[str, ...] = ("医院", "商店")
# v1.1：专项专家每局决策预算（detection 的"存在隐身对手且无探测"是
# 目标型条件，专家自己未必能清除它——v1 基线中 detection 吃掉 3p 27% /
# 4p 40% 的决策并拖满轮次；预算耗尽后回落默认专家）。
EXPERT_DECISION_BUDGETS: Dict[str, int] = {
    "detection": 4,
}
# v1.2（P4.1 根因修复）：局内粘性专家。pp_finish 的触发条件（PP 足额且
# 差 1 点过斩杀线）在 PP 花掉后即消失，但课程序列的剩余步骤仍属该专家
# 的职责域——v1 路由下 s121 每局只拿到 1 拍标签（40/881），学生学不到
# "PP 加伤 → 补刀"的完整链。启用后本局其余拍继续由它接管。
EXPERT_STICKY: frozenset = frozenset({"pp_finish"})
# 修甲课程从 1 耐久陶瓷甲开始；耐久比低于 50% 视为需要修理（待校准）。
ARMOR_REPAIR_MAX_RATIO = 0.5
# action_space 磨刀门槛的镜像：base_damage >= 2 视为已磨。
KNIFE_SHARPENED_DAMAGE = 2
# choose situation 命中警察族的关键词（引擎实际 situation 名含英文
# "captain_election"——全量采集实证中文关键词会漏掉整个 police 专家）。
POLICE_SITUATION_KEYWORDS: Tuple[str, ...] = (
    "police", "警察", "队长", "竞选", "指挥", "举报", "captain",
)
# 属性课三轨探测集合（rl/env.py 火矢课布置：{"普通", "魔法", "科技"}）。
DETECTION_ATTR_TARGETS = frozenset({"普通", "魔法", "科技"})


# ═════════════════════════════════════════════════════════════════════════════
#  公开状态快照
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class OpponentFacts:
    """单个对手槽的公开事实（None 槽不产生记录）。"""

    alive: bool
    hp: int
    invisible: bool
    has_ceramic_armor: bool


@dataclass(frozen=True)
class GameFacts:
    """路由所需的公开游戏状态快照。

    全部字段经 getattr 兜底采集；引擎字段缺失时取默认值，对应路由规则
    自然不触发（路由回退到默认专家），不抛错。
    """

    kill_count: int = 0
    hp: int = 0
    max_hp: int = 0
    pp: int = 0
    location: str = ""
    choose_mode: bool = False
    situation: str = ""
    has_bow: bool = False
    best_weapon_damage: int = 0
    has_unsharpened_knife: bool = False
    has_detection: bool = False
    detection_attrs: frozenset = frozenset()
    own_armor_min_ratio: float = 1.0
    opponents: Tuple[OpponentFacts, ...] = ()


def _armor_pieces(armor: Any) -> List[Any]:
    pieces: List[Any] = []
    for layer in ("outer", "inner"):
        pieces.extend(getattr(armor, layer, ()) or ())
    return [piece for piece in pieces if piece is not None]


def _opponent_facts(opponent: Any) -> Optional[OpponentFacts]:
    if opponent is None:
        return None
    ceramic = any(
        str(getattr(piece, "name", "")) == "陶瓷护甲"
        for piece in _armor_pieces(getattr(opponent, "armor", None))
    )
    return OpponentFacts(
        alive=bool(getattr(opponent, "hp", 0) > 0),
        hp=int(getattr(opponent, "hp", 0) or 0),
        invisible=bool(getattr(opponent, "is_invisible", False)),
        has_ceramic_armor=ceramic,
    )


def collect_game_facts(
    player: Any,
    game_state: Any,
    *,
    choose_mode: bool = False,
    choose_context: Optional[Mapping[str, Any]] = None,
) -> GameFacts:
    """从 (player, game_state) 采集路由快照；只读公开状态，绝不修改。"""
    from rl.action_space import get_opponent_slots

    weapons = [w for w in (getattr(player, "weapons", None) or []) if w is not None]
    best_damage = max(
        (int(getattr(w, "base_damage", 0) or 0) for w in weapons), default=0,
    )
    has_unsharpened_knife = any(
        str(getattr(w, "name", "")) == "小刀"
        and int(getattr(w, "base_damage", 0) or 0) < KNIFE_SHARPENED_DAMAGE
        for w in weapons
    )

    ratios: List[float] = []
    for piece in _armor_pieces(getattr(player, "armor", None)):
        durability = float(getattr(piece, "durability", 0) or 0)
        max_durability = float(getattr(piece, "max_durability", 0) or 0)
        if max_durability > 0 and not bool(getattr(piece, "is_broken", False)):
            ratios.append(durability / max_durability)

    pp_ledger = getattr(game_state, "m9_pp", None)
    pp = 0
    if pp_ledger is not None and callable(getattr(pp_ledger, "balance", None)):
        try:
            pp = int(pp_ledger.balance(getattr(player, "player_id", "")) or 0)
        except Exception:  # noqa: BLE001 —— PP 读取失败按 0 处理
            pp = 0

    detection_attrs = frozenset(
        str(item) for item in (getattr(player, "detection_attrs", None) or ())
    )
    situation = str((choose_context or {}).get("situation", "") or "")
    opponents = tuple(
        facts
        for facts in (
            _opponent_facts(opp)
            for opp in get_opponent_slots(player, game_state)
        )
        if facts is not None
    )

    return GameFacts(
        kill_count=int(getattr(player, "kill_count", 0) or 0),
        hp=int(getattr(player, "hp", 0) or 0),
        max_hp=int(getattr(player, "max_hp", 0) or 0),
        pp=pp,
        location=str(getattr(player, "location", "") or ""),
        choose_mode=bool(choose_mode),
        situation=situation,
        has_bow=any(str(getattr(w, "name", "")) == "弓" for w in weapons),
        best_weapon_damage=best_damage,
        has_unsharpened_knife=has_unsharpened_knife,
        has_detection=bool(getattr(player, "has_detection", False)),
        detection_attrs=detection_attrs,
        own_armor_min_ratio=min(ratios) if ratios else 1.0,
        opponents=opponents,
    )


# ═════════════════════════════════════════════════════════════════════════════
#  确定性路由（纯函数）
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RouteDecision:
    """一次路由的结果：专家 key + 可归因理由。"""

    expert: str
    reason: str


def _alive_opponents(facts: GameFacts) -> Tuple[OpponentFacts, ...]:
    return tuple(opp for opp in facts.opponents if opp.alive)


def _rule_police(facts: GameFacts) -> Optional[str]:
    if facts.choose_mode and any(
        keyword in facts.situation for keyword in POLICE_SITUATION_KEYWORDS
    ):
        return f"choose situation={facts.situation!r} 命中警察族"
    return None


def _rule_pp_finish(facts: GameFacts) -> Optional[str]:
    if facts.pp < PP_FINISH_MIN_PP or facts.best_weapon_damage <= 0:
        return None
    for opp in _alive_opponents(facts):
        margin = opp.hp - facts.best_weapon_damage
        if 0 < margin <= PP_FINISH_HP_MARGIN:
            return (
                f"PP={facts.pp} 且对手 HP−最优伤害={margin} ∈ (0,"
                f"{PP_FINISH_HP_MARGIN}]"
            )
    return None


def _rule_fire_antiarmor(facts: GameFacts) -> Optional[str]:
    if not facts.has_bow:
        return None
    if any(opp.has_ceramic_armor for opp in _alive_opponents(facts)):
        return "持弓且存活对手带陶瓷护甲"
    return None


def _rule_survival_heal(facts: GameFacts) -> Optional[str]:
    if not (
        0 < facts.hp <= SURVIVAL_HEAL_HP_MAX
        and facts.location in SURVIVAL_LOCATIONS
        and _alive_opponents(facts)
    ):
        return None
    return (
        f"HP={facts.hp} ≤ {SURVIVAL_HEAL_HP_MAX} 且位于{facts.location}"
        "（可执行治疗/购甲）"
    )


def _rule_armor_repair(facts: GameFacts) -> Optional[str]:
    if facts.own_armor_min_ratio < ARMOR_REPAIR_MAX_RATIO:
        return (
            f"护甲最低耐久比={facts.own_armor_min_ratio:.2f} < "
            f"{ARMOR_REPAIR_MAX_RATIO}"
        )
    return None


def _rule_sharpen(facts: GameFacts) -> Optional[str]:
    if facts.kill_count >= 1 and facts.has_unsharpened_knife:
        return "首杀后仍有未磨小刀"
    return None


def _rule_detection(facts: GameFacts) -> Optional[str]:
    if facts.has_detection:
        return None
    if any(opp.invisible for opp in _alive_opponents(facts)):
        return "存在隐身存活对手且自身无探测"
    return None


def _rule_attribute(facts: GameFacts) -> Optional[str]:
    if not facts.has_detection:
        return None
    missing = DETECTION_ATTR_TARGETS - facts.detection_attrs
    if missing and _alive_opponents(facts):
        return f"探测属性不全（缺 {sorted(missing)}）且存在存活对手"
    return None


_ROUTING_RULES: Tuple[Tuple[str, Callable[[GameFacts], Optional[str]]], ...] = (
    ("police", _rule_police),
    ("pp_finish", _rule_pp_finish),
    ("fire_antiarmor", _rule_fire_antiarmor),
    ("survival_heal", _rule_survival_heal),
    ("armor_repair", _rule_armor_repair),
    ("sharpen", _rule_sharpen),
    ("detection", _rule_detection),
    ("attribute", _rule_attribute),
)


def route_with_budgets(
    facts: GameFacts,
    spent: Optional[Mapping[str, int]] = None,
    sticky_active: Optional[Mapping[str, int]] = None,
) -> RouteDecision:
    """按 ROUTE_PRIORITY 评估规则，跳过本局预算耗尽的专项专家。

    ``spent`` 是本局各专家已消耗的决策数（EpisodeSummary.routing_counts）；
    未登记或预算 0 表示不限量。默认分流（opening/postkill）不受预算约束。
    粘性专家（EXPERT_STICKY）本局一旦启用即接管余局——但 ``sticky_active``
    （专家 → 启用时的 kill_count）记录的击杀数一旦变化即解除：PP 斩杀
    完成后交回默认分流追第二杀（P4.1：s121 整局锁死导致标签只教到首杀）。
    """
    for sticky in sorted(EXPERT_STICKY):
        if int((spent or {}).get(sticky, 0)) > 0 and (
            sticky_active is None
            or sticky_active.get(sticky) == facts.kill_count
        ):
            return RouteDecision(sticky, f"粘性接管：{sticky} 本局已启用")
    for expert, rule in _ROUTING_RULES:
        budget = EXPERT_DECISION_BUDGETS.get(expert, 0)
        if budget > 0 and int((spent or {}).get(expert, 0)) >= budget:
            continue
        reason = rule(facts)
        if reason is not None:
            return RouteDecision(expert, reason)
    if facts.kill_count == 0:
        return RouteDecision("opening", "默认：首杀前通用开局")
    return RouteDecision("postkill", "默认：首杀后经济续战")


def route(facts: GameFacts) -> RouteDecision:
    """无预算视角的纯路由（等价 route_with_budgets(facts, {})）。"""
    return route_with_budgets(facts)


# ═════════════════════════════════════════════════════════════════════════════
#  predict 侧路由器
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class EpisodeSummary:
    """单局路由统计（二杀/续战口径的 kill 为决策点最大可见值）。"""

    final_kill_count: int = 0
    decisions: int = 0
    routing_counts: Dict[str, int] = field(default_factory=dict)
    switches: int = 0


class ExpertStackModel:
    """把多个专家包装成单模型接口（evaluate_policy 可直接使用）。

    - 每个专家独立维护递归状态；episode_start=True 时统一清空；
    - facts_fn 在每次 predict 时采集 GameFacts 并路由；
    - routing_log 记录 (步, expert, reason)；episode_summaries 按局汇总
      路由计数与击杀进度（供二杀率/续战率/切换率统计）。
    """

    def __init__(
        self,
        experts: Mapping[str, Any],
        facts_fn: Callable[[], GameFacts],
        *,
        logits_fn: Optional[
            Callable[[Any, np.ndarray, Any, bool, np.ndarray], np.ndarray]
        ] = None,
    ) -> None:
        missing = set(ROUTE_PRIORITY) | {"opening", "postkill"}
        absent = missing - set(experts)
        if absent:
            raise ValueError(f"专家栈缺少必要专家: {sorted(absent)}")
        self._experts: Dict[str, Any] = dict(experts)
        self._facts_fn = facts_fn
        self._states: Dict[str, Any] = {}
        self._logits_fn = logits_fn
        # 粘性专家 → 启用时的 kill_count（击杀数变化即解除粘性）
        self._sticky_marks: Dict[str, int] = {}
        # P3/P4.1 采集用：强制指定专家（绕过路由；police 等命令模态课程
        # 的公开状态路由不可达，只能显式指派教师）。
        self.forced_expert: Optional[str] = None
        # P3 采集模式：predict 时额外计算被选专家的掩码概率分布，
        # 结果存 capture_last（不改变动作/状态语义）。
        self.capture_enabled = False
        self.capture_last: Optional[Dict[str, Any]] = None
        self.routing_log: List[Tuple[int, str, str]] = []
        self.episode_summaries: List[EpisodeSummary] = []
        self._step = 0
        self._current: Optional[EpisodeSummary] = None
        self._current_expert: Optional[str] = None

    # schema 预检需要（rl/schema.validate_model_against_env）
    @property
    def observation_space(self) -> Any:
        return self._experts[DEFAULT_EXPERT_KEY].observation_space

    @property
    def action_space(self) -> Any:
        return self._experts[DEFAULT_EXPERT_KEY].action_space

    def _close_episode(self, next_kill_count: int) -> None:
        if self._current is not None:
            self._current.final_kill_count = max(
                self._current.final_kill_count, next_kill_count,
            )
            self.episode_summaries.append(self._current)
        self._current = EpisodeSummary()
        self._current_expert = None
        self._states.clear()
        self._sticky_marks.clear()

    def flush(self) -> None:
        """收口尚未遇到下一 episode 边界的最后一局（评估结束前调用）。

        只归档统计，不清空各专家递归状态（与 episode 边界语义不同）。
        """
        if self._current is not None and self._current.decisions > 0:
            self.episode_summaries.append(self._current)
        self._current = None
        self._current_expert = None

    def predict(
        self,
        obs: np.ndarray,
        state: Any = None,
        episode_start: Optional[np.ndarray] = None,
        action_masks: Optional[np.ndarray] = None,
        deterministic: bool = True,
    ) -> Tuple[np.ndarray, Any]:
        """路由到选中专家并透传预测。

        边界检测双保险：``episode_start`` 任一真，或 ``state is None``
        （winrate_eval 每局首拍 state=None 且 episode_start=[False]——
        局末的 [True] 会被外层循环重置丢弃，单靠 episode_start 会把
        整个 seed 的局合并成一个汇总）。返回非 None 哨兵 state 供
        调用方回传，用以区分"局内下一拍"与"新局首拍"。
        """
        facts = self._facts_fn()
        boundary = bool(
            (episode_start is not None and np.any(np.asarray(episode_start)))
            or state is None
        )
        if boundary or self._current is None:
            self._close_episode(facts.kill_count)

        if self.forced_expert is not None:
            decision = RouteDecision(
                self.forced_expert, f"采集强制指派：{self.forced_expert}",
            )
        else:
            decision = route_with_budgets(
                facts,
                self._current.routing_counts
                if self._current is not None else None,
                sticky_active=self._sticky_marks,
            )
            if (
                decision.expert in EXPERT_STICKY
                and decision.expert not in self._sticky_marks
            ):
                self._sticky_marks[decision.expert] = facts.kill_count
        expert = self._experts[decision.expert]
        state_before = self._states.get(decision.expert)
        probs = None
        if self.capture_enabled and self._logits_fn is not None:
            probs = self._logits_fn(
                expert, obs, state_before, boundary, action_masks,
            )
        action, expert_state = expert.predict(
            obs,
            state=state_before,
            episode_start=np.array([boundary]),
            action_masks=action_masks,
            deterministic=deterministic,
        )
        self._states[decision.expert] = expert_state
        if self.capture_enabled:
            self.capture_last = {
                "expert": decision.expert,
                "reason": decision.reason,
                "probs": probs,
                "boundary": boundary,
            }
        else:
            self.capture_last = None

        assert self._current is not None
        self._current.decisions += 1
        self._current.final_kill_count = max(
            self._current.final_kill_count, facts.kill_count,
        )
        self._current.routing_counts[decision.expert] = (
            self._current.routing_counts.get(decision.expert, 0) + 1
        )
        if (
            self._current_expert is not None
            and self._current_expert != decision.expert
        ):
            self._current.switches += 1
        self._current_expert = decision.expert
        self.routing_log.append((self._step, decision.expert, decision.reason))
        self._step += 1
        return action, True  # 非 None 哨兵：调用方回传后表示"局内下一拍"


def load_experts(
    keys: Optional[Sequence[str]] = None,
    *,
    device: str = "cpu",
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """按注册表加载专家 checkpoint；缺文件时给出可操作的错误。"""
    from rl.maskable_recurrent import MaskableRecurrentPPO

    base = Path(root) if root is not None else _REPO_ROOT
    selected = list(keys) if keys else list(EXPERT_CHECKPOINTS)
    experts: Dict[str, Any] = {}
    for key in selected:
        spec = EXPERT_CHECKPOINTS[key]
        path = base / spec.path
        if not path.exists():
            raise FileNotFoundError(
                f"专家 {key} 的 checkpoint 不存在: {path}"
                f"（职责：{spec.role}）"
            )
        experts[key] = MaskableRecurrentPPO.load(str(path), device=device)
    return experts


def expert_masked_probs(
    model: Any,
    obs: np.ndarray,
    state: Any,
    boundary: bool,
    action_masks: np.ndarray,
) -> np.ndarray:
    """计算专家在 (obs, state, mask) 下的掩码后概率分布（teacher logits）。

    仅用于 P3 蒸馏数据采集：与 predict 分开做一次 forward（部署语义仍由
    expert.predict 保证，采集/部署零漂移）。tensor 流程与
    tools/rung0_chain_probe._predict_with_commitment_prior 同源。
    """
    import torch

    policy = model.policy
    policy.set_training_mode(False)
    obs_array = np.asarray(obs, dtype=np.float32)
    if obs_array.ndim == 1:
        obs_array = obs_array[None]
    mask_array = np.asarray(action_masks)
    if mask_array.ndim == 1:
        mask_array = mask_array[None]
    obs_tensor, _ = policy.obs_to_tensor(obs_array)
    if state is None:
        hidden = np.zeros(policy.lstm_hidden_state_shape, dtype=np.float32)
        state = (hidden, hidden.copy())
    torch_state = tuple(
        torch.as_tensor(value, dtype=torch.float32, device=policy.device)
        for value in state
    )
    starts = torch.as_tensor(
        [boundary], dtype=torch.float32, device=policy.device,
    )
    with torch.no_grad():
        distribution, _next_state = policy.get_distribution(
            obs_tensor, torch_state, starts, action_masks=mask_array,
        )
        probs = (
            distribution.distribution.probs.detach().cpu().numpy()[0].copy()
        )
    return probs


def make_env_facts_fn(env: Any) -> Callable[[], GameFacts]:
    """绑定 BadtimeWarEnv 的 GameFacts 采集器（只读公开状态）。

    经 ``unwrapped`` 取底层 env：gymnasium Wrapper 对下划线私有属性
    （_rl_player/_state 等）抛 AttributeError，直接 getattr 会兜底成 None。
    """

    def facts_fn() -> GameFacts:
        target = getattr(env, "unwrapped", env)
        return collect_game_facts(
            getattr(target, "_rl_player", None),
            getattr(target, "_state", None),
            choose_mode=bool(getattr(target, "_choose_mode", False)),
            choose_context=getattr(target, "_choose_context", None),
        )

    return facts_fn
