"""M9 十四天赋聚光接口的抽象纸面推演器。

本工具不调用现行游戏引擎，也不声称预测胜率。它只验证设计稿中的结构量：
聚光取得频率、因目标不合法而让位的次数、一次性伏笔兑现概率，以及用
"标准行动等价值"表示的演出预算。现行 V2.0 规则不会因此改变。
"""

from __future__ import annotations

import argparse
import random
import statistics
from dataclasses import dataclass, field
from itertools import permutations
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


TALENT_ORDER: Tuple[str, ...] = (
    "T1",
    "T2",
    "T3",
    "T4",
    "T5",
    "T6",
    "T7",
    "G1",
    "G2",
    "G3",
    "G4",
    "G5",
    "G6",
    "G7",
)


def initial_g5_reminiscence(player_count: int) -> int:
    """按初始玩家数给 G5 提供残局起步追忆。"""

    missing_players = max(0, 6 - player_count)
    return (5 * missing_players + 1) // 2


@dataclass(frozen=True)
class TalentSpec:
    """一个天赋在抽象模型中的稳定参数。"""

    code: str
    name: str
    legal_probability: float
    spotlight_value: float
    mode: str = "cast"


TALENT_SPECS: Dict[str, TalentSpec] = {
    "T1": TalentSpec("T1", "一刀缭断", 0.58, 2.00),
    "T2": TalentSpec("T2", "剪刀手", 0.78, 1.70),
    "T3": TalentSpec("T3", "天星", 0.46, 2.20),
    "T4": TalentSpec("T4", "六爻", 0.76, 1.70),
    "T5": TalentSpec("T5", "combo", 0.82, 1.30),
    "T6": TalentSpec("T6", "朝阳好市民", 0.62, 1.30),
    "T7": TalentSpec("T7", "死者苏生", 1.00, 0.00, "foreshadow"),
    "G1": TalentSpec("G1", "火萤", 0.82, 1.70),
    "G2": TalentSpec("G2", "请一直注视着我", 1.00, 0.80, "set_piece"),
    "G3": TalentSpec("G3", "神话之外", 0.52, 1.80),
    "G4": TalentSpec("G4", "愿负世", 0.88, 1.20, "turning_point"),
    "G5": TalentSpec("G5", "往世的涟漪", 1.00, 1.00, "resource"),
    "G6": TalentSpec("G6", "要有笑声", 0.78, 1.40),
    "G7": TalentSpec("G7", "星野", 1.00, 1.00, "loadout"),
}


G7_COMMAND_COSTS: Dict[str, int] = {
    "shield": 2,
    "shoot": 2,
    "reload": 0,
    "hold": 1,
    "throw": 1,
    "medicine": 0,
    "dash": 1,
    "cancel": 0,
    "find": 1,
    "lock": 1,
}
G7_OFFENSIVE_COMMANDS = frozenset({"shoot", "throw"})
G7_STANCE_COMMANDS = frozenset({"shield", "hold"})
PLAYER_SCALING_TALENTS = frozenset({"T1", "T2", "T3", "T4", "T6", "G1", "G3", "G4"})


def is_legal_g7_macro(commands: Sequence[str], budget: int = 4) -> bool:
    """验证 M9 星野宏的命令槽、临时 Cost 与载荷边界。"""

    if not commands or len(commands) > 4 or len(set(commands)) != len(commands):
        return False
    if any(command not in G7_COMMAND_COSTS for command in commands):
        return False
    if sum(G7_COMMAND_COSTS[command] for command in commands) > budget:
        return False
    if sum(command in G7_OFFENSIVE_COMMANDS for command in commands) > 1:
        return False
    if sum(command in G7_STANCE_COMMANDS for command in commands) > 1:
        return False
    if "dash" in commands:
        if "hold" not in commands or commands.index("hold") > commands.index("dash"):
            return False
    if "cancel" in commands:
        prior_commands = commands[: commands.index("cancel")]
        if not any(command in G7_STANCE_COMMANDS for command in prior_commands):
            return False
    return True


def g7_macro_sae(commands: Sequence[str]) -> float:
    """按载荷类别估算一个合法星野宏的标准行动等价值。"""

    if not is_legal_g7_macro(commands, budget=6):
        raise ValueError("illegal G7 macro")
    payload = 1.0 if "shoot" in commands else 0.8 if "throw" in commands else 0.0
    stance = 0.55 if any(command in G7_STANCE_COMMANDS for command in commands) else 0.0
    mobility = 0.35 if "dash" in commands else 0.0
    targeting = 0.25 if "find" in commands or "lock" in commands else 0.0
    support = 0.25 if "reload" in commands or "medicine" in commands else 0.0
    return payload + stance + mobility + targeting + support


def enumerate_g7_macros(budget: int = 4) -> List[Tuple[Tuple[str, ...], float]]:
    """枚举最多四条命令的合法星野宏及其抽象 SAE。"""

    commands = tuple(G7_COMMAND_COSTS)
    macros: List[Tuple[Tuple[str, ...], float]] = []
    for length in range(1, 5):
        for candidate in permutations(commands, length):
            if is_legal_g7_macro(candidate, budget):
                macros.append((candidate, g7_macro_sae(candidate)))
    return macros


def terror_attack_capacity(
    extra_hp: float,
    incoming_damage_per_round: float = 0.0,
) -> int:
    """估算 Terror 连续攻击直至额外生命归零前能打出的波数。"""

    if extra_hp <= 0 or incoming_damage_per_round < 0:
        raise ValueError("invalid Terror durability")
    attacks = 0
    remaining_hp = extra_hp
    while remaining_hp > 0:
        attacks += 1
        remaining_hp -= 1.5
        if remaining_hp <= 0:
            break
        remaining_hp -= incoming_damage_per_round
    return attacks


def resolve_t5_chart(
    judgments: Sequence[str],
    rescue_one_miss: bool = False,
) -> str:
    """结算 T5 谱面；聚光只能把一个 Miss 修正为 Good。"""

    if not judgments or len(judgments) > 3:
        raise ValueError("T5 chart must contain one to three notes")
    normalized = [judgment.upper() for judgment in judgments]
    if any(judgment not in {"P", "G", "M"} for judgment in normalized):
        raise ValueError("unknown T5 judgment")
    if rescue_one_miss and "M" in normalized:
        normalized[normalized.index("M")] = "G"
    if all(judgment == "P" for judgment in normalized):
        return "fc"
    if "M" not in normalized:
        return "clear"
    if any(judgment != "M" for judgment in normalized):
        return "partial"
    return "failed"


@dataclass
class TalentRuntime:
    """单局中焦点天赋需要保存的最小状态。"""

    spent: bool = False
    pending: bool = False
    prepared: bool = False
    stage_rounds: int = 0
    form_rounds: int = 0
    embers: int = 0
    reminiscence: int = 0
    applause_bonuses_this_cycle: int = 0
    first_full_ripple_used: bool = False
    minor_ripple_available: bool = True
    g7_preparations: int = 0


@dataclass
class TrialResult:
    """一局纸面推演的汇总。"""

    uses: int = 0
    offers: int = 0
    yields: int = 0
    impact: float = 0.0
    first_use_round: Optional[int] = None
    full_payoffs: int = 0
    stage_start_round: Optional[int] = None


@dataclass
class AggregateResult:
    """多局试验的均值与分位数。"""

    talent: str
    players: int
    rounds: int
    uses_mean: float
    offers_mean: float
    yields_mean: float
    yield_rate: float
    impact_mean: float
    first_use_mean: float
    payoff_mean: float
    payoff_probability: float
    stage_start_mean: float
    uses_p95: float


@dataclass
class MixedTrialResult:
    """一桌真实天赋混排时的聚光吞吐。"""

    uses: List[int]
    offers: List[int]
    yields: List[int]
    impacts: List[float]
    non_stage_rounds: int = 0
    stage_rounds: int = 0
    empty_rounds: int = 0
    used_rounds: int = 0
    yielded_rounds: int = 0
    max_dry_streak: int = 0


@dataclass
class MixedAggregateResult:
    """混排多局的全局指标。"""

    roster: Tuple[str, ...]
    rounds: int
    r0_handoff: bool
    handoff_limit: int
    utilization: float
    yield_rate: float
    empty_rate: float
    stage_share: float
    max_dry_streak_p95: float
    uses_by_talent: Dict[str, float]
    impacts_by_talent: Dict[str, float]


@dataclass
class GameModel:
    """一名焦点天赋与若干通用对手共享 FIFO 队列的抽象局。"""

    talent: str
    players: int
    rounds: int
    event_probability: float
    rng: random.Random
    stage_duration: int = 6
    applause_probability: float = 0.18
    sp_states: List[str] = field(init=False)
    queue: List[int] = field(default_factory=list)
    attention_seen: List[bool] = field(init=False)
    runtime: TalentRuntime = field(default_factory=TalentRuntime)
    result: TrialResult = field(default_factory=TrialResult)
    spotlight_actor: Optional[int] = None

    def __post_init__(self) -> None:
        self.sp_states = ["S"] * self.players
        self.attention_seen = [False] * self.players
        if self.talent == "G5":
            self.runtime.reminiscence = initial_g5_reminiscence(self.players)

    @property
    def spec(self) -> TalentSpec:
        """返回焦点天赋参数。"""

        return TALENT_SPECS[self.talent]

    @property
    def effective_legal_probability(self) -> float:
        """残局目标更集中，目标依赖演出通常更容易合法。"""

        player_bonus = (
            max(0, 6 - self.players) * 0.05
            if self.talent in PLAYER_SCALING_TALENTS
            else 0.0
        )
        return min(1.0, self.spec.legal_probability + player_bonus)

    @property
    def stage_gate_round(self) -> int:
        """公共白昼中点；六人局为第 18 轮，二人局为第 12 轮。"""

        segment_length = 6 + self.players
        return segment_length + (segment_length + 1) // 2

    def focal_is_eligible(self) -> bool:
        """伏笔待兑现或永久落幕后不再占据聚光队列。"""

        if self.talent == "G4":
            return self.runtime.form_rounds > 0
        return not self.runtime.spent and not self.runtime.pending

    def clean_queue(self) -> None:
        """移除重复、失效或已不具备主角位阶的队列项。"""

        cleaned: List[int] = []
        seen = set()
        for player_id in self.queue:
            eligible = player_id != 0 or self.focal_is_eligible()
            if (
                player_id not in seen
                and self.sp_states[player_id] == "L"
                and eligible
            ):
                cleaned.append(player_id)
                seen.add(player_id)
        self.queue = cleaned

    def promote(self, player_id: int) -> None:
        """按每人每轮最多一次关注推进 SP。"""

        if self.attention_seen[player_id]:
            return
        if player_id == 0 and not self.focal_is_eligible():
            return
        self.attention_seen[player_id] = True
        if self.sp_states[player_id] == "E":
            self.sp_states[player_id] = "S"
        elif self.sp_states[player_id] == "S":
            self.sp_states[player_id] = "L"
            if player_id not in self.queue:
                self.queue.append(player_id)

    def record_use(self, round_num: int, value: float) -> None:
        """记录一次焦点聚光使用并执行通用 E 回落。"""

        self.result.uses += 1
        self.result.impact += value
        if self.result.first_use_round is None:
            self.result.first_use_round = round_num
        if self.queue and self.queue[0] == 0:
            self.queue.pop(0)
        self.sp_states[0] = "E"
        self.spotlight_actor = 0
        self.promote_spotlight_focus(0)

    def promote_spotlight_focus(self, actor: int) -> None:
        """多数演出给焦点对象关注，但不向发起者自返还。"""

        if self.rng.random() >= 0.75:
            return
        targets = [candidate for candidate in range(self.players) if candidate != actor]
        self.promote(self.rng.choice(targets))

    def offer_focal_spotlight(self, round_num: int) -> None:
        """按天赋类型结算焦点玩家本轮唯一聚光入口。"""

        self.result.offers += 1
        runtime = self.runtime

        if self.talent == "T7":
            runtime.pending = True
            self.record_use(round_num, 0.0)
            return

        if self.talent == "G2":
            if not runtime.prepared:
                runtime.prepared = True
                self.record_use(round_num, 0.8)
            elif round_num >= self.stage_gate_round:
                runtime.stage_rounds = self.stage_duration
                self.result.full_payoffs += 1
                self.result.stage_start_round = round_num
                self.record_use(round_num, 3.0)
            else:
                self.result.yields += 1
                self.queue.append(self.queue.pop(0))
            return

        if self.talent == "G5":
            value = 1.0
            if not runtime.first_full_ripple_used and runtime.reminiscence >= 24:
                runtime.first_full_ripple_used = True
                runtime.reminiscence -= 12
                runtime.applause_bonuses_this_cycle = 0
                runtime.minor_ripple_available = True
                self.result.full_payoffs += 1
                value = 3.0
            elif runtime.first_full_ripple_used and runtime.reminiscence >= 12:
                runtime.reminiscence -= 12
                runtime.applause_bonuses_this_cycle = 0
                runtime.minor_ripple_available = True
                self.result.full_payoffs += 1
                value = 2.3
            elif runtime.minor_ripple_available:
                runtime.minor_ripple_available = False
            else:
                self.result.yields += 1
                self.queue.append(self.queue.pop(0))
                return
            self.record_use(round_num, value)
            return

        if self.talent == "G7":
            if runtime.g7_preparations < 2:
                runtime.g7_preparations += 1
                self.record_use(round_num, 1.0)
            else:
                self.record_use(round_num, 1.8)
            return

        if self.rng.random() < self.effective_legal_probability:
            self.record_use(round_num, self.spec.spotlight_value)
            return

        self.result.yields += 1
        self.queue.append(self.queue.pop(0))

    def resolve_holder(self, round_num: int) -> None:
        """在 R0 固定队首；通用对手总会使用其合法聚光。"""

        self.clean_queue()
        if not self.queue:
            return
        holder = self.queue[0]
        if holder == 0:
            self.offer_focal_spotlight(round_num)
            return
        self.queue.pop(0)
        self.sp_states[holder] = "E"
        self.spotlight_actor = holder
        self.promote_spotlight_focus(holder)

    def run_actions(self) -> None:
        """生成通用场面事件，并按焦点对象优先推进关注。"""

        order = list(range(self.players))
        self.rng.shuffle(order)
        for actor in order:
            if actor == self.spotlight_actor:
                continue
            if self.rng.random() >= self.event_probability:
                continue
            targets = [candidate for candidate in range(self.players) if candidate != actor]
            target = self.rng.choice(targets)
            self.promote(target)
            self.promote(actor)
            if self.talent == "G4" and target == 0:
                self.runtime.embers = min(20, self.runtime.embers + 1)

    def lethal_probability(self, round_num: int, start_round: int) -> float:
        """世界时钟压力的抽象死亡风险；只用于一次性兑现测试。"""

        if round_num < start_round:
            return 0.0
        return min(0.18, 0.025 + 0.008 * (round_num - start_round))

    def resolve_automatic_turning_points(self, round_num: int) -> None:
        """结算 G4 濒死转折与 T7 延迟复活，不新增聚光行动。"""

        runtime = self.runtime
        if (
            self.talent == "G4"
            and not runtime.spent
            and runtime.form_rounds == 0
            and self.rng.random() < self.lethal_probability(round_num, 8)
        ):
            runtime.form_rounds = min(6, 2 + (runtime.embers + 2) // 3)
            self.result.impact += 3.2
            self.result.full_payoffs += 1
            self.sp_states[0] = "L"
            if 0 in self.queue:
                self.queue.remove(0)
            self.queue.insert(0, 0)

        if (
            self.talent == "T7"
            and runtime.pending
            and self.rng.random() < self.lethal_probability(round_num, 6)
        ):
            runtime.pending = False
            runtime.spent = True
            self.result.impact += 3.0
            self.result.full_payoffs += 1

    def advance_round_states(self) -> None:
        """推进副舞台、救世主形态与追忆的自动状态。"""

        runtime = self.runtime
        if self.talent == "G5":
            applause_bonus = int(
                runtime.reminiscence < 24
                and runtime.applause_bonuses_this_cycle < 2
                and self.rng.random() < self.applause_probability
            )
            runtime.applause_bonuses_this_cycle += applause_bonus
            runtime.reminiscence = min(
                24,
                runtime.reminiscence + 1 + applause_bonus,
            )
        if runtime.form_rounds > 0:
            runtime.form_rounds -= 1
            if runtime.form_rounds == 0:
                runtime.spent = True
        if runtime.stage_rounds > 0:
            runtime.stage_rounds -= 1
            if runtime.stage_rounds == 0:
                runtime.spent = True

    def run(self) -> TrialResult:
        """执行一局从第二轮开始的抽象聚光推演。"""

        for round_num in range(2, self.rounds + 1):
            self.attention_seen = [False] * self.players
            self.spotlight_actor = None
            self.advance_round_states()
            if self.runtime.stage_rounds == 0:
                self.resolve_holder(round_num)
                if self.runtime.stage_rounds == 0:
                    self.run_actions()
            self.resolve_automatic_turning_points(round_num)
            self.clean_queue()
        return self.result


@dataclass
class MixedGameModel:
    """让一桌不同天赋同时竞争同一条演出队列。"""

    roster: Tuple[str, ...]
    rounds: int
    event_probability: float
    rng: random.Random
    r0_handoff: bool = False
    handoff_limit: int = 3
    stage_duration: int = 6
    applause_probability: float = 0.18
    sp_states: List[str] = field(init=False)
    queue: List[int] = field(default_factory=list)
    attention_seen: List[bool] = field(init=False)
    runtimes: List[TalentRuntime] = field(init=False)
    result: MixedTrialResult = field(init=False)
    dry_streak: int = 0
    spotlight_actor: Optional[int] = None

    def __post_init__(self) -> None:
        player_count = len(self.roster)
        self.sp_states = ["S"] * player_count
        self.attention_seen = [False] * player_count
        self.runtimes = [TalentRuntime() for _ in self.roster]
        initial_reminiscence = initial_g5_reminiscence(player_count)
        for player_id, talent in enumerate(self.roster):
            if talent == "G5":
                self.runtimes[player_id].reminiscence = initial_reminiscence
        self.result = MixedTrialResult(
            uses=[0] * player_count,
            offers=[0] * player_count,
            yields=[0] * player_count,
            impacts=[0.0] * player_count,
        )

    @property
    def stage_gate_round(self) -> int:
        """返回本桌共享的公共白昼中点。"""

        segment_length = 6 + len(self.roster)
        return segment_length + (segment_length + 1) // 2

    def legal_probability(self, player_id: int) -> float:
        """按存活人数调整目标集中度。"""

        talent = self.roster[player_id]
        spec = TALENT_SPECS[talent]
        player_bonus = (
            max(0, 6 - len(self.roster)) * 0.05
            if talent in PLAYER_SCALING_TALENTS
            else 0.0
        )
        return min(1.0, spec.legal_probability + player_bonus)

    def eligible(self, player_id: int) -> bool:
        """判断玩家是否还会进入天赋聚光队列。"""

        runtime = self.runtimes[player_id]
        if self.roster[player_id] == "G4":
            return runtime.form_rounds > 0
        return not runtime.spent and not runtime.pending

    def active_stage_owner(self) -> Optional[int]:
        """返回当前 G2 舞台拥有者。"""

        for player_id, runtime in enumerate(self.runtimes):
            if runtime.stage_rounds > 0:
                return player_id
        return None

    def clean_queue(self) -> None:
        """清理混排队列中的失效成员。"""

        cleaned: List[int] = []
        seen = set()
        for player_id in self.queue:
            if (
                player_id not in seen
                and self.sp_states[player_id] == "L"
                and self.eligible(player_id)
            ):
                cleaned.append(player_id)
                seen.add(player_id)
        self.queue = cleaned

    def promote(self, player_id: int) -> None:
        """推进一名混排玩家的 SP。"""

        if self.attention_seen[player_id] or not self.eligible(player_id):
            return
        self.attention_seen[player_id] = True
        if self.sp_states[player_id] == "E":
            self.sp_states[player_id] = "S"
        elif self.sp_states[player_id] == "S":
            self.sp_states[player_id] = "L"
            if player_id not in self.queue:
                self.queue.append(player_id)

    def use_spotlight(self, player_id: int, value: float) -> None:
        """记录一次成功演出。"""

        self.result.uses[player_id] += 1
        self.result.impacts[player_id] += value
        self.result.used_rounds += 1
        self.queue.pop(0)
        self.sp_states[player_id] = "E"
        self.spotlight_actor = player_id
        if self.rng.random() < 0.75:
            targets = [
                candidate
                for candidate in range(len(self.roster))
                if candidate != player_id
            ]
            self.promote(self.rng.choice(targets))

    def yield_spotlight(self, player_id: int) -> None:
        """记录让位并把队首轮转至队尾。"""

        self.result.yields[player_id] += 1
        self.result.yielded_rounds += 1
        self.queue.append(self.queue.pop(0))

    def offer_spotlight(self, player_id: int, round_num: int) -> None:
        """按与单焦点模型相同的状态规则处理队首。"""

        talent = self.roster[player_id]
        runtime = self.runtimes[player_id]
        spec = TALENT_SPECS[talent]
        self.result.offers[player_id] += 1

        if talent == "T7":
            runtime.pending = True
            self.use_spotlight(player_id, 0.0)
            return

        if talent == "G2":
            if not runtime.prepared:
                runtime.prepared = True
                self.use_spotlight(player_id, 0.8)
            elif round_num >= self.stage_gate_round:
                runtime.stage_rounds = self.stage_duration
                self.use_spotlight(player_id, 3.0)
            else:
                self.yield_spotlight(player_id)
            return

        if talent == "G5":
            if not runtime.first_full_ripple_used and runtime.reminiscence >= 24:
                runtime.first_full_ripple_used = True
                runtime.reminiscence -= 12
                runtime.applause_bonuses_this_cycle = 0
                runtime.minor_ripple_available = True
                self.use_spotlight(player_id, 3.0)
            elif runtime.first_full_ripple_used and runtime.reminiscence >= 12:
                runtime.reminiscence -= 12
                runtime.applause_bonuses_this_cycle = 0
                runtime.minor_ripple_available = True
                self.use_spotlight(player_id, 2.3)
            elif runtime.minor_ripple_available:
                runtime.minor_ripple_available = False
                self.use_spotlight(player_id, 1.0)
            else:
                self.yield_spotlight(player_id)
            return

        if talent == "G7":
            if runtime.g7_preparations < 2:
                runtime.g7_preparations += 1
                self.use_spotlight(player_id, 1.0)
            else:
                self.use_spotlight(player_id, 1.8)
            return

        if self.rng.random() < self.legal_probability(player_id):
            self.use_spotlight(player_id, spec.spotlight_value)
        else:
            self.yield_spotlight(player_id)

    def resolve_spotlight_round(self, round_num: int) -> None:
        """分配本轮席位；候选规则可允许 R0 公开顺延一圈。"""

        self.clean_queue()
        if not self.queue:
            self.result.empty_rounds += 1
            return
        candidate_count = (
            min(len(self.queue), self.handoff_limit)
            if self.r0_handoff
            else 1
        )
        used_before = self.result.used_rounds
        for _ in range(candidate_count):
            self.offer_spotlight(self.queue[0], round_num)
            if self.result.used_rounds > used_before:
                return

    def advance_states(self) -> None:
        """推进全桌自动资源和一次性形态。"""

        for player_id, talent in enumerate(self.roster):
            runtime = self.runtimes[player_id]
            if talent == "G5":
                bonus = int(
                    runtime.reminiscence < 24
                    and runtime.applause_bonuses_this_cycle < 2
                    and self.rng.random() < self.applause_probability
                )
                runtime.applause_bonuses_this_cycle += bonus
                runtime.reminiscence = min(24, runtime.reminiscence + 1 + bonus)
            if runtime.form_rounds > 0:
                runtime.form_rounds -= 1
                if runtime.form_rounds == 0:
                    runtime.spent = True
            if runtime.stage_rounds > 0:
                runtime.stage_rounds -= 1
                if runtime.stage_rounds == 0:
                    runtime.spent = True

    def run_actions(self) -> None:
        """生成一轮全桌场面事件。"""

        order = list(range(len(self.roster)))
        self.rng.shuffle(order)
        for actor in order:
            if actor == self.spotlight_actor:
                continue
            if self.rng.random() >= self.event_probability:
                continue
            target = self.rng.choice(
                [candidate for candidate in order if candidate != actor]
            )
            self.promote(target)
            self.promote(actor)
            if self.roster[target] == "G4":
                runtime = self.runtimes[target]
                runtime.embers = min(20, runtime.embers + 1)

    def lethal_probability(self, round_num: int, start_round: int) -> float:
        """返回与单焦点模型相同的抽象死亡风险。"""

        if round_num < start_round:
            return 0.0
        return min(0.18, 0.025 + 0.008 * (round_num - start_round))

    def resolve_turning_points(self, round_num: int) -> None:
        """结算混排中的 G4 与 T7 自动转折。"""

        for player_id, talent in enumerate(self.roster):
            runtime = self.runtimes[player_id]
            if (
                talent == "G4"
                and not runtime.spent
                and runtime.form_rounds == 0
                and self.rng.random() < self.lethal_probability(round_num, 8)
            ):
                runtime.form_rounds = min(6, 2 + (runtime.embers + 2) // 3)
                self.result.impacts[player_id] += 3.2
                self.sp_states[player_id] = "L"
                if player_id in self.queue:
                    self.queue.remove(player_id)
                self.queue.insert(0, player_id)
            if (
                talent == "T7"
                and runtime.pending
                and self.rng.random() < self.lethal_probability(round_num, 6)
            ):
                runtime.pending = False
                runtime.spent = True
                self.result.impacts[player_id] += 3.0

    def record_dry_round(self, used_before: int) -> None:
        """统计非舞台轮次中连续没有聚光结算的长度。"""

        if self.result.used_rounds == used_before:
            self.dry_streak += 1
            self.result.max_dry_streak = max(
                self.result.max_dry_streak,
                self.dry_streak,
            )
        else:
            self.dry_streak = 0

    def run(self) -> MixedTrialResult:
        """执行一局混排推演。"""

        for round_num in range(2, self.rounds + 1):
            self.attention_seen = [False] * len(self.roster)
            self.spotlight_actor = None
            self.advance_states()
            if self.active_stage_owner() is not None:
                self.result.stage_rounds += 1
                self.dry_streak = 0
                continue

            self.result.non_stage_rounds += 1
            used_before = self.result.used_rounds
            self.resolve_spotlight_round(round_num)
            if self.active_stage_owner() is None:
                self.run_actions()
            self.resolve_turning_points(round_num)
            self.clean_queue()
            self.record_dry_round(used_before)
        return self.result


def percentile(values: Sequence[float], fraction: float) -> float:
    """以 nearest-rank 方式计算小型报告所需分位数。"""

    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction) - 1))
    return float(ordered[index])


def aggregate(
    talent: str,
    players: int,
    rounds: int,
    trials: int,
    event_probability: float,
    seed: int,
    stage_duration: int = 6,
    applause_probability: float = 0.18,
) -> AggregateResult:
    """对一个焦点天赋执行多次试验并汇总。"""

    results: List[TrialResult] = []
    for trial_index in range(trials):
        model = GameModel(
            talent=talent,
            players=players,
            rounds=rounds,
            event_probability=event_probability,
            rng=random.Random(seed + trial_index),
            stage_duration=stage_duration,
            applause_probability=applause_probability,
        )
        results.append(model.run())

    def mean(attribute: str) -> float:
        values = [float(getattr(result, attribute) or 0.0) for result in results]
        return statistics.fmean(values)

    first_rounds = [
        result.first_use_round
        for result in results
        if result.first_use_round is not None
    ]
    stage_rounds = [
        result.stage_start_round
        for result in results
        if result.stage_start_round is not None
    ]
    payoff_games = sum(result.full_payoffs > 0 for result in results)
    total_offers = sum(result.offers for result in results)
    total_yields = sum(result.yields for result in results)

    return AggregateResult(
        talent=talent,
        players=players,
        rounds=rounds,
        uses_mean=mean("uses"),
        offers_mean=mean("offers"),
        yields_mean=mean("yields"),
        yield_rate=total_yields / total_offers if total_offers else 0.0,
        impact_mean=mean("impact"),
        first_use_mean=statistics.fmean(first_rounds) if first_rounds else 0.0,
        payoff_mean=mean("full_payoffs"),
        payoff_probability=payoff_games / trials,
        stage_start_mean=statistics.fmean(stage_rounds) if stage_rounds else 0.0,
        uses_p95=percentile([float(result.uses) for result in results], 0.95),
    )


def aggregate_mixed(
    roster: Tuple[str, ...],
    rounds: int,
    trials: int,
    event_probability: float,
    seed: int,
    r0_handoff: bool = False,
    handoff_limit: int = 3,
    stage_duration: int = 6,
    applause_probability: float = 0.18,
) -> MixedAggregateResult:
    """汇总一组真实天赋混排的全局聚光吞吐。"""

    results: List[MixedTrialResult] = []
    for trial_index in range(trials):
        model = MixedGameModel(
            roster=roster,
            rounds=rounds,
            event_probability=event_probability,
            rng=random.Random(seed + trial_index),
            r0_handoff=r0_handoff,
            handoff_limit=handoff_limit,
            stage_duration=stage_duration,
            applause_probability=applause_probability,
        )
        results.append(model.run())

    total_non_stage = sum(result.non_stage_rounds for result in results)
    total_offers = sum(sum(result.offers) for result in results)
    total_uses = sum(sum(result.uses) for result in results)
    total_yields = sum(sum(result.yields) for result in results)
    total_empty = sum(result.empty_rounds for result in results)
    total_stage = sum(result.stage_rounds for result in results)
    total_observed = total_non_stage + total_stage

    uses_by_talent: Dict[str, float] = {}
    impacts_by_talent: Dict[str, float] = {}
    for player_id, talent in enumerate(roster):
        uses_by_talent[talent] = statistics.fmean(
            result.uses[player_id] for result in results
        )
        impacts_by_talent[talent] = statistics.fmean(
            result.impacts[player_id] for result in results
        )

    return MixedAggregateResult(
        roster=roster,
        rounds=rounds,
        r0_handoff=r0_handoff,
        handoff_limit=handoff_limit,
        utilization=total_uses / total_non_stage if total_non_stage else 0.0,
        yield_rate=total_yields / total_offers if total_offers else 0.0,
        empty_rate=total_empty / total_non_stage if total_non_stage else 0.0,
        stage_share=total_stage / total_observed if total_observed else 0.0,
        max_dry_streak_p95=percentile(
            [float(result.max_dry_streak) for result in results],
            0.95,
        ),
        uses_by_talent=uses_by_talent,
        impacts_by_talent=impacts_by_talent,
    )


def render_table(rows: Iterable[AggregateResult]) -> str:
    """输出便于粘贴进设计文档的 Markdown 表格。"""

    lines = [
        "| 天赋 | 人数 | 轮数 | 使用均值 | 使用P95 | 让位率 | "
        "首次使用 | 完整兑现概率 | 影响均值 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.talent} | {row.players} | {row.rounds} | "
            f"{row.uses_mean:.2f} | {row.uses_p95:.0f} | "
            f"{row.yield_rate:.1%} | {row.first_use_mean:.1f} | "
            f"{row.payoff_probability:.1%} | {row.impact_mean:.2f} |"
        )
    return "\n".join(lines)


def render_mixed(result: MixedAggregateResult) -> str:
    """输出混排压力测试摘要。"""

    lines = [
        f"阵容: {','.join(result.roster)}",
        f"轮数: {result.rounds}",
        f"R0公开顺延: {'是' if result.r0_handoff else '否'}",
        f"顺延候选上限: {result.handoff_limit if result.r0_handoff else 1}",
        f"非舞台轮聚光利用率: {result.utilization:.1%}",
        f"队首让位率: {result.yield_rate:.1%}",
        f"队列为空率: {result.empty_rate:.1%}",
        f"舞台冻结占比: {result.stage_share:.1%}",
        f"连续空窗 P95: {result.max_dry_streak_p95:.0f} 轮",
        "| 天赋 | 使用均值 | SAE均值 |",
        "|---|---:|---:|",
    ]
    for talent in result.roster:
        lines.append(
            f"| {talent} | {result.uses_by_talent[talent]:.2f} | "
            f"{result.impacts_by_talent[talent]:.2f} |"
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=2000)
    parser.add_argument("--players", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--event-probability", type=float, default=0.5)
    parser.add_argument("--stage-duration", type=int, default=6, choices=range(4, 9))
    parser.add_argument("--applause-probability", type=float, default=0.18)
    parser.add_argument("--seed", type=int, default=20260805)
    parser.add_argument("--talent", choices=TALENT_ORDER)
    parser.add_argument("--mixed", action="store_true")
    parser.add_argument("--r0-handoff", action="store_true")
    parser.add_argument("--handoff-limit", type=int, default=3)
    parser.add_argument(
        "--roster",
        default="T1,T3,G2,G3,G5,G7",
        help="混排模式下以逗号分隔的天赋代码",
    )
    return parser.parse_args()


def main() -> None:
    """运行指定规模的十四天赋聚光推演。"""

    args = parse_args()
    if args.mixed:
        roster = tuple(part.strip().upper() for part in args.roster.split(","))
        if len(roster) < 2:
            raise SystemExit("混排模式至少需要两个天赋")
        unknown = [talent for talent in roster if talent not in TALENT_SPECS]
        if unknown:
            raise SystemExit(f"未知天赋代码: {','.join(unknown)}")
        result = aggregate_mixed(
            roster=roster,
            rounds=args.rounds,
            trials=args.trials,
            event_probability=args.event_probability,
            seed=args.seed,
            r0_handoff=args.r0_handoff,
            handoff_limit=args.handoff_limit,
            stage_duration=args.stage_duration,
            applause_probability=args.applause_probability,
        )
        print(render_mixed(result))
        return
    talents: Iterable[str] = (args.talent,) if args.talent else TALENT_ORDER
    rows = [
        aggregate(
            talent=talent,
            players=args.players,
            rounds=args.rounds,
            trials=args.trials,
            event_probability=args.event_probability,
            seed=args.seed + index * 100_000,
            stage_duration=args.stage_duration,
            applause_probability=args.applause_probability,
        )
        for index, talent in enumerate(talents)
    ]
    print(render_table(rows))


if __name__ == "__main__":
    main()
