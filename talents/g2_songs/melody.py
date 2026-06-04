"""旋律 —— 序曲 + 第一音节 + 第二间章 + 第三间章"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from talents.g2_songs.base import BaseSong

if TYPE_CHECKING:
    from engine.ish_bosheth import IshBosheth


class Overture(BaseSong):
    """序曲：开幕免费触发 1 次，不计入 melody_1_used。"""
    name = "旋律·序曲"
    rhythm = "序曲"
    cost = 0
    desc = "开幕免费：双座位 1.0/0.5/0.5/0.5 安定値修正"
    is_melody = True
    needs_target = False
    _rhythm_key = "序曲"

    base_dmg_seq = [1.0, 0.5, 0.5, 0.5]

    @staticmethod
    def is_available(ish):
        return False  # 不会出现在 get_available_songs 列表中

    def execute(self, g2_player, target, ish, game_state):
        ish.execute_melody(game_state, g2_player,
                           base_dmg_seq=self.base_dmg_seq)
        return f"🎵 {self.name}"


class Melody1(BaseSong):
    """第一音节：累计≥3.0 解锁，可用 1 次。"""
    name = "旋律·第一音节"
    rhythm = "第一音节"
    cost = 0
    desc = "双座位 1.0/0.5/0.5/0.5 安定値修正"
    is_melody = True
    needs_target = False
    _rhythm_key = "第一音节"

    base_dmg_seq = [1.0, 0.5, 0.5, 0.5]

    @staticmethod
    def is_available(ish):
        return (ish.cumulative_delta_regard >= ish.MELODY_1_THRESHOLD
                and not ish.melody_1_used)

    def execute(self, g2_player, target, ish, game_state):
        ish.melody_1_used = True
        ish.execute_melody(game_state, g2_player,
                           base_dmg_seq=self.base_dmg_seq)
        return f"🎵 {self.name}"


class Melody2(BaseSong):
    """第二间章：累计≥7.0 解锁，可用 1 次。"""
    name = "旋律·第二间章"
    rhythm = "第二间章"
    cost = 0
    desc = "双座位 1.0/1.0/0.5/0.5 安定値修正"
    is_melody = True
    needs_target = False
    _rhythm_key = "第二间章"

    base_dmg_seq = [1.0, 1.0, 0.5, 0.5]

    @staticmethod
    def is_available(ish):
        return (ish.cumulative_delta_regard >= ish.MELODY_2_THRESHOLD
                and not ish.melody_2_used)

    def execute(self, g2_player, target, ish, game_state):
        ish.melody_2_used = True
        ish.execute_melody(game_state, g2_player,
                           base_dmg_seq=self.base_dmg_seq)
        return f"🎵 {self.name}"


class Melody3(BaseSong):
    """第三间章：累计≥11.0 解锁，可用 1 次。"""
    name = "旋律·第三间章"
    rhythm = "第三间章"
    cost = 0
    desc = "双座位 2.0/2.0/1.0/1.0 安定値修正"
    is_melody = True
    needs_target = False
    _rhythm_key = "第三间章"

    base_dmg_seq = [2.0, 2.0, 1.0, 1.0]

    @staticmethod
    def is_available(ish):
        return (ish.cumulative_delta_regard >= ish.MELODY_3_THRESHOLD
                and not ish.melody_3_used)

    def execute(self, g2_player, target, ish, game_state):
        ish.melody_3_used = True
        ish.execute_melody(game_state, g2_player,
                           base_dmg_seq=self.base_dmg_seq)
        return f"🎵 {self.name}"
