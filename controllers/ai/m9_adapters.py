"""M9 天赋 AI adapter 分派层（按 profile × slot_id，废除显示名分派）。

- `resolve_talent_hook(controller, player)`：优先 (profile, slot_id) 查
  `_slot_hook_instances`，回退显示名 `_talent_hook_instances`（v2exp 兼容）；
- `M9AdapterBase`：M9 天赋 adapter 基类——第一版只识别 M9 situation 并给出
  保守默认（options[0]），不改变现有行为；策略本体留给 BasicAI 讨论批次；
- `build_slot_hook_map(controller)`：为 G1/G2/G5/G6（此前无专属 M9 决策的槽位）
  注册 (profile, slot_id) → adapter 入口。

设计意图：新增天赋只增加 adapter 条目，不再膨胀 orchestrator/choose_mixin；
M9 的 situation（m9_g3 / 焚诏拉条 / 微澜 / g7 演出等）在此层有明确的接管点。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from engine.m9.gate import m9_enabled
from engine.m9.text import m9_text

# M9 choose situation 前缀/已知键（第一版保守默认名单）
M9_SITUATIONS: Tuple[str, ...] = (
    "talent_t0",
    "m9_g3",
    "petrified",
    "g1_", "g2_", "g3_", "g4_", "g5_", "g6_", "g7_",
    "t1_", "t2_", "t3_", "t4_", "t6_", "t7_",
)


def profile_of(state: Any) -> str:
    try:
        return "m9-rfc" if m9_enabled(state) else "v2exp"
    except Exception:
        return "v2exp"


def slot_id_of(talent: Any) -> str:
    from controllers.ai.decision.snapshot import _slot_id_for
    return _slot_id_for(talent)


class M9AdapterBase:
    """M9 天赋 adapter 基类（最小骨架；策略本体后续批次填充）。"""

    slot_id: str = ""

    def __init__(self, controller: Any) -> None:
        self._ctrl = controller

    def handle_choose(self, player: Any, state: Any, situation: str,
                      options: List[str], context: Optional[Dict] = None) -> Optional[str]:
        """M9 situation 保守默认（options[0]）；非 M9 situation 放行给旧层。"""
        if not options:
            return None
        if situation in M9_SITUATIONS or situation.startswith("m9_"):
            return options[0]
        return None

    def should_override_candidates(self, player: Any, state: Any,
                                   available: List[str]) -> Optional[List[str]]:
        """第一版不整体接管候选生成。"""
        return None

    def get_development_needs_override(self, player: Any, state: Any,
                                       needs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return None

    def is_development_complete(self, player: Any, state: Any) -> Optional[bool]:
        return None

    def get_develop_commands(self, player: Any, state: Any) -> Optional[List[str]]:
        return None

    def modify_target_score(self, target: Any, base_score: float,
                            player: Any) -> float:
        return base_score

    def get_talent_special_candidates(self, player: Any, state: Any,
                                      available: List[str],
                                      snapshot: Any = None) -> List[str]:
        """C 层：槽位专属 special 时机候选（非接管，追加进 orchestrator 候选）。"""
        return []


class _G2Adapter(M9AdapterBase):
    """M9 G2：光身保命、影身进攻的分工。

    - 影身（owner 附属 actor，受限菜单）：死亡不杀死 G2，价值 = 输出最大化，
      有攻击就打、有锁定就锁，发育/移动交通用管道；
    - 光身：影身存活时避免同地点交战（光身死亡连坐影身），有敌即撤离安全点。
    """

    slot_id = "G2"

    def should_override_candidates(self, player: Any, state: Any,
                                   available: List[str]) -> Optional[List[str]]:
        if getattr(player, "_m9_shadow_actor", False):
            return self._shadow_override(player, state, available)
        return self._light_override(player, state, available)

    def _shadow_override(self, player: Any, state: Any,
                         available: List[str]) -> Optional[List[str]]:
        from engine.action_enumerator import build_action_options
        options = build_action_options(player, state, list(available))
        attacks = options.get("attack", [])
        if attacks:
            # 审计修复：枚举序把拳击放最前，影身长期用最弱武器。
            # 按命令尾部武器名解析真实伤害，伤害高者优先。
            def _weapon_damage(cmd: str) -> float:
                parts = str(cmd).rsplit(maxsplit=1)
                weapon_name = parts[-1] if len(parts) == 2 else ""
                weapon = player.get_weapon(weapon_name) \
                    if hasattr(player, "get_weapon") else None
                if weapon is None:
                    return -1.0
                try:
                    return float(weapon.get_effective_damage())
                except Exception:
                    return -1.0
            attacks = sorted(attacks, key=_weapon_damage, reverse=True)
            return list(attacks[:2]) + ["forfeit"]
        # 影身武装链（裁决 A+ 共享钱包）：无实装武器且有积蓄 → 商店买小刀
        real = [w for w in getattr(player, "weapons", [])
                if w and getattr(w, "name", "") != "拳击"]
        if not real and int(getattr(player, "credits", 0) or 0) >= 2:
            if getattr(player, "location", None) == "商店":
                interacts = options.get("interact", [])
                picks = [c for c in interacts if "小刀" in c]
                if picks:
                    return picks[:1] + ["forfeit"]
            else:
                moves = set(options.get("move", []) or [])
                if "move 商店" in moves:
                    return ["move 商店", "forfeit"]
        # 无攻击/无购物需求 → 先消费同点关系槽。远程主战武器优先 lock
        # （lock 是远程攻击前置），否则 find（面对面近战前置）。
        from models.equipment import WeaponRange
        ranged = any(
            w is not None and getattr(w, "weapon_range", None) == WeaponRange.RANGED
            for w in getattr(player, "weapons", []) or [])
        if ranged:
            locks = options.get("lock", [])
            if locks:
                return list(locks[:2]) + ["forfeit"]
        finds = options.get("find", [])
        if finds:
            return list(finds[:2]) + ["forfeit"]
        locks = options.get("lock", [])
        if locks:
            return list(locks[:2]) + ["forfeit"]
        my_loc = getattr(player, "location", None)
        density = {}
        for pid in getattr(state, "player_order", []):
            other = state.get_player(pid)
            if other is None or not other.is_alive():
                continue
            loc = getattr(other, "location", None)
            if loc and loc != my_loc:
                density[loc] = density.get(loc, 0) + 1
        if density:
            best = max(sorted(density), key=lambda loc: density[loc])
            moves = set(options.get("move", []) or [])
            if f"move {best}" in moves:
                return [f"move {best}", "forfeit"]
        return None

    def _light_override(self, player: Any, state: Any,
                        available: List[str]) -> Optional[List[str]]:
        talent = getattr(player, "talent", None)
        shadow = None
        try:
            shadow = talent._shadow() if talent is not None else None
        except Exception:
            shadow = None
        if shadow is None:
            return None  # 无影身：正常发育，T0 创建影身
        my_loc = getattr(player, "location", None)
        enemies_here = any(
            other is not None and other.is_alive()
            and getattr(other, "location", None) == my_loc
            for pid in getattr(state, "player_order", [])
            if pid != getattr(player, "player_id", None)
            for other in (state.get_player(pid),)
        )
        if not enemies_here:
            return None
        # 有底线避战（裁决）：同地点存在可击杀目标（残血无外甲）时不逃，
        # 交回通用管道收割——光身避战保护影身连坐，但白给的人头不能放
        from controllers.ai.game_query import GameQuery
        from controllers.ai.decision.value import _best_weapon
        weapon = _best_weapon(player)
        kill_dmg = max(2.0, float(getattr(weapon, "base_damage", 0) or 0))
        for pid in getattr(state, "player_order", []):
            if pid == getattr(player, "player_id", None):
                continue
            other = state.get_player(pid)
            if other is None or not other.is_alive():
                continue
            if getattr(other, "location", None) != my_loc:
                continue
            if (float(getattr(other, "hp", 0) or 0) <= kill_dmg
                    and GameQuery.count_outer_armor(other) == 0):
                return None
        if "move" in available:
            from controllers.ai.game_query import GameQuery
            safe = GameQuery.find_safe_location(player, state)
            if safe and safe != my_loc:
                return [f"move {safe}", "forfeit"]
        return None


class _G0Adapter(M9AdapterBase):
    """M9 G0：自伤 + 苟活模板的 AI 实现。

    生存优先（调整呼吸窗口/低血 → 医院治疗链），其次无人机+SP≥2 时提前
    移动到敌人最密地点——为下一轮公演十字炮火（当前地点全员 DIRECT_DAMAGE）
    布局。
    """

    slot_id = "G0"

    _HEAL_THRESHOLD_PCT = 0.4  # HP 低于 40% 才回医院治疗（自伤链需要缓冲但避免蹲医院）

    def should_override_candidates(self, player: Any, state: Any,
                                   available: List[str]) -> Optional[List[str]]:
        from engine.action_enumerator import build_action_options
        talent = getattr(player, "talent", None)
        if talent is None:
            return None

        # ── 1. 呼吸窗口（重设计 2026-09）：duration=2、forfeit 回 4、
        #    40% 止损线——先 forfeit 到跨过止损线，再恢复主战/布局；
        #    低血线仍走医院治疗链。──
        breath = bool(getattr(talent, "breath_active", False))
        hp = float(getattr(player, "hp", 0) or 0)
        max_hp = float(getattr(player, "max_hp", 20) or 20)
        if breath:
            from engine.balance import get as _bget
            pct = float(_bget(
                "m9_talents_extended", "g0",
                "breath_recovery_threshold_pct", default=40)) / 100.0
            if hp <= max_hp * pct:
                # 免疫期内 forfeit 会经 m9_on_forfeit 回血；过线前不把
                # 行动花在主战上。
                if "forfeit" in available:
                    return ["forfeit"]
                return None
            # 已过止损线：呼吸期 T0 被引擎禁用，但 T1 主战合法。
        elif hp > 0 and hp < max_hp * self._HEAL_THRESHOLD_PCT:
            loc = getattr(player, "location", None)
            if loc == "医院" and "interact" in available:
                from controllers.ai.constants import ai_wallet
                if float(ai_wallet(player) or 0) >= 2:
                    return ["interact 治疗", "forfeit"]
                return ["interact 打工", "forfeit"]  # 攒治疗费
            if "move" in available:
                moves = set(build_action_options(
                    player, state, ["move"]).get("move", []) or [])
                if "move 医院" in moves:
                    return ["move 医院", "forfeit"]
            return None

        # ── 2. 布局：SP≥2 → 敌人最密地点（十字炮火链前置：先占位，
        #       次轮召唤无人机，再轮公演开火）──
        m9 = getattr(state, "m9_system", None)
        if m9 is None or m9.get_sp(player.player_id) < 2:
            return None
        if "move" not in available:
            return None
        current = getattr(player, "location", None)
        density: Dict[str, int] = {}
        for pid in getattr(state, "player_order", []):
            if pid == getattr(player, "player_id", None):
                continue
            other = state.get_player(pid)
            if other is None or not other.is_alive():
                continue
            loc = getattr(other, "location", None)
            if loc:
                density[loc] = density.get(loc, 0) + 1
        if not density:
            return None
        best = max(sorted(density), key=lambda loc: density[loc])
        if best == current or density[best] < 2:
            return None
        moves = set(build_action_options(player, state, ["move"]).get("move", []))
        if f"move {best}" not in moves:
            return None
        # 布局后必须仍付得起十字炮火 HP 代价（20% 当前 HP + 自伤）
        from engine.balance import get as bget
        cost_pct = float(bget(
            "m9_talents_extended", "g0", "crossfire_hp_cost",
            default=20)) / 100.0
        if hp - hp * cost_pct < 1:
            return None
        return [f"move {best}", "forfeit"]


class _G6Adapter(M9AdapterBase):
    """M9 G6：持公演位且 SP≥2 时移动到敌人最密地点——为公演借用天星
    （同地点 AOE+石化）布局。重演/借用具体选择由 c_policy 处理。"""

    slot_id = "G6"

    def get_development_needs_override(self, player: Any, state: Any,
                                       needs: Optional[Dict[str, Any]] = None
                                       ) -> Optional[Dict[str, Any]]:
        """G6 重演/借用均以自身装备结算 → 需要磨刀石与第二武器提升输出。"""
        return {"needs": ["whetstone", "second_weapon"]}

    def should_override_candidates(self, player: Any, state: Any,
                                   available: List[str]) -> Optional[List[str]]:
        m9 = getattr(state, "m9_system", None)
        if m9 is None or m9.get_sp(player.player_id) < 2:
            return None
        from controllers.ai.decision.t0_policy import _is_public_holder
        if not _is_public_holder(state, player):
            return None
        # 天星不在场时冲进敌人堆没有收益，反而送血。
        try:
            from engine.m9.talents.g6 import G6Mechanics
            mech = G6Mechanics(getattr(state, "g6_template_pool", None))
            if "t3_heavenly_star" not in mech.borrowable_core_keys(state):
                return None
        except Exception:
            return None
        if "move" not in available:
            return None
        current = getattr(player, "location", None)
        density: Dict[str, int] = {}
        for pid in getattr(state, "player_order", []):
            if pid == getattr(player, "player_id", None):
                continue
            other = state.get_player(pid)
            if other is None or not other.is_alive():
                continue
            loc = getattr(other, "location", None)
            if loc:
                density[loc] = density.get(loc, 0) + 1
        if not density:
            return None
        best = max(sorted(density), key=lambda loc: density[loc])
        if best == current or density[best] < 2:
            return None
        from engine.action_enumerator import build_action_options
        moves = set(build_action_options(player, state, ["move"]).get("move", []))
        if f"move {best}" not in moves:
            return None
        return [f"move {best}", "forfeit"]


class _G3Adapter(M9AdapterBase):
    """M9 G3：公演位和捕捉对象同时存在才展开，否则保留普通投影。"""

    slot_id = "G3"

    def handle_choose(self, player: Any, state: Any, situation: str,
                      options: List[str], context: Optional[Dict] = None) -> Optional[str]:
        expand = m9_text("ai.adapters.g3.option_expand")
        projection = m9_text("ai.adapters.g3.option_projection")
        if situation == "m9_g3" \
                and expand in options \
                and projection in options:
            from controllers.ai.decision.t0_policy import (
                _g3_has_capture_target, _is_public_holder)
            if (_g3_has_capture_target(state, player)
                    and _is_public_holder(state, player)):
                return expand
            return projection
        return super().handle_choose(player, state, situation, options, context)


class _G1Adapter(M9AdapterBase):
    """M9 火萤：隔离退役 Phase hook；繁育时优先用移动触发缩圈。"""

    slot_id = "G1"

    def should_override_candidates(self, player: Any, state: Any,
                                   available: List[str]) -> Optional[List[str]]:
        talent = getattr(player, "talent", None)
        form = getattr(talent, "form", "")
        # 次级/完全燃烧的过载只在 move 根上结算；先把已就绪载荷送到
        # 敌人最密地点，避免通用攻击优先级让整局超新星保持 0 次发动。
        if (form in ("secondary", "full_burn")
                and getattr(talent, "has_supernova", False)
                and "move" in available):
            from engine.action_enumerator import build_action_options
            options = build_action_options(player, state, list(available))
            moves = set(options.get("move", []))
            density: Dict[str, int] = {}
            for pid in getattr(state, "player_order", []):
                if pid == getattr(player, "player_id", None):
                    continue
                other = state.get_player(pid)
                if other is None or not other.is_alive():
                    continue
                loc = getattr(other, "location", None)
                if loc and f"move {loc}" in moves:
                    density[loc] = density.get(loc, 0) + 1
            if density:
                destination = max(sorted(density), key=lambda loc: density[loc])
                return [f"move {destination}", "forfeit"]
        # 完全燃烧只有三轮：先消费当前已经合法的攻击；没有攻击时优先锁定，
        # 让本轮受限追加（仅 move/attack）能够接上远程攻击。最后才向敌人
        # 密集地点移动，避免额外行动在家/商店/军事基地之间往返。
        if form == "full_burn":
            from engine.action_enumerator import build_action_options
            options = build_action_options(player, state, list(available))
            attacks = options.get("attack", [])
            if attacks:
                return list(attacks) + ["forfeit"]
            locks = options.get("lock", [])
            if locks:
                return list(locks) + ["forfeit"]
            moves = set(options.get("move", []))
            density: Dict[str, int] = {}
            for pid in getattr(state, "player_order", []):
                if pid == getattr(player, "player_id", None):
                    continue
                other = state.get_player(pid)
                if other is None or not other.is_alive():
                    continue
                loc = getattr(other, "location", None)
                if loc and f"move {loc}" in moves:
                    density[loc] = density.get(loc, 0) + 1
            if density:
                destination = max(sorted(density), key=lambda loc: density[loc])
                return [f"move {destination}", "forfeit"]
        # G1 的战斗循环以外甲为着装前置。进攻人格会在出生点直接用弓
        # 进入战斗，导致整局都不满足着装门；这里先完成一次最小防护。
        if form == "armorless" and "interact" in available:
            from controllers.ai.game_query import GameQuery
            own_home = f"home_{getattr(player, 'player_id', '')}"
            if (getattr(player, "location", None) == own_home
                    and GameQuery.count_outer_armor(player) < 1):
                return ["interact 盾牌", "forfeit"]
        # 卸甲态的每轮免费 find 是不消费根槽的能力；优先消费后仍会重新
        # 进入正常 T1 决策。没有同地点合法目标时引擎不会开放该 special。
        if (form == "armorless" and "special" in available
                and hasattr(talent, "free_find_available")
                and talent.free_find_available(
                    getattr(state, "current_round", 1))):
            return ["special 卸甲免费find", "forfeit"]
        if "move" not in available:
            return None
        # 卸甲低血线时旧通用危险门仍会在医院反复打工。这里直接把“换点脱锁”
        # 放在攻击/发育之前，且不依赖同地点敌人（弓锁定可跨地点追击）。
        max_hp = float(getattr(player, "max_hp", 20) or 20)
        if form == "armorless" and float(getattr(player, "hp", 0)) <= max(4.0, max_hp * 0.25):
            from actions.move import get_all_valid_locations
            destroyed = set(getattr(state, "m9_destroyed_locations", set()) or set())
            current = getattr(player, "location", None)
            locations = [
                loc for loc in get_all_valid_locations(state)
                if loc != current and loc not in destroyed
            ]
            if locations:
                own_home = f"home_{getattr(player, 'player_id', '')}"
                destination = min(
                    locations,
                    key=lambda loc: (
                        sum(1 for pid in getattr(state, "player_order", [])
                            if pid != getattr(player, "player_id", None)
                            and state.get_player(pid) is not None
                            and state.get_player(pid).is_alive()
                            and getattr(state.get_player(pid), "location", None) == loc),
                        0 if loc == own_home else 1,
                        loc,
                    ),
                )
                return [f"move {destination}", "forfeit"]
        if form != "propagation":
            return None
        destroyed = set(getattr(state, "m9_destroyed_locations", set()) or set())
        current = getattr(player, "location", None)
        density: Dict[str, int] = {}
        for pid in getattr(state, "player_order", []):
            if pid == getattr(player, "player_id", None):
                continue
            other = state.get_player(pid)
            if other is None or not other.is_alive():
                continue
            location = getattr(other, "location", None)
            if not location or location == current or location in destroyed:
                continue
            density[location] = density.get(location, 0) + 1
        if not density:
            return None
        destination = max(sorted(density), key=lambda loc: density[loc])
        return [f"move {destination}", "forfeit"]


class _G5Adapter(M9AdapterBase):
    """M9 G5：锚定激活期主动参与博弈——优先亲自执行脚本的攻击/拾取槽，
    让预言在 R4 前自然实现（§7.2 锁定），而非依赖再投影强制或因果改写。
    """

    slot_id = "G5"

    def should_override_candidates(self, player: Any, state: Any,
                                   available: List[str]) -> Optional[List[str]]:
        talent = getattr(player, "talent", None)
        if talent is None or not getattr(talent, "active_anchor", False):
            return None
        script = getattr(talent, "anchor_script", []) or []
        slot = int(getattr(talent, "anchor_slot_index", 0) or 0)
        if not script:
            # 兼容旧状态/单测夹具：无显式脚本时从候选事件回推来源动作。
            script = [
                getattr(cand, "source_action", None)
                for cand in (getattr(talent, "anchor_candidates", []) or [])
                if cand is not None and not getattr(cand, "realized", False)
                and int(getattr(cand, "slot_index", 0) or 0) >= slot
            ]
        # 从当前槽向后执行脚本本身（不再只遍历候选）：find/lock/move 是
        # attack 槽合法性的前置，缺槽会让 ranged 攻击必落空、只能因果改写。
        from engine.action_enumerator import build_action_options
        from models.equipment import WeaponRange
        for idx in range(slot, len(script)):
            action = script[idx]
            if not isinstance(action, (list, tuple)) or not action:
                continue
            kind = action[0]
            if kind == "attack":
                target = state.get_player(action[1]) if len(action) > 1 else None
                if target is None or not target.is_alive():
                    continue  # 已死亡：latch 会锁定，不必重复
                weapon_name = action[2] if len(action) > 2 else ""
                attacks = (build_action_options(
                    player, state, ["attack"]).get("attack", []) or []
                    if "attack" in available else [])
                picks = [a for a in attacks
                         if target.name in a and weapon_name in a] or \
                    [a for a in attacks if target.name in a]
                if picks:
                    return [picks[0], "forfeit"]
                # 攻击不可用：异地先拉近；同点按武器范围补 lock（远程）/
                # find（近战）前置。
                my_loc = getattr(player, "location", None)
                tgt_loc = getattr(target, "location", None)
                if tgt_loc and tgt_loc != my_loc and "move" in available:
                    moves = set(build_action_options(
                        player, state, ["move"]).get("move", []) or [])
                    if f"move {tgt_loc}" in moves:
                        return [f"move {tgt_loc}", "forfeit"]
                if tgt_loc and tgt_loc == my_loc:
                    weapon = next(
                        (w for w in getattr(player, "weapons", [])
                         if w is not None and w.name == weapon_name), None)
                    range_kind = getattr(
                        weapon, "weapon_range", WeaponRange.MELEE)
                    if range_kind == WeaponRange.RANGED \
                            and "lock" in available:
                        locks = build_action_options(
                            player, state, ["lock"]).get("lock", []) or []
                        picks = [c for c in locks if target.name in c]
                        if picks:
                            return [picks[0], "forfeit"]
                    if "find" in available:
                        finds = build_action_options(
                            player, state, ["find"]).get("find", []) or []
                        picks = [c for c in finds if target.name in c]
                        if picks:
                            return [picks[0], "forfeit"]
            elif kind == "find":
                target = state.get_player(action[1]) if len(action) > 1 else None
                if target is not None and target.is_alive() \
                        and "find" in available:
                    finds = build_action_options(
                        player, state, ["find"]).get("find", []) or []
                    picks = [c for c in finds if target.name in c]
                    if picks:
                        return [picks[0], "forfeit"]
            elif kind == "lock":
                target = state.get_player(action[1]) if len(action) > 1 else None
                if target is not None and target.is_alive() \
                        and "lock" in available:
                    locks = build_action_options(
                        player, state, ["lock"]).get("lock", []) or []
                    picks = [c for c in locks if target.name in c]
                    if picks:
                        return [picks[0], "forfeit"]
            elif kind == "move":
                dest = action[1] if len(action) > 1 else ""
                if dest and dest != getattr(player, "location", None) \
                        and "move" in available:
                    moves = set(build_action_options(
                        player, state, ["move"]).get("move", []) or [])
                    if f"move {dest}" in moves:
                        return [f"move {dest}", "forfeit"]
            elif kind == "interact" and "interact" in available:
                obj = action[1] if len(action) > 1 else ""
                if obj:
                    interacts = build_action_options(
                        player, state, ["interact"]).get("interact", []) or []
                    picks = [c for c in interacts if obj in c]
                    if picks:
                        return [picks[0], "forfeit"]
        return None


class _T6Adapter(M9AdapterBase):
    """C 层简单版：热线举报 / 竞选队长 / 指挥X移动 三 special 时机。"""

    slot_id = "T6"

    def should_override_candidates(self, player: Any, state: Any,
                                   available: List[str]) -> Optional[List[str]]:
        """联防整备可达性：队长/队长候选 + 基础装备齐 + SP≥1 且不与
        警员同地点时，移动到警员所在地（整备核心的 T0 前置门）。"""
        police = getattr(state, "m9_police", None)
        if police is None:
            return None
        try:
            if police.is_disabled():
                return None
        except Exception:
            pass
        captain = getattr(police, "captain_id", None)
        if captain not in (None, player.player_id):
            return None  # 警察归别人管：整备不是我的路线
        m9 = getattr(state, "m9_system", None)
        if m9 is None or m9.get_sp(player.player_id) < 1:
            return None
        from controllers.ai.game_query import GameQuery
        if not GameQuery.has_real_weapon(player) \
                or GameQuery.count_outer_armor(player) < 1:
            return None  # 基础装备未齐：先发育，不专程绕路
        if float(getattr(player, "hp", 0)) <= 5:
            return None  # 危险状态不绕路
        if getattr(self._ctrl, "_in_combat", False):
            return None  # 战斗中不脱离战线
        my_loc = getattr(player, "location", None)
        roster = getattr(police, "_roster", None) or getattr(police, "roster", []) or []
        alive = [u for u in roster if getattr(u, "alive", True)]
        if any(getattr(u, "location", None) == my_loc for u in alive):
            return None  # 已与警员同地点
        if not alive:
            return None
        target_loc = max(
            {getattr(u, "location", None) for u in alive
             if getattr(u, "location", None)},
            key=lambda loc: sum(1 for u in alive
                                if getattr(u, "location", None) == loc),
        )
        if target_loc == my_loc or "move" not in available:
            return None
        from engine.action_enumerator import build_action_options
        moves = set(build_action_options(player, state, ["move"]).get("move", []))
        if f"move {target_loc}" not in moves:
            return None
        return [f"move {target_loc}", "forfeit"]

    def get_talent_special_candidates(self, player: Any, state: Any,
                                      available: List[str],
                                      snapshot: Any = None) -> List[str]:
        if "special" not in available:
            return []
        police = getattr(state, "m9_police", None)
        if police is None:
            return []
        try:
            if police.is_disabled():
                return []
        except Exception:
            pass
        out: List[str] = []
        personality = str(getattr(self._ctrl, "personality", "balanced")
                          or "balanced")
        # 1. 热线举报：只能在没有现行通缉时，用已经绑定到事件的证据建立新案。
        # 旧逻辑反向地在“已有通缉”时重复举报同一人，而引擎必然拒绝该候选。
        try:
            wanted = police.open_wanted()
        except Exception:
            wanted = None
        talent = getattr(player, "talent", None)
        hotline_cmds: List[str] = []
        if wanted is None and talent is not None \
                and hasattr(talent, "_evidence_for"):
            threats = getattr(self._ctrl, "_threat_scores", None) or {}
            suspects = []
            for pid in getattr(state, "player_order", []):
                if pid == player.player_id:
                    continue
                suspect = state.get_player(pid)
                if suspect is None or not suspect.is_alive():
                    continue
                try:
                    _, valid = talent._evidence_for(pid)
                except Exception:
                    valid = False
                if valid:
                    suspects.append(suspect)
            suspects.sort(
                key=lambda suspect: threats.get(suspect.name, 0.0),
                reverse=True)
            hotline_cmds = [f"special 热线举报{suspect.name}"
                            for suspect in suspects]
        # 2. 竞选队长：队长空缺且非防守人格（R2 判定上任）。
        #    警察线收益链以队长为根，无队长时优先竞选、热线随后（P2 裁决）。
        try:
            captain = police.captain_id
        except Exception:
            captain = None
        if captain is None and personality not in ("defensive", "builder"):
            out.append("special 竞选队长")
        out.extend(hotline_cmds)
        # 3. 指挥：攻击优先（通缉目标与警员同地点 → 立即执法，警察线真输出），
        #    否则移动调度（指挥X移动：我即队长且存在不在警察局的警员）
        if captain == player.player_id:
            wanted = None
            try:
                wanted = police.open_wanted()
            except Exception:
                wanted = None
            suspect = None
            if wanted is not None:
                suspect = state.get_player(wanted.suspect_id)
            roster = police.alive_units() if hasattr(
                police, "alive_units") else (getattr(police, "_roster", []) or [])
            for unit in roster:
                if not getattr(unit, "alive", True):
                    continue
                if suspect is not None and suspect.is_alive() \
                        and getattr(unit, "location", None) \
                        == getattr(suspect, "location", None):
                    out.append(f"special 指挥{getattr(unit, 'unit_id', 'unit1')}攻击")
                    continue
                if getattr(unit, "location", None) == "警察局":
                    continue
                out.append(f"special 指挥{getattr(unit, 'unit_id', 'unit1')}移动")
                break
        return out


# M9 专属 adapter 入口（覆盖此前无专属 M9 决策的槽位；可扩展）
_M9_ADAPTER_CLASSES: Tuple[type, ...] = (
    _G0Adapter, _G1Adapter, _G2Adapter, _G3Adapter, _G5Adapter, _G6Adapter,
    _T6Adapter)


def build_slot_hook_map(controller: Any) -> Dict[Tuple[str, str], Any]:
    """(profile, slot_id) → M9 adapter 实例。"""
    return {
        ("m9-rfc", cls.slot_id): cls(controller)
        for cls in _M9_ADAPTER_CLASSES
    }


def resolve_talent_hook(controller: Any, player: Any) -> Optional[Any]:
    """按 (profile, slot_id) 优先分派；回退显示名（v2exp 兼容）。

    所有者附属 actor（G2 影身等）自身 talent 为 None，回退到所属玩家的
    天赋解析——让影身的决策也走 G2 adapter 的分工策略。
    """
    talent = getattr(player, "talent", None)
    if talent is None:
        owner_pid = getattr(player, "owner_pid", None)
        state = getattr(controller, "_game_state", None)
        if owner_pid and state is not None:
            owner = state.get_player(owner_pid)
            talent = getattr(owner, "talent", None) if owner is not None else None
    if talent is None:
        return None
    slot_map = getattr(controller, "_slot_hook_instances", {})
    state = getattr(controller, "_game_state", None)
    hook = slot_map.get((profile_of(state), slot_id_of(talent)))
    if hook is not None:
        return hook
    name_map = getattr(controller, "_talent_hook_instances", {})
    return name_map.get(getattr(talent, "name", ""))
