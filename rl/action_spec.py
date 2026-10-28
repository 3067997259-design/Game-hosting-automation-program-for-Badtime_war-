"""RL 动作的结构化语义契约。

字符串命令仍是游戏引擎的执行接口；``ActionSpec`` 是训练、BC、trace 和未来
候选动作打分头共享的中间表示。它刻意不依赖具体动作索引，避免 schema 扩容后
把“旧 id 恰好等于新 id”误当成相同语义。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Optional, Sequence


ACTION_SPEC_VERSION = "m9-action-spec-v1"

_TARGET_VERBS = frozenset({"lock", "find", "shoot", "report", "designate"})
_SPECIAL_FAMILIES = ("热线举报", "指挥", "交易", "拆卸")


@dataclass(frozen=True)
class ActionSpec:
    """一个可审计、可序列化的动作语义。"""

    kind: str
    verb: str
    raw: str
    argument: str = ""
    target_slot: Optional[int] = None
    target_token: str = ""
    object_name: str = ""
    weapon_name: str = ""
    family: str = ""
    situation: str = ""
    option_index: Optional[int] = None
    version: str = ACTION_SPEC_VERSION

    @property
    def stable_key(self) -> str:
        """不依赖玩家显示名的语义键；未解析目标会显式标成 unresolved。"""
        target = ""
        if self.target_token:
            target = (
                f"slot:{self.target_slot}"
                if self.target_slot is not None
                else "slot:unresolved"
            )
        payload = self.family or self.object_name or self.weapon_name or self.argument
        if self.kind == "choose":
            payload = f"{self.situation}|{self.option_index}|{target or self.argument}"
        return f"{self.version}|{self.kind}|{self.verb}|{target}|{payload}"

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["stable_key"] = self.stable_key
        return data


def command_action_spec(
    command: str,
    *,
    target_slots: Optional[Mapping[str, int]] = None,
) -> ActionSpec:
    """把一条具体引擎命令解析成 ``ActionSpec``。"""
    raw = (command or "").strip()
    if not raw:
        return ActionSpec(kind="command", verb="invalid", raw=raw)

    verb, _, argument = raw.partition(" ")
    argument = argument.strip()
    slots = target_slots or {}

    if verb == "attack":
        target, _, weapon = argument.partition(" ")
        return ActionSpec(
            kind="command", verb=verb, raw=raw, argument=argument,
            target_slot=slots.get(target), target_token=target,
            weapon_name=weapon.strip(),
        )
    if verb in _TARGET_VERBS:
        return ActionSpec(
            kind="command", verb=verb, raw=raw, argument=argument,
            target_slot=slots.get(argument), target_token=argument,
        )
    if verb == "move":
        return ActionSpec(
            kind="command", verb=verb, raw=raw, argument=argument,
            object_name=argument,
        )
    if verb == "interact":
        return ActionSpec(
            kind="command", verb=verb, raw=raw, argument=argument,
            object_name=argument,
        )
    if verb == "special":
        family = next((f for f in _SPECIAL_FAMILIES if argument.startswith(f)), "")
        target_token = ""
        if family in ("热线举报", "交易"):
            possible_target = argument[len(family):]
            if possible_target in slots:
                target_token = possible_target
        return ActionSpec(
            kind="command", verb=verb, raw=raw, argument=argument,
            target_slot=slots.get(target_token) if target_token else None,
            target_token=target_token, object_name=argument, family=family,
        )
    return ActionSpec(kind="command", verb=verb, raw=raw, argument=argument)


def choose_action_spec(
    selected: str,
    options: Sequence[str],
    situation: str,
    *,
    target_slots: Optional[Mapping[str, int]] = None,
) -> ActionSpec:
    """把 choose 回调的实际选项转为同一语义契约。"""
    selected = str(selected)
    try:
        option_index: Optional[int] = list(options).index(selected)
    except ValueError:
        option_index = None
    slots = target_slots or {}
    target_token = next((token for token in slots if token and token in selected), "")
    return ActionSpec(
        kind="choose", verb="choose", raw=selected, argument=selected,
        target_slot=slots.get(target_token) if target_token else None,
        target_token=target_token, situation=str(situation or ""),
        option_index=option_index,
    )
