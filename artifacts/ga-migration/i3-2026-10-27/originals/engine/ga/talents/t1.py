"""T1一刀缭断：核心倍率、双倍计费、公演穿屏障与三个战斗章节。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.ga.contracts import Performance, TurnStage
from engine.ga.extensions import chapter, units
from engine.ga.talents.base import Talent, attack_choices, own_slot, weapon_option

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ResolutionRecord
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant


class T1(Talent):
    """不恢复旧无视一半防御或破甲直灌算法。"""

    talent_id = "T1"

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
                     for original in attack_choices(state, grant, melee=True, local=True)
                     for mode in (Performance.INSTANT, Performance.PUBLIC))

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """只监听本人的实际斩击，不靠空演出刷章。"""
        for record in records:
            if record.effect.actor_id != self.owner_id or record.effect.source_id != "t1_slash":
                continue
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
