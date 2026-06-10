"""test_material_deck.py — material_deck 物料牌系统单元测试"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from engine.material_deck import (
    MaterialDeck, TRANSFER_TICKET_NAME, MAX_HAND_SIZE,
    VOICE_ACC, VOICE_IND, VOICE_STR, get_card_count, _CARD_DEFS,
)


def _make_player(pid, name="P", hp=3.0, emotion=None, **kw):
    p = SimpleNamespace(
        player_id=pid, name=name, hp=hp, max_hp=5.0,
        location="商店", is_awake=True, emotion=emotion,
        **kw,
    )
    p.is_alive = lambda: p.hp > 0
    return p


def _make_chorus(cid, name="C", hp=3.0, emotion=None, **kw):
    c = SimpleNamespace(
        player_id=cid, name=name, hp=hp, max_hp=5.0,
        location="商店", is_awake=True, emotion=emotion,
        weapons=[], armor=None, is_chorus=True,
        **kw,
    )
    c.is_alive = lambda: c.hp > 0
    return c


class TestMaterialDeckInit(unittest.TestCase):
    """测试 MaterialDeck 初始化与基本属性"""

    def test_initial_state(self):
        deck = MaterialDeck()
        self.assertEqual(len(deck.draw_pile), 0)
        self.assertEqual(len(deck.discard_pile), 0)
        self.assertEqual(len(deck.removed_pile), 0)
        self.assertIsNone(deck.transfer_ticket_holder)
        self.assertEqual(len(deck.hands), 0)
        self.assertEqual(len(deck.chorus_slots), 0)
        self.assertEqual(len(deck.dropped_goods), 0)

    def test_build_deck_creates_all_cards(self):
        deck = MaterialDeck()
        deck.build_deck()
        expected_count = sum(cd["count"] for cd in _CARD_DEFS)
        self.assertEqual(len(deck.draw_pile), expected_count)
        self.assertEqual(len(deck.discard_pile), 0)

    def test_build_deck_clears_previous(self):
        deck = MaterialDeck()
        deck.draw_pile.append("test")
        deck.discard_pile.append("test")
        deck.build_deck()
        self.assertEqual(len(deck.discard_pile), 0)
        self.assertNotIn("test", deck.draw_pile)


class TestOpeningDeal(unittest.TestCase):
    """测试开场发牌"""

    def test_opening_deal_real_players_get_2_cards(self):
        deck = MaterialDeck()
        p1 = _make_player("p1", emotion=VOICE_ACC)
        p2 = _make_player("p2", emotion=VOICE_STR)
        chorus = []
        deck.opening_deal([p1, p2], chorus, {})

        self.assertIn("p1", deck.hands)
        self.assertIn("p2", deck.hands)
        self.assertEqual(len(deck.hands["p1"]), 2)
        self.assertEqual(len(deck.hands["p2"]), 2)

    def test_opening_deal_chorus_get_1_card(self):
        deck = MaterialDeck()
        c1 = _make_chorus("c1", emotion=VOICE_ACC)
        deck.opening_deal([], [c1], {"c1": "舞台"})

        self.assertEqual(deck.chorus_slots.get("c1"), deck.chorus_slots.get("c1"))
        self.assertIsNotNone(deck.chorus_slots.get("c1"))

    def test_transfer_ticket_goes_to_ind_player(self):
        deck = MaterialDeck()
        p_ind = _make_player("p_ind", emotion=VOICE_IND)
        p_acc = _make_player("p_acc", emotion=VOICE_ACC)
        deck.opening_deal([p_ind, p_acc], [], {})

        self.assertEqual(deck.transfer_ticket_holder, "p_ind")
        self.assertIn(TRANSFER_TICKET_NAME, deck.hands.get("p_ind", []))
        self.assertEqual(len(deck.hands["p_ind"]), 3)  # 2 + 改签票

    def test_transfer_ticket_falls_back_to_ind_chorus(self):
        deck = MaterialDeck()
        p_acc = _make_player("p_acc", emotion=VOICE_ACC)
        c_ind = _make_chorus("c_ind", emotion=VOICE_IND)
        deck.opening_deal([p_acc], [c_ind], {"c_ind": "舞台"})

        self.assertEqual(deck.transfer_ticket_holder, "c_ind")
        self.assertEqual(deck.chorus_slots.get("c_ind"), TRANSFER_TICKET_NAME)

    def test_transfer_ticket_absent_when_no_ind_unit(self):
        deck = MaterialDeck()
        p_acc = _make_player("p_acc", emotion=VOICE_ACC)
        p_str = _make_player("p_str", emotion=VOICE_STR)
        deck.opening_deal([p_acc, p_str], [], {})

        self.assertIsNone(deck.transfer_ticket_holder)


class TestDrawAndDiscard(unittest.TestCase):
    """测试摸牌与弃牌"""

    def test_draw_from_empty_returns_none(self):
        deck = MaterialDeck()
        self.assertIsNone(deck._draw_one())

    def test_draw_recycles_discard(self):
        deck = MaterialDeck()
        deck.draw_pile = []
        deck.discard_pile = ["荧光棒", "前排票"]
        card = deck._draw_one()
        self.assertIn(card, ["荧光棒", "前排票"])
        # 剩余 1 张在 draw_pile
        self.assertEqual(len(deck.draw_pile) + len(deck.discard_pile), 1)

    def test_discard_normal_card(self):
        deck = MaterialDeck()
        deck.hands["p1"] = ["荧光棒"]
        result = deck.discard_from_hand("p1", "荧光棒")
        self.assertTrue(result)
        self.assertEqual(len(deck.hands["p1"]), 0)
        self.assertIn("荧光棒", deck.discard_pile)

    def test_discard_transfer_ticket_goes_to_removed(self):
        deck = MaterialDeck()
        deck.hands["p1"] = [TRANSFER_TICKET_NAME]
        deck.transfer_ticket_holder = "p1"
        result = deck.discard_from_hand("p1", TRANSFER_TICKET_NAME)
        self.assertTrue(result)
        self.assertIn(TRANSFER_TICKET_NAME, deck.removed_pile)
        self.assertIsNone(deck.transfer_ticket_holder)

    def test_discard_nonexistent_card_returns_false(self):
        deck = MaterialDeck()
        deck.hands["p1"] = ["荧光棒"]
        result = deck.discard_from_hand("p1", "不存在的牌")
        self.assertFalse(result)

    def test_pickup_floor_moves_card(self):
        deck = MaterialDeck()
        deck.dropped_goods["舞台"] = ["前排票"]
        result = deck.pickup_floor("p1", "舞台", "前排票")
        self.assertTrue(result)
        self.assertIn("前排票", deck.hands["p1"])
        self.assertNotIn("舞台", deck.dropped_goods)

    def test_pickup_floor_nonexistent_returns_false(self):
        deck = MaterialDeck()
        deck.dropped_goods["舞台"] = ["荧光棒"]
        result = deck.pickup_floor("p1", "舞台", "前排票")
        self.assertFalse(result)


class TestHandLimit(unittest.TestCase):
    """测试手牌上限"""

    def test_take_chorus_card_with_room_in_hand(self):
        deck = MaterialDeck()
        deck.chorus_slots["c1"] = "荧光棒"
        deck.hands["p1"] = ["前排票"]
        card = deck.take_chorus_card("p1", "c1")
        self.assertEqual(card, "荧光棒")
        self.assertIn("荧光棒", deck.hands["p1"])
        self.assertIsNone(deck.chorus_slots.get("c1"))

    def test_take_chorus_card_full_hand_drops_to_seat(self):
        deck = MaterialDeck()
        deck._seat_assignments = {"c1": "舞台"}
        deck.chorus_slots["c1"] = "荧光棒"
        deck.hands["p1"] = ["前排票", "小卡交换", "空白票根"]  # 3 = MAX
        card = deck.take_chorus_card("p1", "c1")
        self.assertEqual(card, "荧光棒")
        self.assertIn("荧光棒", deck.dropped_goods.get("舞台", []))


class TestTrade(unittest.TestCase):
    """测试换牌"""

    def test_propose_trade_valid(self):
        deck = MaterialDeck()
        deck.hands["p1"] = ["荧光棒"]
        deck.hands["p2"] = ["前排票"]
        result = deck.propose_trade("p1", "p2", "荧光棒", "前排票")
        self.assertTrue(result)

    def test_propose_trade_missing_card_returns_false(self):
        deck = MaterialDeck()
        deck.hands["p1"] = ["荧光棒"]
        deck.hands["p2"] = ["前排票"]
        result = deck.propose_trade("p1", "p2", "不存在的牌", "前排票")
        self.assertFalse(result)

    def test_propose_trade_already_traded_returns_false(self):
        deck = MaterialDeck()
        deck.hands["p1"] = ["荧光棒"]
        deck.hands["p2"] = ["前排票"]
        deck.traded_this_round.add("p1")
        result = deck.propose_trade("p1", "p2", "荧光棒", "前排票")
        self.assertFalse(result)

    def test_execute_trade_swaps_cards(self):
        deck = MaterialDeck()
        deck.hands["p1"] = ["荧光棒"]
        deck.hands["p2"] = ["前排票"]
        result = deck.execute_trade("p1", "p2", "荧光棒", "前排票")
        self.assertTrue(result)
        self.assertIn("前排票", deck.hands["p1"])
        self.assertIn("荧光棒", deck.hands["p2"])
        self.assertIn("p1", deck.traded_this_round)
        self.assertIn("p2", deck.traded_this_round)


class TestDropOnDeath(unittest.TestCase):
    """测试死亡掉落"""

    def test_drop_all_on_seat(self):
        deck = MaterialDeck()
        deck.hands["p1"] = ["荧光棒", "前排票"]
        deck.drop_all_on_seat("p1", "舞台")
        self.assertNotIn("p1", deck.hands)
        self.assertEqual(deck.dropped_goods["舞台"], ["荧光棒", "前排票"])

    def test_drop_all_transfer_ticket_clears_holder(self):
        deck = MaterialDeck()
        deck.hands["p1"] = [TRANSFER_TICKET_NAME]
        deck.transfer_ticket_holder = "p1"
        deck.drop_all_on_seat("p1", "商店")
        self.assertIsNone(deck.transfer_ticket_holder)

    def test_drop_chorus_card(self):
        deck = MaterialDeck()
        deck.chorus_slots["c1"] = "荧光棒"
        deck.drop_chorus_card("c1", "舞台")
        self.assertNotIn("c1", deck.chorus_slots)
        self.assertIn("荧光棒", deck.dropped_goods.get("舞台", []))


class TestPlayability(unittest.TestCase):
    """测试牌可打性"""

    def test_is_playable_generic_card(self):
        deck = MaterialDeck()
        p = _make_player("p1", emotion=VOICE_ACC)
        self.assertTrue(deck.is_playable(p, "荧光棒"))  # voice=None → 通用

    def test_is_playable_voice_restricted_match(self):
        deck = MaterialDeck()
        p = _make_player("p1", emotion=VOICE_ACC)
        self.assertTrue(deck.is_playable(p, "应援连呼"))  # voice=VOICE_ACC

    def test_is_playable_voice_restricted_mismatch(self):
        deck = MaterialDeck()
        p = _make_player("p1", emotion=VOICE_STR)
        self.assertFalse(deck.is_playable(p, "应援连呼"))  # 需要 Acc

    def test_is_playable_transfer_ticket_returns_false(self):
        deck = MaterialDeck()
        p = _make_player("p1", emotion=VOICE_IND)
        self.assertFalse(deck.is_playable(p, TRANSFER_TICKET_NAME))

    def test_is_playable_unknown_card(self):
        deck = MaterialDeck()
        p = _make_player("p1")
        self.assertFalse(deck.is_playable(p, "不存在的牌"))


class TestChorusPlayCard(unittest.TestCase):
    """测试 Chorus 打牌"""

    def test_chorus_play_card_valid(self):
        deck = MaterialDeck()
        c = _make_chorus("c1", emotion=VOICE_ACC)
        deck.chorus_slots["c1"] = "荧光棒"
        result = deck.chorus_play_card(c, "荧光棒")
        self.assertTrue(result)
        self.assertIsNone(deck.chorus_slots["c1"])
        self.assertIn("荧光棒", deck.discard_pile)

    def test_chorus_play_card_wrong_card_in_slot(self):
        deck = MaterialDeck()
        c = _make_chorus("c1")
        deck.chorus_slots["c1"] = "荧光棒"
        result = deck.chorus_play_card(c, "前排票")
        self.assertFalse(result)

    def test_chorus_play_card_voice_mismatch(self):
        deck = MaterialDeck()
        c = _make_chorus("c1", emotion=VOICE_STR)  # Str
        deck.chorus_slots["c1"] = "应援连呼"  # 需要 Acc
        result = deck.chorus_play_card(c, "应援连呼")
        self.assertFalse(result)


class TestTransferTicket(unittest.TestCase):
    """测试改签票"""

    def test_use_transfer_ticket_from_player_hand(self):
        deck = MaterialDeck()
        deck.hands["p1"] = [TRANSFER_TICKET_NAME, "荧光棒"]
        deck.transfer_ticket_holder = "p1"
        result = deck.use_transfer_ticket()
        self.assertTrue(result)
        self.assertNotIn(TRANSFER_TICKET_NAME, deck.hands["p1"])
        self.assertIn(TRANSFER_TICKET_NAME, deck.removed_pile)
        self.assertIsNone(deck.transfer_ticket_holder)

    def test_use_transfer_ticket_from_chorus(self):
        deck = MaterialDeck()
        deck.chorus_slots["c1"] = TRANSFER_TICKET_NAME
        deck.transfer_ticket_holder = "c1"
        result = deck.use_transfer_ticket()
        self.assertTrue(result)
        self.assertIsNone(deck.chorus_slots["c1"])
        self.assertIsNone(deck.transfer_ticket_holder)

    def test_use_transfer_ticket_no_holder(self):
        deck = MaterialDeck()
        result = deck.use_transfer_ticket()
        self.assertFalse(result)

    def test_transfer_ticket_available(self):
        deck = MaterialDeck()
        self.assertFalse(deck.transfer_ticket_available())
        deck.transfer_ticket_holder = "p1"
        self.assertTrue(deck.transfer_ticket_available())


class TestQuery(unittest.TestCase):
    """测试查询方法"""

    def test_get_hand_returns_copy(self):
        deck = MaterialDeck()
        deck.hands["p1"] = ["荧光棒"]
        hand = deck.get_hand("p1")
        hand.append("前排票")
        self.assertEqual(len(deck.hands["p1"]), 1)  # 不应影响原始数据

    def test_get_chorus_card(self):
        deck = MaterialDeck()
        deck.chorus_slots["c1"] = "荧光棒"
        self.assertEqual(deck.get_chorus_card("c1"), "荧光棒")

    def test_get_chorus_card_empty(self):
        deck = MaterialDeck()
        self.assertIsNone(deck.get_chorus_card("c1"))

    def test_get_seat_drops(self):
        deck = MaterialDeck()
        deck.dropped_goods["舞台"] = ["荧光棒", "前排票"]
        drops = deck.get_seat_drops("舞台")
        self.assertEqual(len(drops), 2)

    def test_get_card_info(self):
        deck = MaterialDeck()
        info = deck.get_card_info("荧光棒")
        self.assertIsNotNone(info)
        self.assertEqual(info["name"], "荧光棒")
        self.assertEqual(info["count"], 2)

    def test_get_card_info_transfer_ticket(self):
        deck = MaterialDeck()
        info = deck.get_card_info(TRANSFER_TICKET_NAME)
        self.assertIsNotNone(info)
        self.assertTrue(info["unique"])

    def test_get_card_info_unknown(self):
        deck = MaterialDeck()
        self.assertIsNone(deck.get_card_info("不存在的牌"))


class TestReset(unittest.TestCase):
    """测试重置与清理"""

    def test_reset_round_tracking(self):
        deck = MaterialDeck()
        deck.traded_this_round.add("p1")
        deck.played_this_turn["p1"] = True
        deck.reset_round_tracking()
        self.assertEqual(len(deck.traded_this_round), 0)
        self.assertEqual(len(deck.played_this_turn), 0)

    def test_clear_all(self):
        deck = MaterialDeck()
        deck.build_deck()
        deck.hands["p1"] = ["荧光棒"]
        deck.chorus_slots["c1"] = "前排票"
        deck.dropped_goods["商店"] = ["小卡交换"]
        deck.transfer_ticket_holder = "p1"
        deck.traded_this_round.add("p1")
        deck.played_this_turn["p1"] = True

        deck.clear_all()
        self.assertEqual(len(deck.draw_pile), 0)
        self.assertEqual(len(deck.discard_pile), 0)
        self.assertEqual(len(deck.hands), 0)
        self.assertEqual(len(deck.chorus_slots), 0)
        self.assertEqual(len(deck.dropped_goods), 0)
        self.assertIsNone(deck.transfer_ticket_holder)


class TestGetCardCount(unittest.TestCase):
    """测试模块级工具函数"""

    def test_get_card_count_known(self):
        self.assertEqual(get_card_count("荧光棒"), 2)

    def test_get_card_count_transfer_ticket(self):
        self.assertEqual(get_card_count(TRANSFER_TICKET_NAME), 1)

    def test_get_card_count_unknown(self):
        self.assertEqual(get_card_count("不存在的牌"), 0)


class TestCardDefs(unittest.TestCase):
    """测试牌定义完整性"""

    def test_total_unique_cards(self):
        """总牌数应由 _CARD_DEFS 中的 count 求和得出"""
        deck = MaterialDeck()
        deck.build_deck()
        total = sum(cd["count"] for cd in _CARD_DEFS)
        self.assertEqual(len(deck.draw_pile), total)

    def test_voice_restrictions_valid(self):
        """所有声部限制必须为有效值或 None"""
        valid_voices = {None, VOICE_ACC, VOICE_IND, VOICE_STR}
        for cd in _CARD_DEFS:
            self.assertIn(cd.get("voice"), valid_voices,
                          f"{cd['name']} voice={cd.get('voice')}")

    def test_all_cards_have_name_and_count(self):
        for cd in _CARD_DEFS:
            self.assertIsInstance(cd["name"], str)
            self.assertIsInstance(cd["count"], int)
            self.assertGreater(cd["count"], 0)


class TestDescribe(unittest.TestCase):
    """测试 describe 调试输出"""

    def test_describe_empty(self):
        deck = MaterialDeck()
        desc = deck.describe()
        self.assertIn("物料牌系统", desc)
        self.assertIn("改签票持有者: 无", desc)

    def test_describe_with_data(self):
        deck = MaterialDeck()
        deck.build_deck()
        deck.hands["p1"] = ["荧光棒"]
        deck.chorus_slots["c1"] = "前排票"
        desc = deck.describe()
        self.assertIn("p1 手牌", desc)
        self.assertIn("c1 持牌", desc)
