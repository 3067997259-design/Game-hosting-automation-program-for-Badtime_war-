"""
神代天赋2：请一直，注视着我

主动1次，T0启动，消耗行动回合。
在所在地点展开「全息影像」持续3轮。

释放瞬间：
  - 所有非玩家单位（警察等）强制移动到影像位置，眩晕解除
  - 进入「沉沦」（无法行动/移动/被唤醒）

影像内通用：
  - 隐身无效
  - 受伤时额外+0.5伤害

其他玩家（非发动者）在影像内：
  - 无法执行「锁定」和「找到」
  - 进入影像瞬间：自动与发动者建立面对面
  - 连续停留2轮 → 震荡

发动者在影像内：
  - 免疫上述限制（但隐身仍被破除）

消失时：
  - 非玩家单位解除沉沦
  - 所有「由影像产生的锁定/面对面标记」清除

倒计时：R4递减 3→2→1→0消失
"""

from talents.base_talent import BaseTalent
from cli import display
from engine.prompt_manager import prompt_manager


class Hologram(BaseTalent):
    name = "请一直，注视着我"
    description = "主动1次：展开3轮全息影像。隐身无效/+0.5伤害/禁锁定找到/自动面对面。"
    tier = "神代"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)

        # 使用状态
        self.used = False
        self.max_uses = 1  # 涟漪献诗可+1

        # 影像状态
        self.active = False
        self.location = None          # 影像所在地点
        self.remaining_rounds = 0     # 剩余轮次

        # 连续停留追踪：{player_id: 连续轮数}
        self.stay_count = {}

        # 影像产生的标记，消失时清除
        self.hologram_markers = []    # [(p1_id, p2_id, marker_type), ...]

        # 沉沦的非玩家单位ID列表（存储unit_id字符串）
        self.submerged_npcs = []

        # 涟漪献诗增强
        self.enhanced = False         # True时易伤+1（而非+0.5）

    # ============================================
    #  T0选项
    # ============================================

    def get_t0_option(self, player):
        if player.player_id != self.player_id:
            return None
        if self.used and self.max_uses <= 0:
            return None
        if self.active:
            return None  # 已有一个影像展开中
        return f"发动天赋：{self.name}（在当前地点展开全息影像，持续3轮）"

    def execute_t0(self, player):
        if self.active:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g2eternity.already_active",
                    default="全息影像已经展开中。"
                )
            )
            return None, "cancelled"

        self.active = True
        self.location = player.location
        self.remaining_rounds = self._get_initial_duration()
        self.stay_count = {}
        self.hologram_markers = []
        self.submerged_npcs = []

        if self.max_uses > 0:
            self.max_uses -= 1
        if self.max_uses <= 0:
            self.used = True

        lines = [
            f"\n{'='*50}",
            prompt_manager.get_prompt(
                "talent", "g2eternity.activation_header",
                default="  \U0001f441 {player_name} 展开了「全息影像」！"
            ).format(player_name=player.name),
            prompt_manager.get_prompt(
                "talent", "g2eternity.activation_location",
                default="  \U0001f4cd 位置：{location}"
            ).format(location=self.location),
            prompt_manager.get_prompt(
                "talent", "g2eternity.activation_duration",
                default="  \u23f3 持续：{remaining_rounds} 轮"
            ).format(remaining_rounds=self.remaining_rounds),
            prompt_manager.get_prompt(
                "talent", "g2eternity.activation_effects",
                default="  效果：隐身无效 | 受伤+{bonus_damage} | 禁锁定/找到"
            ).format(bonus_damage=self._get_bonus_damage()),
            f"{'='*50}",
        ]

        # 释放瞬间：处理非玩家单位
        npc_lines = self._pull_npcs()
        lines.extend(npc_lines)

        # 释放瞬间：处理同地点玩家
        setup_lines = self._setup_players_at_location(player)
        lines.extend(setup_lines)

        display.show_info("\n".join(lines))
        return "\n".join(lines), "talent"

    # ============================================
    #  释放瞬间：拉非玩家单位（适配ver1.9新警察模型）
    # ============================================

    def _pull_npcs(self):
        """将所有非玩家单位拉到影像位置并沉沦
        
        ver1.9重构：使用 police.units 扁平列表替代旧的 squads 二层结构。
        每个 PoliceUnit 拥有独立的 location、debuff状态等属性。
        """
        lines = []

        # 警察系统
        if hasattr(self.state, 'police') and self.state.police:
            police = self.state.police
            for unit in police.units:
                if not unit.is_alive():
                    continue

                # 强制移动到影像位置
                if unit.location != self.location:
                    old_loc = unit.location or "不在地图上"
                    unit.location = self.location
                    lines.append(
                        prompt_manager.get_prompt(
                            "talent", "g2eternity.police_pull",
                            default="  \U0001f46e 警察{unit_id}从 {old_loc} 被拉到 {location}！"
                        ).format(unit_id=unit.unit_id, old_loc=old_loc, location=self.location)
                    )

                # 清除所有现有debuff（眩晕/震荡/石化等），然后施加沉沦
                if unit.is_disabled():
                    debuff_names = []
                    if unit.is_stunned:
                        debuff_names.append("眩晕")
                    if unit.is_shocked:
                        debuff_names.append("震荡")
                    if unit.is_petrified:
                        debuff_names.append("石化")
                    unit.clear_all_debuffs()
                    if debuff_names:
                        lines.append(
                            prompt_manager.get_prompt(
                                "talent", "g2eternity.police_debuff_clear",
                                default="  \U0001f46e 警察{unit_id}的{debuffs}被解除！"
                            ).format(unit_id=unit.unit_id, debuffs="/".join(debuff_names))
                        )

                # 施加沉沦状态
                unit.is_submerged = True
                self.submerged_npcs.append(unit.unit_id)
                lines.append(
                    prompt_manager.get_prompt(
                        "talent", "g2eternity.police_submerge",
                        default="  \U0001f46e 警察{unit_id}进入「沉沦」状态！"
                    ).format(unit_id=unit.unit_id)
                )

        if not lines:
            lines.append(
                prompt_manager.get_prompt(
                    "talent", "g2eternity.no_npc_affected",
                    default="  （当前无非玩家单位受影响）"
                )
            )

        return lines

    # ============================================
    #  释放瞬间 / 进入时：处理玩家
    # ============================================

    def _setup_players_at_location(self, caster):
        """对当前在影像地点的其他玩家建立关系"""
        lines = []
        for pid in self.state.player_order:
            if pid == self.player_id:
                continue
            p = self.state.get_player(pid)
            if not p or not p.is_alive():
                continue
            if p.location != self.location:
                continue

            lines.extend(self._on_player_enter_hologram(p, caster))

        return lines

    def _on_player_enter_hologram(self, player, caster=None):
        """玩家进入影像区域时的处理"""
        if caster is None:
            caster = self.state.get_player(self.player_id)
        lines = []

        # 破除隐身
        if self.state.markers.has(player.player_id, "INVISIBLE"):
            self.state.markers.remove(player.player_id, "INVISIBLE")
            lines.append(
                prompt_manager.get_prompt(
                    "talent", "g2eternity.stealth_removed",
                    default="  \U0001f441 {player_name} 的隐身被全息影像破除！"
                ).format(player_name=player.name)
            )

        # 发动者也破除隐身
        if caster and self.state.markers.has(caster.player_id, "INVISIBLE"):
            self.state.markers.remove(caster.player_id, "INVISIBLE")
            lines.append(
                prompt_manager.get_prompt(
                    "talent", "g2eternity.caster_stealth_removed",
                    default="  \U0001f441 {caster_name} 的隐身被全息影像破除！"
                ).format(caster_name=caster.name)
            )

        # 自动建立面对面
        if caster and caster.is_alive() and caster.location == self.location:
            marker_key = (player.player_id, caster.player_id, "ENGAGED_WITH")
            if marker_key not in self.hologram_markers:
                self.state.markers.set_engaged(player.player_id, caster.player_id)
                self.hologram_markers.append(marker_key)
                self.hologram_markers.append(
                    (caster.player_id, player.player_id, "ENGAGED_WITH"))
                lines.append(
                    prompt_manager.get_prompt(
                        "talent", "g2eternity.auto_engaged",
                        default="  \U0001f441 {player_name} 自动与 {caster_name} 建立面对面！"
                    ).format(player_name=player.name, caster_name=caster.name)
                )

        # 初始化停留计数
        if player.player_id not in self.stay_count:
            self.stay_count[player.player_id] = 0

        return lines

    # ============================================
    #  玩家移动进入影像区域（由move调用）
    # ============================================

    def on_player_move_to(self, player, new_location):
        """
        玩家移动到某地点时检查是否进入全息影像。
        由 actions/move.py 调用。
        """
        if not self.active:
            return []
        if new_location != self.location:
            # 离开影像区域：重置停留计数
            if player.player_id in self.stay_count:
                del self.stay_count[player.player_id]
            return []

        # 进入影像区域
        return self._on_player_enter_hologram(player)

    # ============================================
    #  查询接口：是否在影像内
    # ============================================

    def is_in_hologram(self, player_id):
        """某玩家是否处于全息影像区域内"""
        if not self.active:
            return False
        p = self.state.get_player(player_id)
        if not p:
            return False
        return p.location == self.location

    def is_caster(self, player_id):
        """是否是影像发动者"""
        return player_id == self.player_id

    # ============================================
    #  规则修改查询
    # ============================================

    def can_lock_or_find(self, player_id):
        """
        影像内非发动者不能执行锁定/找到。
        返回 (allowed, reason)
        """
        if not self.active:
            return True, ""
        if not self.is_in_hologram(player_id):
            return True, ""
        if self.is_caster(player_id):
            return True, ""
        return False, "全息影像区域内无法执行「锁定」和「找到」！"

    def is_stealth_blocked(self, player_id):
        """影像内隐身无效"""
        if not self.active:
            return False
        return self.is_in_hologram(player_id)

    def _get_bonus_damage(self):
        """影像内额外伤害"""
        if self.enhanced:
            return 1.0
        return 0.5

    def get_bonus_damage(self, target_id):
        """
        计算影像对目标的额外伤害。
        由 damage_resolver 调用。
        返回额外伤害值，0表示无影响。
        """
        if not self.active:
            return 0
        if not self.is_in_hologram(target_id):
            return 0
        return self._get_bonus_damage()

    # ============================================
    #  R4：倒计时 + 震荡检查
    # ============================================

    def on_round_end(self, round_num):
        if not self.active:
            return

        me = self.state.get_player(self.player_id)
        name = me.name if me else self.player_id

        # 更新停留计数
        for pid in list(self.stay_count.keys()):
            p = self.state.get_player(pid)
            if not p or not p.is_alive():
                if pid in self.stay_count:
                    del self.stay_count[pid]
                continue
            if p.location == self.location:
                self.stay_count[pid] += 1
            else:
                self.stay_count[pid] = 0

        # 震荡检查：连续2轮
        for pid, count in self.stay_count.items():
            if pid == self.player_id:
                continue  # 发动者免疫
            if count >= 2:
                p = self.state.get_player(pid)
                if p and p.is_alive() and not p.is_shocked:
                    p.is_shocked = True
                    p.is_stunned = True
                    self.state.markers.on_shock(pid)
                    display.show_info(
                        prompt_manager.get_prompt(
                            "talent", "g2eternity.shock_from_stay",
                            default="  \U0001f441\u26a1 {player_name} 在全息影像中停留2轮，进入震荡！"
                        ).format(player_name=p.name)
                    )

        # 倒计时
        self.remaining_rounds -= 1
        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g2eternity.round_countdown",
                default="\U0001f441 {name} 的全息影像剩余 {remaining_rounds} 轮（位置：{location}）"
            ).format(
                name=name,
                remaining_rounds=self.remaining_rounds,
                location=self.location
            )
        )

        if self.remaining_rounds <= 0:
            self._expire()

    # ============================================
    #  影像消失（适配ver1.9新警察模型）
    # ============================================

    def _expire(self):
        """全息影像消失
        
        ver1.9重构：使用 police.units + unit.unit_id 替代旧的 squads + id(squad)。
        通过 unit_id 匹配之前记录的沉沦单位，解除沉沦状态。
        """
        me = self.state.get_player(self.player_id)
        name = me.name if me else self.player_id

        lines = [
            f"\n{'='*50}",
            prompt_manager.get_prompt(
                "talent", "g2eternity.expire_header",
                default="  \U0001f441 {name} 的全息影像消失了！"
            ).format(name=name),
        ]

        # 解除沉沦：遍历police.units，匹配之前记录的unit_id
        if hasattr(self.state, 'police') and self.state.police:
            police = self.state.police
            for unit in police.units:
                if unit.unit_id in self.submerged_npcs:
                    if unit.is_submerged:
                        unit.is_submerged = False
                        lines.append(
                            prompt_manager.get_prompt(
                                "talent", "g2eternity.expire_police_recover",
                                default="  \U0001f46e 警察{unit_id}解除「沉沦」，恢复行动！"
                            ).format(unit_id=unit.unit_id)
                        )

        # 清除影像产生的标记
        cleared_pairs = set()
        for p1_id, p2_id, marker_type in self.hologram_markers:
            pair = tuple(sorted([p1_id, p2_id]))
            if pair in cleared_pairs:
                continue
            cleared_pairs.add(pair)

            if marker_type == "ENGAGED_WITH":
                self.state.markers.disengage(p1_id, p2_id)
                p1 = self.state.get_player(p1_id)
                p2 = self.state.get_player(p2_id)
                n1 = p1.name if p1 else p1_id
                n2 = p2.name if p2 else p2_id
                lines.append(
                    prompt_manager.get_prompt(
                        "talent", "g2eternity.expire_disengage",
                        default="  \U0001f4ce {player1_name} 与 {player2_name} 的面对面关系解除。"
                    ).format(player1_name=n1, player2_name=n2)
                )
            elif marker_type == "LOCKED_BY":
                self.state.markers.remove_lock(p1_id, p2_id)
                lines.append(
                    prompt_manager.get_prompt(
                        "talent", "g2eternity.expire_lock_clear",
                        default="  \U0001f4ce 锁定关系解除。"
                    )
                )

        lines.append(f"{'='*50}")
        display.show_info("\n".join(lines))

        # 重置状态
        self.active = False
        self.location = None
        self.remaining_rounds = 0
        self.stay_count = {}
        self.hologram_markers = []
        self.submerged_npcs = []

    # ============================================
    #  涟漪献诗增强
    # ============================================

    def enhance_by_ripple(self):
        """涟漪献诗：易伤+1，最大使用次数+1（ver1.9移除了持续时间-1的效果）"""
        self.enhanced = True
        self.max_uses += 1
        if self.used and self.max_uses > 0:
            self.used = False
        me = self.state.get_player(self.player_id)
        name = me.name if me else self.player_id
        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g2eternity.ripple_enhance",
                default="\U0001f441 {name} 的全息影像被涟漪增强！易伤+1（替代+0.5）| 可用次数+1"
            ).format(name=name)
        )

    def _get_initial_duration(self):
        """初始持续轮数（ver1.9：增强后不再缩短持续时间，始终为3轮）"""
        return 3

    # ============================================
    #  描述
    # ============================================

    def describe_status(self):
        parts = []
        if self.active:
            parts.append(f"\U0001f441影像展开中@{self.location}")
            parts.append(f"剩余{self.remaining_rounds}轮")
            parts.append(f"易伤+{self._get_bonus_damage()}")
        else:
            uses_left = self.max_uses
            if uses_left > 0:
                parts.append(f"可用次数：{uses_left}")
            else:
                parts.append("已用尽")
        if self.enhanced:
            parts.append("\u2728涟漪增强")
        return " | ".join(parts)

    def describe(self):
        return (
            f"【{self.name}】"
            f"\n  主动{self.max_uses}次：在所在地点展开全息影像，持续3轮"
            f"\n  隐身无效 | 受伤+{self._get_bonus_damage()} | 非发动者禁锁定/找到"
            f"\n  进入影像自动与发动者建立面对面"
            f"\n  连续停留2轮→震荡 | 非玩家单位沉沦"
            f"\n  消失时清除影像产生的所有标记")
