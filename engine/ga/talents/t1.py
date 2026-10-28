"""T1一刀缭断：核心倍率、双倍计费、公演穿屏障与三个战斗章节。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.ga.contracts import Performance, TurnStage
from engine.ga.extensions import chapter, relocate, units
from engine.ga.talents.base import Talent, attack_choices, own_slot, weapon_option

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, ResolutionRecord
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant


class T1(Talent):
    """不恢复旧无视一半防御或破甲直灌算法。"""

    talent_id = "T1"

    def __init__(self, owner_id: str) -> None:
        super().__init__(owner_id)
        self.ranger = False
        self.ranger_roots: set[str] = set()
        self.refunded: set[str] = set()

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """一SP即演或二SP公演，均占一个T1。"""
        if stage is not TurnStage.T1 or not own_slot(state, self.owner_id, grant):
            return ()
        cfg = state.ga_config.get("talents", self.talent_id)
        return tuple(weapon_option(state, grant, original, "t1_slash", mode,
                                   multiplier=cfg["core_multiplier"], wear_price=cfg["wear_price"],
                                   bypass=mode is Performance.PUBLIC)
                     for original in attack_choices(state, grant, melee=True, local=not self.ranger,
                                                    chase=self.ranger)
                     if units(state)[original.request.target_ids[0]].location == state.players[
                         self.owner_id].location or original.request.target_ids[0] in state.players[
                             self.owner_id].locked_targets
                     for mode in (Performance.INSTANT, Performance.PUBLIC))

    def before_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """真实下一刀消费一次标记，追近不额外发行动槽。"""
        if plan.request.actor_id == self.owner_id and plan.request.operation == (
            "t1_slash") and self.ranger:
            self.ranger = False
            self.ranger_roots.add(plan.request.root_id)
            relocate(state, self.owner_id, units(state)[plan.request.target_ids[0]].location)

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """只监听本人的实际斩击，不靠空演出刷章。"""
        for record in records:
            if record.effect.actor_id != self.owner_id or record.effect.source_id != "t1_slash":
                continue
            root = record.effect.root_id
            if root in self.ranger_roots and root not in self.refunded and (
                any(c.broken for c in record.armor_changes) or record.death_outcome.value in (
                    "dead", "absolute_dead")):
                player = state.players[self.owner_id]
                player.sp = min(state.ga_config.get("action", "sp_cap"), player.sp +
                                state.ga_config.get("poems", "ranger_sp_refund"))
                self.refunded.add(root)
            if record.effective_hit:
                chapter(state, self.owner_id, 1)
            if any(change.broken for change in record.armor_changes):
                chapter(state, self.owner_id, 2)
            meta = state.ga_action_meta.get(record.effect.root_id, {})
            if record.death_outcome.value in ("dead", "absolute_dead") or (
                meta.get("performance") == "public" and record.hp_loss and
                any(d.kind.value != "armor" and d.durability for d in
                    units(state)[record.target_id].defenses.values())
            ):
                chapter(state, self.owner_id, 3)

    def receive_poem(self, state: GameState, giver: str) -> None:
        """重复刷新下一刀标记，不叠出多份返还。"""
        self.ranger = True
