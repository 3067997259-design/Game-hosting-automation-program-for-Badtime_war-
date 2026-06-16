"""花束 ×2 — 通用"""
from engine.cards.base import BaseCard
from engine.ish_bosheth import _g2_num
from cli import display

class Bouquet(BaseCard):
    name = "花束"
    count = 2
    desc = "选择一名单位获 0.5 临时 HP 至下个 R4。若目标为 Chorus，额外恢复 0.5 HP。"

    def play(self, player, ish, turn_mgr):
        display.show_info(f"🎴 {player.name} 使用 {self.name}")
        game_state = turn_mgr.state
        all_units = [game_state.get_player(p) for p in ish.participants
                     if game_state.get_player(p) and game_state.get_player(p).is_alive()]
        all_units += [c for c in ish.chorus_list if c.is_alive()]

        # v2.0 duet: 可上供舞台（G2/G5）
        if ish.phase == "duet":
            g2 = game_state.get_player(ish.g2_owner_id)
            g5 = game_state.get_player(ish.duet_g5_pid) if ish.duet_g5_pid else None
            if g2 and g2.is_alive():
                all_units.append(g2)
            if g5 and g5.is_alive():
                all_units.append(g5)

        if all_units:
            chosen = player.controller.choose("花束：选择目标", [t.name for t in all_units],
                context={"phase":"T0","situation":"g2_card_bouquet"})
            target = next((t for t in all_units if t.name == chosen), all_units[0])

            temp_hp = _g2_num("card_bouquet_temp_hp", v1=0.5)
            chorus_heal = _g2_num("card_bouquet_chorus_heal", v1=0.5)

            # v2.0 duet: 上供舞台 → 热力
            if ish.phase == "duet" and target.player_id in (ish.g2_owner_id, ish.duet_g5_pid):
                target._card_temp_hp_until_r4 = getattr(target, '_card_temp_hp_until_r4', 0) + temp_hp
                ish.offer_heat(player, temp_hp, self.name)
                return

            target._card_temp_hp_until_r4 = temp_hp
            if getattr(target, 'is_chorus', False):
                target.hp = min(target.max_hp, round(target.hp + chorus_heal, 2))
