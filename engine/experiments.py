"""实验开关 —— V2.0 EXP 机制的特性门控（feature gate）。

配置来源（优先级从低到高）：
1. config/game_config.json 的 "experiments" 分区：{"实验名": true/false}
2. 运行时 enable()/disable() 覆盖（CLI --experiment 透传用）

约定：
- 实验名用 snake_case 英文（如 "k_action_quota"、"hit_dodge_system"）。
- 查询未声明的实验返回 False（默认关闭，v1 行为不变）。
- 与 engine/balance.py 分工：experiments 管"开不开"，balance.json 管"开了之后数值多少"。
"""
from __future__ import annotations
import json
import os
from typing import Dict, List, Optional

_CONFIG_PATHS = ["config/game_config.json", "game_config.json"]

_config_flags: Optional[Dict[str, bool]] = None
_runtime_overrides: Dict[str, bool] = {}


def _load() -> Dict[str, bool]:
    """惰性加载 game_config.json 的 experiments 分区（首次调用时载入一次）。"""
    global _config_flags
    if _config_flags is not None:
        return _config_flags
    _config_flags = {}
    for path in _CONFIG_PATHS:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("experiments", {})
                if isinstance(raw, dict):
                    _config_flags = {
                        str(k): bool(v) for k, v in raw.items()
                        if not str(k).startswith("_")
                    }
            except Exception as e:
                print(f"  ⚠️ experiments 配置加载失败 {path}: {e}")
            break
    return _config_flags


def is_enabled(name: str) -> bool:
    """查询实验是否启用。未声明的实验返回 False（v1 行为）。"""
    if name in _runtime_overrides:
        return _runtime_overrides[name]
    return _load().get(name, False)


def enable(name: str) -> None:
    """运行时启用实验（CLI 覆盖，优先于配置文件）。"""
    _runtime_overrides[name] = True


def disable(name: str) -> None:
    """运行时禁用实验（CLI 覆盖，优先于配置文件）。"""
    _runtime_overrides[name] = False


def active() -> List[str]:
    """返回当前启用的实验名列表（排序稳定，供 run header 打印与日志）。"""
    merged = dict(_load())
    merged.update(_runtime_overrides)
    return sorted(k for k, v in merged.items() if v)


def reset() -> None:
    """清空运行时覆盖并强制重读配置（测试用）。"""
    global _config_flags
    _config_flags = None
    _runtime_overrides.clear()
