"""
向后兼容 shim —— 原 ai_basic.py 已拆分至 controllers/ai/ 包。
所有外部 `from controllers.ai_basic import ...` 无需修改。
"""
from controllers.ai.controller import BasicAIController          # noqa: F401
from controllers.ai.controller import create_ai_controller       # noqa: F401
from controllers.ai.controller import create_random_ai_controller # noqa: F401
from controllers.ai.constants import (                            # noqa: F401
    EQUIPMENT_LOCATION, LOCATIONS, LOCATION_ITEMS,
    NEED_PROVIDERS, EFFECTIVE_AGAINST, POLICE_AOE_WEAPONS,
    SPELL_PREREQUISITES, PERSONALITY_NEEDS,
)