# CLAUDE.md

@AGENTS.md

---

## Claude Code 专属补充

项目规则、现状、命令与流程全部在上面引入的 `AGENTS.md` 里，它由 Claude Code、Codex、OpenCode、
CherryStudio agent 共用。这里只放 Claude Code 才需要的事项。要改项目规则，请改 `AGENTS.md`，
不要在本文件另写一份。

- **环境**：Windows。Bash 工具是 Git Bash，PowerShell 也可以用；Python 命令是 `python`（3.13）。
  pytest 遇到临时目录 PermissionError 时，加 `--basetemp` 指向会话的 scratchpad。
- **Hook**：`.claude/hooks/fallow-gate.sh` 会在 `git commit` / `git push` 前跑 fallow 审计，审计不过就拦截。
  遇到拦截先看原因再修，不要绕过。
- **读大文件**：GA 手册约 140KB、实施合同约 40KB。先 grep 标题定位，再按章节读取。
- **记忆**：长期有效的项目约定写进 `AGENTS.md` 或 `docs/`；auto-memory 只记用户偏好和在仓库里找不到的背景。
- **追溯文档来历**：GA 设计会话和手册初稿不在本机 Claude Code 的会话记录里。
  - 手册初稿（2026-10-25）由 CherryStudio 内置的 Claude agent 拼装，会话记录在
    `%APPDATA%/CherryStudio/Data/Agents/.claude/projects/<本仓库>/`；
  - 10-26 的 DEC 收口与 N0 数值，以及 10-27 的 I0–I3 实施，在 Codex 会话里（`~/.codex/sessions/2026/10/`）；
  - 还原手册在某个时间点的原文时，用 `artifacts/ga-migration/*/baseline.json` 记录的散列校验，
    方法见[重写计划](docs/v2.0-ga/manual_rewrite_plan.md)附录。
