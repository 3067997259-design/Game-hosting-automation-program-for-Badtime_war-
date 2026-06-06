"""controllers/ai/stage/ — G2 舞台 AI 决策模块

提供 Chorus 和 BasicAI 共享的舞台内攻击/目标/移动决策。
非 TalentHook，通过统一入口 StageAI 分发到 normal_mode / duet_mode。
"""

from controllers.ai.stage.stage_ai import StageAI
from controllers.ai.stage.target_filter import (
    get_legal_normal_targets,
    get_legal_duet_targets,
    get_teammates,
    get_opponents,
)

__all__ = [
    "StageAI",
    "get_legal_normal_targets",
    "get_legal_duet_targets",
    "get_teammates",
    "get_opponents",
]
