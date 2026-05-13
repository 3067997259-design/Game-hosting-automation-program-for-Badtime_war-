## 问题

`rl/action_space.py` 的 `SPECIAL_OPS` 仅包含 6 个基础操作（磨刀/吟唱魔法护盾/展开AT力场/蓄力电磁步枪/蓄力高斯步枪/释放病毒），不包含 G7 星野的 special 操作（Hoshino/取消盾牌/修复/肾上腺素/更衣）。

`engine/action_enumerator.py` 已修复为动态调用 `get_available_specials()`。RL 的 130 维 Discrete 动作空间未扩展。

## 影响

- RL 模型无法通过主动作空间输出 G7 专用操作
- G7 战术宏通过 `choose()` 通道处理，不受此影响

## 修复方向

扩展 `ACTION_COUNT` 和动作空间索引，新增 G7 special 操作索引（约 5-8 个新索引），更新 `idx_to_command` 和 `build_action_mask`。
