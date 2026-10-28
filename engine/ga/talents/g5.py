"""G5三阶段：根级追忆、主动献诗、真实历史倒放与有限闭环。"""

from __future__ import annotations

from dataclasses import replace
import math
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.actions import GrantKind
from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    CostEntry, DamageAttribute, DamageKind, Performance, TargetScope, TerminalReason, TurnStage,
    freeze_json,
)
from engine.ga.events import EventKind
from engine.ga.extensions import IncomingModifier, chapter, high_impact, units
from engine.ga.state import emit, finish_if_needed, touch, trace
from engine.ga.status import heal
from engine.ga.talents.base import Talent, ability, enemies, own_slot, packet

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec, ResolutionRecord
    from engine.ga.events import DomainEvent
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant, GaPlayer


class G5(Talent):
    """第二阶段可永久停留；第三阶段不继续吃追忆或献诗。"""

    talent_id = "G5"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.stage = 1
        self.body: GaPlayer | None = None
        self.highlight_roots: set[tuple[str, str]] = set()
        self.poetry_rounds: set[int] = set()
        self.arrow_growth = 0
        self.reversal_round = 0
        self.pointer = 0
        self.last_reverse_round = 0
        self.required: dict[str, str] = {}
        self.disobedience: dict[str, int] = {}

    def public(self) -> Mapping[str, Any]:
        """倒放动词在本轮R0公开，不把具体目标或历史对象锁死。"""
        return {"stage": self.stage, "memory": self.memory, "history_pointer": self.pointer,
                "required_verbs": self.required, "disobedience": self.disobedience}

    @property
    def memory(self) -> int:
        """属性接口读取正式资源账户，不保存另一份追忆余额。"""
        return self.body.resources["memory"] if self.body is not None else 0

    @memory.setter
    def memory(self, value: int) -> None:
        """增长/场景前置都写同一资源；实际支付由共同执行器扣除。"""
        if self.body is None:
            raise ValueError("G5 has not been registered")
        self.body.resources["memory"] = value

    def register(self, state: GameState) -> None:
        """倒流从下一R0启动，普通调度器不识别昔涟名字。"""
        super().register(state)
        self.body = state.players[self.owner_id]
        self.memory = state.ga_config.get("talents", "G5", "memory_initial")
        state.ga_hooks.setdefault("before_clock", []).append(self.before_clock)

    def gain(self, state: GameState, amount: int) -> None:
        """追忆使用角色唯一状态，不与SP或PP钱包混用。"""
        if self.stage < 3 and state.players[self.owner_id].alive:
            self.memory = min(state.ga_config.get("talents", "G5", "memory_cap"),
                              self.memory + amount)

    def stat(self, state: GameState) -> int:
        """代付追忆后立即按当前值下降，第三阶段不保留数值被动。"""
        cfg = state.ga_config.get("talents", "G5")
        return min(cfg["stat_bonus_cap"], self.memory // cfg["stat_memory_divisor"]) if (
            self.stage == 2) else 0

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """故事/献诗均大即演，只有德谬歌升格占公演位。"""
        if not own_slot(state, self.owner_id, grant):
            return ()
        cfg = state.ga_config.get("talents", "G5")
        result = []
        if stage is TurnStage.T0 and self.stage < 3:
            if self.memory < cfg["memory_cap"]:
                result.append(ability(grant, stage, "g5_write", after=self.perform))
            if self.stage == 1 and self.memory >= cfg["stage2_memory"]:
                result.append(ability(grant, stage, "g5_stage2",
                                      costs=(CostEntry(self.owner_id, "sp",
                                                       cfg["stage2_sp_cost"]),),
                                      after=self.perform))
        if stage is not TurnStage.T1:
            return tuple(result)
        if self.stage == 1:
            result.extend(ability(grant, stage, "g5_story", mode=Performance.MAJOR_INSTANT,
                                  targets=(target,), after=self.perform)
                          for target in enemies(state, self.owner_id) if target in state.players)
        if self.stage == 2:
            targets = [key for key, p in state.players.items() if p.alive and p.home_locked and
                       key in state.ga_roles and
                       p.isolation_id == state.players[self.owner_id].isolation_id]
            for paid_sp in range(state.ga_config.get("poems", "sp_cost") + 1):
                memory_cost = (state.ga_config.get("poems", "sp_cost") - paid_sp) * (
                    state.ga_config.get("poems", "memory_per_missing_sp"))
                if self.memory < memory_cost:
                    continue
                for target in targets:
                    if target == self.owner_id and not enemies(state, self.owner_id, local=False):
                        continue
                    candidate = ability(grant, stage, "g5_poem", mode=Performance.MAJOR_INSTANT,
                                        targets=(target,), parameters={"paid_sp": paid_sp,
                                                                      "memory": memory_cost},
                                        costs=(CostEntry(self.owner_id, "resource:memory",
                                                         memory_cost),),
                                        after=self.perform)
                    candidate = replace(candidate, allow_untargetable=True,
                        definition=replace(candidate.definition, performance_sp_override=paid_sp))
                    result.append(candidate)
            if self.memory >= cfg["ascension_memory"] and state.world_tick >= state.ga_config.get(
                "clock", "dusk_start"):
                result.append(ability(grant, stage, "g5_ascend", mode=Performance.PUBLIC,
                                      after=self.perform))
        return tuple(result)

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """第二阶段只给普通攻击加当前追忆权重，星光箭在总池单独计一次。"""
        if grant.actor_id != self.owner_id:
            return options
        return tuple(replace(o, effects=tuple(replace(e, amount=e.amount + self.stat(state))
                     if e.damage_kind is DamageKind.NORMAL else e for e in o.effects)) if (
                         o.request.operation == "attack" or o.weapon_use) else o for o in options)

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """追忆减伤不向外甲重复计费。"""
        reduction = self.stat(state) if target.player_id == self.owner_id else 0
        return IncomingModifier(reduction=reduction)

    def perform(self, state: GameState, plan: ActionPlan) -> None:
        """先付资源再征询接受；拒绝和未答都不偷偷退款。"""
        request = plan.request
        cfg = state.ga_config.get("talents", "G5")
        if request.operation == "g5_write":
            self.gain(state, cfg["write_gain"])
        elif request.operation == "g5_stage2":
            self.stage = 2
            high_impact(state, self.owner_id, request.root_id)
        elif request.operation == "g5_story":
            self.gain(state, cfg["story_memory_gain"])
            target = state.players[request.target_ids[0]]
            heal(target, cfg["story_heal"])
            target.sp = cfg["story_target_sp"]
            high_impact(state, self.owner_id, request.root_id)
            chapter(state, self.owner_id, 1)
        elif request.operation == "g5_poem":
            if request.target_ids == (self.owner_id,):
                self.arrows(state, plan)
                effective = True
            else:
                effective = state.ga_poems.give(state, self.owner_id, request.target_ids[0],
                                                request.root_id)
            if effective and not state.game_over:
                self.record_poetry(state)
        else:
            self.stage = 3
            self.reversal_round = state.current_round + 1
            self.pointer = state.current_round - 1
            chapter(state, self.owner_id, 3)
        touch(state)

    def record_poetry(self, state: GameState) -> None:
        """实际接受/箭段生效后在终局定格前登记，重复同轮不多算。"""
        self.poetry_rounds.add(state.current_round)
        chapter(state, self.owner_id, 2)
        if len(self.poetry_rounds) >= state.ga_config.get("talents", "G5", "poetry_chapter_rounds"):
            chapter(state, self.owner_id, 3)

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """最后一箭击杀不会丢失已完成的献诗章节。"""
        if any(r.effect.source_id == "g5_arrows" and r.effect.actor_id == self.owner_id and (
            r.effective_hit or r.armor_changes or r.barrier_changes) for r in records):
            self.record_poetry(state)

    def arrows(self, state: GameState, plan: ActionPlan) -> None:
        """人数/成长决定有限总段数，每目标最多两段，固定加值先入总池。"""
        cfg = state.ga_config.get("talents", "G5")
        targets = sorted((key for key in enemies(state, self.owner_id, local=False) if
                          key in state.players), key=lambda a: (-state.ga_initiative.get(a, 0), a))
        count = min(cfg["arrow_segment_cap"], cfg["arrow_base_segments"] + math.ceil(
            len(state.players) / cfg["arrow_player_divisor"]) + self.arrow_growth)
        count = min(count, len(targets) * cfg["arrows_per_target"])
        self.arrow_growth = min(cfg["arrow_segment_cap"], self.arrow_growth + cfg["arrow_growth"])
        pool = count * cfg["arrow_damage"] + self.stat(state)
        for callback in tuple(state.ga_pool_bonus_hooks):
            pool = callback(state, self.owner_id, pool)
        for index in range(count):
            target = targets[index % len(targets)]
            if state.game_over:
                break
            if not state.players[target].alive:
                continue
            attribute = DamageAttribute.UNATTRIBUTED
            if index < cfg["unattributed_arrow_start"] - 1:
                attribute = (DamageAttribute.TECH, DamageAttribute.NORMAL,
                             DamageAttribute.MAGIC)[index]
            amount = pool // count + (index < pool % count)
            resolve_batch(state, (packet(state, plan.request, amount,
                targets=(target,), source="g5_arrows", suffix=f"arrow/{index}", attribute=attribute,
                scope=TargetScope.RANGED_SINGLE),))

    def on_events(self, state: GameState, events: tuple[DomainEvent, ...]) -> None:
        """同根高光只领一次；无根系统事件以批次为作用域。"""
        for event in events:
            if event.kind in (EventKind.ARMOR_BROKEN, EventKind.PUBLIC_PERFORMED,
                              EventKind.ACTOR_DIED):
                if event.kind is EventKind.ACTOR_DIED and event.target_id not in state.players:
                    continue
                if event.root_scope not in self.highlight_roots:
                    self.highlight_roots.add(event.root_scope)
                    self.gain(state, state.ga_config.get(
                        "talents", "G5", "highlight_gain_per_root"))
        if not state.players[self.owner_id].alive and self.owner_id in state.ga_world.reversers:
            state.ga_world.end_reversal(state, self.owner_id)

    def before_clock(self, state: GameState) -> None:
        """升格轮其他行动照常，下一R0才开始倒流。"""
        if self.stage == 3 and self.reversal_round == state.current_round and (
            state.players[self.owner_id].alive):
            state.ga_world.begin_reversal(state, self.owner_id)
            self.reverse_step(state)

    def reverse_step(self, state: GameState) -> None:
        """到R1先保存终分，再做只用于展示的未起床面板重置。"""
        if self.last_reverse_round == state.current_round:
            return
        self.last_reverse_round = state.current_round
        if self.pointer <= 1:
            finish_if_needed(state, reason=TerminalReason.REVERSAL_CLOSURE)
            state.ga_closure_display = freeze_json({p.player_id: {
                "hp": state.ga_config.get("start", "hp"),
                "max_hp": state.ga_config.get("start", "max_hp"),
                "credits": state.ga_config.get("start", "credits"),
                "sp": state.ga_config.get("start", "sp"),
                "awake": False, "location": f"home:{p.player_id}",
                "weapons": ("fist",), "ammo": {}, "defenses": {},
            } for p in state.players.values() if p.alive})
            for player in state.players.values():
                if player.alive:
                    player.awake = player.home_locked = False
                    player.location = f"home:{player.player_id}"
            return
        frame = self.pointer
        self.required = {actor: verb for rnd, actor, verb in state.ga_history if rnd == frame and
                         actor != self.owner_id and state.players[actor].alive}
        self.pointer -= 1
        trace(state, "reversal_frame", frame=frame, required=self.required)

    def after_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """有合法同类动词仍违逆才惩罚，不能因先扣费用伪装成无合法动作。"""
        request = plan.request
        if self.owner_id not in state.ga_world.reversers or state.current_grant.kind is not (
            GrantKind.STANDARD) or request.stage is not TurnStage.T1:
            return
        required = self.required.get(request.actor_id)
        if required and request.verb.value != required and required in state.ga_action_meta.get(
            request.root_id, {}).get("legal_verbs", ()):
            self.punish(state, plan)

    def punish(self, state: GameState, plan: ActionPlan) -> None:
        """普通状态继续计时；惩罚不冒充敌人普通攻击或自刷关注。"""
        actor = plan.request.actor_id
        count = self.disobedience.get(actor, 0) + 1
        self.disobedience[actor] = count
        player = state.players[actor]
        cfg = state.ga_config.get("talents", "G5")
        root = f"{plan.request.root_id}/fate"
        request = replace(plan.request, root_id=root, target_ids=(actor,))
        state.ga_quiet_roots.add(root)
        if count == 1:
            barriers = [key for key, d in player.defenses.items() if d.durability and
                        d.kind.value != "armor"]
            if barriers:
                player.defenses.pop(sorted(barriers)[0])
                emit(state, EventKind.STATE_CHANGED, target_id=actor,
                     payload={"fate": "barrier_broken"})
                return
            armors = [d for d in player.defenses.values() if d.durability and
                      d.kind.value == "armor"]
            kind = DamageKind.PURE_WEAR if armors else DamageKind.DIRECT
            amount = cfg["reversal_first_wear" if armors else "reversal_first_hp_loss"]
            statuses = ()
        elif count == 2:
            kind, amount, statuses = DamageKind.DIRECT, cfg["reversal_second_damage"], ("SHOCKED",)
        else:
            kind = DamageKind.DIRECT
            amount = cfg["reversal_later_base_damage"] + cfg["reversal_later_step"] * (
                count - cfg["reversal_later_start"])
            player.sp = 0
            statuses = ()
        effect = packet(state, request, amount, kind=kind, certain=True, statuses=statuses,
                        scope=TargetScope.GLOBAL_AREA)
        resolve_batch(state, (replace(effect, actor_id=None, score_owner_id=None,
                                      crime_owner_id=None,
                                      parameters={**dict(effect.parameters), "hostile": False}),))

    def receive_poem(self, state: GameState, giver: str) -> None:
        """自身爱与记忆由献诗动作的星光箭路径执行，不能复制永久段数。"""
        touch(state)
