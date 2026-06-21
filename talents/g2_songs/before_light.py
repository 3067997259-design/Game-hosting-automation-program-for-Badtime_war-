"""Before light —— Riposato(1费) + Dolente(2费)"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from engine.prompt_manager import prompt_manager
from talents.g2_songs.base import BaseSong

if TYPE_CHECKING:
    from engine.ish_bosheth import IshBosheth


class Riposato(BaseSong):
    name = "Before light"
    rhythm = "休息 (Riposato)"
    cost = 1
    desc = "改变本轮规则：提高pivot，旋律变温柔"
    needs_target = False
    _rhythm_key = "Riposato"

    def execute(self, g2_player, target, ish, game_state):
        from talents.talent_balance import talent_num
        ish.before_light = "riposato"
        # 安定値 pivot 抬高（hp20 默认 pivot 6.0 → 重锚，[待风洞]）；v1=5.0 字节不变
        ish._pivot_override = talent_num("g2", "before_light_riposato_pivot", v1=5.0)
        prompt_manager.show("g2reset", "song.riposato_v06")
        return f"🎵 {self.name}·{self.rhythm}"


class Dolente(BaseSong):
    name = "Before light"
    rhythm = "悲伤 (Dolente)"
    cost = 2
    desc = "改变本轮规则：降低pivot，旋律变残酷"
    needs_target = False
    _rhythm_key = "Dolente"

    @staticmethod
    def is_available(ish):
        return ish.regard >= 2

    def execute(self, g2_player, target, ish, game_state):
        from talents.talent_balance import talent_num
        ish.before_light = "dolente"
        # 安定値 pivot 降低（hp20 重锚，[待风洞]）；v1=2.0 字节不变
        ish._pivot_override = talent_num("g2", "before_light_dolente_pivot", v1=2.0)
        prompt_manager.show("g2reset", "song.dolente_v06")
        return f"🎵 {self.name}·{self.rhythm}"
