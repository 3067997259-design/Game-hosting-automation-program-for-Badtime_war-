"""M9 机制层（profile: m9-rfc）。

独立于 v2exp 流水线：v2exp 代码不引用本包；只有 m9-rfc 接入层使用。
数值一律读 `data/balance.json` 的 `m9_system.*` / `m9_talents_extended.*`（[待风洞]）。
"""
