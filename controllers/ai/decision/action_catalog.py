"""BasicAI 决策内核：ActionCatalog（唯一合法动作信源）。

从引擎侧预枚举（`engine.action_enumerator.build_action_options`，含 M9 感知的
special_op 动态列表）构建当前决策点的完整合法动作目录；M9 感知：profile 判定 +
M9 special 全覆盖（破界/热线举报/竞选队长/指挥X移动/PP 四项/交易/卸甲免费find）。

`ActionCatalog` 是 AI 命令生成的**唯一合法信源**：Orchestrator 候选经
`validate/specify` 校验与补全后转 `ScoredActionSpec`；`to_command` 是
adapter（spec → parser 可执行的字符串），供旧执行管线兼容。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from controllers.ai.decision.action_spec import ActionSpec, ScoredActionSpec


def _attack_key(raw: str) -> Optional[str]:
    """`attack <目标> <武器> [...]` → 主键 `attack <目标> <武器>`。"""
    parts = str(raw).split(" ", 3)
    if len(parts) >= 3 and parts[0] == "attack":
        return f"{parts[0]} {parts[1]} {parts[2]}"
    return None


class ActionCatalog:
    """当前决策点的合法动作目录（不可变语义：构建后只读）。"""

    def __init__(self, specs: List[ActionSpec], state_version: int,
                 profile: str) -> None:
        self._specs: List[ActionSpec] = list(specs)
        self._by_raw: Dict[str, ActionSpec] = {s.raw: s for s in specs}
        self.state_version = state_version
        self.profile = profile
        # attack 主键索引：`attack <目标> <武器>` 三段前缀（覆盖 builders 的
        # 显式 layer/attr 四参写法——与三参形式是同一合法动作）
        self._by_attack_key: Dict[str, ActionSpec] = {}
        for s in specs:
            if s.action_type == "attack":
                key = _attack_key(s.raw)
                if key is not None:
                    self._by_attack_key.setdefault(key, s)

    # ── 构建 ──

    @classmethod
    def build(cls, player: Any, game_state: Any,
              available_names: List[str], grant: Any = None,
              prebuilt_options: Optional[Dict[str, List[str]]] = None
              ) -> "ActionCatalog":
        """从引擎枚举器 + special_op 动态列表构建目录。

        `available_names` 为引擎 T1 已判定合法的动作类型名列表；`grant` 为当前
        ActionGrant（若有，附 grant_id / 即演公演许可）。

        性能注记：T1 引擎已经预枚举过 `context["action_options"]`，传入
        `prebuilt_options` 时直接复用，不再重复调用 build_action_options。
        """
        from engine.action_enumerator import build_action_options
        from engine.m9.gate import m9_enabled
        profile = "m9-rfc" if m9_enabled(game_state) else "v2exp"
        state_version = getattr(game_state, "current_round", 0)
        grant_id = getattr(grant, "grant_id", "") if grant is not None else ""
        allow_instant = bool(getattr(grant, "allow_instant", False)) \
            if grant is not None else True
        allow_public = bool(getattr(grant, "allow_public", False)) \
            if grant is not None else True

        raw_map: Dict[str, str] = {}
        # 带参类型：优先复用引擎预枚举结果；缺失/不可用时再现场枚举。
        options = prebuilt_options
        if options is None:
            try:
                options = build_action_options(player, game_state, available_names)
            except Exception:
                options = {}
        name_set = set(available_names or [])
        if options:
            for atype, cmds in options.items():
                if atype not in name_set:
                    continue
                for cmd in cmds:
                    raw_map[cmd] = atype
        # 预枚举未覆盖的带参类型做一次补齐（兼容非 T1 调用方）。
        if name_set - set(raw_map.values()):
            try:
                extra = build_action_options(
                    player, game_state, list(name_set - set(raw_map.values())))
            except Exception:
                extra = {}
            for atype, cmds in extra.items():
                for cmd in cmds:
                    raw_map.setdefault(cmd, atype)
        # 无参类型：自身即合法指令
        name_set = set(available_names or [])
        for atype in ("forfeit", "wake", "assemble", "track_guide", "recruit",
                      "election", "study", "police_command", "police_status",
                      "status", "help", "allstatus"):
            if atype in name_set and atype not in raw_map:
                raw_map[atype] = atype

        specs = [
            ActionSpec(action_type=t, raw=r, profile=profile,
                       grant_id=grant_id, state_version=state_version,
                       params={"allow_instant": allow_instant,
                               "allow_public": allow_public})
            for r, t in sorted(raw_map.items())
        ]
        return cls(specs, state_version, profile)

    # ── 只读查询 ──

    def specs(self) -> List[ActionSpec]:
        return list(self._specs)

    def raws(self) -> List[str]:
        return list(self._by_raw.keys())

    def contains(self, raw: str) -> bool:
        return raw in self._by_raw

    def get(self, raw: str) -> Optional[ActionSpec]:
        return self._by_raw.get(raw)

    def validate(self, raw: str) -> bool:
        """revalidate：候选指令是否在本目录内（含无参自合法类型）。"""
        return self.match(raw) is not None \
            or str(raw).strip().lower() == "forfeit"

    def match(self, raw: str) -> Optional[Tuple[ActionSpec, str]]:
        """主键匹配：返回 (合法锚点 spec, 实际执行 raw)。

        - 精确命中 → (spec, raw)；
        - attack 按 目标+武器 三段主键匹配 → (catalog 锚点, 原始四参 raw)，
          保留 builders 的显式 layer/attr 指定（合法但枚举器未枚举的写法）；
        - 无参类型（raw 本身即 action_type）→ 精确匹配。
        """
        spec = self._by_raw.get(raw)
        if spec is not None:
            return spec, raw
        key = _attack_key(raw)
        if key is not None:
            anchor = self._by_attack_key.get(key)
            if anchor is not None:
                return anchor, raw
        return None

    def specify(self, raw: str, score: float = 0.0,
                reason: str = "") -> Optional[ScoredActionSpec]:
        """把候选指令字符串绑定到目录内 ActionSpec 并打分。"""
        spec = self._by_raw.get(raw)
        if spec is None:
            return None
        return ScoredActionSpec(spec=spec, score=score, reason=reason)

    def substitute(self, raw: str) -> Optional[ScoredActionSpec]:
        """未命中候选 → 同 action_type 的目录内合法候选（取首项，score=0）。

        返回 None 表示目录内无该动作类型（调用方应剔除该候选）。
        这是"唯一合法信源"的输出层强制：不返回不在目录内的命令。
        """
        action_type = str(raw).strip().split(" ", 1)[0]
        for spec in self._specs:
            if spec.action_type == action_type:
                return ScoredActionSpec(spec=spec, score=0.0,
                                        reason="substituted-from-catalog")
        return None

    def to_command(self, spec: ActionSpec) -> str:
        """adapter：ActionSpec → parser 可执行字符串（旧管线兼容）。"""
        return spec.raw
