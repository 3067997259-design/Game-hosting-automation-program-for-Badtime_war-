# AGENTS.md - OpenCode Configuration

This project uses the Python Expert Agent pack for OpenCode.

## Project Info

| Field | Value |
|-------|-------|
| Type | Python |
| Framework | FastAPI |
| Python Version | 3.13+ |

## Available Skills

| Skill | Triggers | Purpose |
|-------|----------|---------|
| python-fundamentals | `*.py`, `python`, `dataclass` | Core Python patterns |
| python-fastapi | `fastapi`, `pydantic`, `endpoint` | FastAPI production patterns |
| python-backend | `sqlalchemy`, `database`, `orm` | SQLAlchemy 2.0 async |
| python-testing-general | `pytest`, `test`, `mock` | pytest fundamentals |
| python-testing-deep | `hypothesis`, `property-based` | Advanced testing |
| python-asyncio | `async`, `await`, `asyncio` | Async patterns |
| python-type-hints | `typing`, `mypy`, `pyright` | Type system |
| python-package-management | `uv`, `pip`, `pyproject` | UV package manager |
| python-tooling | `docker`, `ci`, `cd` | DevOps/CI-CD |
| python-fundamentals-313 | `3.13`, `jit`, `free-threading` | Python 3.13+ features |

## Usage

```
skill(name="python-fastapi")
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

- Skills: `.opencode/skills/*/SKILL.md`
- Standards: `.opencode/context/python/standards.md`
- Patterns: `.opencode/context/python/patterns.md`
- Security: `.opencode/context/python/security.md`
