"""[DECOMMISSION] 旧架构 Mixin —— C5 后全部方法已委托到 HoshinoImpl。
旧管道（use_new_arch=False）在 C7 前仍需本文件的 MRO 存在，但方法体均为单行委托。
"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional, Any
from controllers.ai.talents.hoshino_impl import HoshinoImpl
from controllers.ai.constants import debug_ai_basic

if TYPE_CHECKING:
    from controllers.ai.controller import BasicAIController

_Base = BasicAIController if TYPE_CHECKING else object


class HoshinoMixin(_Base):

    def _get_hoshino_impl(self):
        """懒初始化 HoshinoImpl 实例。"""
        if not hasattr(self, '_hoshino_impl_'):
            self._hoshino_impl_ = HoshinoImpl(self)
        return self._hoshino_impl_

    def _has_hoshino_talent(self, player) -> bool:
        return self._get_hoshino_impl()._has_hoshino_talent(player)

    def _hoshino_is_terror(self, player) -> bool:
        return self._get_hoshino_impl()._hoshino_is_terror(player)

    def _hoshino_tactical_unlocked(self, player) -> bool:
        return self._get_hoshino_impl()._hoshino_tactical_unlocked(player)

    def _hoshino_get_shield_mode(self, player) -> Optional[str]:
        return self._get_hoshino_impl()._hoshino_get_shield_mode(player)

    def _hoshino_has_ammo(self, player) -> bool:
        return self._get_hoshino_impl()._hoshino_has_ammo(player)

    def _hoshino_get_cost(self, player) -> int:
        return self._get_hoshino_impl()._hoshino_get_cost(player)

    def _hoshino_get_form(self, player) -> str:
        return self._get_hoshino_impl()._hoshino_get_form(player)

    def _hoshino_has_fusion_shield(self, player) -> bool:
        return self._get_hoshino_impl()._hoshino_has_fusion_shield(player)

    def _hoshino_has_fusion_weapon(self, player) -> bool:
        return self._get_hoshino_impl()._hoshino_has_fusion_weapon(player)

    def _hoshino_iron_horus_hp(self, player) -> float:
        return self._get_hoshino_impl()._hoshino_iron_horus_hp(player)

    def _hoshino_find_consumable_for_reload(self, player) -> Optional[str]:
        return self._get_hoshino_impl()._hoshino_find_consumable_for_reload(player)

    def _hoshino_captain_has_police_protection(self, state) -> bool:
        return self._get_hoshino_impl()._hoshino_captain_has_police_protection(state)

    def _hoshino_has_enough_tactical_items(self, player) -> bool:
        return self._get_hoshino_impl()._hoshino_has_enough_tactical_items(player)

    def _hoshino_count_throwables(self, player) -> int:
        return self._get_hoshino_impl()._hoshino_count_throwables(player)

    def _hoshino_find_safe_repair_location(self, player, state) -> Optional[str]:
        return self._get_hoshino_impl()._hoshino_find_safe_repair_location(player, state)

    def _hoshino_find_target(self, player, state) -> Optional[Any]:
        return self._get_hoshino_impl()._hoshino_find_target(player, state)

    def _hoshino_target_same_location(self, player, target) -> bool:
        return self._get_hoshino_impl()._hoshino_target_same_location(player, target)

    def _hoshino_is_engaged_with(self, player, target, state) -> bool:
        return self._get_hoshino_impl()._hoshino_is_engaged_with(player, target, state)

    def _hoshino_is_in_front(self, player, target) -> bool:
        return self._get_hoshino_impl()._hoshino_is_in_front(player, target)

    def _hoshino_pick_best_item(self, player, state, loc) -> Optional[dict]:
        return self._get_hoshino_impl()._hoshino_pick_best_item(player, state, loc)

    def _hoshino_best_item_destination(self, player, state) -> Optional[str]:
        return self._get_hoshino_impl()._hoshino_best_item_destination(player, state)

    def _hoshino_prefer_deploy_shield(self, player) -> bool:
        return self._get_hoshino_impl()._hoshino_prefer_deploy_shield(player)

    def _hoshino_has_missing_halo(self, player) -> bool:
        return self._get_hoshino_impl()._hoshino_has_missing_halo(player)

    def _hoshino_target_is_hard_to_kill(self, target) -> bool:
        return self._get_hoshino_impl()._hoshino_target_is_hard_to_kill(target)

    def _hoshino_target_is_police_protected(self, target) -> bool:
        return self._get_hoshino_impl()._hoshino_target_is_police_protected(target)

    def _hoshino_pick_throw_item(self, player, target) -> Optional[str]:
        return self._get_hoshino_impl()._hoshino_pick_throw_item(player, target)

    def _hoshino_should_use_epo(self, player, cost, used_cost) -> bool:
        return self._get_hoshino_impl()._hoshino_should_use_epo(player, cost, used_cost)

    def _hoshino_should_use_chocolate(self, player) -> bool:
        return self._get_hoshino_impl()._hoshino_should_use_chocolate(player)

    def _hoshino_has_enough_ammo_for_burst(self, player) -> bool:
        return self._get_hoshino_impl()._hoshino_has_enough_ammo_for_burst(player)

    def _hoshino_can_effectively_shoot(self, player, target) -> bool:
        return self._get_hoshino_impl()._hoshino_can_effectively_shoot(player, target)

    def _hoshino_find_finishable_target(self, player, state) -> Optional[Any]:
        return self._get_hoshino_impl()._hoshino_find_finishable_target(player, state)

    def _hoshino_should_use_adrenaline(self, player, target) -> bool:
        return self._get_hoshino_impl()._hoshino_should_use_adrenaline(player, target)

    def _hoshino_build_finish_and_switch_macro(self, player, state, finish_target, switch_target) -> List[str]:
        return self._get_hoshino_impl()._hoshino_build_finish_and_switch_macro(player, state, finish_target, switch_target)

    def _hoshino_get_target_outer_armor_attrs(self, target) -> list:
        return self._get_hoshino_impl()._hoshino_get_target_outer_armor_attrs(target)

    def _hoshino_compute_optimal_ammo_order(self, player, target) -> list:
        return self._get_hoshino_impl()._hoshino_compute_optimal_ammo_order(player, target)

    def _hoshino_has_effective_ammo_for_target(self, player, target) -> bool:
        return self._get_hoshino_impl()._hoshino_has_effective_ammo_for_target(player, target)

    def _hoshino_needed_ammo_attr_for_target(self, target) -> Optional[str]:
        return self._get_hoshino_impl()._hoshino_needed_ammo_attr_for_target(target)

    def _hoshino_grab_while_here(self, player, state, available_actions) -> List[str]:
        return self._get_hoshino_impl()._hoshino_grab_while_here(player, state, available_actions)

    def _hoshino_build_macro(self, player, state, target) -> List[str]:
        return self._get_hoshino_impl()._hoshino_build_macro(player, state, target)

    def _hoshino_build_anti_captain_approach_macro(self, player, state, captain) -> List[str]:
        return self._get_hoshino_impl()._hoshino_build_anti_captain_approach_macro(player, state, captain)

    def _hoshino_build_anti_captain_unshielded_macro(self, player, state, captain) -> List[str]:
        return self._get_hoshino_impl()._hoshino_build_anti_captain_unshielded_macro(player, state, captain)

    def _hoshino_build_fullfire_macro(self, player, state, target) -> List[str]:
        return self._get_hoshino_impl()._hoshino_build_fullfire_macro(player, state, target)

    def _hoshino_needs_reorder(self, player, target) -> bool:
        return self._get_hoshino_impl()._hoshino_needs_reorder(player, target)

    def _hoshino_get_tactical_command(self, player, state, available_actions) -> str:
        return self._get_hoshino_impl()._hoshino_get_tactical_command(player, state, available_actions)

    def _hoshino_terror_command(self, player, state, available_actions) -> List[str]:
        return self._get_hoshino_impl()._hoshino_terror_command(player, state, available_actions)
