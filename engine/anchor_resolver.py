"""
锚定路径验证器
用假人系统模拟：发动者能否在5个无干扰回合内完成锚定事件
只负责：可行性验证 + 命数计算
不负责：破坏性行动判定（交给DM）
"""

import math
from dataclasses import dataclass, field
from typing import Optional, List

from utils.attribute import Attribute, is_effective
from models.equipment import WeaponRange


@dataclass
class AnchorVerification:
    """锚定验证结果"""
    feasible: bool          # 是否可行
    fate: int               # 命数
    variance: int           # 变数
    path_description: list  # 每回合做什么
    reason: str             # 不可行时的原因
    attr_counts: dict = field(default_factory=dict)  # 路径属性命中数（m7 开拓·防御公式用）


class AnchorVerifier:
    """
    用假人模拟验证锚定可行性

    核心：发动者独占5个回合，无干扰，能否完成事件？
    移动规则：任意地点→任意地点 = 1回合
    """

    def __init__(self, game_state):
        self.state = game_state

    # ================================================================
    #  公开接口
    # ================================================================

    def verify_kill(self, caster, target) -> AnchorVerification:
        """验证击杀类锚定"""
        return self._simulate_combat_anchor(caster, target, goal="kill")

    def verify_break_armor(self, caster, target,
                            armor_description: str) -> AnchorVerification:
        """验证攻击护甲类锚定"""
        return self._simulate_combat_anchor(
            caster, target, goal="break_armor",
            target_armor_desc=armor_description
        )

    def verify_acquire(self, caster, item_name: Optional[str]) -> AnchorVerification:
        """获取类：不需要命数，5轮存活即成功"""
        return AnchorVerification(
            feasible=True, fate=0, variance=5,
            path_description=["获取类事件：5轮后存活即成功"],
            reason="获取类不需要命数",
        )

    def verify_arrive(self, caster, target_location: str) -> AnchorVerification:
        """到达类：不需要命数，5轮存活即成功"""
        return AnchorVerification(
            feasible=True, fate=0, variance=5,
            path_description=["到达类事件：5轮后存活即成功"],
            reason="到达类不需要命数",
        )

    # ================================================================
    #  假人模拟
    # ================================================================

    def _simulate_combat_anchor(self, caster, target,
                                 goal="kill",
                                 target_armor_desc=None,
                                 sequence=None) -> AnchorVerification:
        """
        模拟5回合无干扰，每回合做一件事：移动 或 攻击
        现在考虑攻击前置条件：近战需要find，远程需要lock

        m7_talents 开 → 走信源统一评估器（anchor_eval，hp20 正确）；
        关 → 保留下方旧闭式公式（v1 字节不变）。
        """
        from talents.talent_balance import m7_enabled
        if m7_enabled():
            return self._eval_verify(caster, target, goal,
                                     target_armor_desc=target_armor_desc,
                                     sequence=sequence)
        # 1. 选最优武器
        best_weapon = self._pick_best_weapon(caster, target)
        if best_weapon is None:
            return AnchorVerification(
                feasible=False, fate=0, variance=0,
                path_description=[],
                reason="没有能有效打击目标的武器",
            )

        damage_per_hit = best_weapon.base_damage

        # 2. 算要打多少HP
        if goal == "kill":
            hp_to_deplete = self._total_effective_hp(target)
        elif goal == "break_armor":
            if target_armor_desc is None:
                return AnchorVerification(
                    feasible=False, fate=0, variance=0,
                    path_description=[],
                    reason="破坏护甲目标必须指定护甲描述",
                )
            armor_piece = self._find_armor_by_desc(target, target_armor_desc)
            if armor_piece is None:
                return AnchorVerification(
                    feasible=False, fate=0, variance=0,
                    path_description=[],
                    reason=f"目标没有该护甲：{target_armor_desc}",
                )
            hp_to_deplete = armor_piece.current_hp
        else:
            return AnchorVerification(
                feasible=False, fate=0, variance=0,
                path_description=[], reason=f"未知目标：{goal}",
            )

        # 3. 攻击回合数
        if damage_per_hit <= 0:
            return AnchorVerification(
                feasible=False, fate=0, variance=0,
                path_description=[],
                reason="武器伤害为0",
            )
        attack_rounds = math.ceil(hp_to_deplete / damage_per_hit)

        # 4. 计算前置回合数（移动、find、lock）
        prep_rounds = self._calculate_prep_rounds(caster, target, best_weapon)

        # 5. 命数
        fate = prep_rounds + attack_rounds

        # 6. 可行？
        if fate > 5:
            return AnchorVerification(
                feasible=False, fate=fate, variance=5 - fate,
                path_description=self._build_path_desc(
                    caster, target, best_weapon,
                    prep_rounds, attack_rounds, goal
                ),
                reason=f"命数{fate}超过5回合上限",
            )

        variance = 5 - fate

        return AnchorVerification(
            feasible=True,
            fate=fate,
            variance=variance,
            path_description=self._build_path_desc(
                caster, target, best_weapon,
                prep_rounds, attack_rounds, goal
            ),
            reason="可行",
        )

    # ================================================================
    #  hp20 评估器路径（m7，信源统一）
    # ================================================================

    def verify_sequence(self, caster, target, goal: str, sequence: list,
                        target_armor_desc=None) -> AnchorVerification:
        """判断**发动者自传的动作序列**能否在 horizon 内达成事件（人类自定路径）。

        G5 的特权：不认神谕发的牌，就自己编一条路。AI 永不走此路（只用神谕底线/模板）。
        """
        return self._eval_verify(caster, target, goal,
                                 target_armor_desc=target_armor_desc,
                                 sequence=sequence)

    def _eval_verify(self, caster, target, goal,
                     target_armor_desc=None, sequence=None) -> AnchorVerification:
        """信源统一评估：候选序列 = 自传序列 OR（预留模板 + 直取底线）→ 取 min-命数 可行者。"""
        from engine import anchor_eval, anchor_templates
        from engine.balance import get as bget

        horizon = int(bget("anchor", "feasibility_horizon", default=8))
        break_piece = None
        if goal == "break_armor":
            piece = self._find_armor_by_desc(target, target_armor_desc)
            if piece is None:
                return AnchorVerification(
                    feasible=False, fate=0, variance=0, path_description=[],
                    reason=f"目标没有该护甲：{target_armor_desc}")
            break_piece = piece.name

        weapons = anchor_eval.resolve_weapons(caster)     # 评估器弓感知（弓走 compute_shot）
        if sequence is not None:
            candidates = [sequence]                       # 人类自定路径
        else:
            # 预留模板（当前空）+ 直取底线（神谕默认牌）
            candidates = anchor_templates.propose_paths(
                self.state, caster, target, goal, break_piece)
            candidates.append(anchor_eval.build_direct_sequence(
                self.state, caster, target, weapons,
                goal=goal, break_piece=break_piece, horizon=horizon))

        best = None
        for seq in candidates:
            res = anchor_eval.simulate_path(
                target, weapons, seq, goal=goal,
                break_piece=break_piece, horizon=horizon)
            if res.achieved and (best is None or res.rounds < best.rounds):
                best = res

        if best is None:
            probe = anchor_eval.simulate_path(
                target, weapons, candidates[-1], goal=goal,
                break_piece=break_piece, horizon=horizon)
            return AnchorVerification(
                feasible=False, fate=probe.rounds, variance=0,
                path_description=list(probe.log),
                reason=f"命数 {probe.rounds} 超过可行上限 {horizon}",
                attr_counts=dict(probe.attr_counts))

        # 变数：G5b 暂用 horizon - 命数（G5c 改为 variance_base + (K - 命数)）
        variance = max(0, horizon - best.rounds)
        return AnchorVerification(
            feasible=True, fate=best.rounds, variance=variance,
            path_description=list(best.log), reason="可行",
            attr_counts=dict(best.attr_counts))

    def _calculate_prep_rounds(self, caster, target, weapon):
        """计算前置回合数：移动、find、lock"""
        prep_rounds = 0

        # 检查武器射程
        weapon_range = getattr(weapon, 'weapon_range', None)

        # 移动回合：只有近战武器需要同位置
        if weapon_range == WeaponRange.MELEE:
            if caster.location != target.location:
                prep_rounds += 1  # 移动回合
            # 检查是否已有engaged关系
            if not self._has_engaged(caster, target):
                prep_rounds += 1  # find回合
        elif weapon_range == WeaponRange.RANGED:
            # 远程武器需要lock
            if not self._has_locked(caster, target):
                prep_rounds += 1  # lock回合
            # 远程不需要移动（假设目标可见）
        else:
            # 范围武器或其他类型，默认不需要前置
            pass

        return prep_rounds

    def _has_engaged(self, caster, target):
        """检查是否已有engaged关系"""
        if not hasattr(self.state, 'markers'):
            return False
        markers = self.state.markers
        if hasattr(markers, 'has_relation'):
            return markers.has_relation(caster.player_id, "ENGAGED_WITH", target.player_id)
        return False

    def _has_locked(self, caster, target):
        """检查是否已有locked关系"""
        if not hasattr(self.state, 'markers'):
            return False
        markers = self.state.markers
        if hasattr(markers, 'has_relation'):
            # 检查目标是否被发动者锁定
            return markers.has_relation(target.player_id, "LOCKED_BY", caster.player_id)
        return False

    # ================================================================
    #  辅助
    # ================================================================

    def _pick_best_weapon(self, caster, target):
        """选对目标最有效的武器（考虑属性克制）"""
        target_armor_attr = self._get_outermost_armor_attr(target)

        best = None
        best_dmg = 0

        for weapon in getattr(caster, 'weapons', []):
            if target_armor_attr is not None:
                if not is_effective(weapon.attribute, target_armor_attr):
                    continue
            if weapon.base_damage > best_dmg:
                best_dmg = weapon.base_damage
                best = weapon

        return best

    def _get_outermost_armor_attr(self, player):
        """最外层护甲属性（外层护甲优先，按priority取最外）"""
        if not hasattr(player, 'armor') or not player.armor:
            return None
        try:
            from models.equipment import ArmorLayer

            outer = player.armor.get_active(ArmorLayer.OUTER)
            if not outer:
                return None
            outer.sort(key=lambda a: getattr(a, 'priority', 0), reverse=True)
            return outer[0].attribute
        except Exception:
            return None

    def _total_effective_hp(self, player) -> float:
        """总有效HP = 血量 + 所有护甲HP"""
        total = player.hp
        if hasattr(player, 'armor') and player.armor and hasattr(player.armor, 'get_all_active'):
            for a in player.armor.get_all_active():
                total += a.current_hp
        return total

    def _find_armor_by_desc(self, player, description: str):
        """根据描述找护甲（仅匹配未破碎护甲）"""
        if not hasattr(player, 'armor') or not player.armor:
            return None
        if not hasattr(player.armor, 'get_all_active'):
            return None
        desc_lower = description.lower()
        for a in player.armor.get_all_active():
            if (
                a.name.lower() in desc_lower
                or desc_lower in a.name.lower()
            ):
                return a
        return None

    def _build_path_desc(self, caster, target, weapon,
                          prep_rounds, attack_rounds, goal) -> list:
        """生成路径描述"""
        path = []
        round_num = 1

        # 前置回合描述
        weapon_range = getattr(weapon, 'weapon_range', None)

        # 移动回合（仅近战需要）
        if weapon_range == WeaponRange.MELEE and caster.location != target.location:
            path.append(
                f"回合{round_num}：从 {caster.location} "
                f"移动到 {target.location}"
            )
            round_num += 1

        # find/lock回合
        if weapon_range == WeaponRange.MELEE and not self._has_engaged(caster, target):
            path.append(
                f"回合{round_num}：找到 {target.name}（建立面对面）"
            )
            round_num += 1
        elif weapon_range == WeaponRange.RANGED and not self._has_locked(caster, target):
            path.append(
                f"回合{round_num}：锁定 {target.name}"
            )
            round_num += 1

        goal_text = "击杀" if goal == "kill" else "破坏护甲"
        for i in range(attack_rounds):
            if i == attack_rounds - 1:
                path.append(
                    f"回合{round_num}：用 {weapon.name} "
                    f"攻击 {target.name}（完成{goal_text}）"
                )
            else:
                path.append(
                    f"回合{round_num}：用 {weapon.name} "
                    f"攻击 {target.name}"
                )
            round_num += 1

        return path


# ================================================================
#  便捷函数
# ================================================================

def verify_anchor(game_state, caster, anchor_type: str,
                   target=None, item_name: Optional[str]=None,
                   target_location: Optional[str]=None,
                   armor_description: Optional[str]=None) -> AnchorVerification:
    """
    一步验证锚定可行性

    用法：
        result = verify_anchor(gs, caster, "kill", target=enemy)
        if result.feasible:
            print(f"命数{result.fate}，变数{result.variance}")
            for step in result.path_description:
                print(f"  {step}")
    """
    v = AnchorVerifier(game_state)

    if anchor_type == "kill":
        return v.verify_kill(caster, target)
    elif anchor_type == "break_armor":
        if armor_description is None:
            return AnchorVerification(
                feasible=False, fate=0, variance=0,
                path_description=[],
                reason="破坏护甲类型必须指定护甲描述",
            )
        return v.verify_break_armor(caster, target, armor_description)
    elif anchor_type == "acquire":
        return v.verify_acquire(caster, item_name)
    elif anchor_type == "arrive":
        if target_location is None:
            return AnchorVerification(
                feasible=False, fate=0, variance=0,
                path_description=[],
                reason="到达类型必须指定目标位置",
            )
        return v.verify_arrive(caster, target_location)
    else:
        return AnchorVerification(
            feasible=False, fate=0, variance=0,
            path_description=[],
            reason=f"未知锚定类型：{anchor_type}",
        )