"""M9 天赋注册表、槽位迁移与公共数值挂点。

注册表是 ``m9-rfc`` 的唯一选池/实例化信源。未登记、未迁移或已退役的
槽位必须 fail closed，不能静默实例化 ``legacy`` / ``v2exp`` 类。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from typing import Any, Iterable

from engine.balance import get as bget
from engine.m9.text import m9_text


class TalentAvailability(StrEnum):
    """M9 槽位当前可执行状态。"""

    IMPLEMENTED = "implemented"
    BLOCKED = "blocked"
    RETIRED = "retired"


class M9TalentUnavailableError(ValueError):
    """请求了 M9 尚不能安全执行的天赋槽位。"""


@dataclass(frozen=True, slots=True)
class M9TalentRegistration:
    """一个稳定槽位及其 profile 迁移状态。"""

    slot_id: str
    legacy_number: int | None
    display_name: str
    legacy_class_path: str | None
    m9_class_path: str | None
    availability: TalentAvailability
    reason: str = ""
    aliases: tuple[str, ...] = ()
    replacement_slot_id: str | None = None

    @property
    def is_selectable(self) -> bool:
        return (
            self.availability is TalentAvailability.IMPLEMENTED
            and self.m9_class_path is not None
        )

    def unavailable_message(self) -> str:
        replacement = (
            m9_text("registry.unavailable_replacement_suffix",
                    slot_id=self.replacement_slot_id)
            if self.replacement_slot_id else ""
        )
        detail = self.reason or m9_text("registry.unavailable_default_reason")
        return m9_text("registry.unavailable_message", slot_id=self.slot_id,
                       display_name=self.display_name, detail=detail,
                       replacement=replacement)


def _registration(
    slot_id: str,
    legacy_number: int | None,
    display_name: str,
    legacy_class_path: str | None,
    *,
    m9_class_path: str | None = None,
    availability: TalentAvailability = TalentAvailability.BLOCKED,
    reason: str = "",
    aliases: tuple[str, ...] = (),
    replacement_slot_id: str | None = None,
) -> M9TalentRegistration:
    return M9TalentRegistration(
        slot_id=slot_id,
        legacy_number=legacy_number,
        display_name=display_name,
        legacy_class_path=legacy_class_path,
        m9_class_path=m9_class_path,
        availability=availability,
        reason=reason,
        aliases=aliases,
        replacement_slot_id=replacement_slot_id,
    )


_SP_ADAPTER_MISSING = m9_text("registry.reason_sp_adapter_missing")

M9_TALENT_REGISTRY: dict[str, M9TalentRegistration] = {
    "T1": _registration(
        "T1", 1, "一刀缭断", "talents.t1_one_slash.OneSlash",
        m9_class_path="engine.m9.talents.t1.OneSlash9",
        availability=TalentAvailability.IMPLEMENTED,
    ),
    "T2": _registration(
        "T2", 2, "剪刀手一突", "talents.t2_scissor_rush.ScissorRush",
        m9_class_path="engine.m9.talents.t2.ScissorRush9",
        availability=TalentAvailability.IMPLEMENTED,
    ),
    "T3": _registration(
        "T3", 3, "天星", "talents.t3_star.Star",
        m9_class_path="engine.m9.talents.t3.Star9",
        availability=TalentAvailability.IMPLEMENTED,
    ),
    "T4": _registration(
        "T4", 4, "六爻", "talents.t4_hexagram.Hexagram",
        m9_class_path="engine.m9.talents.t4.Hexagram9",
        availability=TalentAvailability.IMPLEMENTED,
    ),
    "T5": _registration(
        "T5", None, "combo", None,
        availability=TalentAvailability.RETIRED,
        reason=m9_text("registry.reason_t5_retired"),
        replacement_slot_id="G0",
    ),
    "T6": _registration(
        "T6", 6, "朝阳好市民", "talents.t6_good_citizen.GoodCitizen",
        m9_class_path="engine.m9.talents.t6.GoodCitizen9",
        availability=TalentAvailability.IMPLEMENTED,
    ),
    "T7": _registration(
        "T7", 7, "死者苏生", "talents.t7_resurrection.Resurrection",
        m9_class_path="engine.m9.talents.t7.Resurrection9",
        availability=TalentAvailability.IMPLEMENTED,
    ),
    "G0": _registration(
        "G0", 5, "砂狼白子*Terror", "talents.t5_combo.Combo",
        m9_class_path="engine.m9.talents.g0.ShirokoTerror9",
        availability=TalentAvailability.IMPLEMENTED,
        aliases=("砂狼白子", "白子Terror", "白子*Terror"),
    ),
    "G1": _registration(
        "G1", 8, "神代天赋-火萤IV型-完全燃烧",
        "talents.g1_firefly.G1MythFire",
        m9_class_path="engine.m9.talents.g1.G1MythFire9",
        availability=TalentAvailability.IMPLEMENTED,
        aliases=("火萤IV型-完全燃烧",),
    ),
    "G2": _registration(
        "G2", 9, "神代天赋-请一直注视着我",
        "talents.g2_hologram.Hologram",
        m9_class_path="engine.m9.talents.g2.Hologram9",
        availability=TalentAvailability.IMPLEMENTED,
        aliases=("请一直注视着我", "请一直，注视着我"),
    ),
    "G3": _registration(
        "G3", 10, "神代天赋-神话之外", "talents.g3_mythland.Mythland",
        m9_class_path="engine.m9.talents.g3.Mythland9",
        availability=TalentAvailability.IMPLEMENTED,
        aliases=("神话之外",),
    ),
    "G4": _registration(
        "G4", 11, "神代天赋-愿负世，照拂黎明",
        "talents.g4_savior.Savior",
        m9_class_path="engine.m9.talents.g4.Savior9",
        availability=TalentAvailability.IMPLEMENTED,
        aliases=("愿负世，照拂黎明",),
    ),
    "G5": _registration(
        "G5", 12, "神代天赋-往世的涟漪", "talents.g5.ripple.Ripple",
        m9_class_path="engine.m9.talents.g5.Ripple9",
        availability=TalentAvailability.IMPLEMENTED,
        aliases=("往世的涟漪",),
    ),
    "G6": _registration(
        "G6", 13, "神代天赋-要有笑声！", "talents.g6_cutaway.CutawayJoke",
        m9_class_path="engine.m9.talents.g6.CutawayJoke9",
        availability=TalentAvailability.IMPLEMENTED,
        aliases=("要有笑声！",),
    ),
    "G7": _registration(
        "G7", 14, "神代天赋-大叔我啊，剪短发了", "talents.g7.hoshino.Hoshino",
        m9_class_path="engine.m9.talents.g7.Hoshino9",
        availability=TalentAvailability.IMPLEMENTED,
        aliases=("大叔我啊，剪短发了",),
    ),
}

M9_ACTIVE_SLOT_IDS: tuple[str, ...] = (
    "T1", "T2", "T3", "T4", "T6", "T7",
    "G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7",
)


def _class_path(cls: type) -> str:
    return f"{cls.__module__}.{cls.__name__}"


def registration_for_slot(slot_id: str) -> M9TalentRegistration | None:
    return M9_TALENT_REGISTRY.get(slot_id.strip().upper())


def registration_for_legacy_number(number: int) -> M9TalentRegistration | None:
    return next(
        (item for item in M9_TALENT_REGISTRY.values()
         if item.legacy_number == number),
        None,
    )


def registration_for_legacy_class(cls: type) -> M9TalentRegistration | None:
    path = _class_path(cls)
    return next(
        (item for item in M9_TALENT_REGISTRY.values()
         if item.legacy_class_path == path),
        None,
    )


def resolve_registration(query: str) -> M9TalentRegistration | None:
    """按稳定槽位或唯一名称/别名解析 M9 注册项。"""
    normalized = query.strip().casefold()
    if not normalized:
        return None
    if normalized.isdecimal():
        return registration_for_legacy_number(int(normalized))
    by_slot = registration_for_slot(query)
    if by_slot is not None:
        return by_slot

    exact: list[M9TalentRegistration] = []
    partial: list[M9TalentRegistration] = []
    for item in M9_TALENT_REGISTRY.values():
        names = (item.display_name, *item.aliases)
        folded = tuple(name.casefold() for name in names)
        if normalized in folded:
            exact.append(item)
        elif any(normalized in name for name in folded):
            partial.append(item)
    matches = exact or partial
    unique = {item.slot_id: item for item in matches}
    if len(unique) > 1:
        slots = ", ".join(sorted(unique))
        raise ValueError(m9_text("registry.ambiguous_query", query=repr(query),
                                 slots=slots))
    return next(iter(unique.values()), None)


def require_selectable(registration: M9TalentRegistration) -> None:
    if not registration.is_selectable:
        raise M9TalentUnavailableError(registration.unavailable_message())


def m9_class_for_legacy(cls: type) -> type:
    registration = registration_for_legacy_class(cls)
    if registration is None:
        raise M9TalentUnavailableError(
            m9_text("registry.unregistered_legacy_class",
                    class_path=_class_path(cls))
        )
    require_selectable(registration)
    assert registration.m9_class_path is not None
    module_name, class_name = registration.m9_class_path.rsplit(".", 1)
    return getattr(import_module(module_name), class_name)


def selectable_legacy_numbers() -> frozenset[int]:
    return frozenset(
        item.legacy_number
        for item in M9_TALENT_REGISTRY.values()
        if item.is_selectable and item.legacy_number is not None
    )


def filter_selectable_entries(entries: Iterable[tuple]) -> list[tuple]:
    """过滤共享旧表，只暴露已经实现的 M9 adapter。

    表内出现未登记类时直接失败；新增天赋不能绕过注册表进入 M9。
    """
    selectable: list[tuple] = []
    for entry in entries:
        registration = registration_for_legacy_class(entry[2])
        if registration is None:
            raise RuntimeError(
                m9_text("registry.unregistered_table_class",
                        class_path=_class_path(entry[2]))
            )
        if registration.is_selectable:
            selectable.append((
                entry[0], registration.display_name, entry[2], entry[3],
            ))
    return selectable


def active_registrations() -> tuple[M9TalentRegistration, ...]:
    return tuple(M9_TALENT_REGISTRY[slot] for slot in M9_ACTIVE_SLOT_IDS)


def _ext(talent_key: str, value_key: str, default):
    return bget("m9_talents_extended", talent_key, value_key, default=default)


SPOTLIGHT_IMPROVISE = "improvise"
SPOTLIGHT_PUBLIC = "public"
SPOTLIGHT_FORESIGHT = "foresight"
SPOTLIGHT_TURNING = "turning"


@dataclass
class SpotlightIdentity:
    slot_id: str
    identity: str = SPOTLIGHT_IMPROVISE
    uses_sp: int = 1
    retired: bool = False


SLOT_MIGRATION: dict[str, str] = {
    "T5": "G0",
}

SPOTLIGHT_INDEX: dict[str, SpotlightIdentity] = {
    "T1": SpotlightIdentity("T1", SPOTLIGHT_IMPROVISE, 1),
    "T2": SpotlightIdentity("T2", SPOTLIGHT_FORESIGHT, 0),
    "T3": SpotlightIdentity("T3", SPOTLIGHT_PUBLIC, 2),
    "T4": SpotlightIdentity("T4", SPOTLIGHT_IMPROVISE, 1),
    "T5": SpotlightIdentity("T5", SPOTLIGHT_IMPROVISE, 1, retired=True),
    "T6": SpotlightIdentity("T6", SPOTLIGHT_FORESIGHT, 0),
    "T7": SpotlightIdentity("T7", SPOTLIGHT_IMPROVISE, 1),
    "G0": SpotlightIdentity("G0", SPOTLIGHT_PUBLIC, 2),
    "G1": SpotlightIdentity("G1", SPOTLIGHT_TURNING, 1),
    "G2": SpotlightIdentity("G2", SPOTLIGHT_PUBLIC, 2),
    "G3": SpotlightIdentity("G3", SPOTLIGHT_PUBLIC, 2),
    "G4": SpotlightIdentity("G4", SPOTLIGHT_TURNING, 1),
    "G5": SpotlightIdentity("G5", SPOTLIGHT_FORESIGHT, 0),
    "G6": SpotlightIdentity("G6", SPOTLIGHT_IMPROVISE, 1),
    "G7": SpotlightIdentity("G7", SPOTLIGHT_PUBLIC, 2),
}


def resolve_slot(slot_id: str) -> str:
    """槽位迁移（T5 退役 → G0；其余原样）。"""
    return SLOT_MIGRATION.get(slot_id, slot_id)


def spotlight(slot_id: str) -> SpotlightIdentity:
    return SPOTLIGHT_INDEX[resolve_slot(slot_id)]


def g0_drone_stats() -> dict:
    return {
        "max_hp": int(_ext("g0", "drone_hp", 4)),
        "bonus_damage": int(_ext("g0", "drone_bonus_damage", 1)),
    }


def g6_template_pool_categories() -> tuple[str, ...]:
    return ("move", "interact", "find", "lock", "attack")
