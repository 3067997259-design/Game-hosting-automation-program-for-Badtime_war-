"""OrchestratorContext —— 编排器上下文（跨模块共享的运行时状态）

各 Mind / CommandBuilder 的 assess/build 方法通过此对象访问
来自 Orchestrator 维护的运行时上下文，避免传递大量散落参数。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set


@dataclass
class OrchestratorContext:
    """Orchestrator 传递给各模块的上下文快照。"""

    # ── 威胁 ──
    threat_scores: Dict[str, float] = field(default_factory=dict)
    low_threat_streak: Dict[str, int] = field(default_factory=dict)
    been_attacked_by: Set[str] = field(default_factory=set)
    players_who_attacked: Set[str] = field(default_factory=set)

    # ── 战斗 ──
    in_combat: bool = False
    combat_target: Any = None
    danger_mode: bool = False

    # ── LLM 修正 ──
    llm_aggression_mod: float = 0.0
    llm_alliance: Set[str] = field(default_factory=set)

    # ── 天星后续 ──
    star_follow_up_rounds: int = 0

    # ── Terror 防御 ──
    terror_defense: Any = None

    # ── 警察 ──
    police_protected_ids: Set[str] = field(default_factory=set)
    police_stance: Any = None
    police_cache: Optional[Dict] = None
    political_fallback_level: str = "none"
    personality: str = "balanced"
    police_dev_assignments: Dict = field(default_factory=dict)
    police_dev_initialized: bool = False
    last_criminal_target_id: Optional[str] = None
    ai_state: Any = None
