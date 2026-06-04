"""反光板 ×2 — Ind 限定"""
from cli import display
from engine.cards.base import BaseCard
from engine.ish_bosheth import INDIFFERENZA


class ReflectBoard(BaseCard):
    name = "反光板"
    count = 2
    voice = INDIFFERENZA
    desc = "Ind 限定。选择一名观众，其下次旋律中 decay_factor 强制=1.0。"

    def play(self, player, ish, turn_mgr):
        game_state = turn_mgr.state
        targets = [game_state.get_player(p) for p in ish.participants
                    if game_state.get_player(p) and game_state.get_player(p).is_alive()]
        targets += [c for c in ish.chorus_list if c.is_alive()]

        # v2.0 duet: 可上供舞台（G2/G5）
        if ish.phase == "duet":
            g2 = game_state.get_player(ish.g2_owner_id)
            g5 = game_state.get_player(ish.duet_g5_pid) if ish.duet_g5_pid else None
            if g2 and g2.is_alive():
                targets.append(g2)
            if g5 and g5.is_alive():
                targets.append(g5)

        if targets:
            chosen = player.controller.choose("反光板：选择目标",
                [t.name for t in targets],
                context={"phase":"T0","situation":"g2_card_reflect_board"})
            t = next((x for x in targets if x.name == chosen), targets[0])

            # v2.0 duet: 上供舞台 → 热力（旋律在 duet 中禁用，反光板改为纯热力贡献）
            if ish.phase == "duet" and t.player_id in (ish.g2_owner_id, ish.duet_g5_pid):
                ish.offer_heat(player, 0.5, self.name)
                return

            t._stability_force_decay = 1.0
            display.show_info(f"🔦 {t.name} 被反光板标记：下次旋律衰减=1.0")
