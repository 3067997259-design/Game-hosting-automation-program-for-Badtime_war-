---
doc_id: reference.commands
status: candidate
profile: v2exp
canonical_for: ["reference.commands.summary"]
requires: ["core.rounds_actions"]
topics: ["reference", "commands"]
source_body_sha256: 7202c7bf7e346b23b6a6adb38da6cfe9b47a49b27be6d081794ded763abec4d5
---
## 附录 B · 指令速查
> 完整且最新的指令清单见 `docs/operations/commands.md`（该文件仍需一轮 V2.0 更新）。以下为稳定动词速查。
| 行动 | 关键字 |
|---|---|
| 起床 | `wake` / `起床` / `w` |
| 移动 | `move <地点>` / `移动` / `m` |
| 交互 | `interact <项目>` / `买` / `学` / `做` / `打工` |
| 锁定 | `lock <目标>` / `锁定` |
| 找到 | `find <目标>` / `找` |
| 攻击 | `attack <目标> <武器>` / `打` |
| 射箭（弓） | `shoot <目标>` / `射箭`（V2.0 新增，跨地点） |
| 钩索 | `hook ...`（V2.0 新增，拉人 / 拉己） |
| 特殊操作 | `special <操作>` / `蓄力<武器>` 等 |
| 放弃 | `forfeit` / `放弃` / `f` |
| 警察 | `report` / `assemble` / `election` / `designate` / `police ...` |
| 查看（不耗行动） | `status` / `allstatus` / `help` |
> 弓模块装卸、补给箭矢走 `interact`；星光行动、喝彩消费等 V2.0 新指令的精确写法随实现敲定，以游戏内 `help` 为准。
---
