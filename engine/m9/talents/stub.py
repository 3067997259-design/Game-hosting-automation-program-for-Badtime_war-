"""M9 天赋兼容桩（引擎 v2exp 钩子调用点的安全空实现）。

独立 M9 天赋类（G2/G6）继承本 mixin，保证引擎各处
（on_turn_start/on_turn_end/check_response_window/on_crime_check/
_on_any_player_death/process_burn_damage/is_immune_to_damage）不因缺方法崩溃。
继承 v2exp 的 M9 类（G1/G4/G5/G7）已有对应方法，不受影响。
"""
from __future__ import annotations

from typing import Any


class M9TalentStub:
    """v2exp 钩子兼容桩：全部返回 None/False（不读字段即安全）。"""

    def on_register(self):
        return None

    def on_round_start(self, *args, **kwargs):
        return None

    def on_round_end(self, *args, **kwargs):
        return None

    def on_turn_start(self, *args, **kwargs):
        return None

    def on_turn_end(self, *args, **kwargs):
        return None

    def check_response_window(self, *args, **kwargs):
        return False  # M9 天赋不参与 v2exp 响应窗口

    def on_crime_check(self, *args, **kwargs):
        return None

    def _on_any_player_death(self, *args, **kwargs):
        return None

    def process_burn_damage(self, *args, **kwargs):
        return None

    def on_death_check(self, *args, **kwargs):
        return None

    def is_immune_to_damage(self, damage_type: str) -> bool:
        return False

    def receive_damage_to_temp_hp(self, damage: float, is_embrace: bool = False):
        return damage

    def show_activation(self, player_name: str = "", show_lore: bool = False):
        return None

    def describe_status(self) -> str:
        return ""
