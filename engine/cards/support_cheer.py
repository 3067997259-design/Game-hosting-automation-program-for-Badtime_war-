"""应援连呼 ×1 — Acc限定"""
import random
from engine.cards.base import BaseCard
from engine.ish_bosheth import ACCAREZZEVOLE, STRAPPANDO, _g2_num
from cli import display

class SupportCheer(BaseCard):
    name = "应援连呼"
    count = 1
    voice = ACCAREZZEVOLE
    desc = "Acc 限定。选一名 Acc 单位获 0.5 临时 HP。若为 Acc Chorus，它立刻执行一次攻击。"

    def play(self, player, ish, turn_mgr):
        display.show_info(f"🎴 {player.name} 使用 {self.name}")
        acc_units = [turn_mgr.state.get_player(p) for p in ish.participants
                     if turn_mgr.state.get_player(p) and turn_mgr.state.get_player(p).is_alive()
                     and getattr(turn_mgr.state.get_player(p), 'emotion', None) == ACCAREZZEVOLE]
        acc_chorus = [c for c in ish.chorus_list if c.is_alive() and c.emotion == ACCAREZZEVOLE]
        all_acc = acc_units + acc_chorus
        if all_acc:
            chosen = player.controller.choose("应援连呼：选择 Acc 目标", [t.name for t in all_acc],
                context={"phase":"T0","situation":"g2_card_support_cheer"})
            target = next((t for t in all_acc if t.name == chosen), all_acc[0])
            target.temp_hp_g2 = getattr(target, 'temp_hp_g2', 0) + _g2_num("card_support_cheer_temp_hp", v1=0.5)
            if getattr(target, 'is_chorus', False):
                str_targets = [c for c in ish.chorus_list if c.is_alive() and c.emotion == STRAPPANDO]
                for p2_id in ish.participants:
                    p2 = turn_mgr.state.get_player(p2_id)
                    if p2 and p2.is_alive() and getattr(p2, 'emotion', None) == STRAPPANDO:
                        str_targets.append(p2)
                if str_targets:
                    target._g2_commanded_target_id = random.choice(str_targets).player_id
