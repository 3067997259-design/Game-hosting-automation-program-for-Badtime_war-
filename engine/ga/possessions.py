"""可转移的普通实物；不复制天赋资源、已学法术、身份或永久改造。"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from engine.ga.contracts import ContractError, DamageAttribute, DefenseKind
from engine.ga.gameplay import ITEM_SHOPS, MODULE_SHOPS, TACTICAL_ITEMS
from engine.ga.state import touch

if TYPE_CHECKING:
    from engine.game_state import GameState


PHYSICAL_WEAPONS = frozenset({"bow", "knife", "electromagnetic_rifle", "gauss_rifle", "hook"})


def transferable_items(state: GameState, donor_id: str, receiver_id: str) -> tuple[str, ...]:
    """枚举当前持有且受方装得下的一件实物；枚举不改库存或随机流。

    弹药不单独转移，只跟着它的武器走。
    """
    donor, receiver = state.players[donor_id], state.players[receiver_id]
    cfg = state.ga_config
    choices = [f"weapon:{name}" for name in sorted(
        donor.weapons & PHYSICAL_WEAPONS - receiver.weapons)]
    choices.extend(f"armor:{key}" for key, defense in sorted(donor.defenses.items()) if
                   defense.kind is DefenseKind.ARMOR and defense.durability > 0 and
                   donor.armor_kinds.get(key) in cfg.get("armor") and key not in receiver.defenses)
    for name in sorted({*ITEM_SHOPS, *TACTICAL_ITEMS}):
        cap = cfg.get("hospital", "syringe_hold_cap") if name == "syringe" else (
            cfg.get("consumables", "hold_cap") if name in TACTICAL_ITEMS else 1)
        if donor.inventory.get(name, 0) > 0 and receiver.inventory.get(name, 0) < cap:
            choices.append(f"item:{name}")
    for name in sorted(MODULE_SHOPS):
        if donor.inventory.get(f"module:{name}", 0) > 0 or name in donor.modules:
            choices.append(f"module:{name}")
    return tuple(choices)


def ammo_cap(state: GameState, weapon: str) -> int:
    """弓按箭矢上限，枪按各自弹药上限。"""
    if weapon == "bow":
        return state.ga_config.get("start", "arrow_cap")
    return state.ga_config.get("weapons", weapon, "ammo_cap")


def transfer_item(state: GameState, donor_id: str, receiver_id: str, item: str) -> None:
    """扣除原持有者的一件再交付受方，护甲保持ID、属性与实际耐久，武器连同弹药。"""
    if item not in transferable_items(state, donor_id, receiver_id):
        raise ContractError("transfer.item", "item is missing, exclusive or exceeds capacity")
    donor, receiver = state.players[donor_id], state.players[receiver_id]
    category, _, name = item.partition(":")
    if category == "weapon":
        donor.weapons.remove(name)
        receiver.weapons.add(name)
        if name in donor.disabled_weapons:
            receiver.disabled_weapons[name] = replace(donor.disabled_weapons.pop(name),
                                                     target_id=receiver_id)
        for weapon, flag in (("knife", "sharpened"), ("gauss_rifle", "charged"),
                             ("hook", "hook_cooldown")):
            if name == weapon:
                setattr(receiver, flag, getattr(donor, flag))
                setattr(donor, flag, 0 if flag == "hook_cooldown" else False)
        if name == "bow":
            for module in donor.modules:
                key = f"module:{module}"
                donor.inventory[key] = donor.inventory.get(key, 0) + 1
            donor.modules = ()
            donor.bow_attribute = DamageAttribute.NORMAL
        carried = donor.ammo.get(name, 0)
        if carried:
            # 弹药随武器转手；受方装不下的部分留在原主手里。
            held = receiver.ammo.get(name, 0)
            moved = max(0, min(carried, ammo_cap(state, name) - held))
            receiver.ammo[name] = held + moved
            donor.ammo[name] = carried - moved
    elif category == "armor":
        receiver.defenses[name] = donor.defenses.pop(name)
        receiver.armor_kinds[name] = donor.armor_kinds.pop(name)
        receiver.armor_affinities[name] = donor.armor_affinities.pop(name)
    elif category == "module":
        key = f"module:{name}"
        if donor.inventory.get(key, 0):
            donor.inventory[key] -= 1
        else:
            modules = list(donor.modules)
            modules.remove(name)
            donor.modules = tuple(modules)
            attrs = {DamageAttribute.MAGIC for m in modules if m == "fire"} | {
                DamageAttribute.TECH for m in modules if m == "pierce"}
            if donor.bow_attribute not in attrs:
                donor.bow_attribute = min(attrs, key=lambda attr: attr.value) if attrs else (
                    DamageAttribute.NORMAL)
        receiver.inventory[key] = receiver.inventory.get(key, 0) + 1
    else:
        donor.inventory[name] -= 1
        receiver.inventory[name] = receiver.inventory.get(name, 0) + 1
    touch(state)
