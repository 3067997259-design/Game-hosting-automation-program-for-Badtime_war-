"""
DevelopMind —— 发育优先级、地点选择、完成判定

职责：
- 根据 Strategy 的 needs_order 评估当前发育需求
- 选择最优发育地点
- 判断发育是否完成

纯函数分析器，不保持跨轮次状态。
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Set

from controllers.ai.strategies.base_strategy import DecisionPhase
from controllers.ai.minds.base import BaseMind, MindAssessment
from controllers.ai.constants import (
    NEED_PROVIDERS, LOCATION_ITEMS, LOCATIONS, need_providers_for,
    ai_wallet, m4_item_price,
    debug_ai_development_plan,
)


class DevelopMind(BaseMind):
    """发育分析器。"""

    def assess(
        self,
        player: Any,
        state: Any,
        strategy: Any,
        talent_hooks: Optional[Dict[str, Any]] = None,
        ctx=None,
        snapshot: Optional[Any] = None,
        assessment: Optional[Any] = None,
    ) -> MindAssessment:
        """分析发育态势。

        Returns MindAssessment with data:
            - development_complete: bool
            - needs: List[str] 发育需求优先级列表
            - unmet_needs: List[(need, providers)] 未满足的需求
            - best_location: Optional[str] 最优发育地点
            - current_location_actions: List[str] 当前地点可执行的交互
            - best_move: Optional[str] 推荐移动指令
        """
        my_loc = self._query.get_location_str(player)
        wallet = ai_wallet(player)
        has_pass = getattr(player, 'has_military_pass', False)

        # 发育完成判定（委托给 Strategy）
        development_complete = strategy.is_development_complete(
            player, state,
            count_outer_armor=self._query.count_outer_armor,
            count_inner_armor=self._query.count_inner_armor,
            has_real_weapon=self._query.has_real_weapon(player),
            has_pass=has_pass,
            has_stealth=self._query.has_stealth(player),
            real_weapon_count=self._query.count_real_weapons(player),
        )

        if development_complete:
            return MindAssessment(
                mind_name="develop",
                urgency=0,
                phase=DecisionPhase.DEVELOP,
                summary="发育已完成",
                data={
                    "development_complete": True,
                    "needs": [],
                    "unmet_needs": [],
                    "best_location": None,
                    "current_location_actions": [],
                    "best_move": None,
                },
            )

        # 获取发育需求优先级
        needs_order = strategy.get_development_needs_order()

        # 天赋钩子覆盖：注入额外需求（如 T1 需要磨刀石；M9 slot adapter 走 __slot__ 键）
        if talent_hooks:
            talent_name = getattr(getattr(player, 'talent', None), 'name', '')
            hook = talent_hooks.get(talent_name) or talent_hooks.get("__slot__")
            if hook and hasattr(hook, 'get_development_needs_override'):
                try:
                    override = hook.get_development_needs_override(player)
                    if override:
                        extra = override.get("needs") \
                            if isinstance(override, dict) else override
                        if extra:
                            needs_order = self._merge_override_needs(
                                needs_order, list(extra))
                except Exception:
                    pass

        # 计算未满足的需求及满足途径
        unmet = self._compute_unmet_needs(player, state, needs_order)

        # 选择最优发育地点
        best_location = self._pick_ideal_destination(
            player, state, unmet, snapshot=snapshot)

        # 当前地点的可用交互
        current_actions = self._get_interact_commands_at_location(
            player, state, my_loc, unmet, wallet, has_pass
        )

        # 推荐移动（标准化后比较，home_p5 应视为已在 home）
        best_move = None
        if (not current_actions and best_location
                and self._query.normalize_location(best_location) != self._query.normalize_location(my_loc)):
            best_move = f"move {best_location}"

        # ── 快照化派生输出：地点拥挤度写入 AssessmentLayer ──
        self._snapshot_derived(snapshot, assessment, my_loc)

        return MindAssessment(
            mind_name="develop",
            urgency=max(5, 10 - len(unmet) * 2) if unmet else 0,
            phase=DecisionPhase.DEVELOP,
            summary=(
                "发育已完成" if development_complete else
                f"发育未完成: 缺{len(unmet)}项" if unmet else
                "发育未完成(策略判定)"
            ),
            data={
                "development_complete": development_complete,
                "needs": needs_order,
                "unmet_needs": unmet,
                "best_location": best_location,
                "current_location_actions": current_actions,
                "best_move": best_move,
            },
        )

    # ════════════════════════════════════════════════════════
    #  需求计算
    # ════════════════════════════════════════════════════════

    # ── 快照化派生输出（2026-08-12）──
    def _snapshot_derived(self, snapshot, assessment, my_loc):
        """用 ProjectedSnapshot 计算发育相关派生字段写入 AssessmentLayer。

        新增字段不改变现有决策（行为不变）：
        - notes["location_crowding"]：各地点玩家数（拥挤度，未来目的地评估输入）。
        """
        if snapshot is None or assessment is None:
            return
        crowding = {}
        for loc, units in snapshot.location_occupancy.items():
            players = sum(1 for u in units if u.kind == "player")
            if players > 0:
                crowding[loc] = players
        assessment.notes["location_crowding"] = crowding
        if my_loc:
            assessment.notes["my_location"] = my_loc

    @staticmethod
    def _merge_override_needs(needs_order: List[str], override: List[str]) -> List[str]:
        """将天赋覆盖需求合并到策略需求列表中。
        'whetstone' 插在 weapon 相关需求之后、outer_armor 之前。
        """
        result = list(needs_order)
        for need in override:
            if need not in result:
                if need == "whetstone":
                    insert_after = None
                    for key in ("second_weapon", "weapon"):
                        if key in result:
                            insert_after = key
                            break
                    if insert_after:
                        idx = result.index(insert_after) + 1
                        result.insert(idx, need)
                    else:
                        result.append(need)
                else:
                    result.append(need)
        return result

    def _compute_unmet_needs(self, player, state, needs_order: List[str]) -> List[tuple]:
        """计算未满足的发育需求，返回 [(need_type, providers_list)]"""
        unmet = []
        wallet = ai_wallet(player)
        has_pass = getattr(player, 'has_military_pass', False)
        learned = getattr(player, 'learned_spells', set())
        weapons = getattr(player, 'weapons', [])
        real_weapons = [w for w in weapons if w and getattr(w, 'name', '') != "拳击"]
        outer = self._query.count_outer_armor(player)

        for need in needs_order:
            providers = need_providers_for(need)
            if not providers:
                continue
            if self._is_need_met(player, need, real_weapons, outer, wallet, has_pass, learned):
                continue
            # 过滤掉不可达的提供者
            available = [
                (loc, item, cost) for loc, item, cost in providers
                if self._can_afford(player, cost, wallet, has_pass, loc, item)
            ]
            if available:
                unmet.append((need, available))
        return unmet

    def _is_need_met(self, player, need: str, real_weapons, outer, wallet, has_pass, learned) -> bool:
        if need == "voucher":
            return wallet >= 1
        elif need == "weapon":
            return len(real_weapons) >= 1
        elif need == "second_weapon":
            return len(real_weapons) >= 2
        elif need == "outer_armor":
            return outer >= 1
        elif need == "second_outer_armor":
            return outer >= 2
        elif need == "detection":
            return getattr(player, 'has_detection', False)
        elif need == "inner_armor":
            return self._query.count_inner_armor(player) >= 1
        elif need == "stealth":
            return self._query.has_stealth(player)
        elif need == "military_pass":
            return has_pass
        elif need == "whetstone":
            return any(getattr(it, 'name', '') == "磨刀石" for it in getattr(player, 'items', []))
        return False
    # ════════════════════════════════════════════════════════
    #  地点选择
    # ════════════════════════════════════════════════════════

    def _pick_ideal_destination(self, player, state, unmet: List[tuple],
                                snapshot=None) -> Optional[str]:
        """选择最优发育地点"""
        my_loc = self._query.get_location_str(player)
        my_loc_norm = self._query.normalize_location(my_loc)

        # 对所有未满足需求的所有提供者地点评分
        scores: Dict[str, float] = {}
        for need_type, providers in unmet:
            for loc, item, cost in providers:
                # ★ 已拥有的物品不计入该地点的吸引力
                if self._already_has_item(player, item):
                    continue
                score = self._score_location(
                    loc, player, state, snapshot=snapshot)
                # 当前位置加分（归一化比较）
                if self._query.normalize_location(loc) == my_loc_norm:
                    score += 50
                scores[loc] = max(scores.get(loc, 0), score)

        if not scores:
            # 没有需要发育的地点 — 不是受阻，是已满足
            # 移动到安全地点是 FALLBACK 阶段的职责，不是 DEVELOP 的
            return None

        best = max(scores, key=scores.get)
        # 如果最优地点就是当前位置（归一化），返回 None（不移动）
        if self._query.normalize_location(best) == my_loc_norm:
            return None
        return best

    def _score_location(self, location: str, player, state,
                        snapshot=None) -> float:
        """对发育地点评分：人越少越好。
        快照优先（location_occupancy 玩家数；home 变体保留旧读法）。"""
        enemies = 0
        if snapshot is not None and location != "home":
            units = snapshot.location_occupancy.get(location, ())
            enemies = sum(1 for u in units
                          if u.kind == "player"
                          and u.uid != snapshot.actor_id
                          and u.alive)
        else:
            enemies = self._query.count_enemies_at(location, player, state)
        base = 100.0
        base -= enemies * 25  # 每个敌人扣25分
        return max(base, 10.0)

    # ════════════════════════════════════════════════════════
    #  当前地点交互
    # ════════════════════════════════════════════════════════

    def _get_interact_commands_at_location(
        self, player, state, location: str, unmet: List[tuple],
        vouchers: int, has_pass: bool
    ) -> List[str]:
        """获取当前地点可直接执行的交互指令"""
        commands = []
        # ★ 归一化：home_p5 → home，便于查询 LOCATION_ITEMS
        norm_loc = self._query.normalize_location(location)
        items = LOCATION_ITEMS.get(norm_loc, [])
        learned = getattr(player, 'learned_spells', set())
        wallet = ai_wallet(player)
        from engine import experiments
        m4 = experiments.is_enabled("m4_gear")

        for need_type, providers in unmet:
            for loc, item, cost in providers:
                # ★ 标准化位置比较：home_p5 应匹配 home
                if self._query.normalize_location(loc) != self._query.normalize_location(location):
                    continue
                # ★ 已学会的法术跳过（必须在任何 add 之前检查）
                if item in learned:
                    continue
                # ★ 已拥有的护甲/武器跳过（避免 "已有同名护甲" 错误）
                if self._already_has_item(player, item):
                    continue
                if not self._can_afford(player, cost, wallet, has_pass, loc, item):
                    # 买不起但能打工 → 打工（m4 按价格差判断，legacy 按 vouchers<1）
                    if location in ("商店", "医院"):
                        need_work = True
                        if m4:
                            price = m4_item_price(location, item)
                            need_work = price > 0 and wallet < price
                        else:
                            need_work = wallet < 1
                        if need_work:
                            cmd = "interact 打工"
                            if cmd not in commands:
                                commands.append(cmd)
                    continue
                if item in items:
                    cmd = f"interact {item}"
                    if cmd not in commands:
                        commands.append(cmd)
                # 魔法所/魔法类法术
                if item in ("魔法弹幕", "远程魔法弹幕", "魔法护盾", "封闭", "地震", "地动山摇", "隐身术", "探测魔法"):
                    if location == "魔法所":
                        cmd = f"interact {item}"
                        if cmd not in commands:
                            commands.append(cmd)

        return commands[:3]  # 最多3个

    # ════════════════════════════════════════════════════════
    #  工具方法
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _already_has_item(player, item_name: str) -> bool:
        """检查玩家是否已拥有同名装备（护甲/武器），避免建议重复获取"""
        # 护甲（使用旧代码的 get_all_active() 接口）
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_all_active'):
            for piece in armor.get_all_active():
                if getattr(piece, 'name', '') == item_name:
                    return True
        # 武器
        for w in getattr(player, 'weapons', []):
            if w and getattr(w, 'name', '') == item_name:
                return True
        return False
    @staticmethod
    def _can_afford(player, cost: str, wallet: int, has_pass: bool,
                    location: str, item: str = "") -> bool:
        if item == "打工":
            return True  # 打工是收入来源，恒可执行
        if cost == "free":
            from engine import experiments
            if experiments.is_enabled("m4_gear"):
                # “free”标签是旧凭证模型遗留：m4 下部分条目实际收费
                # （通行证/手术），按真实价格校验，避免无限追逐买不起的目标。
                price = m4_item_price(location, item)
                return price <= 0 or wallet >= price
            return True
        if cost in ("voucher", "voucher_consume"):
            from engine import experiments
            if experiments.is_enabled("m4_gear"):
                price = m4_item_price(location, item)
                return price <= 0 or wallet >= price
            return wallet >= 1
        if cost == "pass":
            return has_pass
        return False
