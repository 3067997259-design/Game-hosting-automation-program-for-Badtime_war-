## Summary

- **interact 小刀凭证检查**：小刀在商店需凭证，改为地点感知检查
- **special 动态枚举**：_enumerate_special 改为委托 get_available_specials()，覆盖 G7 操作
- **lock 远程武器前置**：仅在持有远程武器时出现 lock
- **Stage 2 时间戳解析**：剥离 AIRI 回复中的时间戳前缀
- **聊天竞态修复**：_on_chat_message 不再等待回复
- **上下文覆盖修复**：_context_frozen 防止迟到事件覆盖清空
- **reset_airi_gamestate.py 修复**：从 config 读取一致的 module_id
- **test_human_bridge.py 新增**：人类测试模式

## Known issues (follow-up issues)

- report 不在警察局也可看到
- G7 战术宏子命令无枚举
- RL SPECIAL_OPS 仅 6 个基础操作

## Test plan

- [x] `python test_human_bridge.py` — 人类测试模式可连接游戏
- [x] lint 全部通过
- [ ] `python stats_runner.py --players 6 --games 100` — 集成回归
