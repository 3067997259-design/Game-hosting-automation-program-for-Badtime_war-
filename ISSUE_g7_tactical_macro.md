## 问题

G7 星野执行 `special Hoshino` 后进入战术宏模式，可用的子命令（架盾、射击、持盾、投掷、冲刺、服药、重新装填、find、lock、转向、排弹、取消、terminal）没有任何枚举逻辑。

当前 `build_action_options` 不覆盖战术宏子模式。Stage 1/Stage 2 在宏模式中完全失效。

## 影响

- bot_bridge 无法向 AIRI 展示可用的战术命令
- AIRI 只能通过传统回退路径（自由输入）选择战术动作
- BasicAI 通过控制器硬编码绕过，不受影响

## 修复方向

需改造 `_execute_tactical_macro` 的 input 循环，使 bot_bridge 在宏模式下能接收枚举选项并发送给 AIRI。
