"""T4六爻：双向秘密出拳、六结果、有限金身和公演双目标。"""

from __future__ import annotations

from dataclasses import replace
from itertools import combinations
from typing import Any, Mapping, TYPE_CHECKING

from engine.ga.combat import resolve_batch
from engine.ga.contracts import DamageKind, Performance, TargetScope, TurnStage
from engine.ga.extensions import IncomingModifier, ask, chapter, high_impact, reveal_choices, units
from engine.ga.runtime import queue_extra_turn
from engine.ga.state import LOCATIONS, next_id, touch
from engine.ga.status import STATUS_NAMES, put_status
from engine.ga.talents.base import Talent, ability, enemies, own_slot, packet, timer

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec, ResolutionRecord
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant, GaPlayer


HEXAGRAMS = {("scissors", "scissors"): "pierce", ("rock", "rock"): "copy",
             ("paper", "paper"): "immune", ("rock", "scissors"): "disable",
             ("paper", "scissors"): "extra", ("paper", "rock"): "conceal"}


class T4(Talent):
    """金身止于下轮R0，不能靠重复回调制造再动。"""

    talent_id = "T4"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.results: set[str] = set()
        self.public_used = False
        self.immunity_round = 0
        self.confirmed: set[tuple[str, str, str]] = set()
        self.yin_yang = 0

    def public(self) -> Mapping[str, Any]:
        """已完成卦象公开，不公开尚未收齐的秘密出拳。"""
        return {"hexagrams": sorted(self.results), "immunity_round": self.immunity_round}

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """公演最多两个真实玩家目标，双人局不要求补第二人。"""
        if stage is not TurnStage.T1 or not own_slot(state, self.owner_id, grant):
            return ()
        targets = tuple(key for key in enemies(state, self.owner_id, local=False) if
                        key in state.players)
        result = []
        cap = state.ga_config.get("talents", "T4", "public_target_cap")
        for mode in (Performance.INSTANT, Performance.PUBLIC):
            for count in range(1, min(len(targets), cap if mode is Performance.PUBLIC else 1) + 1):
                for group in combinations(targets, count):
                    result.append(ability(grant, stage, "t4_hexagram", mode=mode, targets=group,
                                          after=self.cast))
        return tuple(result)

    def cast(self, state: GameState, plan: ActionPlan) -> None:
        """每个目标独立猜拳，双方完成后才揭示答案。"""
        chosen = None
        if self.yin_yang:
            chosen = ask(state, self.owner_id, self.owner_id,
                         tuple(name for name in HEXAGRAMS.values() if name != "extra"),
                         kind="poem_hexagram")
            self.yin_yang -= 1
        for target in plan.request.target_ids:
            if state.game_over:
                break
            if chosen:
                if self.apply(state, plan, target, chosen) and not state.game_over:
                    self.confirm(state, plan.request.root_id, target, chosen,
                                 plan.request.performance is Performance.PUBLIC)
                continue
            before = set(state.ga_choice_windows)
            a = ask(state, self.owner_id, self.owner_id, ("rock", "paper", "scissors"),
                    kind="hexagram", context={"opponent": target}, secret=True)
            b = ask(state, self.owner_id, target, ("rock", "paper", "scissors"),
                    kind="hexagram", context={"opponent": self.owner_id}, secret=True)
            pending = tuple(key for key in state.ga_choice_windows if key not in before)
            reveal_choices(state, pending)
            result = HEXAGRAMS[tuple(sorted((a, b)))]
            if not self.apply(state, plan, target, result):
                continue
            if not state.game_over:
                self.confirm(state, plan.request.root_id, target, result,
                             plan.request.performance is Performance.PUBLIC)

    def confirm(self, state: GameState, root: str, target: str, result: str, public: bool) -> None:
        """真实生效结果只登记一次，致死卦象在终局定格前登记。"""
        key = root, target, result
        if key in self.confirmed:
            return
        self.confirmed.add(key)
        self.results.add(result)
        self.public_used |= public
        high_impact(state, self.owner_id, root, target)
        chapter(state, self.owner_id, 1)
        if len(self.results) >= 3:
            chapter(state, self.owner_id, 2)
        if len(self.results) >= 4 and self.public_used:
            chapter(state, self.owner_id, 3)

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """穿甲卦象的实际资源差量已落地后才算有效起卦。"""
        for record in records:
            if record.effect.source_id == "t4_pierce" and (
                record.effect.actor_id == self.owner_id
            ) and (
                record.hp_loss or record.armor_changes or record.barrier_changes
            ):
                meta = state.ga_action_meta.get(record.effect.root_id, {})
                if meta.get("operation") == "encore_T4":
                    continue
                self.confirm(state, record.effect.root_id, record.target_id, "pierce",
                             meta.get("performance") == "public")

    def apply(self, state: GameState, plan: ActionPlan, target: str, result: str) -> bool:
        """六结果只写GA实体与公共效果；复制保留原耐久，不复制屏障。"""
        cfg = state.ga_config.get("talents", "T4")
        player = state.players[plan.request.actor_id]
        enemy = state.players[target]
        if result == "pierce":
            records = resolve_batch(state, (packet(state, plan.request, cfg["pierce_damage"],
                                         targets=(target,), source="t4_pierce",
                                         suffix=f"pierce/{target}",
                                         scope=TargetScope.RANGED_SINGLE,
                                         defense_coefficient=cfg["defense_coefficient"]),))
            return any(r.hp_loss or r.armor_changes or r.barrier_changes for r in records)
        elif result == "copy":
            choices = tuple(key for key, d in enemy.defenses.items() if d.kind.value == "armor" and
                            d.durability > 0)
            if choices:
                key = ask(state, self.owner_id, player.player_id, choices, kind="copy_armor")
                new = next_id(state, "armor/copied")
                player.defenses[new] = replace(enemy.defenses[key], defense_id=new)
                player.armor_affinities[new] = enemy.armor_affinities[key]
                player.armor_kinds[new] = enemy.armor_kinds[key]
            else:
                return False
        elif result == "immune":
            if self.immunity_round == state.current_round:
                return False
            self.immunity_round = state.current_round
            player.status_immunities = STATUS_NAMES
        elif result == "disable":
            choices = tuple(sorted(enemy.weapons - {"fist"} - set(enemy.disabled_weapons)))
            if choices:
                weapon = ask(state, self.owner_id, player.player_id, choices, kind="disable_weapon")
                enemy.disabled_weapons[weapon] = timer(state, target, "disabled", cfg[
                    "weapon_disable_ticks"])
                if weapon == "gauss_rifle":
                    enemy.charged = False
            else:
                put_status(enemy, state.ga_config, state.current_round, "SHOCKED",
                           source_id=plan.request.root_id, actor_id=player.player_id)
        elif result == "extra":
            return queue_extra_turn(state, player.player_id, "t4_or_yue")
        else:
            player.hidden = True
            player.revealed_to.clear()
            player.hidden_timer = timer(state, self.owner_id, "t4/stealth", cfg["stealth_ticks"])
            for other in state.players.values():
                other.locked_targets.discard(player.player_id)
            for grant in state.ga_standard_grants.values():
                if grant.locked_target == player.player_id:
                    grant.locked_target = None
            locations = tuple(loc for loc in LOCATIONS if loc != enemy.location and
                              loc not in state.destroyed_locations)
            if locations:
                from engine.ga.extensions import relocate
                relocate(state, target, locations[state.ga_rng.die("t4/teleport", len(locations),
                                                                  actor_id=self.owner_id) - 1])
        touch(state)
        return True

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """顶格护体免疫普通数值效果，但不能阻止明文直接死亡。"""
        return IncomingModifier(immune=target.player_id == self.owner_id and
                                self.immunity_round == state.current_round and
                                effect.damage_kind is not DamageKind.EXECUTION)

    def boundary(self, state: GameState, *, boundary: str) -> None:
        """金身下轮R0消失，隐身按未来R4到期。"""
        player = state.players[self.owner_id]
        if boundary == "R0" and self.immunity_round < state.current_round:
            player.status_immunities = frozenset()

    def receive_poem(self, state: GameState, giver: str) -> None:
        """阴阳只刷新到两次发动，不钦定完整再动。"""
        self.yin_yang = state.ga_config.get("poems", "yin_yang_uses")
