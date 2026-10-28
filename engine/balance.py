"""balance.json 只读加载器 —— 游戏数值配置的单一信源入口。

约定：
- 启动时加载一次（惰性），后续调用 get() 返回缓存值。
- 所有新代码的游戏数值必须经此读取，禁止在业务代码中硬编码。
- 缺键返回 default 值并发出 warning（不崩溃），方便渐进迁移。
- GA 使用 get_required() 严格读取；文件错误、坏结构和缺键均抛出可定位异常。
"""
from __future__ import annotations
import json
import os
import warnings
from typing import Any, Dict, Optional

_BALANCE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "balance.json")

_balance: Optional[Dict[str, Any]] = None


class BalanceConfigError(ValueError):
    """配置加载或校验失败，携带完整参数路径。"""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


def _load(*, strict: bool = False) -> Dict[str, Any]:
    """惰性加载 balance.json（首次调用时载入）。"""
    global _balance
    if _balance is not None:
        if strict and not isinstance(_balance, dict):
            raise BalanceConfigError("balance", "expected a JSON object")
        return _balance
    try:
        with open(_BALANCE_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if strict and not isinstance(loaded, dict):
            raise BalanceConfigError("balance", "expected a JSON object")
        _balance = loaded
        return _balance
    except FileNotFoundError as exc:
        if strict:
            raise BalanceConfigError("balance", f"file not found: {_BALANCE_PATH}") from exc
        warnings.warn(f"balance.json not found at {_BALANCE_PATH}, using empty fallback")
        _balance = {}
        return _balance
    except json.JSONDecodeError as e:
        if strict:
            raise BalanceConfigError(
                "balance", f"invalid JSON at line {e.lineno}, column {e.colno}"
            ) from e
        warnings.warn(f"balance.json parse error: {e}, using empty fallback")
        _balance = {}
        return _balance
    except (OSError, UnicodeDecodeError) as exc:
        if strict:
            raise BalanceConfigError("balance", f"cannot read {_BALANCE_PATH}") from exc
        raise


def get_required(*keys: str) -> Any:
    """严格读取必填参数；没有默认值或旧版回退，异常包含完整键路径。"""
    if not keys or any(not isinstance(key, str) or not key for key in keys):
        raise ValueError("get_required expects one or more non-empty string keys")
    data = _load(strict=True)
    path = ".".join(keys)
    for key in keys:
        if not isinstance(data, dict):
            raise BalanceConfigError(path, f"cannot descend into non-object before {key}")
        if key not in data:
            raise BalanceConfigError(path, f"missing required key {key}")
        data = data[key]
    return data


def get(*keys: str, default: Any = None) -> Any:
    """读取嵌套键对应的值。缺键时返回 default 并发出 warning。

    注意：返回的是内部缓存的引用（dict/list 为可变对象），调用方**不得修改**
    返回值——否则会污染整个进程的 balance 缓存。需要改动时先做拷贝。

    用法：
        from engine.balance import get as bget
        menu = bget("stage", "encore_reward_menu", default={})
    """
    data = _load()
    for key in keys:
        if not isinstance(data, dict):
            warnings.warn(f"balance: cannot descend into non-dict at key '{key}'")
            return default
        if key not in data:
            warnings.warn(f"balance: key path {keys} not found (missing '{key}')")
            return default
        data = data[key]
    return data


def reload(*, strict: bool = False) -> Dict[str, Any]:
    """强制重新加载（调试用）。"""
    global _balance
    _balance = None
    return _load(strict=strict)
