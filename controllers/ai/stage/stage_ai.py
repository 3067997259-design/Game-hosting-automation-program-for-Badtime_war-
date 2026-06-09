"""stage_ai.py — StageAI 统一入口

薄封装，不存状态。检测舞台状态 → 分发到对应模块 → 返回指令。
ChorusController 和 BasicAI 共用此入口。
"""

from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from engine.ish_bosheth import IshBosheth


class StageAI:
    """舞台 AI 决策入口（纯静态方法，无状态）。"""

    # ================================================================
    #  调试输出
    # ================================================================

    @staticmethod
    def _dbg(level: int, player, msg: str):
        """分级调试输出，遵循 engine.debug_config 体系。

        级别: 1=基本(发生了什么) 2=详细(为什么) 3=完整(全部上下文)
        标签: [Stg] / [Stg·] / [Stg··]（与 [Orch] 区分）
        """
        try:
            from engine.debug_config import DebugConfig
            if not DebugConfig.should_show(level):
                return
        except Exception:
            return
        name = getattr(player, 'name', str(player))
        voice = getattr(player, 'emotion', '')
        voice_str = f"({voice})" if voice else ""
        prefix = {1: "[Stg]", 2: "[Stg·]", 3: "[Stg··]"}.get(level, "[Stg]")
        print(f"{prefix} {name}{voice_str}: {msg}")

    # ================================================================
    #  get_command — 主指令生成
    # ================================================================

    @staticmethod
    def assess(player, game_state) -> dict:
        """T0 评估：预先计算局面，供 T0 选牌 + T1 指令复用。"""
        ish = getattr(game_state, 'ish_bosheth', None)
        if not ish:
            return {"phase": None}

        assessment = {"phase": ish.phase}
        if ish.phase == "duet":
            from controllers.ai.stage.duet_mode import assess_duet_stance
            from controllers.ai.stage.target_filter import get_hand
            button_seats = {b.location for b in getattr(ish, 'duet_buttons', [])}
            assessment.update({
                "stance": assess_duet_stance(player, ish, game_state),
                "button_seats": button_seats,
                "at_button": getattr(player, 'location', None) in button_seats,
                "weapons": getattr(player, 'weapons', []),
                "hand": get_hand(player, ish),
            })
            StageAI._dbg(2, player, f"T0评估: stance={assessment['stance']} "
                         f"at_btn={assessment['at_button']} hand={assessment['hand']}")
        elif ish.phase == "active":
            from controllers.ai.stage.target_filter import (
                get_legal_normal_targets, get_teammates, get_hand,
            )
            from controllers.ai.stage.normal_mode import rank_targets
            legal = get_legal_normal_targets(player, ish, game_state)
            assessment.update({
                "legal_targets": legal,
                "ranked": rank_targets(player, legal, ish, game_state) if legal else [],
                "hand": get_hand(player, ish),
            })
            StageAI._dbg(2, player, f"T0评估: targets={len(legal)} hand={assessment['hand']}")
        return assessment

    @staticmethod
    def decide_t0(player, ish, game_state, assessment: dict) -> Optional[str]:
        """T0 物料阶段：基于 assessment 选择最优牌。返回牌名或 None。"""
        hand = assessment.get("hand", [])
        if not hand or not ish or not ish.deck:
            return None
        playable = [c for c in hand if ish.deck.is_playable(player, c)]
        if not playable:
            return None

        if assessment.get("phase") == "duet":
            from controllers.ai.stage.duet_mode import decide_t0_duet
            return decide_t0_duet(player, ish, game_state, assessment, playable)
        else:
            from controllers.ai.stage.normal_mode import decide_t0_normal
            return decide_t0_normal(player, ish, game_state, assessment, playable)

    @staticmethod
    def get_command(
        player,
        game_state,
        available_actions: List[str],
        context: Optional[dict] = None,
    ) -> Optional[str]:
        """为舞台内单位生成行动指令。

        Returns:
            指令字符串（"attack X Y", "move Z", "forfeit" 等），
            或 None（表示不由 StageAI 处理，调用方自行 fallback）。
        """
        ish = getattr(game_state, 'ish_bosheth', None)
        if not ish:
            return None

        # Duet 模式
        if ish.phase == "duet":
            if player.player_id in (ish.g2_owner_id, ish.duet_g5_pid):
                return None  # G2/G5 由 restricted action 处理
            StageAI._dbg(1, player, "StageAI 接管 (duet模式)")
            from controllers.ai.stage.duet_mode import decide_duet_action
            ctx = context or {}
            cmd = decide_duet_action(player, ish, game_state, available_actions,
                                     threat_scores=ctx.get("threat_scores"),
                                     assessment=ctx.get("assessment"))
            StageAI._dbg(1, player, f"→ {cmd}")
            return cmd

        # 正常模式
        if ish.phase == "active":
            if player.player_id == ish.g2_owner_id:
                return None  # G2 的 special/forfeit 由 restricted action 处理
            StageAI._dbg(1, player, "StageAI 接管 (正常模式)")
            from controllers.ai.stage.normal_mode import decide_normal_action
            ctx = context or {}
            cmd = decide_normal_action(player, ish, game_state, available_actions,
                                       threat_scores=ctx.get("threat_scores"))
            StageAI._dbg(1, player, f"→ {cmd}")
            return cmd

        return None

    # ================================================================
    #  choose — 交互式决策（投票/Embrace/安可等）
    # ================================================================

    @staticmethod
    def choose(
        player,
        game_state,
        situation: str,
        options: List[str],
        context: Optional[dict] = None,
    ) -> Optional[str]:
        """处理舞台内的交互式选择。

        当前 MVP: duet 投票/Embrace/安可 均为 TODO 占位，返回默认值。
        """
        ish = getattr(game_state, 'ish_bosheth', None)
        if not ish:
            return None

        StageAI._dbg(2, player, f"choose {situation} opts={len(options)}")

        # ── Duet 入口投票 ──
        if situation == "duet_vote":
            from controllers.ai.stage.duet_mode import vote_duet_entry
            result = vote_duet_entry(player, ish, game_state, options)
            StageAI._dbg(1, player, f"duet_vote → {result}")
            return result

        # ── Duet 歌曲投票 ──
        if situation == "duet_song_vote":
            from controllers.ai.stage.duet_mode import vote_song
            return vote_song(player, ish, game_state, options)

        # ── 位移目的地选择 ──
        if situation == "displacement_choose":
            from controllers.ai.stage.duet_mode import choose_displacement_target
            return choose_displacement_target(player, ish, game_state, options)

        # ── Embrace 选择 ──
        if situation == "embrace":
            from controllers.ai.stage.duet_mode import decide_embrace
            return decide_embrace(player, ish, game_state, options)

        # ── 安可物品选择 ──
        if situation in ("pick_location", "pick_item"):
            return options[0] if options else ""

        return None

    # ================================================================
    #  T0 换牌决策
    # ================================================================

    @staticmethod
    def decide_trade(player, ish, game_state, hand: list) -> Optional[tuple]:
        """T0 换牌决策。返回 (partner, my_card, their_card) 或 None。"""
        from controllers.ai.stage.target_filter import get_hand
        my_seat = getattr(player, 'location', None)
        # 同座位可选交易对象（需有 controller 以完成 choose 交互）
        partners = []
        for pid in ish.participants:
            p = game_state.get_player(pid)
            if (p and p.is_alive() and p.player_id != player.player_id
                    and p.location == my_seat
                    and ish.deck.can_trade(p.player_id)
                    and getattr(p, 'controller', None) is not None):
                partners.append(p)
        for c in ish.chorus_list:
            if (c.is_alive() and c.player_id != player.player_id
                    and c.location == my_seat
                    and ish.deck.can_trade(c.player_id)
                    and getattr(c, 'controller', None) is not None):
                partners.append(c)
        if not partners:
            return None

        # 识别其他声部的专属牌 → 优先换出
        my_voice = getattr(player, 'emotion', None)
        junk = []
        for card in hand:
            info = ish.deck.get_card_info(card)
            voice = info.get("voice") if info else None
            if voice and voice != my_voice:
                junk.append((card, voice))

        if not junk:
            return None

        # 找最优交易
        for my_card, target_voice in junk:
            partners.sort(key=lambda p: getattr(
                p, 'emotion', None) == target_voice, reverse=True)
            for partner in partners:
                p_hand = get_hand(partner, ish)
                want = _pick_want_card(ish, p_hand, my_voice)
                if want and ish.deck.propose_trade(
                        player.player_id, partner.player_id, my_card, want):
                    return (partner, my_card, want)
        return None

    @staticmethod
    def decide_trade_accept(player, ish, offered_card: str,
                            my_hand: list) -> bool:
        """AI 是否接受换牌请求。"""
        my_voice = getattr(player, 'emotion', None)
        info = ish.deck.get_card_info(offered_card) if ish.deck else {}
        offered_voice = info.get("voice")
        # 是我声部的专属牌 → 接受
        if offered_voice == my_voice:
            return True
        # 通用好牌 + 我手上有其他声部垃圾 → 接受
        if offered_voice is None and offered_card in (
                "荧光棒", "前排票", "花束", "耳塞"):
            for c in my_hand:
                cinfo = ish.deck.get_card_info(c) if ish.deck else {}
                if cinfo.get("voice") and cinfo["voice"] != my_voice:
                    return True
        return False


def _pick_want_card(ish, p_hand: list, my_voice) -> Optional[str]:
    """从对方手牌中选期望换入的牌。"""
    for card in p_hand:
        info = ish.deck.get_card_info(card) if ish.deck else {}
        if info.get("voice") == my_voice:
            return card
    for card in ("前排票", "荧光棒", "耳塞", "花束", "空白票根"):
        if card in p_hand:
            return card
    return None  # 找不到优先牌，不盲目换
