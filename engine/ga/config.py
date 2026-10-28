"""N0 参数装配：严格结构、来源/单位、跨字段约束与不可变快照。

数值唯一来自 data/balance.json 的 ga 块；本模块只定义参数合同，不提供默认数值。
load_ga_config() 由未来 GA 启动入口显式调用，导入本模块不改变旧运行规则。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any

from engine import balance
from engine.balance import BalanceConfigError


class GaConfigError(BalanceConfigError):
    """GA 参数不符合合同，path 指向出错叶子或关联字段。"""


@dataclass(frozen=True)
class ParameterSpec:
    """单个参数的类型、数值范围、来源、单位与运算取整约定。"""

    kind: type
    source_section: str
    unit: str
    rounding: str = "exact"
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Any, ...] = ()


@dataclass(frozen=True)
class SequenceSpec:
    """固定拓扑的数值序列；序列长度属于合同，不代表可回退的默认值。"""

    item: ParameterSpec
    length: int


def _ints(section: str, unit: str, names: str, *, minimum: int = 0) -> dict[str, Any]:
    """以同一来源/量纲声明整数参数，不携带运行数值。"""
    return {
        name: ParameterSpec(int, section, unit, minimum=minimum)
        for name in names.split()
    }


def _positive(section: str, unit: str, names: str) -> dict[str, Any]:
    """声明不得为零的费用、容量或除数。"""
    return _ints(section, unit, names, minimum=1)


def _ratios(section: str, names: str, rounding: str = "exact") -> dict[str, Any]:
    """概率与比例使用0～1，不接受百分数15误填成15.0。"""
    return {
        name: ParameterSpec(float, section, "ratio", rounding, 0, 1)
        for name in names.split()
    }


def _sequence(section: str, unit: str, length: int) -> SequenceSpec:
    """声明非负整数序列及其来源。"""
    return SequenceSpec(ParameterSpec(int, section, unit, minimum=0), length)


_WEAPON_SCHEMA = {
    "fist": _positive("17.2", "hp", "damage"),
    "knife": {
        **_positive("17.2", "hp", "damage sharpen_bonus"),
        **_ints("17.2", "credits", "price"),
        **_positive("17.2", "uses", "sharpen_uses"),
    },
    "bow": {
        **_positive("17.2", "hp", "damage"),
        **_positive("17.2", "arrows", "ammo_per_attack refill_amount"),
        **_ints("17.2", "credits", "refill_price"),
    },
    "magic_barrage": {
        **_positive("17.2", "hp", "damage"),
        **_positive("17.2", "turns", "learn_turns"),
        **_ints("17.2", "credits", "price"),
    },
    "earthquake": {
        **_positive("17.2", "hp", "damage"),
        **_positive("17.2", "turns", "learn_turns"),
        **_ints("17.2", "credits", "price"),
    },
    "electromagnetic_rifle": {
        **_positive("17.2", "hp", "damage"),
        **_ints("17.2", "credits", "price"),
        **_ints("17.2", "bullets", "initial_ammo"),
        **_positive("17.2", "bullets", "ammo_cap ammo_per_attack"),
    },
    "gauss_rifle": {
        **_positive("17.2", "hp", "damage charged_damage"),
        **_positive("17.2", "turns", "charge_turns"),
        **_ints("17.2", "credits", "price"),
        **_ints("17.2", "bullets", "initial_ammo"),
        **_positive("17.2", "bullets", "ammo_cap ammo_per_attack"),
    },
    "gun_ammo": {
        **_ints("17.2", "credits", "refill_price"),
        **_positive("17.2", "bullets", "refill_amount"),
    },
    "hook": {
        **_positive("17.2", "hp", "damage"),
        **_ints("17.2", "credits", "price"),
        **_positive("17.2", "standard_slots", "cooldown_standard_slots"),
    },
}

_TALENT_SCHEMA: dict[str, Any] = {
    "T1": _positive("17.5", "multiplier", "core_multiplier wear_price"),
    "T2": {
        **_positive("11.2;17.5;DEC-051", "rounds", "chapter_performance_rounds"),
        **_positive("17.5", "hits", "armor_hits_per_repair"),
        **_positive("17.5", "durability", "repair_amount"),
        **_positive("17.5", "sp", "shadow_step_sp_cost"),
        **_positive("17.5", "uses", "shadow_step_uses"),
    },
    "T3": {
        **_positive("17.5", "durability", "barrier shockwave_wear"),
        **_positive("17.5", "future_r4_ticks", "barrier_ticks"),
        **_positive("17.5", "hp", "starfall_damage"),
        **_ratios("17.5", "defense_coefficient", "floor_defense"),
    },
    "T4": {
        **_positive("17.5", "hp", "pierce_damage"),
        **_ratios("17.5", "defense_coefficient", "floor_defense"),
        **_positive("17.5", "future_r4_ticks", "weapon_disable_ticks stealth_ticks"),
        **_positive("17.5", "targets", "public_target_cap"),
    },
    "T6": _ints("17.5", "credits", "hotline_price"),
    "T7": {
        **_ratios("17.5", "revive_hp_ratio", "ceil_hp"),
        **_ratios("11.6;17.5;DEC-053", "public_revive_hp_ratio", "ceil_hp"),
        **_ints("17.5", "sp", "revive_sp"),
        **_positive("17.5", "hp", "prayer_heal"),
        **_positive("17.5", "uses", "insurance_uses poem_insurance_uses"),
    },
    "G0": {
        **_ints("17.6", "bullets", "initial_ammo"),
        **_positive("17.6", "bullets", "ammo_cap point_ammo auto_ammo"),
        **_positive("17.6", "bullets_per_arrow", "arrow_conversion"),
        **_positive("17.6", "hp", "point_damage auto_damage splash_damage drone_hp "
                    "drone_attack_bonus crossfire_damage breath_success_hp"),
        **_ints("17.6", "hp", "glasses_heal"),
        **_positive("17.6", "durability", "auto_attached_wear shield_barrier synergy_barrier"),
        **_positive("17.6", "count", "relic_slots initial_relics glasses_heals_per_round "
                    "breath_uses"),
        **_positive("17.6", "future_r4_ticks", "drone_ticks breath_ticks"),
        **_positive("17.6", "sp", "drone_sp_cost crossfire_sp_cost"),
        **_ratios("17.6", "drone_hp_ratio crossfire_hp_ratio", "ceil_hp_cost_then_discount"),
        **_ratios("17.6", "breath_lifesteal_ratio", "floor_root_hp_total"),
    },
    "G1": {
        **_ints("17.7", "hp_modifier", "unarmored_attack", minimum=-100),
        **_ints("17.7", "hp", "unarmored_incoming sam_attack full_attack propagation_attack "
                "armored_reduction full_heal break_bonus"),
        **_ratios("17.7", "unarmored_hit_penalty"),
        **_ints("17.7", "credits", "interact_discount"),
        **_positive("17.7", "credits", "minimum_interact_price"),
        **_positive("17.7", "future_r4_ticks", "full_ticks propagation_ticks"),
        **_positive("17.7", "count", "followups_per_round entropy_settlements_per_tick "
                    "entropy_ardent_cost entropy_armor_cost overload_cap overload_gain "
                    "ardent_cap ardent_hit_gain ardent_kill_gain propagation_uses"),
        **_ints("17.7", "entropy", "entropy_initial entropy_unarmored_gain entropy_sam_gain "
                "entropy_full_gain entropy_rest_reduction"),
        **_positive("17.7", "entropy", "entropy_cap entropy_threshold entropy_reset_amount"),
        **_positive("17.7", "hp", "entropy_hp_loss supernova_sam_damage supernova_full_damage "
                    "supernova_propagation_damage ardent_absorption propagation_hp"),
        **_ratios("17.7", "supernova_defense_coefficient", "floor_defense"),
        **_positive("17.7", "burn_stacks", "supernova_sam_burn supernova_full_burn "
                    "supernova_propagation_burn"),
        **_ints("17.7", "ardent", "ardent_initial"),
        **_ints("17.7", "initiative", "propagation_initiative"),
        **_ratios("17.7", "unarmored_apocalypse_ratio armored_apocalypse_ratio", "ceil_hp"),
    },
    "G2": {
        **_ints("17.8", "credits", "light_work_bonus"),
        **_ints("17.8", "hp", "shadow_attack_bonus a_heal a_enhanced_heal b_vulnerability "
                "b_enhanced_vulnerability rain_attack_bonus rain_damage"),
        **_ints("17.8", "loneliness", "loneliness_initial loneliness_min loneliness_cap "
                "white_key_max_loneliness a_loneliness_reduction a_enhanced_loneliness_reduction "
                "b_loneliness_gain b_enhanced_loneliness_gain"),
        **_positive("17.8", "durability", "a_barrier a_enhanced_barrier"),
        **_positive("17.8", "future_r4_ticks", "a_barrier_ticks"),
        **_positive("17.8", "keys", "performance_keys enhanced_performance_keys"),
        **_positive("17.8", "hp", "lethal_fusion_hp reactor_hp_loss"),
        **_positive("17.8", "uses", "fusion_uses"),
        **_ratios("17.8", "reactor_halving_ratio", "ceil_hp_or_durability"),
    },
    "G3": {
        **_ints("17.9", "magic", "magic_initial magic_recover_r0 public_temp_magic "
                "poem_temp_magic_cap"),
        **_positive("17.9", "magic", "magic_cap temp_magic_cap spiral_cost "
                    "enhanced_spiral_cost dual_attack_cost dual_guard_cost rho_cost copy_cost "
                    "upkeep_cost gale_threshold collapse_min_magic"),
        **_positive("17.9", "hp", "spiral_damage enhanced_spiral_damage dual_attack_bonus "
                    "dual_guard_reduction instant_dual_bonus collapse_base_damage "
                    "collapse_per_style"),
        **_positive("17.9", "durability", "instant_dual_wear rho_durability "
                    "anchor_durability break_action_power"),
        **_positive("17.9", "future_r4_ticks", "rho_ticks max_barrier_ticks"),
        **_positive("17.9", "count", "array_blocks_per_round gale_per_target_barrier "
                    "collapse_style_cap"),
        **_positive("17.9", "sp", "gale_sp_loss"),
        **_positive("17.9", "multiplier", "collapse_wear_price"),
        **_ratios("17.9", "spiral_hit_bonus array_hit_bonus"),
        **_ratios("17.9", "enhanced_spiral_defense_coefficient collapse_defense_coefficient",
                  "floor_defense"),
        **_ratios("17.9", "outside_copy_ratio inside_copy_ratio", "ceil_output"),
        "chain_costs": _sequence("17.9", "magic", 3),
        "poem_chain_costs": _sequence("17.9", "magic", 4),
    },
    "G4": {
        **_ints("17.10", "embers", "ember_initial hostile_gain hostile_gain_cap_per_round "
                "slash_ember_gain exit_ember_gain"),
        **_positive("17.10", "embers", "ember_cap active_threshold lethal_threshold"),
        **_positive("17.10", "hp", "slash_bonus savior_attack_bonus lethal_body_hp exit_body_hp"),
        **_positive("17.10", "ruin", "attack_ruin_gain full_initial_ruin partial_ruin_floor "
                    "ruin_cap"),
        **_positive("17.10", "durability", "splash_wear"),
        **_positive("17.10", "multiplier", "ember_hp_multiplier counter_pool_per_ruin "
                    "judgment_pool_per_segment"),
        **_positive("17.10", "divisor_floor", "partial_ruin_divisor"),
        **_positive("17.10", "segments", "challenge_segment_reduction"),
        **_positive("17.10", "future_r4_ticks", "savior_ticks"),
    },
    "G5": {
        **_ints("17.11", "memory", "memory_initial highlight_gain_per_root write_gain "
                "story_memory_gain"),
        **_positive("17.11", "memory", "memory_cap stage2_memory ascension_memory"),
        **_positive("17.11", "sp", "stage2_sp_cost ascension_sp_cost story_sp_cost"),
        **_ints("17.11", "sp", "story_target_sp"),
        **_positive("17.11", "divisor_floor", "stat_memory_divisor"),
        **_positive("17.11", "hp", "stat_bonus_cap story_heal arrow_damage "
                    "reversal_first_hp_loss reversal_second_damage reversal_later_base_damage "
                    "reversal_later_step"),
        **_positive("17.11", "segments", "arrow_base_segments arrow_growth arrow_segment_cap "
                    "arrows_per_target unattributed_arrow_start"),
        **_positive("17.11", "divisor_ceil", "arrow_player_divisor"),
        **_positive("17.11", "count", "reversal_first_barrier_breaks reversal_later_start "
                    "poetry_chapter_rounds"),
        **_positive("17.11", "durability", "reversal_first_wear"),
    },
    "G6": {
        **_ints("17.11", "laugh", "laugh_initial self_forfeit_laugh controlled_skip_laugh"),
        **_positive("17.11", "laugh", "laugh_cap laugh_per_root laugh_per_round "
                    "counter_laugh_cost"),
        **_positive("17.11", "credits", "credit_per_laugh card_theft_cap gift_credits "
                    "gift_theft_cap"),
        **_positive("17.11", "hp", "counter_damage poem_counter_damage gift_heal gift_damage"),
        **_positive("17.11", "count", "gift_faces gift_equipment_count gift_broken_items "
                    "gifts_per_recipient"),
        **_ratios("17.11", "gift_face_probability"),
        **_positive("17.11", "future_r4_ticks", "gift_artifact_disable_ticks gift_fallback_ticks"),
    },
    "G7": {
        **_positive("17.12", "durability", "horus_durability component_repair"),
        **_ints("17.12", "defense", "horus_normal horus_tech horus_magic hold_defense_bonus"),
        **_positive("17.12", "count", "repair_components halo_layers halo_per_layer revive_uses "
                    "pellets free_shot_every medicine_hold_cap free_supply_uses "
                    "epo_per_macro adrenaline_uses adrenaline_rounds change_form_uses "
                    "archer_ex_pellets"),
        **_ints("17.12", "durability", "automatic_repair halo_idle_recovery "
                "halo_combat_recovery free_shot_wear nap_repair shielder_ex_repair_base"),
        **_ints("17.12", "layers", "halo_initial_layers nap_halo_layers chocolate_layers"),
        **_positive("17.12", "hp", "revive_hp shot_pool free_shot_pool nap_heal "
                    "nap_low_hp_heal dash_damage swimsuit_ex_base archer_ex_pool "
                    "archer_ex_point_base shielder_ex_damage_base terror_hp_floor "
                    "terror_hp_cap terror_damage terror_attack_hp_cost terror_move_hp_cost"),
        **_positive("17.12", "cost", "cost_initial cost_cap adrenaline_cost_cap"),
        **_ints("17.12", "cost", "macro_fatigue epo_cost_bonus shield_upkeep swimsuit_upkeep "
                "free_shot_cost"),
        "macro_costs": {
            **_positive("17.12", "cost", "deploy_shield shoot hold_shield throw dash search lock"),
            **_ints("17.12", "cost", "reload medicine cancel turn eject"),
        },
        **_positive("17.12", "bullets", "magazine_cap ammo_per_shot reserve_ammo_cap "
                    "ammo_refill_amount free_supply_ammo"),
        **_ints("17.12", "bullets", "initial_loaded_ammo free_shot_ammo nap_loaded_ammo"),
        **_ints("17.12", "credits", "ammo_refill_price epo_price chocolate_price adrenaline_price"),
        **_positive("17.12", "sp", "nap_sp_cost"),
        **_ratios("17.12", "nap_low_hp_ratio"),
        **_ints("17.12", "initiative", "adrenaline_initiative"),
        **_positive("17.12", "divisor_floor", "swimsuit_ex_ammo_divisor archer_ex_point_divisor "
                    "shielder_ex_repair_divisor shielder_ex_damage_divisor"),
        **_positive("17.12", "divisor_ceil", "archer_ex_splash_divisor "
                    "shielder_ex_barrier_divisor terror_horus_divisor"),
        **_positive("17.12", "bullets", "swimsuit_ex_suppress_ammo"),
        **_positive("17.12", "hp", "archer_ex_vulnerability"),
        **_positive("17.12", "future_r4_ticks", "archer_ex_ticks"),
        **_positive("17.12", "multiplier", "archer_ex_wear_price terror_halo_multiplier"),
        **_ratios("17.12", "archer_ex_defense_coefficient", "floor_defense"),
        **_ints("17.12", "color", "color_initial color_exit_gain color_kill_gain"),
        **_positive("17.12", "color", "color_voluntary_threshold color_forced_threshold"),
        **_positive("17.12", "hp_per_armor", "terror_hp_per_armor"),
        "shot_distribution": _sequence("17.12", "hp", 3),
        "free_shot_distribution": _sequence("17.12", "hp", 3),
        "archer_ex_distribution": _sequence("17.12", "hp", 4),
    },
}

_SCHEMA: dict[str, Any] = {
    "_meta": {
        "ruleset": ParameterSpec(str, "17", "identity", choices=("v2.0-ga",)),
        "schema_version": ParameterSpec(int, "17", "version", choices=(1,)),
        "revision": ParameterSpec(str, "17", "identity", choices=("N0",)),
        "source": ParameterSpec(str, "17", "source_reference", choices=(
            "docs/v2.0-ga/manual_draft_v0.1.md#17",)),
        "source_sha256": ParameterSpec(str, "17", "sha256"),
    },
    "start": {
        **_positive("17.1", "hp", "max_hp hp"),
        **_ints("17.1", "credits", "credits"),
        **_ints("17.1", "sp", "sp"),
        **_ints("17.1", "pp", "pp"),
        **_positive("17.1", "count", "bows initiative_sides min_players max_players"),
        **_ints("17.1", "arrows", "arrows"),
        **_positive("17.1", "arrows", "arrow_cap"),
    },
    "action": {
        **_positive("17.1;3.2-3.4;6.2", "sp", "sp_cap instant_sp_cost public_sp_cost"),
        **_positive("17.1;3.2-3.4;6.2", "count", "t0_per_turn t1_per_turn "
                    "attention_per_round public_per_round extra_turns_per_round "
                    "step_followups_per_round"),
    },
    "accuracy": {
        **_ratios("17.1", "base move_evasion stealth_evasion unlocked_cross_location_penalty "
                  "lock_bonus minimum maximum"),
        **_ratios("17.1", "lock_defense_coefficient", "floor_defense"),
    },
    "combat": {
        **_ratios("17.1", "min_damage_ratio", "ceil_output"),
        **_positive("17.3", "durability_per_mitigation", "normal_wear_price counter_wear_price "
                    "mysterious_wear_price"),
        **_positive("17.1", "hp", "minimum_paid_hp_cost paid_hp_survival_floor"),
    },
    "weapons": _WEAPON_SCHEMA,
    "modules": {
        **_positive("17.2", "count", "slots ordinary_stock_min infinite_stock_min"),
        **_positive("17.2", "divisor_ceil", "ordinary_stock_player_divisor"),
        **_positive("17.2", "divisor_floor", "infinite_stock_player_divisor"),
        "power": {**_ints("17.2", "credits", "price"),
                  **_positive("17.2", "hp", "single_bonus double_bonus")},
        "fire": {**_ints("17.2", "credits", "price"),
                 **_positive("17.2", "burn_stacks", "single_burn double_burn")},
        "magic": {**_ints("17.2", "credits", "price"),
                  **_ratios("17.2", "single_hit_bonus double_hit_bonus")},
        "pierce": {**_ints("17.2", "credits", "price"),
                   **_ratios("17.2", "single_coefficient double_coefficient", "floor_defense")},
        "impact": {**_ints("17.2", "credits", "price"),
                   **_positive("17.2", "count", "knockbacks"),
                   **_ints("17.2", "initiative", "double_initiative_penalty", minimum=-100)},
        "infinite": {**_ints("17.2", "credits", "price"),
                     **_positive("17.2", "count", "occupied_slots"),
                     **_positive("17.2", "hp", "damage"),
                     **_ints("17.2", "arrows", "ammo_per_attack")},
    },
    "armor": {
        name: {**_ints("17.3", "defense", "normal tech magic"),
               **_positive("17.3", "durability", "durability"),
               **_ints("17.3", "credits", "price")}
        for name in ("shield", "ceramic", "magic_shield", "at_field")
    },
    "economy": {
        **_ints("17.3", "credits", "work_income resale_price repair_price whetstone_price "
                "visibility_item_price visibility_spell_price black_market_pass_price"),
        **_positive("17.3", "durability", "repair_amount"),
        **_positive("17.3", "count", "visibility_learn_turns pass_uses"),
    },
    "hospital": {
        **_ints("17.3", "credits", "heal_price syringe_price surgery_min_price"),
        **_positive("17.3", "hp", "heal_amount syringe_heal life_max_hp_bonus "
                    "life_current_hp_bonus furnace_hp_cost"),
        **_ints("17.3", "hp", "life_idle_heal furnace_g0_hp_discount"),
        **_positive("17.3", "uses", "heal_uses_per_game syringe_hold_cap syringe_purchase_cap "
                    "surgery_uses furnace_uses"),
        **_ints("17.3", "initiative", "overclock_initiative_bonus"),
        **_ints("17.3", "entropy", "furnace_g1_entropy_discount"),
        **_ints("17.3", "magic", "furnace_g3_magic_bonus"),
        **_positive("17.3", "sp", "furnace_sp_gain"),
    },
    "status": {
        **_positive("17.3", "hp", "burn_damage_per_stack severe_hp_threshold vulnerability"),
        **_positive("17.3", "burn_stacks", "burn_cap"),
        **_positive("17.3", "future_r4_ticks", "burn_ticks petrify_ticks "
                    "unconsumed_control_expiry_ticks modifier_ticks temporary_barrier_ticks"),
        **_ints("17.3", "initiative", "severe_initiative_penalty", minimum=-100),
        **_ints("17.3", "hp_modifier", "weakness", minimum=-100),
        **_positive("17.3", "count", "petrify_release_hits petrify_escape_attempts control_slots"),
        **_positive("17.3", "sp", "petrify_escape_sp_cost"),
        **_ratios("17.3", "petrify_escape_probability"),
    },
    "scoring": {
        **_ints("17.4", "score", "kill_weight armor_break_weight chapter_weight pp_weight crown"),
        **_positive("17.4", "divisor_floor", "damage_divisor"),
        **_positive("17.4", "count", "crown_min_kills crown_min_chapters"),
    },
    "pp": {
        **_ints("17.4", "pp", "kill_gain chapter_gain armor_break_gain natural_gain decay "
                "initiative_reroll_cost"),
        **_positive("17.4", "count", "armor_break_rewards_per_round initiative_rerolls_per_round"),
    },
    "afterlife": {
        **_ints("17.4", "pp", "bet_kill_pool bet_break_pool bet_crown_pool microglow_cost"),
        **_positive("17.4", "count", "bet_break_pools_per_actor_round microglows_per_dead_round "
                    "buffs_per_live_round encore_uses"),
        **_positive("17.4", "durability", "blessing_barrier"),
        **_positive("17.4", "hp", "blessing_heal gaze_next_hit_bonus"),
        **_ints("17.4", "initiative", "revelation_initiative"),
        **_positive("17.4", "sp", "revelation_sp"),
    },
    "police": {
        **_positive("17.4", "count", "roster_cap attacks_per_round attack_upgrade_cap "
                    "wall_upgrade_cap reinforce_count innocent_command_roster_loss"),
        **_positive("17.4", "hp", "officer_hp volley_base gauss_volley_base volley_per_officer "
                    "thrust_base thrust_per_officer"),
        **_positive("17.4", "durability", "wall_per_officer wall_upgrade_per_officer "
                    "wall_repair_amount"),
        **_ints("17.4", "credits", "wall_repair_price"),
    },
    "clock": {
        **_positive("17.4", "world_tick", "initial_tick day_start dusk_start apocalypse_start"),
        **_positive("17.4", "world_ticks_per_round", "normal_step reactor_step"),
        **_positive("17.4", "hp_per_world_tick", "apocalypse_damage_per_tick"),
        **_positive("17.4", "multiplier", "reactor_damage_multiplier"),
        **_positive("17.4", "global_rounds", "target_rounds max_rounds"),
    },
    "talents": _TALENT_SCHEMA,
    "consumables": {
        **_ints("17.13", "credits", "price"),
        **_positive("17.13", "count", "hold_cap per_throw molotov_burn"),
        **_ints("17.13", "hp", "fragment_damage shock_damage flash_damage molotov_damage"),
        **_ratios("17.13", "flash_hit_penalty smoke_hit_penalty"),
        **_positive("17.13", "future_r4_ticks", "flash_ticks smoke_ticks"),
    },
    "poems": {
        **_positive("17.13", "sp", "sp_cost shore_revive_sp ranger_sp_refund"),
        **_positive("17.13", "memory", "memory_per_missing_sp"),
        **_positive("17.13", "future_r4_ticks", "love_ticks firefly_ticks burden_burn_ticks "
                    "joy_ticks"),
        **_ints("17.13", "credits", "tomorrow_work_bonus tomorrow_discount tomorrow_min_price"),
        **_positive("17.13", "count", "spotlight_white_keys "
                    "eternity_chain_extra burden_self_burn_gain burden_burn_cap "
                    "ranger_refunds_per_mark nightwalk_extra_turns nightwalk_shadow_step_reset "
                    "nightwalk_free_steps stars_meteors yin_yang_uses law_roster shore_rearm_uses"),
        **_positive("17.13", "hp", "tomorrow_breath_heal firefly_rescue_hp spotlight_reduction "
                    "spotlight_attack_bonus burden_fire_damage joy_missed_counter_damage "
                    "stars_damage law_officer_hp shore_knife_damage"),
        **_positive("17.13", "magic", "eternity_magic"),
        **_ints("17.13", "magic", "eternity_first_cost"),
        **_positive("17.13", "resource", "burden_resource_gain joy_laugh_set"),
        **_positive("17.13", "hp", "watchman_hp_repair_cost_cap"),
        **_positive("17.13", "durability", "watchman_repaired_durability shore_shield_durability"),
    },
    "encore": {
        **_positive("17.13", "count", "ranger_chases yin_yang_rerolls tomorrow_waivers"),
        **_positive("17.13", "hp", "stars_damage firefly_reduction spotlight_attack_bonus "
                    "spotlight_proxy_heal"),
        **_positive("17.13", "resource", "eternity_discount"),
    },
}


def _validate(value: Any, spec: Any, path: str) -> None:
    """递归检查完整结构；不转换错误类型，也不忽略未知键。"""
    if isinstance(spec, dict):
        if not isinstance(value, Mapping):
            raise GaConfigError(path, "expected an object")
        for key in spec:
            if key not in value:
                raise GaConfigError(f"{path}.{key}", "missing required key")
        for key in value:
            if key not in spec:
                raise GaConfigError(f"{path}.{key}", "unknown parameter")
        for key, child in spec.items():
            _validate(value[key], child, f"{path}.{key}")
        return
    if isinstance(spec, SequenceSpec):
        if not isinstance(value, (list, tuple)) or len(value) != spec.length:
            raise GaConfigError(path, f"expected a sequence of length {spec.length}")
        for index, item in enumerate(value):
            _validate(item, spec.item, f"{path}[{index}]")
        return
    if spec.kind is int:
        valid = type(value) is int
    elif spec.kind is float:
        valid = type(value) is int or (type(value) is float and math.isfinite(value))
    else:
        valid = type(value) is spec.kind
    if not valid:
        raise GaConfigError(path, f"expected {spec.kind.__name__}, got {type(value).__name__}")
    if spec.choices and value not in spec.choices:
        raise GaConfigError(path, f"expected one of {spec.choices}")
    if spec.minimum is not None and value < spec.minimum:
        raise GaConfigError(path, f"must be >= {spec.minimum} {spec.unit}")
    if spec.maximum is not None and value > spec.maximum:
        raise GaConfigError(path, f"must be <= {spec.maximum} {spec.unit}")
    if spec.unit == "sha256" and not re.fullmatch(r"[0-9a-f]{64}", value):
        raise GaConfigError(path, "expected a lowercase SHA-256 digest")


def _freeze(value: Any, spec: Any) -> Any:
    """复制为递归只读对象，避免修改共享balance缓存或已校验快照。"""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item, spec[key]) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, spec.item) for item in value)
    return float(value) if spec.kind is float else value


def _thaw(value: Any) -> Any:
    """导出独立JSON兼容副本；调用方修改副本不会影响配置。"""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _read(data: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    """读取完整路径，不设置任何默认值。"""
    value: Any = data
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            raise GaConfigError("ga." + ".".join(keys), "missing required key")
        value = value[key]
    return value


def _cross_validate(data: Mapping[str, Any]) -> None:
    """检查单字段合法但组合不可执行的配置。"""
    def at(path: str) -> Any:
        return _read(data, tuple(path.split(".")))

    def le(left: str, right: str) -> None:
        if at(left) > at(right):
            raise GaConfigError("ga." + left, f"must not exceed ga.{right}")

    def equal(left: str, right: str) -> None:
        if at(left) != at(right):
            raise GaConfigError("ga." + left, f"must equal ga.{right}")

    def constant(path: str, expected: int) -> None:
        if at(path) != expected:
            raise GaConfigError("ga." + path, f"GA rule requires {expected}")

    for path in ("action.t0_per_turn", "action.t1_per_turn", "action.attention_per_round",
                 "action.public_per_round", "action.extra_turns_per_round",
                 "action.step_followups_per_round"):
        constant(path, 1)
    constant("action.sp_cap", 2)
    constant("action.instant_sp_cost", 1)
    constant("start.min_players", 2)
    constant("start.max_players", 6)
    constant("talents.G6.gift_faces", 6)
    equal("start.hp", "start.max_hp")
    equal("action.public_sp_cost", "action.sp_cap")
    for left, right in (
        ("start.hp", "start.max_hp"), ("start.sp", "action.sp_cap"),
        ("start.arrows", "start.arrow_cap"), ("start.min_players", "start.max_players"),
        ("accuracy.minimum", "accuracy.base"), ("accuracy.base", "accuracy.maximum"),
        ("modules.infinite.occupied_slots", "modules.slots"),
        ("modules.pierce.double_coefficient", "modules.pierce.single_coefficient"),
        ("hospital.syringe_hold_cap", "hospital.syringe_purchase_cap"),
        ("status.severe_hp_threshold", "start.max_hp"),
        ("status.petrify_escape_sp_cost", "action.sp_cap"),
        ("police.reinforce_count", "police.roster_cap"),
        ("clock.target_rounds", "clock.max_rounds"),
        ("talents.G0.initial_ammo", "talents.G0.ammo_cap"),
        ("talents.G0.relic_slots", "talents.G0.initial_relics"),
        ("talents.G0.auto_ammo", "talents.G0.ammo_cap"),
        ("talents.G1.entropy_initial", "talents.G1.entropy_cap"),
        ("talents.G1.entropy_threshold", "talents.G1.entropy_cap"),
        ("talents.G1.entropy_reset_amount", "talents.G1.entropy_threshold"),
        ("talents.G1.ardent_initial", "talents.G1.ardent_cap"),
        ("talents.G2.loneliness_min", "talents.G2.loneliness_initial"),
        ("talents.G2.loneliness_initial", "talents.G2.loneliness_cap"),
        ("talents.G2.white_key_max_loneliness", "talents.G2.loneliness_cap"),
        ("talents.G3.magic_initial", "talents.G3.magic_cap"),
        ("talents.G3.public_temp_magic", "talents.G3.temp_magic_cap"),
        ("talents.G3.poem_temp_magic_cap", "talents.G3.temp_magic_cap"),
        ("talents.G4.ember_initial", "talents.G4.ember_cap"),
        ("talents.G4.lethal_threshold", "talents.G4.active_threshold"),
        ("talents.G4.active_threshold", "talents.G4.ember_cap"),
        ("talents.G4.partial_ruin_floor", "talents.G4.ruin_cap"),
        ("talents.G4.full_initial_ruin", "talents.G4.ruin_cap"),
        ("talents.G5.memory_initial", "talents.G5.memory_cap"),
        ("talents.G5.stage2_memory", "talents.G5.ascension_memory"),
        ("talents.G5.ascension_memory", "talents.G5.memory_cap"),
        ("talents.G5.arrows_per_target", "talents.G5.arrow_segment_cap"),
        ("talents.G6.laugh_initial", "talents.G6.laugh_cap"),
        ("talents.G6.counter_laugh_cost", "talents.G6.laugh_cap"),
        ("talents.G7.halo_initial_layers", "talents.G7.halo_layers"),
        ("talents.G7.cost_initial", "talents.G7.cost_cap"),
        ("talents.G7.cost_cap", "talents.G7.adrenaline_cost_cap"),
        ("talents.G7.macro_fatigue", "talents.G7.cost_cap"),
        ("talents.G7.initial_loaded_ammo", "talents.G7.magazine_cap"),
        ("talents.G7.nap_loaded_ammo", "talents.G7.magazine_cap"),
        ("talents.G7.free_supply_ammo", "talents.G7.reserve_ammo_cap"),
        ("talents.G7.nap_halo_layers", "talents.G7.halo_layers"),
        ("talents.G7.chocolate_layers", "talents.G7.halo_layers"),
        ("talents.G7.swimsuit_ex_suppress_ammo", "talents.G7.magazine_cap"),
        ("talents.G7.color_voluntary_threshold", "talents.G7.color_forced_threshold"),
        ("talents.G7.terror_hp_floor", "talents.G7.terror_hp_cap"),
        ("poems.joy_laugh_set", "talents.G6.laugh_cap"),
        ("poems.watchman_repaired_durability", "talents.G7.horus_durability"),
        ("talents.T2.shadow_step_sp_cost", "action.sp_cap"),
        ("talents.T7.revive_sp", "action.sp_cap"),
        ("afterlife.revelation_sp", "action.sp_cap"),
    ):
        le(left, right)
    for gun in ("electromagnetic_rifle", "gauss_rifle"):
        le(f"weapons.{gun}.initial_ammo", f"weapons.{gun}.ammo_cap")
        le(f"weapons.{gun}.ammo_per_attack", f"weapons.{gun}.ammo_cap")
    equal("modules.infinite.occupied_slots", "modules.slots")
    for previous, following in zip(
        ("initial_tick", "day_start", "dusk_start"),
        ("day_start", "dusk_start", "apocalypse_start"),
    ):
        if at(f"clock.{previous}") >= at(f"clock.{following}"):
            raise GaConfigError(f"ga.clock.{following}", f"must be after {previous}")
    for key in ("shot", "free_shot", "archer_ex"):
        distribution = at(f"talents.G7.{key}_distribution")
        pool = at(f"talents.G7.{key}_pool")
        if sum(distribution) != pool:
            raise GaConfigError(f"ga.talents.G7.{key}_distribution", "must sum to its damage pool")
    if len(at("talents.G7.shot_distribution")) != at("talents.G7.pellets"):
        raise GaConfigError("ga.talents.G7.pellets", "must match shot distribution length")
    if len(at("talents.G7.archer_ex_distribution")) != at("talents.G7.archer_ex_pellets"):
        raise GaConfigError("ga.talents.G7.archer_ex_pellets", "must match EX distribution length")
    for key in ("chain_costs", "poem_chain_costs"):
        costs = at(f"talents.G3.{key}")
        if any(a >= b for a, b in zip(costs, costs[1:])):
            raise GaConfigError(f"ga.talents.G3.{key}", "costs must strictly increase")
    if at("talents.G3.chain_costs")[0] != at("talents.G3.spiral_cost"):
        raise GaConfigError("ga.talents.G3.chain_costs[0]", "must equal spiral_cost")
    gift_probability_sum = at("talents.G6.gift_faces") * at("talents.G6.gift_face_probability")
    if not math.isclose(gift_probability_sum, 1, rel_tol=1e-12, abs_tol=1e-12):
        raise GaConfigError(
            "ga.talents.G6.gift_face_probability", "gift probabilities must sum to 1"
        )
    for name, source in (("initial_ammo", "arrows"), ("ammo_cap", "arrow_cap")):
        if at(f"talents.G0.{name}") != at(f"start.{source}") * at("talents.G0.arrow_conversion"):
            raise GaConfigError(f"ga.talents.G0.{name}", "must preserve the arrow conversion ratio")
    if at("talents.T7.poem_insurance_uses") != (
        at("talents.T7.insurance_uses") + at("poems.shore_rearm_uses")
    ):
        raise GaConfigError(
            "ga.talents.T7.poem_insurance_uses", "must include exactly one rearm budget"
        )
    for path in ("combat.min_damage_ratio", "talents.T7.revive_hp_ratio",
                 "talents.T7.public_revive_hp_ratio", "talents.G2.reactor_halving_ratio"):
        if at(path) <= 0:
            raise GaConfigError("ga." + path, "ratio must be greater than 0")
    for name in ("deploy_shield", "shoot", "hold_shield", "throw", "dash", "search", "lock"):
        le(f"talents.G7.macro_costs.{name}", "talents.G7.cost_cap")
    for left, right in (
        ("poems.nightwalk_shadow_step_reset", "talents.T2.shadow_step_uses"),
        ("poems.law_roster", "police.roster_cap"),
        ("poems.law_officer_hp", "police.officer_hp"),
        ("poems.shore_shield_durability", "armor.shield.durability"),
        ("poems.shore_knife_damage", "weapons.knife.damage"),
        ("talents.G1.followups_per_round", "action.step_followups_per_round"),
    ):
        equal(left, right)
    for path in ("talents.G6.self_forfeit_laugh", "talents.G6.controlled_skip_laugh",
                 "talents.G7.automatic_repair", "talents.G7.halo_combat_recovery",
                 "talents.G7.free_shot_cost", "talents.G7.free_shot_ammo",
                 "talents.T3.defense_coefficient", "talents.T4.defense_coefficient",
                 "talents.G1.armored_apocalypse_ratio", "poems.eternity_first_cost",
                 "pp.natural_gain", "pp.decay", "economy.resale_price"):
        constant(path, 0)
    for section, names in (("talents.G0", ("drone_sp_cost", "crossfire_sp_cost")),
                           ("talents.G5", ("stage2_sp_cost", "ascension_sp_cost", "story_sp_cost")),
                           ("talents.G7", ("nap_sp_cost",)),
                           ("poems", ("sp_cost", "shore_revive_sp"))):
        for name in names:
            le(f"{section}.{name}", "action.sp_cap")


def parameter_specs() -> Mapping[str, ParameterSpec]:
    """返回各必填叶子的来源/单位/取整/范围，用于装配审计与文档校验。"""
    entries: dict[str, ParameterSpec] = {}

    def visit(spec: Any, path: str) -> None:
        if isinstance(spec, dict):
            for key, child in spec.items():
                visit(child, f"{path}.{key}")
        elif isinstance(spec, SequenceSpec):
            for index in range(spec.length):
                entries[f"{path}[{index}]"] = spec.item
        else:
            entries[path] = spec

    visit(_SCHEMA, "ga")
    return MappingProxyType(entries)


@dataclass(frozen=True)
class GaConfig:
    """一次加载的不可变参数快照；fingerprint只随规则身份和运行参数变化。"""

    data: Mapping[str, Any]
    fingerprint: str

    def get(self, *keys: str) -> Any:
        """按ga块内路径读取必填值；返回的子对象同样不可变。"""
        if not keys or any(not isinstance(key, str) or not key for key in keys):
            raise ValueError("GaConfig.get expects non-empty string keys")
        return _read(self.data, keys)

    def to_dict(self) -> dict[str, Any]:
        """导出独立副本，供日志/存档序列化，不暴露共享缓存。"""
        return _thaw(self.data)


def validate_ga_config(data: Mapping[str, Any]) -> GaConfig:
    """校验外部参数并返回快照；不从旧配置、源码默认值或手册临时补键。"""
    _validate(data, _SCHEMA, "ga")
    _cross_validate(data)
    frozen = _freeze(data, _SCHEMA)
    payload = _thaw(frozen)
    payload["_meta"] = {key: payload["_meta"][key]
                        for key in ("ruleset", "schema_version", "revision")}
    digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    return GaConfig(frozen, digest)


def load_ga_config(*, refresh: bool = False) -> GaConfig:
    """通过现有balance入口加载GA；refresh显式重读文件，已有快照保持不变。"""
    if refresh:
        balance.reload(strict=True)
    return validate_ga_config(balance.get_required("ga"))
