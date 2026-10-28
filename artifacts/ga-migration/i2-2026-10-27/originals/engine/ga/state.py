"""GA正式状态装配与公共账目；所有可写领域数据归属于GameState。"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from engine.ga.actions import GrantKind
from engine.ga.config import GaConfig, load_ga_config
from engine.ga.contracts import (
    ActionRequest, ContractError, DamageAttribute, DamageKind, DefenseKind, DefenseSpec,
    EventStamp, PlayerResult, RoundPhase, TargetScope, TerminalReason, TerminalSnapshot,
    TimedEffect, TurnStage, checked_ids, freeze_json, require_int,
)
from engine.ga.events import BeneficiaryRole, DomainEvent, EventKind, EventLedger, RewardRule

if TYPE_CHECKING:
    from engine.game_state import GameState


LOCATIONS = ("shop", "magic", "hospital", "military", "police")
ARMOR_AFFINITIES = {
    "shield": DamageAttribute.NORMAL, "ceramic": DamageAttribute.NORMAL,
    "magic_shield": DamageAttribute.MAGIC, "at_field": DamageAttribute.TECH,
}


@dataclass(frozen=True)
class StatusEntry:
    """带来源和未来R4计时的状态；灼烧逐层保存归因。"""

    name: str
    timer: TimedEffect
    actor_id: str | None = None
    score_owner_id: str | None = None
    crime_owner_id: str | None = None
    hits: int = 0


@dataclass
class GaPlayer:
    """GameState中的唯一玩家实体；不复用旧Player的规则默认值。"""

    player_id: str
    hp: int
    max_hp: int
    credits: int
    sp: int
    pp: int
    location: str
    awake: bool = False
    home_locked: bool = False
    absolute_dead: bool = False
    exited: bool = False
    hidden: bool = False
    moved: bool = False
    engaged: bool = False
    flat_reduction: int = 0
    weapons: set[str] = field(default_factory=lambda: {"fist"})
    ammo: dict[str, int] = field(default_factory=dict)
    inventory: dict[str, int] = field(default_factory=dict)
    modules: tuple[str, ...] = ()
    bow_attribute: DamageAttribute = DamageAttribute.NORMAL
    defenses: dict[str, DefenseSpec] = field(default_factory=dict)
    armor_affinities: dict[str, DamageAttribute] = field(default_factory=dict)
    statuses: dict[str, StatusEntry] = field(default_factory=dict)
    burns: list[StatusEntry] = field(default_factory=list)
    barrier_timers: dict[str, TimedEffect] = field(default_factory=dict)
    revealed_to: set[str] = field(default_factory=set)
    immune_kinds: frozenset[DamageKind] = frozenset()
    isolation_id: str | None = None
    surgery: str | None = None
    hospital_heals: int = 0
    syringe_purchases: int = 0
    furnace_uses: int = 0
    sharpened: bool = False
    charged: bool = False
    hook_cooldown: int = 0
    kills: int = 0
    hp_damage: int = 0
    armor_breaks: int = 0
    chapters: set[str] = field(default_factory=set)

    @property
    def alive(self) -> bool:
        """普通死者保留成绩，但不再获得生者槽。"""
        return self.hp > 0 and not self.exited and not self.absolute_dead


@dataclass
class ActionGrant:
    """一个已授予的槽；阶段占用和锁定仅属于这个槽。"""

    grant_id: str
    actor_id: str
    kind: GrantKind
    source_id: str = "standard"
    occupied: set[TurnStage] = field(default_factory=set)
    started: bool = False
    finished: bool = False
    t0_disabled: bool = False
    shocked: bool = False
    locked_target: str | None = None
    must_public: bool = False
    hook_on_cooldown: bool = False
    moved_this_slot: bool = False


class RecordedRandom:
    """局内独立随机流；预检不调用，记录每次骰子用于重放诊断。"""

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)
        self.draws: list[tuple[str, int | float]] = []

    def die(self, key: str, sides: int) -> int:
        """掷具名整数骰，不使用模块级随机状态。"""
        result = self._random.randint(1, sides)
        self.draws.append((key, result))
        return result

    def chance(self, key: str, probability: float) -> bool:
        """包括必中的普通单体也只掷一次；明文必中不调用本方法。"""
        result = self._random.random()
        self.draws.append((key, result))
        return result < probability


def initialize_state(
    state: GameState, player_ids: list[str], *, seed: int, config: GaConfig | None,
) -> None:
    """仅由GameState.new_ga建立候选GA链路，不注册过渡profile。"""
    cfg = load_ga_config() if config is None else config
    if type(cfg) is not GaConfig:
        raise ContractError("config", "requires validated GaConfig")
    ids = checked_ids(player_ids, "players", allow_empty=False)
    if not cfg.get("start", "min_players") <= len(ids) <= cfg.get("start", "max_players"):
        raise ContractError("players", "GA requires 2 through 6 participants")
    require_int(seed, "seed")
    state.ga_config = cfg
    state.ga_seed = seed
    state.profile = "v2.0-ga"
    state.experiment_flags = frozenset()
    state.m9_enabled = False
    state.players = {
        actor: GaPlayer(actor, cfg.get("start", "hp"), cfg.get("start", "max_hp"),
                        cfg.get("start", "credits"), cfg.get("start", "sp"),
                        cfg.get("start", "pp"), f"home:{actor}")
        for actor in ids
    }
    state.player_order = list(ids)
    state.current_round = 0
    state.world_tick = 0
    state.current_phase = RoundPhase.R0
    state.current_stage = None
    state.current_grant = None
    state.game_over = False
    state.winner = None
    state.score_winners = ()
    state.terminal_snapshot = None
    state.ga_revision = 0
    state.ga_id_counter = 0
    state.ga_rng = RecordedRandom(seed)
    state.ga_ledger = EventLedger()
    state.ga_trace = []
    state.ga_resolution_batches = {}
    state.ga_death_cleaned = set()
    state.ga_executed_requests = {}
    state.ga_hooks = {}
    state.ga_action_providers = []
    state.ga_standard_grants = {}
    state.ga_pending_extras = {}
    state.ga_pending_followups = {}
    state.ga_extra_used = set()
    state.ga_followup_used = set()
    state.ga_initiative = {}
    state.ga_round_order = ()
    state.ga_public_used = False
    state.ga_smoke = {}
    state.ga_impact_penalties = {}
    state.ga_running = False
    state.ga_crimes = []
    state.ga_clock_accelerated = False
    state.ga_clock_paused = False
    state.ga_history = []
    state.destroyed_locations = set()
    state.ground_loot = {}
    ordinary = max(cfg.get("modules", "ordinary_stock_min"),
                   -(-len(ids) // cfg.get("modules", "ordinary_stock_player_divisor")))
    infinite = max(cfg.get("modules", "infinite_stock_min"),
                   len(ids) // cfg.get("modules", "infinite_stock_player_divisor"))
    state.module_stock = {kind: ordinary for kind in ("power", "fire", "magic", "pierce",
                                                    "impact")}
    state.module_stock["infinite"] = infinite


def touch(state: GameState) -> None:
    """每次正式变更使旧计划失效。"""
    state.ga_revision += 1


def snapshot_id(state: GameState) -> str:
    """计划绑定本局版本，不能拿另一个局的同号快照支付。"""
    return f"ga:{id(state)}:{state.ga_revision}"


def next_id(state: GameState, prefix: str) -> str:
    """局内稳定ID；候选枚举不得调用。"""
    state.ga_id_counter += 1
    return f"{prefix}:{state.ga_id_counter}"


def stamp(state: GameState) -> EventStamp:
    """事件同时携带实际轮和世界刻度。"""
    return EventStamp(state.current_round, state.world_tick, state.current_phase)


def trace(state: GameState, label: str, **details: Any) -> None:
    """运行轨迹供验收，不充当业务状态或发奖来源。"""
    state.ga_trace.append({"round": state.current_round, "tick": state.world_tick,
                           "phase": state.current_phase.value, "label": label, **details})


def run_hooks(state: GameState, boundary: str) -> None:
    """通用时序接缝；无天赋时没有具名角色分支或旧实现回退。"""
    for callback in tuple(state.ga_hooks.get(boundary, ())):
        if state.game_over:
            break
        callback(state)


def add_armor(state: GameState, actor_id: str, kind: str) -> str:
    """按N0产生有稳定ID的普通外甲，不生成第二个吸收池。"""
    cfg = state.ga_config.get("armor", kind)
    defense_id = next_id(state, f"armor/{kind}")
    player = state.players[actor_id]
    player.defenses[defense_id] = DefenseSpec(
        defense_id, DefenseKind.ARMOR, cfg["durability"], cfg["durability"],
        frozenset({DamageKind.NORMAL, DamageKind.PURE_WEAR}),
        frozenset(TargetScope),
        {attr: cfg[attr.value] for attr in
         (DamageAttribute.NORMAL, DamageAttribute.TECH, DamageAttribute.MAGIC)},
        state.ga_config.get("combat", "normal_wear_price"),
    )
    player.armor_affinities[defense_id] = ARMOR_AFFINITIES[kind]
    touch(state)
    return defense_id


def balance(player: GaPlayer, account: str) -> int:
    """统一费用账户；缺账户不能回退到旧Player属性。"""
    if account in ("hp", "sp", "pp", "credits"):
        return getattr(player, account)
    if account.startswith("ammo:"):
        return player.ammo.get(account[5:], 0)
    if account.startswith("item:"):
        return player.inventory.get(account[5:], 0)
    raise ContractError("account", f"unsupported GA account: {account}")


def set_balance(player: GaPlayer, account: str, amount: int) -> None:
    """执行器扣费专用；先做全部账户预检，再集中写入。"""
    require_int(amount, "balance")
    if account in ("hp", "sp", "pp", "credits"):
        setattr(player, account, amount)
    elif account.startswith("ammo:"):
        player.ammo[account[5:]] = amount
    elif account.startswith("item:"):
        player.inventory[account[5:]] = amount
    else:
        raise ContractError("account", f"unsupported GA account: {account}")


def award_events(state: GameState, events: tuple[DomainEvent, ...]) -> None:
    """已提交事实在同一次执行中入账；SP上限只消费实际发放的额度。"""
    cfg = state.ga_config
    attention_kinds = frozenset({EventKind.ACTION_ACTIVATED, EventKind.HP_DAMAGED,
                                EventKind.ARMOR_BROKEN, EventKind.BARRIER_BROKEN})
    attention = RewardRule("attention", attention_kinds, BeneficiaryRole.OBSERVER, True,
                           cfg.get("action", "attention_per_round"))
    break_pp = RewardRule("armor_break_pp", frozenset({EventKind.ARMOR_BROKEN}),
                          BeneficiaryRole.SCORE_OWNER, False,
                          cfg.get("pp", "armor_break_rewards_per_round"))
    kill_pp = RewardRule("kill_pp", frozenset({EventKind.ACTOR_DIED}),
                         BeneficiaryRole.SCORE_OWNER, False, None)
    for event in events:
        targets = {event.actor_id, event.target_id} if event.kind in (
            EventKind.ACTION_ACTIVATED, EventKind.ARMOR_BROKEN
        ) else {event.target_id}
        if event.kind in attention_kinds and event.effective:
            for actor_id in sorted(targets - {None}):
                player = state.players.get(actor_id)
                if player is not None and player.alive:
                    grant = state.ga_ledger.claim(
                        attention, event.event_id, actor_id, requested=1,
                        available_room=max(0, cfg.get("action", "sp_cap") - player.sp),
                    )
                    player.sp += grant.amount
        owner = state.players.get(event.score_owner_id)
        if owner is None or event.target_id == event.score_owner_id:
            continue
        if event.kind is EventKind.HP_DAMAGED:
            owner.hp_damage += event.payload["hp_loss"]
        if event.kind is EventKind.ARMOR_BROKEN:
            owner.armor_breaks += 1
            if not owner.absolute_dead:
                grant = state.ga_ledger.claim(break_pp, event.event_id, owner.player_id,
                                              requested=cfg.get("pp", "armor_break_gain"))
                owner.pp += grant.amount
        if event.kind is EventKind.ACTOR_DIED:
            owner.kills += 1
            if not owner.absolute_dead:
                grant = state.ga_ledger.claim(kill_pp, event.event_id, owner.player_id,
                                              requested=cfg.get("pp", "kill_gain"))
                owner.pp += grant.amount


def publish(state: GameState, events: tuple[DomainEvent, ...]) -> None:
    """发布后立刻付实际奖励，调用者不能重复消费旧事件。"""
    if not events:
        return
    committed = state.ga_ledger.commit_batch(events[0].batch_id, events)
    award_events(state, committed)


def emit(
    state: GameState, kind: EventKind, *, root_id: str | None = None,
    actor_id: str | None = None, target_id: str | None = None, effective: bool = False,
    payload: Mapping[str, Any] | None = None,
) -> DomainEvent:
    """产生非战斗事实；战斗必须由完整ResolutionRecord批次发布。"""
    batch = next_id(state, "event-batch")
    event = DomainEvent(f"{batch}/event", kind, stamp(state), root_id, batch, actor_id,
                        target_id, actor_id, actor_id, effective, payload or {})
    publish(state, (event,))
    return event


def record_death(state: GameState, actor_id: str, *, absolute: bool) -> None:
    """普通和绝对死亡共享一次清理，战果与实际PP保留。"""
    if actor_id in state.ga_death_cleaned:
        return
    state.ga_death_cleaned.add(actor_id)
    player = state.players[actor_id]
    player.hp = 0
    player.sp = 0
    player.absolute_dead = absolute
    if state.world_tick >= state.ga_config.get("clock", "day_start"):
        pile = state.ground_loot.setdefault(player.location, [])
        pile.append({"owner_id": actor_id, "credits": player.credits,
                     "ammo": dict(player.ammo), "inventory": dict(player.inventory),
                     "modules": player.modules, "weapons": set(player.weapons),
                     "defenses": dict(player.defenses),
                     "affinities": dict(player.armor_affinities)})
        player.credits = 0
        player.ammo.clear()
        player.inventory.clear()
        player.modules = ()
        player.weapons.clear()
        player.defenses.clear()
        player.armor_affinities.clear()
    player.statuses.clear()
    player.burns.clear()
    trace(state, "death", actor=actor_id, absolute=absolute)


def finish_if_needed(state: GameState, *, round_limit: bool = False) -> bool:
    """完整目标批次后才检查终局，保存实际钱包，综合同分并列。"""
    if state.game_over:
        return True
    survivors = tuple(sorted(key for key, p in state.players.items() if p.alive))
    if len(survivors) > 1 and not round_limit:
        return False
    reason = TerminalReason.ROUND_LIMIT if len(survivors) > 1 else (
        TerminalReason.LAST_SURVIVOR if survivors else TerminalReason.ALL_DEAD
    )
    cfg = state.ga_config
    results = {}
    for actor, player in state.players.items():
        crown = cfg.get("scoring", "crown") if survivors == (actor,) and (
            player.kills >= cfg.get("scoring", "crown_min_kills") or
            len(player.chapters) >= cfg.get("scoring", "crown_min_chapters")
        ) else 0
        total = (player.kills * cfg.get("scoring", "kill_weight") +
                 player.hp_damage // cfg.get("scoring", "damage_divisor") +
                 player.armor_breaks * cfg.get("scoring", "armor_break_weight") +
                 len(player.chapters) * cfg.get("scoring", "chapter_weight") +
                 player.pp * cfg.get("scoring", "pp_weight") + crown)
        results[actor] = PlayerResult(player.kills, player.hp_damage, player.armor_breaks,
                                      tuple(sorted(player.chapters)), player.pp, total)
    terminal = TerminalSnapshot(
        next_id(state, "terminal"), stamp(state), reason, survivors, results,
        cfg.get("_meta", "ruleset"), str(cfg.get("_meta", "schema_version")),
        cfg.fingerprint, state.ga_seed,
    )
    state.ga_ledger.finalize(terminal)
    state.terminal_snapshot = terminal
    state.game_over = True
    state.winner = survivors[0] if len(survivors) == 1 else None
    best = max(result.total_score for result in results.values())
    state.score_winners = tuple(sorted(key for key, r in results.items() if r.total_score == best))
    trace(state, "terminal", reason=reason.value, survivors=list(survivors),
          pace_anomaly=reason is TerminalReason.ROUND_LIMIT)
    touch(state)
    return True


def observe(state: GameState, actor_id: str) -> Mapping[str, Any]:
    """控制器只读投影；隐身不隐藏公开状态，候选负责可攻击资格。"""
    return freeze_json({
        "actor_id": actor_id, "round": state.current_round, "world_tick": state.world_tick,
        "phase": state.current_phase.value, "public_used": state.ga_public_used,
        "players": {key: {
            "hp": p.hp, "max_hp": p.max_hp, "credits": p.credits, "sp": p.sp, "pp": p.pp,
            "location": p.location, "alive": p.alive, "awake": p.awake, "hidden": p.hidden,
            "weapons": sorted(p.weapons), "ammo": p.ammo, "inventory": p.inventory,
            "defenses": {key: d.durability for key, d in p.defenses.items()},
        } for key, p in state.players.items()},
    })
