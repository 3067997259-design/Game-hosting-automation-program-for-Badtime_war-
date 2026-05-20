"""AIState —— 新架构统一可变状态

Controller 和 Orchestrator 共享同一 AIState 引用。
事件回调直接修改此对象，Orchestrator.generate() 下次调用时自动可见。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class AIState:
    threat_scores: Dict[str, float] = field(default_factory=dict)
    low_threat_streak: Dict[str, int] = field(default_factory=dict)
    been_attacked_by: Set[str] = field(default_factory=set)
    players_who_attacked: Set[str] = field(default_factory=set)
    in_combat: bool = False
    combat_target: Any = None
    danger_mode: bool = False
    my_kills: int = 0
    round_number: int = 0
    current_phase: str = "development"
    consecutive_forfeits: int = 0
    last_action: Optional[str] = None
    last_commands: List[str] = field(default_factory=list)
    police_cache: Optional[Dict] = None
    llm_aggression_mod: float = 0.0
    llm_alliance: Set[str] = field(default_factory=set)
    terror_defense: Any = None
    political_fallback_level: str = "none"
    police_dev_assignments: Dict = field(default_factory=dict)
    police_dev_initialized: bool = False
    last_criminal_target_id: Optional[str] = None
    virus_active: bool = False
    virus_location: Optional[str] = None
    star_follow_up_rounds: int = 0
    missile_cooldown: int = 0
    action_used: bool = False
    last_combat_location: Any = None
    combat_just_ended_at: Any = None
