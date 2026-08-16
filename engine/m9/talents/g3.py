"""M9 G3「神话之外」投影固有结界天赋（profile: m9-rfc，固有结界 RFC v0.2 +
连续投影 RFC v0.1）。

继承 M9TalentStub（v2exp 钩子兼容桩），不继承 v2exp `talents.g3_mythland.Mythland`：
猜拳/时停/内部回合/全天赋封锁/控制免疫等冻结项一律不迁移。

职责分离：
- SP 0/1/2 决定何时可以报名并展开公演（结界展开 = 2 SP 公演）；SP 不支付单项投影；
- 魔力账本支付投影创造/维持/超限与结界持续；普通魔力先支付、临时魔力后支付；
  结界展开把 2 SP 转化为 `public_temp_magic` 临时超额魔力（仅结界内可用，退出清零）。

结界外投影（标准根行动，耗魔力不耗 SP）：
- 螺旋剑（伪）：同一攻击根内创建并立即发射，不占持续通道；
- 双刀·攻势/守势：攻守择态；守势建立防御标记（下一起来袭攻击固定减伤后消失）；
- 七重圆环：普通掩体类防护（COVERED 语义，独立耐久，击破不溢出）；
- 复制武器：白名单复制（见证式样优先，无见证回退白名单），结界外 ×outside_copy_ratio
  half-up 取整；结界外只维持一个持续投影通道，新持续投影替换旧。

无限剑制（2 SP 公演）：
- 快照捕捉同地点其他存活玩家与独立行动 NPC；警察永不捕捉；主目标公开指定；
- 边界：结界内外不能跨边界攻击/治疗/支援/指定；警察案件挂起；
- 结界内沿用全局 ActionGrant，不创建内部轮次/完整额外行动；G3 不免疫控制；
- 螺旋剑连发（ProjectionChain：递增成本、赤原猎风 SP−1+移出公演队列、同根终段崩坏）；
- 剑阵三功能（弹道校正/拦截/崩坏准备）；理想燃烧（式样计数解锁）；幻想崩坏
  （消耗全部魔力，统一攻击主目标，defense_coefficient=0.5，结算后无条件解除）；
- R4 维持费用（建立轮不 tick；不足强制解除不部分支付）；破界（武器攻击 A / 固定
  结构伤害）；全部退出路径幂等清理。

数值一律读 `m9_talents_extended.g3.*`（[待风洞]，DOC-048 已登记首轮值）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from engine.balance import get as bget
from combat import numeric_v2
from engine.m9.g3_chain import ProjectionChain, default_chain_config
from engine.m9.talents.stub import M9TalentStub
from engine.m9.text import m9_text, m9_text_list
from models.equipment import WeaponRange, make_bow, make_weapon

# ── 复制武器白名单（RFC v0.2 §4.4 关闭列举，冻结）──
COPY_WEAPON_CLOSED_LIST: tuple = (
    "拳击", "小刀", "警棍", "魔法弹幕", "远程魔法弹幕",
    "地震", "地动山摇", "电磁步枪", "高斯步枪", "弓",
)

# ── 剑阵三功能（RFC v0.2 §7.1 关闭列举）──
SWORD_ARRAY_FUNCTIONS: Dict[str, str] = {
    "弹道校正": "ballistic",
    "拦截": "intercept",
    "崩坏准备": "collapse_prep",
}

# ── 摧毁地点后的安全回退地点（v1 简化最近安全地点规则）──
SAFE_FALLBACK_LOCATIONS: tuple = ("公园", "商店", "医院", "军事基地", "住宅区")


def half_up(x: float) -> int:
    """half-up 取整（0.5 进位）。"""
    return math.floor(x + 0.5)


def _g3(key: str, default):
    return bget("m9_talents_extended", "g3", key, default=default)


def active_barrier(game_state: Any) -> Optional["Mythland9"]:
    """Return the one active M9 reality marble, if any.

    The talent slot is unique in normal setup, but scanning the stable player
    order also keeps hand-built tests deterministic.

    性能注记：在 GameState（或其 VisibilityProxy 的真实底板上）缓存当前
    active barrier；命中时校验 cached 对象的 `barrier_active` 仍为真，
    破除/退场后自然失效并重扫。同一决策点不再每调用扫一遍全玩家表。
    """
    cache_owner = getattr(game_state, "_real", game_state)
    cached = getattr(cache_owner, "_m9_active_barrier_cache", None)
    if cached is not None and getattr(cached, "barrier_active", False):
        return cached
    result = None
    for player_id in getattr(game_state, "player_order", []):
        player = game_state.get_player(player_id)
        talent = getattr(player, "talent", None) if player is not None else None
        if isinstance(talent, Mythland9) and talent.barrier_active:
            result = talent
            break
    try:
        cache_owner._m9_active_barrier_cache = result
    except Exception:
        pass
    return result


def attack_crosses_active_barrier(game_state: Any, attacker: Any,
                                  target: Any) -> bool:
    """Shared production legality check for ordinary attack entry points."""
    barrier = active_barrier(game_state)
    return bool(barrier and barrier.cross_boundary(attacker, target))


def lock_crosses_active_barrier(game_state: Any, observer: Any,
                                target: Any) -> bool:
    """无限剑制内部只能锁定同在结界内部的单位。"""
    barrier = active_barrier(game_state)
    if barrier is None:
        return False
    return bool(barrier._is_inside(observer) and not barrier._is_inside(target))


class Mythland9(M9TalentStub):
    """M9 G3（m9-rfc 实例化；v2exp 同名字符串引用保持兼容）。"""

    name = "神话之外"

    def __init__(self, player_id: str, game_state: Any) -> None:
        self.player_id = player_id
        self.state = game_state

        # ── 魔力账本（§三）──
        self.magic: int = int(_g3("magic_initial", 6))
        self.magic_cap: int = int(_g3("magic_cap", 8))
        self.magic_recover_r0: int = int(_g3("magic_recover_r0", 1))
        self.public_temp_magic: int = int(_g3("public_temp_magic", 4))
        self.temp_magic: int = 0          # 临时超额魔力（仅结界内可用）
        self._last_recover_round: int = -1
        self._last_upkeep_round: int = -1

        # ── 结界外持续投影通道（唯一）──
        self.outside_kind: Optional[str] = None   # "defense" / "rho_aias" / "copy"
        self.defense_marker: bool = False         # 双刀·守势标记
        self.rho_aias: Optional[Dict[str, Any]] = None   # {"durability", "location"}
        self.copy_weapon: Optional[Dict[str, Any]] = None  # {"name","base_damage",...}

        # ── 结界（无限剑制）──
        self.barrier_active: bool = False
        self.barrier_location: Optional[str] = None
        self.captured: List[str] = []
        self.original_locations: Dict[str, str] = {}
        self.main_target: Optional[str] = None
        self.barrier_anchor_durability: int = 0
        self.barrier_rounds: int = 0
        self.established_round: Optional[int] = None
        self.armament: Optional[str] = None        # 初始配置选定的兵装（v0.3 保留登记）
        self.armament_pool: List[str] = []         # 兵装池（v0.3）：结界内已存在兵装成员
        self.sword_array: Optional[Dict[str, Any]] = None  # {"function","durability",...}
        self.ideal_burn: bool = False
        self.ideal_styles: set = set()
        self._gale_applied: set = set()            # 赤原猎风频率闸（player_id×结界一次）
        self.chain: Optional[ProjectionChain] = None

        # ── 数值（m9_talents_extended.g3.*）──
        self.spiral_cost: int = int(_g3("spiral_cost", 2))
        self.spiral_damage: int = int(_g3("spiral_damage", 5))
        self.spiral_hit_bonus: int = int(_g3("spiral_hit_bonus", 15))
        self.dual_blade_cost: int = int(_g3("dual_blade_cost", 1))
        self.dual_blade_attack_bonus: int = int(_g3("dual_blade_attack_bonus", 2))
        self.dual_blade_reduction: int = int(_g3("dual_blade_reduction", 2))
        self.rho_aias_cost: int = int(_g3("rho_aias_cost", 2))
        self.rho_aias_durability: int = int(_g3("rho_aias_durability", 8))
        self.copy_weapon_cost: int = int(_g3("copy_weapon_cost", 1))
        self.outside_copy_ratio: float = float(_g3("outside_copy_ratio", 0.75))
        self.sword_array_cost: int = int(_g3("sword_array_cost", 2))
        self.sword_array_hit_bonus: int = int(_g3("sword_array_hit_bonus", 15))
        self.sword_array_durability: int = int(_g3("sword_array_durability", 6))
        self.sword_array_collapse_bonus: int = int(
            _g3("sword_array_collapse_bonus", 2))
        self.barrier_base_upkeep: int = int(_g3("barrier_base_upkeep", 1))
        self.barrier_per_unit_upkeep: int = int(_g3("barrier_per_unit_upkeep", 1))
        self.barrier_wall_upkeep: int = int(_g3("barrier_wall_upkeep", 1))
        self.barrier_array_upkeep: int = int(_g3("barrier_array_upkeep", 1))
        self.ideal_burn_upkeep: int = int(_g3("ideal_burn_upkeep", 1))
        self.max_barrier_rounds: int = int(_g3("max_barrier_rounds", 5))
        self.ideal_burn_styles: int = int(_g3("ideal_burn_styles", 3))
        self.ideal_burn_cost_reduction: int = int(
            _g3("ideal_burn_cost_reduction", 1))
        self.collapse_base_damage: int = int(_g3("collapse_base_damage", 5))
        self.collapse_per_style: int = int(_g3("collapse_per_style", 2))
        self.collapse_style_cap: int = int(_g3("collapse_style_cap", 5))
        self.collapse_terminal_min_magic: int = int(
            _g3("collapse_terminal_min_magic", 2))
        self.barrier_anchor_max: int = int(_g3("barrier_anchor_durability", 10))
        self.break_action_power: int = int(_g3("break_action_power", 2))

    # ════════════════════════════════════════════════════════
    #  T0 入口
    # ════════════════════════════════════════════════════════

    def get_t0_option(self, player: Any) -> Optional[dict]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return None
        if player is None or getattr(player, "player_id", "") != self.player_id:
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        if self.barrier_active:
            return {"name": m9_text("talents.g3.t0.barrier_action_name"),
                    "description": m9_text(
                        "talents.g3.t0.barrier_action_description"),
                    "m9_kind": "g3_barrier_action"}
        sp = m9.get_sp(self.player_id)
        round_num = getattr(self.state, "current_round", 1)
        phase = getattr(self.state, "current_phase", "")
        seated = m9._public_holder_by_round.get(round_num) == self.player_id
        public_ready = sp >= 2 and (phase != "r3_actions" or seated)
        if public_ready:
            return {"name": m9_text("talents.g3.t0.expand_name"),
                    "description": m9_text("talents.g3.t0.expand_description"),
                    "m9_kind": "g3_barrier_expand"}
        return {"name": m9_text("talents.g3.t0.projection_name"),
                "description": m9_text("talents.g3.t0.projection_description"),
                "m9_kind": "g3_projection"}

    def execute_t0(self, player: Any) -> Tuple[str, bool]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return m9_text("talents.g3.t0.err_m9_disabled"), False
        if getattr(player, "player_id", "") != self.player_id:
            return m9_text("talents.g3.t0.err_not_self"), False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return m9_text("talents.g3.t0.err_m9_not_mounted"), False
        if self.barrier_active:
            return self._execute_inside(player, m9)
        return self._execute_outside(player, m9)

    def _execute_outside(self, player: Any, m9: Any) -> Tuple[str, bool]:
        round_num = getattr(self.state, "current_round", 1)
        sp = m9.get_sp(self.player_id)
        public_ready = sp >= 2 \
            and m9.assign_public_slot(round_num) == self.player_id
        if public_ready:
            choice = self._choose(
                player, m9_text("talents.g3.t0.choose_outside_prompt"),
                [m9_text("talents.g3.t0.option_expand"),
                 m9_text("talents.g3.t0.option_projection")])
            if choice == "投影魔术":
                return self._run_outside_projection(player)
            if choice == "展开固有结界":
                return self._expand_barrier(player, m9, round_num)
            return m9_text("talents.g3.t0.err_condition_not_met"), False
        return self._run_outside_projection(player)

    def _execute_inside(self, player: Any, m9: Any) -> Tuple[str, bool]:
        self._refresh_main_target(player)
        actions = [m9_text("talents.g3.inside.action_spiral_chain"),
                   m9_text("talents.g3.inside.action_sword_array"),
                   m9_text("talents.g3.inside.action_projection_create"),
                   m9_text("talents.g3.inside.action_break")]
        if self.armament_pool:
            actions.insert(1, m9_text("talents.g3.inside.action_armament"))
        if self._collapse_legal():
            actions.append(m9_text("talents.g3.inside.action_collapse"))
        choice = self._choose(player, m9_text("talents.g3.inside.choose_prompt"),
                              actions)
        if choice == "螺旋剑连发":
            return self._spiral_chain(player)
        if choice == "兵装攻击":
            return self._armament_attack(player)
        if choice == "剑阵":
            return self._sword_array_create(player)
        if choice == "投影创建":
            return self._inside_projection_create(player)
        if choice == "幻想崩坏":
            return self._collapse(player)
        if choice == "破界":
            return self.break_barrier(player)
        return m9_text("talents.g3.inside.err_unknown_action"), False

    # ════════════════════════════════════════════════════════
    #  兵装攻击 + 超限灌注（v0.3 §7.2）
    # ════════════════════════════════════════════════════════

    def _armament_attack(self, player: Any) -> Tuple[str, bool]:
        """兵装攻击根：从兵装池任选一把兵装结算（不重新投影、不支付魔力），
        结算后兵装保留；可超限灌注（消耗临时魔力 → 固定攻击方加值）。"""
        if not self.barrier_active:
            return m9_text("talents.g3.inside.armament.err_not_inside"), False
        if not self.armament_pool:
            return m9_text("talents.g3.inside.armament.err_pool_empty"), False
        member = self._choose(
            player, m9_text("talents.g3.inside.armament.choose_prompt"),
            list(self.armament_pool))
        if member not in self.armament_pool:
            return m9_text("talents.g3.inside.armament.err_not_exists"), False
        copied_range = (self.copy_weapon or {}).get("range") \
            if member.startswith("复制武器:") else WeaponRange.MELEE
        ranged = copied_range is WeaponRange.RANGED
        area = copied_range is WeaponRange.AREA
        targets = self._legal_targets(player, ranged=ranged, area=area)
        if not targets:
            return m9_text("talents.g3.inside.armament.err_no_legal_target"), False
        target = self._pick_target(player, targets)
        # 超限灌注（可选）：只消耗临时超额魔力，普通魔力不动
        overload = 0
        cost = int(_g3("armament_overload_cost", 2))
        if self.temp_magic >= cost:
            want = self._choose(
                player, m9_text("talents.g3.inside.armament.overload_prompt"),
                [m9_text("talents.g3.inside.armament.overload_yes"),
                 m9_text("talents.g3.inside.armament.overload_no")])
            if want == "灌注":
                self.temp_magic = max(0, self.temp_magic - cost)
                overload = int(_g3("armament_overload_bonus", 2))
        from engine.m9.combat import resolve_damage
        if member == "双刀":
            base = int(_g3("dual_blade_base_damage", 3))
            result = resolve_damage(
                player, target, weapon=None, game_state=self.state,
                raw_damage_override=base + overload,
                damage_attribute_override="普通",
                source_kind="g3_armament_dual")
            self._register_style("双刀·守势")
        else:
            base = int(self.copy_weapon["base_damage"]) if self.copy_weapon else 0
            attr = str(self.copy_weapon.get("attribute", "普通")) \
                if self.copy_weapon else "普通"
            result = resolve_damage(
                player, target, weapon=None, game_state=self.state,
                raw_damage_override=base + overload,
                damage_attribute_override=attr,
                source_kind="g3_copy_weapon")
            self._register_style(
                f"复制武器:{self.copy_weapon['name']}" if self.copy_weapon
                else "复制武器")
        tag = m9_text("talents.g3.inside.armament.overload_tag") if overload else ""
        return (m9_text("talents.g3.inside.armament.attack_result",
                        member=member, target=target.name,
                        damage=result['hp_damage'], tag=tag), True)

    # ════════════════════════════════════════════════════════
    #  魔力账本（§三）
    # ════════════════════════════════════════════════════════

    def _recover_magic(self, round_num: int) -> None:
        """结界外每个 R0 自动恢复 magic_recover_r0（不读取上一轮是否行动）。"""
        if round_num == self._last_recover_round:
            return
        self._last_recover_round = round_num
        self.magic = min(self.magic_cap, self.magic + self.magic_recover_r0)

    def _pay_magic(self, cost: int) -> bool:
        """支付预检：普通魔力先支付、临时魔力后支付；两者不足不扣任何魔力。"""
        if cost <= 0:
            return True
        if self.magic + self.temp_magic < cost:
            return False
        self._consume_magic(cost)
        return True

    def _consume_magic(self, amount: int) -> None:
        """扣除魔力（无预检，调用方保证足额）：普通先、临时后。"""
        amount = max(0, int(amount))
        ordinary = min(self.magic, amount)
        self.magic -= ordinary
        amount -= ordinary
        self.temp_magic = max(0, self.temp_magic - amount)

    def _proj_cost(self, key: str) -> int:
        """投影创建费用：理想燃烧后 −ideal_burn_cost_reduction（最低 0）。"""
        base = int(getattr(self, key))
        if self.barrier_active and self.ideal_burn:
            return max(0, base - self.ideal_burn_cost_reduction)
        return base

    def on_round_start(self, *args, **kwargs) -> None:
        """R0：结界外魔力恢复；结界内拦截额度重置；G3 死亡兜底解除。"""
        round_num = args[0] if args else getattr(self.state, "current_round", 1)
        if self.barrier_active:
            if not self._g3_alive():
                self._dismiss_barrier("g3_death")
                return
            if self.sword_array is not None \
                    and self.sword_array.get("function") == "intercept":
                self.sword_array["intercept_used"] = False  # 拦截额度 R0 重置
        else:
            self._recover_magic(round_num)

    def on_round_end(self, round_num) -> None:
        """R4-3：结界外魔力恢复（round 变更守卫）；结界内维持费用（建立轮不 tick）。"""
        self.on_r4_upkeep(round_num)

    def on_r4_upkeep(self, round_num: int) -> None:
        """Idempotent early-R4 upkeep entry used before environment ticks."""
        if self._last_upkeep_round == round_num:
            return
        self._last_upkeep_round = round_num
        if not self.barrier_active:
            self._recover_magic(round_num)
            return
        if not self._g3_alive():
            self._dismiss_barrier("g3_death")
            return
        if self.established_round is not None and round_num <= self.established_round:
            return  # 建立轮 R4 不收费、不 tick
        cost = self._upkeep_cost()
        if not self._pay_magic(cost):
            self._dismiss_barrier("upkeep_failed")  # 不部分支付，强制解除
            return
        self.barrier_rounds += 1
        if self.barrier_rounds >= self.max_barrier_rounds:
            self._dismiss_barrier("max_rounds")  # 全局轮次硬上限

    def _upkeep_cost(self) -> int:
        """维持费用：base + 人数×单价 + 圆环 + 剑阵 + 理想燃烧（§九）。
        永恒诗：一次性维持费折扣（`poem_eternity_cost_reduction`，消费后清除）。"""
        cost = self.barrier_base_upkeep
        cost += max(0, len(self._captured_alive())) * self.barrier_per_unit_upkeep
        if self.rho_aias is not None:
            cost += self.barrier_wall_upkeep
        if self.sword_array is not None:
            cost += self.barrier_array_upkeep
        if self.ideal_burn:
            cost += self.ideal_burn_upkeep
        markers = getattr(self, "m9_poem_markers", None)
        if markers and markers.pop("eternity_discount", None):
            cost = max(0, cost - int(bget(
                "m9_talents_extended", "g5",
                "poem_eternity_cost_reduction", default=2)))
        return cost

    # ════════════════════════════════════════════════════════
    #  结界外投影（§四）
    # ════════════════════════════════════════════════════════

    def _run_outside_projection(self, player: Any) -> Tuple[str, bool]:
        choice = self._choose(player, m9_text("talents.g3.outside.choose_prompt"), [
            m9_text("talents.g3.outside.option_spiral"),
            m9_text("talents.g3.outside.option_dual_offense"),
            m9_text("talents.g3.outside.option_dual_defense"),
            m9_text("talents.g3.outside.option_rho"),
            m9_text("talents.g3.outside.option_copy")])
        if choice == "螺旋剑（伪）":
            return self._spiral_outside(player)
        if choice == "双刀·攻势":
            return self._dual_blade_offense(player)
        if choice == "双刀·守势":
            return self._dual_blade_defense(player)
        if choice == "七重圆环":
            return self._rho_aias_create(player)
        if choice == "复制武器":
            return self._copy_weapon_create(player)
        return m9_text("talents.g3.outside.err_unknown_projection"), False

    def _spiral_outside(self, player: Any) -> Tuple[str, bool]:
        """螺旋剑（伪）：同一攻击根创建并立即发射，不存储为持续投影。"""
        targets = self._legal_targets(player, ranged=True)
        if not targets:
            return m9_text("talents.g3.spiral.err_no_legal_target"), False
        target = self._pick_target(player, targets)
        if not self._pay_magic(self.spiral_cost):
            return m9_text("talents.g3.spiral.err_insufficient_magic"), False
        from engine.m9.combat import resolve_damage
        result = resolve_damage(
            player, target, weapon=None, game_state=self.state,
            raw_damage_override=self.spiral_damage,
            accuracy_bonus=self.spiral_hit_bonus,
            damage_attribute_override="普通", source_kind="g3_spiral")
        return m9_text("talents.g3.spiral.hit_result",
                       target=target.name, damage=result['hp_damage']), True

    def _dual_blade_offense(self, player: Any) -> Tuple[str, bool]:
        """双刀·攻势：近战攻击根，所选近战武器提供基础，双刀加攻击方固定加值。"""
        targets = self._legal_targets(player, ranged=False)
        if not targets:
            return m9_text("talents.g3.dual.err_no_melee_target"), False
        target = self._pick_target(player, targets)
        weapon = self._pick_melee_weapon(player)
        if not self._pay_magic(self.dual_blade_cost):
            return m9_text("talents.g3.dual.err_insufficient_magic"), False
        from engine.m9.combat import resolve_damage
        result = resolve_damage(
            player, target, weapon=weapon, game_state=self.state,
            bonus_damage=float(self.dual_blade_attack_bonus),
            source_kind="g3_dual_blade_offense")
        self._register_style("双刀·攻势")
        return m9_text("talents.g3.dual.offense_hit_result",
                       target=target.name, damage=result['hp_damage']), True

    def _dual_blade_defense(self, player: Any) -> Tuple[str, bool]:
        """双刀·守势：非敌对标准根建立防御标记（结界外占唯一持续通道）。
        结界内创建时双刀进入兵装池（v0.3 §7.2）。"""
        if not self._pay_magic(self.dual_blade_cost):
            return m9_text("talents.g3.dual.err_insufficient_magic"), False
        self.defense_marker = True
        if not self.barrier_active:
            self.outside_kind = "defense"
            self.rho_aias = None
            self.copy_weapon = None
        else:
            if "双刀" not in self.armament_pool:
                self.armament_pool.append("双刀")
        self._register_style("双刀·守势")
        return m9_text("talents.g3.dual.defense_established"), True

    def _rho_aias_create(self, player: Any) -> Tuple[str, bool]:
        """七重圆环：普通掩体类防护（独立耐久；远程直接攻击先命中圆环）。"""
        if not self._pay_magic(self.rho_aias_cost):
            return m9_text("talents.g3.rho.err_insufficient_magic"), False
        self.rho_aias = {"durability": self.rho_aias_durability,
                         "location": getattr(player, "location", None)}
        if not self.barrier_active:
            self.outside_kind = "rho_aias"
            self.defense_marker = False
            self.copy_weapon = None
        self._register_style("七重圆环")
        return m9_text("talents.g3.rho.established",
                       durability=self.rho_aias_durability), True

    def _copy_weapon_create(self, player: Any) -> Tuple[str, bool]:
        """复制武器：见证式样优先（event_log attack 事件的 weapon 键），
        无见证回退白名单；结界外占唯一持续通道。"""
        witnessed = self.witnessed_weapons()
        options = list(witnessed) if witnessed else list(COPY_WEAPON_CLOSED_LIST)
        choice = self._choose(player, m9_text("talents.g3.copy.choose_prompt"),
                              options)
        if choice not in COPY_WEAPON_CLOSED_LIST:
            return m9_text("talents.g3.copy.err_not_in_whitelist"), False
        if not self._pay_magic(self.copy_weapon_cost):
            return m9_text("talents.g3.copy.err_insufficient_magic"), False
        w = self._make_copy_weapon(choice)
        if w is None:
            return m9_text("talents.g3.copy.err_failed"), False
        self.copy_weapon = {
            "name": choice,
            "base_damage": int(round(float(w.get_effective_damage()))),
            "attribute": getattr(w.attribute, "value", "普通"),
            "range": getattr(w, "weapon_range", None),
        }
        if not self.barrier_active:
            self.outside_kind = "copy"
            self.defense_marker = False
            self.rho_aias = None
        else:
            member = f"复制武器:{choice}"
            if member not in self.armament_pool:
                self.armament_pool.append(member)
        self._register_style(f"复制武器:{choice}")
        return m9_text("talents.g3.copy.registered", choice=choice), True

    def witnessed_weapons(self) -> List[str]:
        """事件日志见证的关闭列举内普通武器名（attack 事件的 weapon 键）。"""
        seen = set()
        for ev in getattr(self.state, "event_log", []):
            if not isinstance(ev, dict):
                continue
            w = ev.get("weapon")
            if isinstance(w, str) and w in COPY_WEAPON_CLOSED_LIST:
                seen.add(w)
        return sorted(seen)

    @staticmethod
    def _make_copy_weapon(name: str):
        if name == "弓":
            return make_bow()
        return make_weapon(name)

    def use_copy_attack(self, player: Any, target: Any) -> Optional[Dict[str, Any]]:
        """复制武器攻击根：结界外基础伤害 × outside_copy_ratio（half-up）；
        结界内使用基础值。"""
        if self.copy_weapon is None:
            return None
        copied_range = self.copy_weapon.get("range", WeaponRange.MELEE)
        if not self.attack_legal(
                player, target,
                ranged=copied_range is WeaponRange.RANGED,
                area=copied_range is WeaponRange.AREA):
            return None
        base = int(self.copy_weapon["base_damage"])
        if not self.barrier_active:
            base = half_up(base * self.outside_copy_ratio)
        from engine.m9.combat import resolve_damage
        return resolve_damage(
            player, target, weapon=None, game_state=self.state,
            raw_damage_override=base,
            damage_attribute_override=self.copy_weapon["attribute"],
            source_kind="g3_copy_weapon")

    # ════════════════════════════════════════════════════════
    #  无限剑制：展开（§五/§六）
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        """只消费 R0 已固化的公演位；T0 不得补报名。"""
        if m9.assign_public_slot(round_num) != player.player_id:
            return False
        return m9.dispatch_public(player.player_id, round_num,
                                  source_id="g3_barrier") is not None

    def _expand_barrier(self, player: Any, m9: Any,
                        round_num: int) -> Tuple[str, bool]:
        """公演展开：SP 归零 → 临时魔力 → 快照捕捉 → 主目标 → 警察挂起 →
        免费初始配置。展开本身不额外收取魔力。"""
        if m9.get_sp(self.player_id) < 2:
            return m9_text("talents.g3.barrier.err_sp_insufficient_2"), False
        loc = getattr(player, "location", None)
        if not loc:
            return m9_text("talents.g3.barrier.err_no_location"), False
        # 裁决（2026-08 风洞）：同地点没有其他存活玩家时不可展开无限剑制——
        # 空结界（只困住自己）没有价值，属规则补全而非数值调整。
        others_here = False
        for pid in self.state.player_order:
            if pid == self.player_id:
                continue
            other = self.state.get_player(pid)
            if (other is not None and other.is_alive()
                    and getattr(other, "location", None) == loc):
                others_here = True
                break
        if not others_here:
            return m9_text("talents.g3.barrier.err_no_other_player_here"), False
        if not self._ensure_public_seat(player, m9, round_num):
            return m9_text("talents.g3.barrier.err_sp_or_seat_before_consume"), False

        self.barrier_active = True
        self.barrier_location = loc
        self.barrier_anchor_durability = self.barrier_anchor_max
        self.barrier_rounds = 0
        self.established_round = round_num
        self.temp_magic += self.public_temp_magic  # 2 SP → 临时超额魔力
        self.defense_marker = False                # 进入结界清除未使用防御标记
        self._gale_applied.clear()
        self.chain = None

        # 捕捉快照：同地点其他存活玩家 + 独立行动 NPC（iter_actors）；
        # 警察和附属对象（G0 无人机等）永不捕捉。
        self.original_locations = {}
        captured: List[str] = []
        for actor in self.state.iter_actors():
            if actor is None or not actor.is_alive():
                continue
            pid = getattr(actor, "player_id", "")
            if pid == self.player_id:
                continue
            if getattr(actor, "_m9_police_actor", False) \
                    or getattr(actor, "_m9_drone_actor", False):
                continue
            if getattr(actor, "location", None) != loc:
                continue
            captured.append(pid)
            self.original_locations[pid] = getattr(actor, "location", None)
        self.captured = captured

        # 主目标：存在 G3 以外存活被捕捉单位时必须公开指定；仅自己则空。
        alive = self._captured_alive()
        if len(alive) == 1:
            self.main_target = alive[0]
        elif len(alive) > 1:
            self.main_target = self._pick_captured(player, alive)
        else:
            self.main_target = None

        # 只在当前通缉目标被捕捉（或是展开者）时挂起该案件；结界不应
        # 全局冻结无关的警务状态。
        m9_police = getattr(self.state, "m9_police", None)
        if m9_police is not None and hasattr(m9_police, "suspend_for_barrier"):
            m9_police.suspend_for_barrier(
                self.player_id, set(captured) | {self.player_id})

        self._initial_config(player)
        self.state.log_event("m9_g3_expand", player=self.player_id, location=loc,
                             captured=list(captured), main_target=self.main_target,
                             temp_magic=self.temp_magic)
        return m9_text("talents.g3.barrier.expanded",
                       player=player.name, count=len(captured)), True

    def _initial_config(self, player: Any) -> None:
        """展开行动的免费初始配置：兵装/防壁/剑阵三选一免费创建（不附带攻击）。"""
        choice = self._choose(
            player, m9_text("talents.g3.barrier.initial_config_prompt"),
            [m9_text("talents.g3.barrier.initial_option_armament"),
             m9_text("talents.g3.barrier.initial_option_wall"),
             m9_text("talents.g3.barrier.initial_option_array")])
        if choice == "防壁（七重圆环）":
            self.rho_aias = {"durability": self.rho_aias_durability,
                             "location": self.barrier_location}
        elif choice == "剑阵":
            func = self._choose(
                player, m9_text("talents.g3.inside.sword_array.choose_prompt"),
                m9_text_list("talents.g3.inside.sword_array.functions"))
            self.sword_array = {"function": SWORD_ARRAY_FUNCTIONS[func],
                                "durability": self.sword_array_durability,
                                "intercept_used": False}
        else:
            self.armament = "spiral"

    def _refresh_main_target(self, player: Any) -> None:
        """主目标死亡/离开 → 清空；空时在后续合法 T0 重新指定（免费，每 T0 一次）。"""
        if self.main_target is not None:
            actor = self.state.get_player(self.main_target)
            if actor is None or not actor.is_alive() \
                    or self.main_target not in self.captured:
                self.main_target = None
        if self.main_target is None:
            alive = self._captured_alive()
            if len(alive) == 1:
                self.main_target = alive[0]
            elif len(alive) > 1:
                self.main_target = self._pick_captured(player, alive)

    def redesignate_main_target(self, target_id: str) -> bool:
        """公开改指主目标：必须是存活被捕捉单位（每 T0 一次免费，由调用方计时）。"""
        if not self.barrier_active or not target_id:
            return False
        if target_id not in self.captured:
            return False
        actor = self.state.get_player(target_id)
        if actor is None or not actor.is_alive():
            return False
        self.main_target = target_id
        return True

    # ════════════════════════════════════════════════════════
    #  结界内：螺旋剑连发（连续投影 RFC v0.1）
    # ════════════════════════════════════════════════════════

    def _spiral_chain(self, player: Any) -> Tuple[str, bool]:
        """螺旋剑连发根：ProjectionChain 预算 = 魔力 + 临时魔力；逐段预检+选目标；
        赤原猎风（累计耗魔 ≥ 阈值后命中玩家单位：SP−1 + 移出公演队列）；
        连发停止后可同根结算终段幻想崩坏。"""
        if not self.barrier_active:
            return m9_text("talents.g3.chain.err_not_inside"), False
        budget = self.magic + self.temp_magic
        if budget < self.spiral_cost:
            return m9_text("talents.g3.chain.err_insufficient_magic"), False
        chain = ProjectionChain(default_chain_config(), inside_barrier=True,
                                weapon_name="螺旋剑（伪）", is_copy_weapon=False)
        chain.magic_budget = budget
        self.chain = chain
        self.last_chain_spent = 0
        from engine.m9.combat import resolve_damage
        lines = [m9_text("talents.g3.chain.header")]
        while True:
            if chain.next_segment_cost() is None:
                break
            targets = [t for t in self._legal_targets(player, ranged=True)
                       if t.is_alive()]
            if not targets:
                break
            target = self._pick_target(player, targets)
            seg = chain.pay(target.player_id)
            if seg is None:
                break
            result = resolve_damage(
                player, target, weapon=None, game_state=self.state,
                raw_damage_override=self.spiral_damage,
                accuracy_bonus=self.spiral_hit_bonus,
                damage_attribute_override="普通", source_kind="g3_spiral")
            seg.hit = result
            gale = False
            if result.get("success") and result.get("hp_damage", 0) >= 1 \
                    and chain.should_apply_gale(target.player_id):
                gale = self._apply_gale(target.player_id)
            gale_suffix = m9_text("talents.g3.chain.gale_suffix") if gale else ""
            lines.append(m9_text("talents.g3.chain.segment_line",
                                 index=seg.index, target=target.name,
                                 damage=result['hp_damage'], gale=gale_suffix))
            if chain.next_segment_cost() is None:
                break
            cont = self._choose(
                player, m9_text("talents.g3.chain.continue_prompt"),
                [m9_text("talents.g3.chain.option_continue"),
                 m9_text("talents.g3.chain.option_stop")])
            if cont != "继续连发":
                break
        self.last_chain_spent = chain.cumulative_magic

        # 同根终段幻想崩坏（理想燃烧 + 剩余魔力 ≥ 下限 + 主目标非空）
        terminal = False
        if chain.segments and chain.can_terminal_collapse(self.ideal_burn) \
                and self.main_target is not None:
            cont = self._choose(
                player, m9_text("talents.g3.chain.terminal_prompt"),
                [m9_text("talents.g3.chain.option_yes"),
                 m9_text("talents.g3.chain.option_no")])
            terminal = (cont == "是")
        if terminal:
            self._register_style("螺旋剑（伪）")
            self._terminal_collapse(chain, player)
            chain.finish_root()
            return m9_text("talents.g3.chain.terminal_result",
                           lines="\n".join(lines)), True

        # 非终段：只扣段位费用（理想燃烧首发减免；连发段成本不减免）。
        # 注：ProjectionChain 只增 cumulative_magic，magic_budget 为总预算不变。
        spent = self.last_chain_spent
        if self.ideal_burn and spent > 0:
            spent = max(0, spent - self.ideal_burn_cost_reduction)
        self._consume_magic(spent)
        chain.finish_root()
        self._register_style("螺旋剑（伪）")
        return "\n".join(lines), True

    def _terminal_collapse(self, chain: ProjectionChain, player: Any) -> None:
        """终段幻想崩坏：清空剩余魔力、牺牲三通道、统一攻击主目标、无条件解除。"""
        chain.terminal_collapse()
        # 终段会牺牲本根已支付段位与剩余预算，账本结果必为零。
        self.magic = 0
        self.temp_magic = 0
        target = self.state.get_player(self.main_target) \
            if self.main_target else None
        if target is not None and target.is_alive():
            from engine.m9.combat import resolve_damage
            styles = len(self.ideal_styles)
            base = half_up(self.collapse_base_damage
                           + self.collapse_per_style
                           * min(styles, self.collapse_style_cap))
            if self.sword_array is not None \
                    and self.sword_array.get("function") == "collapse_prep":
                base += self.sword_array_collapse_bonus
            result = resolve_damage(
                player, target, weapon=None, game_state=self.state,
                raw_damage_override=base,
                damage_attribute_override="普通",
                armor_pierce_factor=0.5, source_kind="g3_collapse")
            self.state.log_event("m9_g3_collapse", player=self.player_id,
                                 target=self.main_target, base=base,
                                 damage=result["hp_damage"], styles=styles,
                                 terminal=True)
        self._dismiss_barrier("g3_collapse")

    # ════════════════════════════════════════════════════════
    #  结界内：剑阵 / 投影创建 / 幻想崩坏（§七/§八）
    # ════════════════════════════════════════════════════════

    def _sword_array_create(self, player: Any) -> Tuple[str, bool]:
        """剑阵：占用一个标准根 + sword_array_cost；三功能关闭列举；换型替换旧对象。"""
        if not self.barrier_active:
            return m9_text("talents.g3.inside.sword_array.err_not_inside"), False
        func = self._choose(
            player, m9_text("talents.g3.inside.sword_array.choose_prompt"),
            m9_text_list("talents.g3.inside.sword_array.functions"))
        if not self._pay_magic(self._proj_cost("sword_array_cost")):
            return m9_text("talents.g3.inside.sword_array.err_insufficient_magic"), False
        self.sword_array = {"function": SWORD_ARRAY_FUNCTIONS[func],
                            "durability": self.sword_array_durability,
                            "intercept_used": False}
        self._register_style("剑阵")
        return m9_text("talents.g3.inside.sword_array.established", func=func), True

    def _inside_projection_create(self, player: Any) -> Tuple[str, bool]:
        """结界内投影创建（防壁/守势/复制，通道与兵装并存）。"""
        choice = self._choose(
            player, m9_text("talents.g3.inside.projection_create.choose_prompt"),
            [m9_text("talents.g3.inside.projection_create.option_rho"),
             m9_text("talents.g3.inside.projection_create.option_dual_defense"),
             m9_text("talents.g3.inside.projection_create.option_copy")])
        if choice == "七重圆环":
            return self._rho_aias_create(player)
        if choice == "双刀·守势":
            return self._dual_blade_defense(player)
        return self._copy_weapon_create(player)

    def _collapse_legal(self) -> bool:
        """幻想崩坏合法性：理想燃烧 + 主目标非空 + 剩余魔力 ≥ 最低下限。"""
        if not self.barrier_active or not self.ideal_burn:
            return False
        if self.main_target is None:
            return False
        return self.magic + self.temp_magic >= self.collapse_terminal_min_magic

    def _collapse(self, player: Any) -> Tuple[str, bool]:
        """幻想崩坏（独立发动）：消耗全部剩余魔力、牺牲三通道、统一攻击主目标
        （普通属性、defense_coefficient=0.5、公共 A/H 两阶段），结算后无条件解除。"""
        if not self._collapse_legal():
            return m9_text("talents.g3.collapse.err_condition_not_met"), False
        target = self.state.get_player(self.main_target) \
            if self.main_target else None
        if target is None or not target.is_alive():
            self.main_target = None
            return m9_text("talents.g3.collapse.err_main_target_invalid"), False
        from engine.m9.combat import resolve_damage
        styles = len(self.ideal_styles)
        base = half_up(self.collapse_base_damage
                       + self.collapse_per_style
                       * min(styles, self.collapse_style_cap))
        if self.sword_array is not None \
                and self.sword_array.get("function") == "collapse_prep":
            base += self.sword_array_collapse_bonus
        result = resolve_damage(
            player, target, weapon=None, game_state=self.state,
            raw_damage_override=base,
            damage_attribute_override="普通",
            armor_pierce_factor=0.5, source_kind="g3_collapse")
        self.state.log_event("m9_g3_collapse", player=self.player_id,
                             target=self.main_target, base=base,
                             damage=result["hp_damage"], styles=styles)
        self.magic = 0
        self.temp_magic = 0
        self._dismiss_barrier("g3_collapse")
        return m9_text("talents.g3.collapse.result",
                       target=target.name, damage=result['hp_damage']), True

    def _register_style(self, style_id: str) -> None:
        """本次结界内式样登记：唯一 ID 达到阈值立即进入理想燃烧。"""
        if not self.barrier_active:
            return
        if style_id in self.ideal_styles:
            return
        self.ideal_styles.add(style_id)
        if len(self.ideal_styles) >= self.ideal_burn_styles and not self.ideal_burn:
            self.ideal_burn = True
            self.state.log_event("m9_g3_ideal_burn", player=self.player_id,
                                 styles=list(self.ideal_styles))

    # ════════════════════════════════════════════════════════
    #  破界（§6.1）
    # ════════════════════════════════════════════════════════

    def weapon_attack_anchor(self, attacker: Any, weapon: Any) -> Tuple[str, bool]:
        """普通武器攻击根改为锚点：攻击方结果 A 直接扣结构耐久（无目标侧 H）。"""
        if not self.barrier_active:
            return m9_text("talents.g3.barrier.err_not_active"), False
        if not self._is_trapped(attacker):
            return m9_text("talents.g3.barrier.err_not_trapped"), False
        a = max(0, int(round(float(weapon.get_effective_damage()))))
        if a <= 0:
            return m9_text("talents.g3.barrier.anchor_attack_no_effect"), True
        self.barrier_anchor_durability = max(
            0, self.barrier_anchor_durability - a)
        if self.barrier_anchor_durability <= 0:
            self._dismiss_barrier("anchor_destroyed")
            return m9_text("talents.g3.barrier.anchor_destroyed"), True
        return m9_text("talents.g3.barrier.anchor_durability_reduced",
                       reduction=a,
                       durability=self.barrier_anchor_durability), True

    def break_barrier(self, actor: Any) -> Tuple[str, bool]:
        """破界：登记的标准根行动，无武器无命中，稳定 break_action_power 结构耐久。"""
        if not self.barrier_active:
            return m9_text("talents.g3.barrier.err_not_active"), False
        if not self._is_trapped(actor):
            return m9_text("talents.g3.barrier.err_not_trapped"), False
        power = self.break_action_power
        self.barrier_anchor_durability = max(
            0, self.barrier_anchor_durability - power)
        if self.barrier_anchor_durability <= 0:
            self._dismiss_barrier("anchor_destroyed")
            return m9_text("talents.g3.barrier.break_success"), True
        return m9_text("talents.g3.barrier.break_durability_reduced",
                       power=power,
                       durability=self.barrier_anchor_durability), True

    # ════════════════════════════════════════════════════════
    #  结界解除 / 死亡清理（§10，幂等）
    # ════════════════════════════════════════════════════════

    def _dismiss_barrier(self, reason: str) -> None:
        """结界解除（幂等）：返回锚点、清临时魔力、清全部投影、恢复警察。"""
        if not self.barrier_active:
            return
        self.barrier_active = False
        # 存活被困单位返回展开快照记录的原地点（摧毁 → 最近安全地点 v1 简化）
        for pid in self.captured:
            actor = self.state.get_player(pid)
            if actor is None or not actor.is_alive():
                continue
            loc = self.original_locations.get(pid)
            if loc is not None:
                actor.location = self._safe_location(loc)
        self.captured = []
        self.original_locations = {}
        self.main_target = None
        self.barrier_anchor_durability = 0
        self.armament = None
        self.armament_pool = []          # 兵装池随结界清理（v0.3 §7.2）
        self.barrier_rounds = 0
        self.established_round = None
        self.temp_magic = 0                       # 临时超额魔力无条件清零
        self.rho_aias = None
        self.sword_array = None
        self.copy_weapon = None
        self.defense_marker = False
        self.ideal_burn = False
        self.ideal_styles.clear()
        self._gale_applied.clear()
        self.chain = None
        m9_police = getattr(self.state, "m9_police", None)
        if m9_police is not None and hasattr(m9_police, "resume_barrier"):
            m9_police.resume_barrier(self.player_id)
        self.state.log_event("m9_g3_dismiss", player=self.player_id, reason=reason)

    def _safe_location(self, loc: str) -> str:
        """摧毁/不可进入地点的安全回退（距离相同按地点 ID 升序，v1 固定表简化）。"""
        destroyed = getattr(self.state, "m9_destroyed_locations", set())
        if loc not in destroyed:
            return loc
        for fallback in SAFE_FALLBACK_LOCATIONS:
            if fallback not in destroyed:
                return fallback
        return loc

    def cleanup_on_death(self) -> None:
        """G3 死亡清理（管线终局后由调用方/惰性钩子触发）。"""
        self._dismiss_barrier("g3_death")

    def force_dismiss(self, reason: str = "forced") -> None:
        """强制解除（显式最高级效果入口；幂等）。"""
        self._dismiss_barrier(reason)

    def release_from_barrier(self, pid: str, reason: str = "forced_exit") -> None:
        """单单位释出（幂等）：强制退场等最高级位移把被困单位带出结界地点时
        同步结界身份——移出捕捉快照、主目标指定立即清空（§5.3/§214）。
        不传送该单位（其正被强制位移）；结界本身与其余被困单位不受影响。"""
        changed = False
        if pid in self.captured:
            self.captured.remove(pid)
            changed = True
        if pid in self.original_locations:
            del self.original_locations[pid]
            changed = True
        if self.main_target == pid:
            self.main_target = None
            changed = True
        if changed:
            self.state.log_event("m9_g3_release", player=self.player_id,
                                 released=pid, reason=reason)

    # ════════════════════════════════════════════════════════
    #  边界 / 目标合法性 / 防御承接
    # ════════════════════════════════════════════════════════

    def cross_boundary(self, actor_a: Any, actor_b: Any) -> bool:
        """边界：结界内单位与结界外单位之间禁止跨边界交互（§5.3）。"""
        if not self.barrier_active:
            return False
        return self._is_inside(actor_a) != self._is_inside(actor_b)

    def attack_legal(self, attacker: Any, target: Any,
                     ranged: bool = True, area: bool = False) -> bool:
        """G3 投影攻击沿用公开 A 根的接敌/锁定合法性。

        投影只替换武器载荷，不能把普通攻击根降格为全图自由点名。M9 独立
        NPC 仍沿用 action_enumerator 的同地点例外。
        """
        if attacker is None or target is None:
            return False
        if not target.is_alive():
            return False
        attacker_id = getattr(attacker, "player_id", "")
        target_id = getattr(target, "player_id", "")
        if not attacker_id or not target_id or target_id == attacker_id:
            return False
        if self.cross_boundary(attacker, target):
            return False
        same_location = (getattr(target, "location", None)
                         == getattr(attacker, "location", None))
        is_m9_npc = bool(getattr(target, "_m9_police_actor", False)
                         or getattr(target, "_m9_drone_actor", False))
        markers = getattr(self.state, "markers", None)
        if markers is None:
            return False
        if area:
            return bool(same_location)
        if ranged:
            return bool(markers.has_relation(
                target_id, "LOCKED_BY", attacker_id)
                or (is_m9_npc and same_location))
        return bool(same_location and (
            markers.has_relation(attacker_id, "ENGAGED_WITH", target_id)
            or is_m9_npc))

    def _legal_targets(self, player: Any, ranged: bool = True,
                       area: bool = False) -> List[Any]:
        out = []
        for actor in self.state.iter_actors():
            if self.attack_legal(player, actor, ranged=ranged, area=area):
                out.append(actor)
        return out

    def _pick_target(self, player: Any, targets: List[Any]) -> Any:
        if len(targets) == 1:
            return targets[0]
        names = [getattr(t, "name", getattr(t, "player_id", "")) for t in targets]
        choice = self._choose(player, m9_text("talents.g3.choose_target_prompt"),
                              names)
        for t in targets:
            if getattr(t, "name", getattr(t, "player_id", "")) == choice:
                return t
        return targets[0]

    def _pick_captured(self, player: Any, pids: List[str]) -> str:
        names = [getattr(self.state.get_player(pid), "name", pid)
                 for pid in pids]
        choice = self._choose(
            player, m9_text("talents.g3.barrier.choose_main_target_prompt"),
            names)
        for pid in pids:
            if getattr(self.state.get_player(pid), "name", pid) == choice:
                return pid
        return pids[0]

    @staticmethod
    def _pick_melee_weapon(player: Any):
        melee = [w for w in getattr(player, "weapons", [])
                 if w and getattr(w, "weapon_range", None) == WeaponRange.MELEE]
        if not melee:
            return make_weapon("拳击")
        return melee[0]

    def defend_ranged(self, attacker: Any, raw_damage: int) -> int:
        """远程直接攻击承接：拦截剑阵 → 七重圆环 → G3 本体（§7.1）。
        返回 G3 实际承受伤害；击破拦截物不溢出。"""
        # 剑阵·拦截：每全局轮第一次以 G3 为目标的远程直接攻击，先于圆环
        if self.sword_array is not None \
                and self.sword_array.get("function") == "intercept" \
                and not self.sword_array.get("intercept_used"):
            self.sword_array["intercept_used"] = True
            self.sword_array["durability"] = max(
                0, self.sword_array["durability"] - max(0, int(raw_damage)))
            if self.sword_array["durability"] <= 0:
                self.sword_array = None
            return 0
        # 七重圆环：地点绑定；击破不溢出
        if self.rho_aias is not None:
            g3 = self._g3()
            if getattr(g3, "location", None) != self.rho_aias["location"]:
                self.rho_aias = None  # 离开地点 → 圆环立即失去
            else:
                self.rho_aias["durability"] = max(
                    0, self.rho_aias["durability"] - max(0, int(raw_damage)))
                if self.rho_aias["durability"] <= 0:
                    self.rho_aias = None
                return 0
        return max(0, int(raw_damage))

    def _apply_gale(self, target_pid: str) -> bool:
        """赤原猎风：目标 SP −1（下限 0）+ 永久移出公演队列；
        频率闸——同一 player_id 每次结界至多一次。"""
        owner_id = self.state.attention_owner_id(target_pid) \
            if hasattr(self.state, "attention_owner_id") else target_pid
        if owner_id in self._gale_applied:
            return False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is not None:
            m9.spend_sp(owner_id, 1)
            m9.queue.remove_permanently(owner_id)
        self._gale_applied.add(owner_id)
        return True

    # ════════════════════════════════════════════════════════
    #  M9 结算钩子（防御标记 / 弹道校正）
    # ════════════════════════════════════════════════════════

    def m9_modify_incoming(self, hit: Any) -> None:
        """双刀·守势：目标侧固定减伤（公共 25% 下限约束）；
        DIRECT_DAMAGE 只消耗标记、跳过这项固定减伤。"""
        if not self.defense_marker:
            return
        self.defense_marker = False  # 下一次通过预检并针对 G3 的攻击即消耗
        if getattr(hit, "direct_damage", False):
            return
        floor = numeric_v2.min_damage(getattr(hit, "raw", 0))
        hit.damage = max(floor, hit.damage - self.dual_blade_reduction)

    def m9_modify_outgoing(self, attacker: Any, target: Any, weapon: Any,
                           raw: float) -> float:
        """G3 没有把命中加值写入伤害的攻击方数值修正。"""
        return raw

    def m9_accuracy_bonus(self, attacker: Any, target: Any, weapon: Any,
                          source_kind: Optional[str]) -> int:
        """剑阵·弹道校正只加命中，不增加攻击方伤害 A。"""
        if self.barrier_active \
                and getattr(attacker, "player_id", None) == self.player_id \
                and self.sword_array is not None \
                and self.sword_array.get("function") == "ballistic":
            return self.sword_array_hit_bonus
        return 0

    # ════════════════════════════════════════════════════════
    #  G6 借用核心（连续投影 RFC v0.1 §六）
    # ════════════════════════════════════════════════════════

    @staticmethod
    def simple_projection(player: Any, game_state: Any) -> str:
        """G6 借用：螺旋剑（伪）单体立即发射（无连发、无赤原猎风、不读 G3 账本）。"""
        from engine.m9.combat import resolve_damage
        spiral_damage = int(_g3("spiral_damage", 5))
        spiral_hit_bonus = int(_g3("spiral_hit_bonus", 15))
        targets = []
        markers = getattr(game_state, "markers", None)
        for pid in getattr(game_state, "player_order", []):
            if pid == getattr(player, "player_id", ""):
                continue
            p = game_state.get_player(pid)
            if (p is not None and p.is_alive() and markers is not None
                    and markers.has_relation(
                        p.player_id, "LOCKED_BY", player.player_id)):
                targets.append(p)
        if not targets:
            return m9_text("talents.g3.spiral.err_no_legal_target")
        target = targets[0]
        ctrl = getattr(player, "controller", None)
        if ctrl is not None:
            try:
                names = [getattr(p, "name", p.player_id) for p in targets]
                choice = ctrl.choose(m9_text("talents.g3.spiral.borrow_prompt"),
                                     names)
                for p in targets:
                    if getattr(p, "name", p.player_id) == choice:
                        target = p
                        break
            except Exception:
                pass
        result = resolve_damage(
            player, target, weapon=None, game_state=game_state,
            raw_damage_override=spiral_damage,
            accuracy_bonus=spiral_hit_bonus,
            damage_attribute_override="普通", source_kind="g3_spiral")
        return m9_text("talents.g3.spiral.borrow_result",
                       target=target.name, damage=result['hp_damage'])

    @staticmethod
    def borrow_simple_projection(player: Any, game_state: Any) -> str:
        """G6 借用核心入口（G6_BORROWABLE_CORE["g3_reality_marble"]）。"""
        return Mythland9.simple_projection(player, game_state)

    # ════════════════════════════════════════════════════════
    #  辅助
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _choose(player: Any, prompt: str, options: List[str]) -> str:
        """controller 选择（缺省/异常回退首个选项，确定性）。"""
        opts = list(options)
        if not opts:
            return ""
        ctrl = getattr(player, "controller", None)
        if ctrl is None:
            return opts[0]
        try:
            c = ctrl.choose(prompt, opts, context={"phase": "T0",
                                                   "situation": "m9_g3"})
            return c if c in opts else opts[0]
        except Exception:
            return opts[0]

    def _g3(self) -> Any:
        return self.state.get_player(self.player_id)

    def _g3_alive(self) -> bool:
        p = self._g3()
        return p is not None and p.is_alive()

    def _is_inside(self, actor: Any) -> bool:
        return self._is_trapped(actor)

    def _is_trapped(self, actor: Any) -> bool:
        if actor is None:
            return False
        pid = getattr(actor, "player_id", "")
        if pid == self.player_id:
            return True
        return pid in self.captured

    def _captured_alive(self) -> List[str]:
        out = []
        for pid in self.captured:
            actor = self.state.get_player(pid)
            if actor is not None and actor.is_alive():
                out.append(pid)
        return out

    def describe_status(self) -> str:
        parts = [m9_text("talents.g3.status.magic",
                         magic=self.magic, cap=self.magic_cap)
                 + (m9_text("talents.g3.status.temp_magic_suffix",
                            temp=self.temp_magic) if self.temp_magic else "")]
        if self.barrier_active:
            parts.append(m9_text("talents.g3.status.inside_barrier",
                                 ticks=self.barrier_rounds,
                                 max=self.max_barrier_rounds))
            parts.append(m9_text(
                "talents.g3.status.main_target",
                target=self.main_target
                or m9_text("talents.g3.status.main_target_empty")))
            if self.ideal_burn:
                parts.append(m9_text("talents.g3.status.ideal_burn"))
        else:
            parts.append(m9_text("talents.g3.status.outside"))
        return " | ".join(parts)
