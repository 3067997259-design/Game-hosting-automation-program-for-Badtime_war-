"""dummy-bot —— 课程梯级 Rung 0：全程不作为的活靶子。

策略轴：起床后留在原地 forfeit，永不发育/移动/攻击/防御。

用途（RL 课程最低一级）：把"能不能学会杀人"这件事从所有其他变量里剥离。
它不叠甲（伤害不被吃）、不移动（不需要预测走位）、不还手（不需要风险权衡），
所以 RL 只要学会 find → attack 这一条链就能赢。
在这一级都赢不了，问题一定在动作空间/奖励/探索，而不在对手太强——
这正是 shoot 失明期最缺的判据：当时只有 archer/turtle 两级，
胜率 5% 无法区分"策略没学会"和"物理上发不出那条指令"。

注意：它有意保留起床（wake）。不起床的玩家不在图上，既打不到也不产生
可学习的交互，那样测的是引擎的缺席结算，不是 RL 的战斗链。
"""
from typing import Any

from controllers.bots.script_bot import ScriptBotController


class DummyBotController(ScriptBotController):

    BOT_NAME = "dummy"

    def decide(self, player: Any, game_state: Any) -> str:
        return "forfeit"
