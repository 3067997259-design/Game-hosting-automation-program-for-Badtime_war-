## 问题

`engine/action_turn.py` 的 `_get_available_actions` 中，`report` 被无条件加入所有地点的可用指令列表（只要 `self.state.police_engine` 存在且有存活对手）。

根据 README §10.2(P1)，举报需要前往警察局（除非玩家持有「朝阳好市民」天赋的「举报热线」能力）。

## 影响

- bot_bridge Stage 1 在所有地点都会向 AIRI 展示 `report` 指令
- AIRI 选择后 Stage 2 会枚举可举报目标（`_enumerate_report`）
- 指令发送到服务端后被 `cli/validator` 拒绝

## 修复方向

`_get_available_actions` 中检查：`player.location == "警察局"` 或 `player.talent == 朝阳好市民`。
