"""BaseSong —— G2 曲目抽象基类。"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from engine.ish_bosheth import IshBosheth


class BaseSong:
    name: str = ""           # 曲目名
    rhythm: str = ""         # 节奏名
    cost: int = 1            # Regard 消耗
    desc: str = ""           # 一行描述
    is_melody: bool = False  # 旋律（不需选听者，走双座位）
    needs_target: bool = True
    _rhythm_key: str = ""    # 在 selected_rhythm['name'] 中匹配的关键词

    @staticmethod
    def is_available(ish: IshBosheth) -> bool:
        return ish.regard >= 1

    def get_legal_targets(self, ish: IshBosheth, game_state: Any) -> list:
        targets = []
        for pid in ish.participants:
            if pid == ish.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if p and p.is_alive():
                targets.append(p)
        for c in ish.chorus_list:
            if c.is_alive():
                targets.append(c)
        return targets

    def execute(self, g2_player: Any, target: Any, ish: IshBosheth,
                game_state: Any) -> str:
        raise NotImplementedError

    def execute_melody(self, g2_player: Any, ish: IshBosheth,
                       game_state: Any, base_dmg_seq: list = None) -> str:
        raise NotImplementedError
