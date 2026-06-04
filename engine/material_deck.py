"""engine/material_deck.py
G2 ish-bosheth v0.6 物料牌系统

管理：
- 牌堆 / 弃牌区 / 移出区
- 真实玩家公开手牌（上限 3）
- Chorus 持牌槽（1 张）
- 座位掉落物料
- 每 T0 摸牌 / 拾取 / 出牌 / 弃牌流程
- 自愿换牌
- 死亡 / 离场掉落
"""

from __future__ import annotations
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from models.player import Player
    from models.chorus import ChorusUnit

VOICE_ACC = "accarezzevole"
VOICE_IND = "indifferenza"
VOICE_STR = "strappando"
# 以上常量值与 engine.ish_bosheth 中的 ACCAREZZEVOLE / INDIFFERENZA / STRAPPANDO 等价。
# material_deck 无法从 ish_bosheth 导入（循环依赖），故在此独立定义。


# ── 牌定义 ──────────────────────────────────────────────────────────
# 每张牌：{name, count, voice_restriction (None=通用), description}
#
# ⚠️ 过渡期注意：_CARD_DEFS 与 engine/cards/ 下的 BaseCard 子类/CARD_REGISTRY
# 维护了两套并行的牌定义。当前 _CARD_DEFS 供 build_deck/get_card_info/is_playable
# 查询，而 CARD_REGISTRY 供 action_turn.py:_resolve_card_play() 分派 play()。
# 后续应统一为从 CARD_REGISTRY 读取元数据（每张 BaseCard 自带 name/count/voice/desc），
# 避免两套数据漂移。新增牌时需同时更新两处。
_CARD_DEFS: List[Dict[str, Any]] = [
    # ── 通用牌 ──
    {"name": "前排票", "count": 2, "voice": None,
     "desc": "移动到任意观众座位，与该座位 1 名单位建立 engage。不能用于离场。"},
    {"name": "小卡交换", "count": 2, "voice": None,
     "desc": "摸 2 张牌，必须将 1 张手牌交给另一名真实观众或弃置。跨声部双方 D6+1 至下个 R4。"},
    {"name": "空白票根", "count": 2, "voice": None,
     "desc": "选择：摸 1 张牌 / 清除 1 条舞台牵连 / 清除 1 层安可。"},
    {"name": "耳塞", "count": 2, "voice": None,
     "desc": "至下个 R4：下一次旋律命中或 Before light 效果无视。清除 1 条舞台牵连。"},
    {"name": "聚光合影", "count": 2, "voice": None,
     "desc": "邀请一名观众单位移动到你的座位（需对方同意）。你的回合结束后插入其额外行动回合。"},
    # ── Accarezzevole 倾向 ──
    {"name": "荧光棒", "count": 2, "voice": None,
     "desc": "本回合下一次 attack 伤害 +0.5。若目标是 Strappando，改为 +1.0。"},
    {"name": "24K钛合金狗牌", "count": 2, "voice": VOICE_ACC,
     "desc": "Acc 限定。本行动轮次内你的所有攻击无视属性克制。"},
    {"name": "应援连呼", "count": 1, "voice": VOICE_ACC,
     "desc": "Acc 限定。选择一名 Acc 单位获 0.5 临时 HP。若为 Acc Chorus，它立刻执行一次攻击。"},
    # ── Strappando 倾向 ──
    {"name": "后台通行证", "count": 2, "voice": VOICE_STR,
     "desc": "Str 限定。在当前座位生成 G2 投影，立刻 engage。攻击投影视为攻击 G2，可触发破幕。"},
    {"name": "撕票", "count": 1, "voice": VOICE_STR,
     "desc": "Str 限定。Regard -0.5。若本回合击杀 Acc 单位，额外 Regard -0.5。"},
    {"name": "倒彩", "count": 2, "voice": None,
     "desc": "选择一名 Acc 单位，其至下个 R4 受到伤害 +0.5。若本回合攻击该目标，额外 Regard -0.25。"},
    # ── Indifferenza 倾向 ──
    {"name": "花束", "count": 2, "voice": None,
     "desc": "选择一名单位获 0.5 临时 HP 至下个 R4。若目标为 Chorus，额外恢复 0.5 HP。"},
    {"name": "调停", "count": 2, "voice": VOICE_IND,
     "desc": "Ind 限定。选 1 Acc + 1 Str，至下个 R4 不能互相 attack。若任一方是 Chorus，摸 1 张牌。"},
    {"name": "场刊整理", "count": 2, "voice": None,
     "desc": "Ind 倾向。选一名观众（含 Chorus），双方各摸 1 张。若声部不同，可令其中一人弃 1 张。"},
    # ── v0.7 安定値交互牌 ──
    {"name": "反光板", "count": 2, "voice": VOICE_IND,
     "desc": "Ind 限定。选择一名观众，其下次旋律中 decay_factor 强制=1.0。"},
    {"name": "耳返", "count": 2, "voice": None,
     "desc": "下次旋律中你的 total_defense 在安定値计算时 -2。"},
    {"name": "和弦谱", "count": 2, "voice": VOICE_ACC,
     "desc": "Acc 限定。累计 ΔRegard +1.5。"},
]

TRANSFER_TICKET_NAME = "改签票"
MAX_HAND_SIZE = 3


class MaterialDeck:
    """ish-bosheth 物料牌系统。"""

    def __init__(self, rng: Optional[random.Random] = None):
        self._rng = rng or random.Random()

        # 牌区
        self.draw_pile: List[str] = []
        self.discard_pile: List[str] = []
        self.removed_pile: List[str] = []
        self.transfer_ticket_holder: Optional[str] = None  # pid 或 chorus_id

        # 手牌与持牌（公开）
        self.hands: Dict[str, List[str]] = {}          # pid → [card_names]
        self.chorus_slots: Dict[str, Optional[str]] = {}  # chorus_id → card_name | None

        # 座位掉落
        self.dropped_goods: Dict[str, List[str]] = {}  # seat → [card_names]

        # 每轮追踪
        self.traded_this_round: Set[str] = set()        # pid 已参与换牌
        self.played_this_turn: Dict[str, bool] = {}     # pid → 已出牌

        # 座位映射（由 IshBosheth 提供，用于 Chorus 持牌掉落定位）
        self._seat_assignments: Dict[str, str] = {}

    # ════════════════════════════════════════════════════════════════
    #  开场建牌
    # ════════════════════════════════════════════════════════════════

    def build_deck(self):
        """根据 _CARD_DEFS 创建牌堆并洗牌。"""
        self.draw_pile.clear()
        self.discard_pile.clear()
        self.removed_pile.clear()
        for card_def in _CARD_DEFS:
            for _ in range(card_def["count"]):
                self.draw_pile.append(card_def["name"])
        self._shuffle_draw()

    def opening_deal(self, real_players: List[Player],
                     chorus_units: List[ChorusUnit],
                     seat_assignments: Dict[str, str]):
        """开场发牌：改签票给 Ind 阵营，真实观众摸 2，Chorus 各摸 1。"""
        self.build_deck()
        self.hands.clear()
        self.chorus_slots.clear()
        self.dropped_goods.clear()
        self.traded_this_round.clear()
        self.played_this_turn.clear()
        self.transfer_ticket_holder = None
        self._seat_assignments = seat_assignments

        # 1. 改签票：优先给真实 Ind 玩家，否则给 Ind Chorus
        ind_real = [p for p in real_players
                    if getattr(p, 'emotion', None) == VOICE_IND and p.is_alive()]
        if ind_real:
            holder = self._rng.choice(ind_real)
            pid = holder.player_id
            self.transfer_ticket_holder = pid
            # 改签票计入起始手牌（先放进去，后面摸牌会追加）
            self.hands.setdefault(pid, []).append(TRANSFER_TICKET_NAME)
        else:
            ind_chorus = [c for c in chorus_units
                          if c.is_alive() and getattr(c, 'emotion', None) == VOICE_IND]
            if ind_chorus:
                holder = self._rng.choice(ind_chorus)
                cid = holder.player_id
                self.transfer_ticket_holder = cid
                self.chorus_slots[cid] = TRANSFER_TICKET_NAME
            # 如果没有任何 Ind 单位：改签票暂不发放，等 ma non troppo 补足后再发

        # 2. 真实观众摸 2 张
        for p in real_players:
            if not p.is_alive():
                continue
            pid = p.player_id
            if pid not in self.hands:
                self.hands[pid] = []
            for _ in range(2):
                card = self._draw_one()
                if card:
                    self.hands[pid].append(card)

        # 3. Chorus 各摸 1 张（跳过已持改签票的 Chorus）
        for c in chorus_units:
            if not c.is_alive():
                continue
            if self.chorus_slots.get(c.player_id) == TRANSFER_TICKET_NAME:
                continue
            card = self._draw_one()
            if card:
                self.chorus_slots[c.player_id] = card

    # ════════════════════════════════════════════════════════════════
    #  T0 物料阶段
    # ════════════════════════════════════════════════════════════════

    def t0_material_phase(self, player: Player, seat: str) -> List[str]:
        """[已废弃] 返回玩家在本 T0 可以执行的物料操作描述。

        实际 T0 物料阶段逻辑在 engine/action_turn.py:_phase_t0 中。
        此方法仅保留用于调试/参考，不应被生产代码调用。
        """
        lines = []
        pid = player.player_id

        # 1. 摸 1 张
        card = self._draw_one()
        if card:
            self.hands.setdefault(pid, []).append(card)
            lines.append(f"摸牌：{card}")

        # 2. 可拾取当前座位 1 张掉落物料
        dropped = self.dropped_goods.get(seat, [])
        if dropped:
            lines.append(f"可拾取({seat})：{dropped}")

        # 3-4. 换牌/出牌由 action_turn._phase_t0 处理

        # 5. 弃至手牌上限（由 action_turn._phase_t0 负责实际弃牌逻辑）
        hand = self.hands.get(pid, [])
        if len(hand) > MAX_HAND_SIZE:
            lines.append(f"手牌超限({len(hand)}/{MAX_HAND_SIZE})，需弃牌（由调用方处理）")

        return lines

    # ════════════════════════════════════════════════════════════════
    #  基础操作
    # ════════════════════════════════════════════════════════════════

    def _draw_one(self) -> Optional[str]:
        """从 draw_pile 摸 1 张，不足时洗入弃牌区。"""
        if not self.draw_pile:
            if self.discard_pile:
                self.draw_pile = list(self.discard_pile)
                self.discard_pile.clear()
                self._shuffle_draw()
            else:
                return None
        return self.draw_pile.pop()

    def _shuffle_draw(self):
        self._rng.shuffle(self.draw_pile)

    def pickup_floor(self, player_id: str, seat: str, card_name: str) -> bool:
        """从座位拾取 1 张掉落物料到手中。"""
        dropped = self.dropped_goods.get(seat, [])
        if card_name in dropped:
            dropped.remove(card_name)
            self.hands.setdefault(player_id, []).append(card_name)
            if not dropped:
                self.dropped_goods.pop(seat, None)
            return True
        return False

    def discard_from_hand(self, player_id: str, card_name: str) -> bool:
        """从手中弃 1 张牌到弃牌区。"""
        hand = self.hands.get(player_id, [])
        if card_name in hand:
            hand.remove(card_name)
            if card_name == TRANSFER_TICKET_NAME:
                self.removed_pile.append(card_name)
                self.transfer_ticket_holder = None
            else:
                self.discard_pile.append(card_name)
            return True
        return False

    def drop_all_on_seat(self, player_id: str, seat: str):
        """玩家死亡或离场：所有手牌掉落在座位。"""
        hand = self.hands.pop(player_id, [])
        if hand:
            self.dropped_goods.setdefault(seat, []).extend(hand)
            # 改签票转移
            if self.transfer_ticket_holder == player_id:
                self.transfer_ticket_holder = None  # 掉落在地，需被拾取

    def drop_chorus_card(self, chorus_id: str, seat: str):
        """Chorus 死亡：持牌掉落在座位。"""
        card = self.chorus_slots.pop(chorus_id, None)
        if card:
            self.dropped_goods.setdefault(seat, []).append(card)
            if self.transfer_ticket_holder == chorus_id:
                self.transfer_ticket_holder = None

    def take_chorus_card(self, killer_id: str, chorus_id: str) -> Optional[str]:
        """击杀 Chorus 后拿取其持牌。返回牌名。"""
        card = self.chorus_slots.pop(chorus_id, None)
        if card:
            hand = self.hands.setdefault(killer_id, [])
            if len(hand) < MAX_HAND_SIZE:
                hand.append(card)
            else:
                # 手牌满：掉落在 Chorus 所在座位
                seat = self._chorus_seat(chorus_id)
                self.dropped_goods.setdefault(seat, []).append(card)
            if self.transfer_ticket_holder == chorus_id:
                self.transfer_ticket_holder = killer_id
        return card

    def _chorus_seat(self, chorus_id: str) -> str:
        """从 seat_assignments 查找 Chorus 的座位，找不到则回退到"商店"。"""
        return self._seat_assignments.get(chorus_id, "商店")

    # ════════════════════════════════════════════════════════════════
    #  换牌
    # ════════════════════════════════════════════════════════════════

    def can_trade(self, player_id: str) -> bool:
        """本回合是否还能参与换牌。"""
        return player_id not in self.traded_this_round

    def propose_trade(self, from_id: str, to_id: str,
                      offer_card: str, want_card: str) -> bool:
        """提出换牌。返回是否有效（牌名是否在手牌中，双方是否可交易）。"""
        if not self.can_trade(from_id):
            return False
        if not self.can_trade(to_id):
            return False
        from_hand = self.hands.get(from_id, [])
        to_hand = self.hands.get(to_id, [])
        if offer_card not in from_hand:
            return False
        if want_card not in to_hand:
            return False
        return True

    def execute_trade(self, from_id: str, to_id: str,
                      offer_card: str, want_card: str) -> bool:
        """执行换牌。调用前需确认双方同意。"""
        from_hand = self.hands.get(from_id, [])
        to_hand = self.hands.get(to_id, [])
        if offer_card not in from_hand or want_card not in to_hand:
            return False
        from_hand.remove(offer_card)
        to_hand.remove(want_card)
        from_hand.append(want_card)
        to_hand.append(offer_card)
        self.traded_this_round.add(from_id)
        self.traded_this_round.add(to_id)
        return True

    def reset_round_tracking(self):
        """R4 或每轮开始时重置换牌和出牌追踪。"""
        self.traded_this_round.clear()
        self.played_this_turn.clear()

    # ════════════════════════════════════════════════════════════════
    #  查询
    # ════════════════════════════════════════════════════════════════

    def get_hand(self, player_id: str) -> List[str]:
        return list(self.hands.get(player_id, []))

    def get_chorus_card(self, chorus_id: str) -> Optional[str]:
        return self.chorus_slots.get(chorus_id)

    def get_seat_drops(self, seat: str) -> List[str]:
        return list(self.dropped_goods.get(seat, []))

    def get_card_info(self, card_name: str) -> Optional[Dict[str, Any]]:
        """查询牌的元信息。

        ⚠️ 过渡期：当前从 _CARD_DEFS 读取。后续应改为从 CARD_REGISTRY 读取
        BaseCard 子类的 name/voice/desc/count 属性。
        """
        if card_name == TRANSFER_TICKET_NAME:
            return {
                "name": TRANSFER_TICKET_NAME, "voice": None,
                "desc": "选择自己或一名同座位且同意的真实观众，改签到任意声部。使用后移出游戏。",
                "unique": True,
            }
        for cd in _CARD_DEFS:
            if cd["name"] == card_name:
                return dict(cd)
        return None

    def transfer_ticket_available(self) -> bool:
        """改签票是否还在某处（未被使用移出）。"""
        return self.transfer_ticket_holder is not None

    def use_transfer_ticket(self) -> bool:
        """使用改签票。将改签票从当前持有者手牌/持牌中移除，移入 removed_pile。"""
        if not self.transfer_ticket_holder:
            return False
        holder = self.transfer_ticket_holder
        if holder in self.hands and TRANSFER_TICKET_NAME in self.hands[holder]:
            self.hands[holder].remove(TRANSFER_TICKET_NAME)
        elif holder in self.chorus_slots and self.chorus_slots[holder] == TRANSFER_TICKET_NAME:
            self.chorus_slots[holder] = None
        self.removed_pile.append(TRANSFER_TICKET_NAME)
        self.transfer_ticket_holder = None
        return True

    def is_playable(self, player: Player, card_name: str) -> bool:
        """检查牌是否可被该玩家打出（声部限制）。

        ⚠️ 过渡期：当前从 _CARD_DEFS 读取 voice 限制。后续可委托给
        CARD_REGISTRY 中对应 BaseCard.is_playable(player)。
        """
        if card_name == TRANSFER_TICKET_NAME:
            return False  # 改签票通过独立机制使用，不走通用出牌流程
        info = self.get_card_info(card_name)
        if not info:
            return False
        voice_req = info.get("voice")
        if voice_req is None:
            return True
        return getattr(player, 'emotion', None) == voice_req

    def chorus_play_card(self, chorus: ChorusUnit, card_name: str) -> bool:
        """Chorus 打出手持物料牌。"""
        cid = chorus.player_id
        if self.chorus_slots.get(cid) != card_name:
            return False
        # Chorus 不能使用需要真实玩家身份的牌
        info = self.get_card_info(card_name)
        if info and info.get("voice") and getattr(chorus, 'emotion', None) != info["voice"]:
            return False
        # 打出后进入弃牌区
        self.chorus_slots[cid] = None
        if card_name == TRANSFER_TICKET_NAME:
            self.removed_pile.append(card_name)
            self.transfer_ticket_holder = None
        else:
            self.discard_pile.append(card_name)
        return True

    def chorus_draw(self, chorus_id: str) -> Optional[str]:
        """Chorus T0 摸牌（若持牌槽为空）。"""
        if self.chorus_slots.get(chorus_id):
            return None  # 已有牌
        card = self._draw_one()
        if card:
            self.chorus_slots[chorus_id] = card
        return card

    # ════════════════════════════════════════════════════════════════
    #  清理
    # ════════════════════════════════════════════════════════════════

    def clear_all(self):
        """结界结束时清空所有牌区。"""
        self.draw_pile.clear()
        self.discard_pile.clear()
        self.removed_pile.clear()
        self.hands.clear()
        self.chorus_slots.clear()
        self.dropped_goods.clear()
        self.traded_this_round.clear()
        self.played_this_turn.clear()
        self.transfer_ticket_holder = None

    # ════════════════════════════════════════════════════════════════
    #  调试
    # ════════════════════════════════════════════════════════════════

    def describe(self) -> str:
        lines = [
            f"物料牌系统: 牌堆={len(self.draw_pile)}, 弃牌={len(self.discard_pile)}, "
            f"移出={len(self.removed_pile)}",
            f"改签票持有者: {self.transfer_ticket_holder or '无'}",
        ]
        if self.hands:
            for pid, cards in self.hands.items():
                lines.append(f"  {pid} 手牌: {cards}")
        if self.chorus_slots:
            for cid, card in self.chorus_slots.items():
                if card:
                    lines.append(f"  {cid} 持牌: {card}")
        if self.dropped_goods:
            for seat, cards in self.dropped_goods.items():
                lines.append(f"  掉落({seat}): {cards}")
        return "\n".join(lines)


# ── 模块级工具 ──────────────────────────────────────────────────────

def get_card_count(card_name: str) -> int:
    """返回该牌在初始牌池中的数量。"""
    if card_name == TRANSFER_TICKET_NAME:
        return 1
    for cd in _CARD_DEFS:
        if cd["name"] == card_name:
            return cd["count"]
    return 0
