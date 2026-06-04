"""花束 ×2 — 通用"""
from engine.cards.base import BaseCard
from cli import display

class Bouquet(BaseCard):
    name = "花束"
    count = 2
    desc = "选择一名单位获 0.5 临时 HP 至下个 R4。若目标为 Chorus，额外恢复 0.5 HP。"

    def play(self, player, ish, turn_mgr):
        display.show_info(f"🎴 {player.name} 使用 {self.name}")
        all_units = [turn_mgr.state.get_player(p) for p in ish.participants
                     if turn_mgr.state.get_player(p) and turn_mgr.state.get_player(p).is_alive()]
        all_units += [c for c in ish.chorus_list if c.is_alive()]
        if all_units:
            chosen = player.controller.choose("花束：选择目标", [t.name for t in all_units],
                context={"phase":"T0","situation":"g2_card_bouquet"})
            target = next((t for t in all_units if t.name == chosen), all_units[0])
            target._card_temp_hp_until_r4 = 0.5
            if getattr(target, 'is_chorus', False):
                target.hp = min(1.0, round(target.hp + 0.5, 2))
