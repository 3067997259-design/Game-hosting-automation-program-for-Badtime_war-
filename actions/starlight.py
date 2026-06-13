"""往世层·星光行动（M6e，experiment: m6_scoring，v2.0 §5）。

死者坠入往世层成星，每轮挣星光，可做星光行动影响生者世界：
- 拨弄命运（fate_cost）：某玩家下轮先攻 ±1
- 预兆（omen_cost）：某地点投预兆，下轮第一个交互者 ±2 HP
- 加冕（coronation_cost）：某玩家下一个喝彩事件分值 ×2

星不能直接造成伤害/击杀。星光行动产生往世分（×afterlife_mult 计入终分）。
死者用原 controller.choose 选目标（死亡时 controller 未替换）。
每轮 1 次，对同一玩家每 2 轮限 1 次。
"""
from typing import Any, List, Optional

from engine.balance import get as bget


def available_actions(star: Any) -> List[str]:
    """该星当前星光够用的行动列表。"""
    sl = getattr(star, "starlight", 0)
    acts = []
    if sl >= bget("afterlife", "fate_cost", default=1):
        acts.append("拨弄命运")
    if sl >= bget("afterlife", "omen_cost", default=2):
        acts.append("预兆")
    if sl >= bget("afterlife", "coronation_cost", default=2):
        acts.append("加冕")
    return acts


def perform(star: Any, game_state: Any) -> Optional[str]:
    """执行一次星光行动（死者用原 controller 选择）。返回行动描述或 None。"""
    acts = available_actions(star)
    if not acts:
        return None
    acts = acts + ["不行动"]
    choice = star.controller.choose(
        "星光行动（往世层）：", acts,
        context={"phase": "starlight", "situation": "afterlife"})
    if choice == "不行动" or choice not in acts:
        return None

    # 选目标玩家（生者）
    living = [game_state.get_player(pid) for pid in game_state.player_order]
    living = [p for p in living if p and p.is_alive()
              and not getattr(p, "is_chorus", False)]
    if not living:
        return None

    # 每 2 轮对同一玩家限 1 次
    rnd = game_state.current_round
    last = getattr(star, "_starlight_targets", {})

    if choice == "拨弄命运":
        names = [p.name for p in living]
        tname = star.controller.choose("拨弄谁的命运（先攻+1）：", names,
                                       context={"phase": "starlight"})
        target = next((p for p in living if p.name == tname), None)
        if target is None or last.get(target.player_id, -99) > rnd - 2:
            return None
        _spend(star, "fate_cost")
        last[target.player_id] = rnd
        star._starlight_targets = last
        # 复用先攻自消耗 flag（与喝彩重掷同款 buff 模式，下轮 R1 生效）
        target._star_fate_bonus = getattr(target, "_star_fate_bonus", 0) + 1
        game_state.log_event("starlight", star=star.player_id,
                             action="fate", target=target.player_id)
        return f"✨ 星·{star.name} 拨弄了 {target.name} 的命运（下轮先攻 +1）"

    if choice == "加冕":
        names = [p.name for p in living]
        tname = star.controller.choose("加冕谁（下个喝彩×2）：", names,
                                       context={"phase": "starlight"})
        target = next((p for p in living if p.name == tname), None)
        if target is None or last.get(target.player_id, -99) > rnd - 2:
            return None
        _spend(star, "coronation_cost")
        last[target.player_id] = rnd
        star._starlight_targets = last
        target._coronation_active = True
        game_state.log_event("starlight", star=star.player_id,
                             action="coronation", target=target.player_id)
        return f"✨ 星·{star.name} 加冕了 {target.name}（下个喝彩分值 ×2）"

    if choice == "预兆":
        from actions.move import get_all_valid_locations
        locs = get_all_valid_locations(game_state)
        loc = star.controller.choose("在哪投下预兆：", locs,
                                     context={"phase": "starlight"})
        if loc not in locs:
            return None
        _spend(star, "omen_cost")
        omens = getattr(game_state, "omens", {})
        omens[loc] = rnd
        game_state.omens = omens
        game_state.log_event("starlight", star=star.player_id,
                             action="omen", location=loc)
        return f"✨ 星·{star.name} 在 {loc} 投下预兆"

    return None


def _spend(star: Any, cost_key: str) -> None:
    """消耗星光并积累往世分（×折算在 final_score 统一处理）。"""
    cost = bget("afterlife", cost_key, default=1)
    star.starlight -= cost
    star.afterlife_score = getattr(star, "afterlife_score", 0) + cost
