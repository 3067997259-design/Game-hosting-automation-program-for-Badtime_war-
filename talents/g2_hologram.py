"""
神代天赋2：请一直，注视着我

主动1次，T0启动，消耗行动回合。
在所在地点展开「全息影像」持续3轮。

释放瞬间：
  - 所有非玩家单位（警察等）强制移动到影像位置，眩晕解除
  - 进入「沉沦」（无法行动/移动/被唤醒）
  - 【新增】最后一曲：每个存活玩家投D6，≥4被强制拉到影像位置

影像内通用：
  - 隐身无效
  - 受伤时额外+0.5伤害

其他玩家（非发动者）在影像内：
  - 无法执行「锁定」和「找到」
  - 进入影像瞬间：自动与影像内所有其他玩家建立面对面
  - 进入即震荡；连续停留2轮再次震荡

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
    description = "主动1次：展开全息影像（3-6轮，按存活人数）。释放时D6判定拉人/隐身无效/+1伤害/禁锁定找到/自动面对面。"
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
        duration = self._get_initial_duration()
        return f"发动天赋：{self.name}（在当前地点展开全息影像，持续{duration}轮）"

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

        # 释放瞬间：最后一曲——D6判定拉玩家
        player_pull_lines = self._pull_players_d6(player)
        lines.extend(player_pull_lines)

        # 释放瞬间：处理同地点玩家（含被拉来的）
        setup_lines = self._setup_players_at_location(player)
        lines.extend(setup_lines)

        # V1.92更新：发动后立刻获得1个额外行动回合
        player.extra_action_after_hologram = True
        lines.append(
            prompt_manager.get_prompt(
                "talent", "g2eternity.extra_action_after",
                default="  ⚡ 发动后立刻获得1个额外行动回合！"
            )
        )

        self.state.log_event("hologram_activate", player=self.player_id,
                             location=self.location,
                             duration=self.remaining_rounds)

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
                if not unit.is_on_map():
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

    def _pull_players_d6(self, caster):
        """释放瞬间：最后一曲——D6判定拉玩家

        每个存活的非发动者玩家投掷D6，≥4（50%概率）被强制拉到影像位置。
        跳过：未起床、已死亡、已在影像地点、在幻想乡结界内的玩家。
        """
        from utils.dice import roll_d6

        lines = []
        lines.append(
            prompt_manager.get_prompt(
                "talent", "g2eternity.last_song_header",
                default="  🎵 最后一曲响起，歌声回荡在每一个角落……"
            )
        )

        pulled_count = 0
        for pid in self.state.player_order:
            if pid == self.player_id:
                continue
            p = self.state.get_player(pid)
            if not p or not p.is_alive():
                continue
            if not p.is_awake:
                continue
            # Skip players already at hologram location
            if p.location == self.location:
                continue
            # Skip players in Mythland barrier (if active)
            if getattr(self.state, 'barrier_active', False):
                barrier_players = getattr(self.state, 'barrier_players', [])
                if pid in barrier_players:
                    continue
            # 爱愿检查：如果G5持有者对发动者（G2 caster）持有爱愿，跳过拉拽
            # (即：G2 caster 有爱愿 → 不能对G5造成负面效果 → 不能拉G5)
            love_wish_blocked = False
            if p.talent and hasattr(p.talent, 'love_wish'):
                # p is G5 holder, check if G2 caster has love_wish from p
                if p.talent.has_love_wish(self.player_id):
                    lines.append(f"  💝 {p.name} 的「爱愿」保护其免受拉拽！")
                    love_wish_blocked = True
            if love_wish_blocked:
                continue

            # 六爻·元亨利贞：免疫拉拽
            if p.talent and hasattr(p.talent, 'is_immune_to_debuff') and p.talent.is_immune_to_debuff("pull"):
                lines.append(f"  ☯️ {p.name} 不为外道所动")
                continue

            # 星野架盾：吸引概率从50%降低至20%（D6放弃式：1成功，2-5失败，6重掷）
            if (p.talent and hasattr(p.talent, 'shield_mode')
                    and p.talent.shield_mode == "架盾"):
                from utils.dice import roll_d6
                while True:
                    shield_roll = roll_d6()
                    if shield_roll <= 5:
                        break
                    # shield_roll == 6: 重掷
                if shield_roll >= 2:  # 2-5 = 抵抗（80%）
                    lines.append(prompt_manager.get_prompt(
                        "talent", "g7hoshino.shield_resist_pull",
                        default="  🛡️ {name}: D6(放弃式) = {roll} → 架盾抵抗了歌声！").format(
                        name=p.name, roll=shield_roll))
                    continue
                else:  # shield_roll == 1 = 被吸引（20%）
                    # 继续执行下面的正常拉拽逻辑（不 continue）
                    pass

            roll = roll_d6()
            old_loc = p.location or "未知"

            if roll >= 3:
                # Pulled! Force move to hologram location
                p.location = self.location
                # Trigger marker cleanup for the forced move (clear locks/engaged from old location)
                if old_loc != self.location:
                    self.state.markers.on_player_move(pid)
                pulled_count += 1
                lines.append(
                    prompt_manager.get_prompt(
                        "talent", "g2eternity.player_pull_success",
                        default="  🎲 {player_name}: D6 = {roll} ≥ 3 → ✨ 被歌声吸引，来到了舞台前！（从{old_loc}）"
                    ).format(player_name=p.name, roll=roll, old_loc=old_loc)
                )
            else:
                lines.append(
                    prompt_manager.get_prompt(
                        "talent", "g2eternity.player_pull_resist",
                        default="  🎲 {player_name}: D6 = {roll} < 3 → 抵抗住了歌声的诱惑。"
                    ).format(player_name=p.name, roll=roll)
                )

        if pulled_count == 0:
            lines.append(
                prompt_manager.get_prompt(
                    "talent", "g2eternity.no_players_pulled",
                    default="  （无人被歌声吸引）"
                )
            )
        else:
            lines.append(
                prompt_manager.get_prompt(
                    "talent", "g2eternity.players_pulled_summary",
                    default="  🎵 {count}名玩家被最后一曲吸引到了舞台！"
                ).format(count=pulled_count)
            )

        return lines

    # ============================================
    #  释放瞬间 / 进入时：处理玩家
    # ============================================

    def _setup_players_at_location(self, caster):
        """对当前在影像地点的其他玩家建立关系"""
        lines = []
        entered_players = []
        for pid in self.state.player_order:
            if pid == self.player_id:
                continue
            p = self.state.get_player(pid)
            if not p or not p.is_alive():
                continue
            if p.location != self.location:
                continue
            lines.extend(self._on_player_enter_hologram(p, caster))
            entered_players.append(p)

        # 二次检查：确保所有影像内玩家互相面对面
        # （因为 _on_player_enter_hologram 按顺序处理，
        #   先进入的玩家可能还没和后进入的玩家建立关系）
        all_in = self._get_players_in_hologram()
        for i, p1 in enumerate(all_in):
            for p2 in all_in[i+1:]:
                marker_key = (p1.player_id, p2.player_id, "ENGAGED_WITH")
                if marker_key not in self.hologram_markers:
                    self.state.markers.set_engaged(p1.player_id, p2.player_id)
                    self.hologram_markers.append(marker_key)
                    self.hologram_markers.append(
                        (p2.player_id, p1.player_id, "ENGAGED_WITH"))
                    lines.append(
                        prompt_manager.get_prompt(
                            "talent", "g2eternity.auto_engaged",
                            default="  👁 {player_name} 自动与 {caster_name} 建立面对面！"
                        ).format(player_name=p1.name, caster_name=p2.name)
                    )
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

        # 自动建立面对面：与影像内所有其他玩家互相面对面
        players_in_hologram = self._get_players_in_hologram(exclude_pid=player.player_id)
        for other in players_in_hologram:
            marker_key = (player.player_id, other.player_id, "ENGAGED_WITH")
            if marker_key not in self.hologram_markers:
                self.state.markers.set_engaged(player.player_id, other.player_id)
                self.hologram_markers.append(marker_key)
                self.hologram_markers.append(
                    (other.player_id, player.player_id, "ENGAGED_WITH"))
                lines.append(
                    prompt_manager.get_prompt(
                        "talent", "g2eternity.auto_engaged",
                        default="  👁 {player_name} 自动与 {caster_name} 建立面对面！"
                    ).format(player_name=player.name, caster_name=other.name)
                )

        # 初始化停留计数
        if player.player_id not in self.stay_count:
            self.stay_count[player.player_id] = 0

        # V1.92更新：进入影像区域立刻触发震荡（无需等待连续2轮）
        # 非发动者进入时触发震荡
        if player.player_id != self.player_id:
            # 六爻·元亨利贞：免疫震荡
            immune = (player.talent and hasattr(player.talent, 'is_immune_to_debuff')
                      and player.talent.is_immune_to_debuff("shock"))
            if immune:
                lines.append(f"  ☯️ {player.name} 的「元亨利贞」免疫了全息影像的震荡！")
            elif not player.is_shocked:
                player.is_shocked = True
                player.is_stunned = True
                self.state.markers.on_shock(player.player_id)
                lines.append(
                    prompt_manager.get_prompt(
                        "talent", "g2eternity.enter_shock",
                        default="  \U0001f441\u26a1 {player_name} 进入全息影像区域，立刻触发震荡！"
                    ).format(player_name=player.name)
                )

        return lines

    def _get_players_in_hologram(self, exclude_pid=None):
        """获取当前在影像区域内的所有存活玩家（可排除指定玩家）"""
        result = []
        if not self.active:
            return result
        for pid in self.state.player_order:
            if pid == exclude_pid:
                continue
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.location == self.location:
                result.append(p)
        return result

    def on_d4_bonus(self, player):
        """全息影像存在期间，释放者D4点数+3"""
        if not self.active:
            return 0
        if player.player_id == self.player_id:
            return 3
        return 0

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
        """影像内额外伤害（含涟漪叠加易伤）"""
        base = 1.0
        if self.enhanced:
            base = 1.5
        return base + getattr(self, 'ripple_extra_vulnerability', 0.0)

    def get_bonus_damage(self, target_id):
        if not self.active:
            return 0
        if target_id == self.player_id:  # ← 新增：发动者不受自己的易伤
            return 0
        if not self.is_in_hologram(target_id):
            return 0
        return self._get_bonus_damage()

    # ============================================
    #  发动者减伤（V1.92新增）
    # ============================================

    def modify_incoming_damage(self, target, attacker, weapon, raw_damage):
        """V1.92: 发动者在影像存在期间受到的伤害降低1"""
        if not self.active:
            return raw_damage
        if target.player_id != self.player_id:
            return raw_damage
        reduced = max(0, raw_damage - 1)
        display.show_info(f"  👁️ 全息影像发动者减伤：{raw_damage} → {reduced}")
        return reduced

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

        # 震荡检查：连续停留2轮再次震荡
        for pid, count in self.stay_count.items():
            if pid == self.player_id:
                continue  # 发动者免疫
            if count >= 2:
                p = self.state.get_player(pid)
                if p and p.is_alive() and not p.is_shocked:
                    # 六爻·元亨利贞：免疫震荡
                    if p.talent and hasattr(p.talent, 'is_immune_to_debuff') and p.talent.is_immune_to_debuff("shock"):
                        display.show_info(f"  ☯️ {p.name} 的「元亨利贞」免疫了全息影像的震荡！")
                    else:
                        p.is_shocked = True
                        p.is_stunned = True
                        self.state.markers.on_shock(pid)
                        display.show_info(
                            prompt_manager.get_prompt(
                                "talent", "g2eternity.shock_from_stay",
                                default="  \U0001f441\u26a1 {player_name} 在全息影像中连续停留2轮，再次进入震荡！"
                            ).format(player_name=p.name)
                        )
                    # 重置计数，下次再停留2轮才会再次触发
                    self.stay_count[pid] = 0

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

        self.state.log_event("hologram_expire", player=self.player_id)

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
        """涟漪献诗：易伤+1，最大使用次数+1，叠加额外易伤（ver1.9移除了持续时间-1的效果）"""
        self.enhanced = True
        self.max_uses += 1
        self.ripple_extra_vulnerability = getattr(self, 'ripple_extra_vulnerability', 0) + 0.5
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
        """初始持续轮数：基于存活玩家数动态计算

        2人 → 3轮, 每多1人 +1轮, 最多6轮
        公式: min(3 + max(alive_count - 2, 0), 6)
        """
        alive_count = 0
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive():
                alive_count += 1
        alive_count = max(alive_count, 2)  # 至少按2人算
        return min(3 + (alive_count - 2), 6)

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
            f"\n  主动{self.max_uses}次：在所在地点展开全息影像，持续{self._get_initial_duration()}轮"
            f"\n  隐身无效 | 受伤+{self._get_bonus_damage()} | 非发动者禁锁定/找到"
            f"\n  释放瞬间：D6≥4的玩家被拉到影像位置"
            f"\n  影像内所有玩家互相建立面对面"
            f"\n  进入即震荡；连续停留2轮再次震荡 | 非玩家单位沉沦"
            f"\n  消失时清除影像产生的所有标记")
