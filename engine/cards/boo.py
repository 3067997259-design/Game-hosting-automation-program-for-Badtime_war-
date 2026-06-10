"""倒彩 ×2 — 通用（Str倾向）"""
from engine.cards.base import BaseCard
from engine.ish_bosheth import ACCAREZZEVOLE
from cli import display

class Boo(BaseCard):
    name = "倒彩"
    count = 2
    desc = "选择一名 Acc 单位，其至下个 R4 受到伤害 +0.5。"

    def play(self, player, ish, turn_mgr):
        display.show_info(f"🎴 {player.name} 使用 {self.name}")
        acc_units = [turn_mgr.state.get_player(p) for p in ish.participants
                     if turn_mgr.state.get_player(p) and turn_mgr.state.get_player(p).is_alive()
                     and getattr(turn_mgr.state.get_player(p), 'emotion', None) == ACCAREZZEVOLE]
        acc_chorus = [c for c in ish.chorus_list if c.is_alive() and c.emotion == ACCAREZZEVOLE]
        all_acc = acc_units + acc_chorus
        if all_acc:
            chosen = player.controller.choose("倒彩：选择 Acc 目标", [t.name for t in all_acc],
                context={"phase":"T0","situation":"g2_card_boo"})
            target = next((t for t in all_acc if t.name == chosen), all_acc[0])
            target._card_debuff_damage_taken = 0.5
