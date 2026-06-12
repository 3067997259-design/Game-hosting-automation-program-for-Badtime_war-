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

    @staticmethod
    def _lacks_funds(player: Any, item: str) -> bool:
        """资金检查：v1 凭证资格 / m4 信用点价格表。"""
        from engine.economy import m4_enabled, price
        if m4_enabled():
            from engine.balance import get as _bget
            cost = price(item)
            if item.endswith("手术"):
                cost = _bget("economy", "surgery_min_cost", default=4)
            return player.credits < cost
        return player.vouchers < 1

    def _crystal_skin_done(self, player: Any) -> bool:
        """晶化皮肤完成判定：v1 内甲 piece / hp20 surgeries_done。"""
        if "晶化皮肤" in getattr(player, 'surgeries_done', set()):
            return True
        return self.has_armor_named(player, "晶化皮肤")

    def decide(self, player: Any, game_state: Any) -> str:
        home = self.my_home(player)

        # 1. 第一件外甲：盾牌（home 免费）
        if not self.has_armor_named(player, "盾牌"):
            if player.location != home:
                return f"move {home}"
            return "interact 盾牌"

        # 2. 第二件外甲：陶瓷护甲（商店）
        if not self.has_armor_named(player, "陶瓷护甲"):
            if player.location != "商店":
                return "move 商店"
            if self._lacks_funds(player, "陶瓷护甲"):
                return "interact 打工"
            return "interact 陶瓷护甲"

        # 3. 内甲：晶化皮肤手术（医院）
        if not self._crystal_skin_done(player):
            if player.location != "医院":
                return "move 医院"
            if self._lacks_funds(player, "晶化皮肤手术"):
                return "interact 打工"
            return "interact 晶化皮肤手术"

        # 4. m4 龟缩经济压力测试腿：耐久受损则去商店修理（钱包烧给修甲）
        from engine.economy import m4_enabled
        if m4_enabled():
            ceramic = next((a for a in getattr(player.armor, 'outer', []) or []
                            if a.name == "陶瓷护甲"
                            and getattr(a, 'durability', 1) < getattr(a, 'max_durability', 1)), None)
            if ceramic is not None:
                if player.location != "商店":
                    return "move 商店"
                if self._lacks_funds(player, "修理陶瓷护甲"):
                    return "interact 打工"
                return "interact 修理陶瓷护甲"

        # 5. 发育完成：回家蹲点，永不攻击
        if player.location != home:
            return f"move {home}"
        return "forfeit"
