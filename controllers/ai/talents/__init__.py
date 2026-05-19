"""AI 天赋钩子 —— 以插件方式提供天赋AI行为

已实现:
- BaseTalentAIHook — 基类
- TerrorDefenseAI — 非星野AI应对Terror
- HoshinoAIHook — 星野(G7)
- HologramAIHook — 全息影像(G2)
- SaviorAIHook — 愿负世(G4)
- FireflyAIHook — 火萤IV型(G1)
"""

from controllers.ai.talents.base_hook import BaseTalentAIHook
from controllers.ai.talents.terror_defense import TerrorDefenseAI
from controllers.ai.talents.hoshino_hook import HoshinoAIHook
from controllers.ai.talents.g1_g2_g4_hooks import HologramAIHook, SaviorAIHook, FireflyAIHook

__all__ = [
    "BaseTalentAIHook",
    "TerrorDefenseAI",
    "HoshinoAIHook", "HologramAIHook", "SaviorAIHook", "FireflyAIHook",
]
