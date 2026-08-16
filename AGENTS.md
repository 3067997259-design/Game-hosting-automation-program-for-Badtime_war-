# AGENTS.md - OpenCode Configuration

This project uses the Python Expert Agent pack for OpenCode.

> **Project-specific rules are in `CLAUDE.md` — read it before touching code.**
> The skill tables below are the generic Python pack; only use what the actual
> change needs. This project is **not** FastAPI/SQLAlchemy.

## Project Info

| Field | Value |
|-------|-------|
| Type | Python (纯后端/命令行；网络为 TCP socket) |
| Framework | 无 Web 框架；RL 可选 torch + stable-baselines3 |
| Python Version | 项目声明 3.8+；当前开发环境实测 3.13 |
| 当前主线 | `m9-rfc` profile（见 CLAUDE.md §0） |
| 核心命令 | `python stats_runner.py --profile m9-rfc --players 6 --games 500` |

## Available Skills

| Skill | Triggers | Purpose |
|-------|----------|---------|
| python-fundamentals | `*.py`, `python`, `dataclass` | Core Python patterns |
| python-testing-general | `pytest`, `test`, `mock` | pytest fundamentals |
| python-testing-deep | `hypothesis`, `property-based` | Advanced testing |
| python-type-hints | `typing`, `mypy`, `pyright` | Type system |

FastAPI / SQLAlchemy / Docker / CI 技能表保留为可选工具，按实际需求选用。

## Usage

```
skill(name="python-testing-general")
```

## Subagents

| Subagent | Use For |
|----------|---------|
| python-coder | Code generation |
| python-reviewer | Code review |
| python-tester | Writing tests |
| python-scout | Finding context |

## Configuration

Main config: `.opencode/config.json`

```json
{
  "agent": "python-expert"
}
```

## Resources

- 项目规则/架构/禁区：`CLAUDE.md`
- Skills: `.opencode/skills/*/SKILL.md`
- Standards: `.opencode/context/python/standards.md`
- Patterns: `.opencode/context/python/patterns.md`
- Security: `.opencode/context/python/security.md`
