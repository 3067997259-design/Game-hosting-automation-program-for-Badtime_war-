"""turtle-bot —— 纯龟缩：只叠甲发育，永不攻击。

策略轴（v1 经济下的极限防御路线）：
home 拿盾牌 → 商店打工攒凭证 → 陶瓷护甲 → 医院打工 → 晶化皮肤手术 → 蹲家 forfeit。
用途：测"龟缩是不是最优解"——v1 基线下它的胜率/存活轮数就是龟缩收益的标尺；
v2.0 经济改革（修甲收费/零被动收入）落地后同一 bot 重跑即得改革效果。
"""
from typing import Any

from controllers.bots.script_bot import ScriptBotController


class TurtleBotController(ScriptBotController):

    BOT_NAME = "turtle"

    def decide(self, player: Any, game_state: Any) -> str:
        home = self.my_home(player)

        # 1. 第一件外甲：盾牌（home 免费）
        if not self.has_armor_named(player, "盾牌"):
            if player.location != home:
                return f"move {home}"
            return "interact 盾牌"

        # 2. 第二件外甲：陶瓷护甲（商店，需凭证）
        if not self.has_armor_named(player, "陶瓷护甲"):
            if player.location != "商店":
                return "move 商店"
            if player.vouchers < 1:
                return "interact 打工"
            return "interact 陶瓷护甲"

        # 3. 内甲：晶化皮肤手术（医院，需凭证，手术清空凭证）
        if not self.has_armor_named(player, "晶化皮肤"):
            if player.location != "医院":
                return "move 医院"
            if player.vouchers < 1:
                return "interact 打工"
            return "interact 晶化皮肤手术"

        # 4. 发育完成：回家蹲点，永不攻击
        if player.location != home:
            return f"move {home}"
        return "forfeit"
