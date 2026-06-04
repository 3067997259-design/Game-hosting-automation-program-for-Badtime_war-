"""荧光棒 ×2 — 通用（Acc倾向）"""
from engine.cards.base import BaseCard
from engine.ish_bosheth import STRAPPANDO
from cli import display

class GlowStick(BaseCard):
    name = "荧光棒"
    count = 2
    desc = "本回合下一次 attack 伤害 +0.5。若目标是 Strappando，改为 +1.0。"

    def play(self, player, ish, turn_mgr):
        display.show_info(f"🎴 {player.name} 使用 {self.name}")

        # v2.0 duet: 可选自用（打按钮加成）或上供舞台（低保热力）
        if ish.phase == "duet":
            choice = player.controller.choose(
                "荧光棒：自用（按钮伤害+0.5）还是上供舞台（热力+0.5）？",
                ["自用（打按钮加成）", "上供舞台（热力贡献）"],
                context={"phase": "T0", "situation": "g2_card_glow_stick_duet"}
            )
            if "上供" in choice:
                ish.offer_heat(player, 0.5, self.name)
                return

        player._card_damage_bonus = 0.5
        player._card_damage_bonus_voice_filter = STRAPPANDO
