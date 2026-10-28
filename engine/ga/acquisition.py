"""普通商品的免费获取核心；T2与Encore复用同一目录、容量和实际库存。

拿东西不引人注目：不登记关注，所以不会把即演的 SP 补回来。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, TYPE_CHECKING

from engine.ga.contracts import ActionVerb, CostEntry, Performance, TurnStage
from engine.ga.gameplay import ActionOption, _apply_builtin, _location_options
from engine.ga.state import touch
from engine.ga.talents.base import ability

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan
    from engine.ga.state import ActionGrant


PURCHASE_OPERATIONS = frozenset({"buy_weapon", "buy_armor", "buy_module", "buy_item", "learn"})


def take_options(
    state: GameState, grant: ActionGrant, operation: str,
) -> tuple[ActionOption, ...]:
    """只豁免商品费用和购买案底，不把补给、治疗等服务变成物品。"""
    result = []

    def collect(
        purchase: str, verb: ActionVerb, *, costs: tuple[CostEntry, ...] = (),
        **parameters: Any,
    ) -> ActionOption:
        """使用正常商店枚举，保证限制和物品清单只有一个信源。"""
        candidate = ability(grant, TurnStage.T1, operation, mode=Performance.INSTANT,
                            verb=ActionVerb.INTERACT,
                            parameters={"purchase": purchase, **parameters}, after=take_goods)
        if purchase in PURCHASE_OPERATIONS:
            result.append(candidate)
        return candidate

    def free(account: str, amount: int) -> tuple[CostEntry, ...]:
        """SP仍由演出公共预检报价，此处只清除商店费用。"""
        return ()

    _location_options(state, state.players[grant.actor_id], collect, free)
    return tuple(result)


def take_goods(state: GameState, plan: ActionPlan) -> None:
    """只接受正式候选验证过的商品，实际扣库存并获得原品，不扣通行证，也不给关注。"""
    params = plan.request.parameters
    purchase = params["purchase"]
    # 借用标记、代付标记等属于外层行动，不能混入普通商品的参数。
    selected = {key: params[key] for key in ("kind", "weapon", "spell") if key in params}
    _apply_builtin(state, replace(plan.request, operation=purchase, parameters=selected),
                   purchase_crime=False)
    touch(state)
