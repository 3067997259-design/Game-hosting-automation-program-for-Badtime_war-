"""M9 文本门面（profile: m9-rfc）。

M9 全部用户可见中文一律经本模块读取 `data/prompts.json` 的 `m9` 命名空间：
JSON 是唯一信源，调用点不保留中文 fallback；缺键时 prompt_manager 返回
`[Missing: m9.<key>]` 诊断标记，由 tests/test_m9_text_governance.py 保证
发布前不出现缺键。

约定：
- 键名 `m9.<子系统>.<模块>.<键>`，例如 `m9.talents.g2.t0.name`；
- 模板只含纯 `{placeholder}`，数值/换行/emoji 全部写在 JSON；
- 判定键（m9_kind、命令词、地点/物品/天赋身份名）不走本门面。

本模块被 engine/m9 各层 import；不得反向 import cli 或控制器。
"""
from __future__ import annotations

from typing import Any

from engine.prompt_manager import prompt_manager

M9_TEXT_CATEGORY = "m9"


def m9_text(key: str, **kwargs: Any) -> str:
    """读取 M9 文本；kwargs 用于 `str.format` 模板替换。"""
    value = prompt_manager.get_prompt(M9_TEXT_CATEGORY, key, **kwargs)
    if isinstance(value, str):
        return value
    if value is None:
        return f"[Missing: {M9_TEXT_CATEGORY}.{key}]"
    return str(value)


def m9_text_list(key: str) -> list:
    """读取 M9 列表型文本（例如 choose 的 option 列表）。"""
    value = prompt_manager.get_prompt(M9_TEXT_CATEGORY, key)
    return list(value) if isinstance(value, list) else []
