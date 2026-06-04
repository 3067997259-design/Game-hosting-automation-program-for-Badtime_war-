"""小卡交换 ×2 — 通用"""
from engine.cards.base import BaseCard

class CardExchange(BaseCard):
    name = "小卡交换"; count = 2
    desc = "摸 2 张牌，必须将 1 张手牌交给另一名真实观众或弃置。跨声部双方 D6+1。"

    def play(self, player, ish, turn_mgr):
        pid = player.player_id
        for _ in range(2):
            c = ish.deck._draw_one()
            if c: ish.deck.hands.setdefault(pid, []).append(c)
        hand = ish.deck.hands.get(pid, [])
        if len(hand) >= 2:
            give = player.controller.choose("小卡交换：选择给出 1 张", hand,
                context={"phase":"T0","situation":"g2_card_exchange_give"})
            other_real = [turn_mgr.state.get_player(p) for p in ish.participants
                          if p != pid and turn_mgr.state.get_player(p) and turn_mgr.state.get_player(p).is_alive()]
            receiver = player.controller.choose("选择接收者", [p.name for p in other_real] + ["弃置"],
                context={"phase":"T0","situation":"g2_card_exchange_target"})
            if receiver != "弃置":
                target = next((p for p in other_real if p.name == receiver), None)
                if target and give in hand:
                    hand.remove(give)
                    ish.deck.hands.setdefault(target.player_id, []).append(give)
                    if getattr(player,'emotion',None) != getattr(target,'emotion',None):
                        player._card_d6_bonus_rounds = 1
                        target._card_d6_bonus_rounds = 1
            else:
                ish.deck.discard_from_hand(pid, give)
