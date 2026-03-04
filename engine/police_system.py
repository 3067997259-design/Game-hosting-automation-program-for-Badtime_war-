"""
警察执法引擎。
管理：举报流程、执法攻击、追踪、队长系统、威信机制。
"""

from models.police import PoliceTeam
from combat.damage_resolver import resolve_damage
from models.equipment import make_weapon, WeaponRange
from utils.attribute import Attribute


class PoliceEngine:
    """警察执法引擎，操作 game_state.police 数据"""

    def __init__(self, game_state):
        self.state = game_state
        self.police = game_state.police
        
        # 允许攻击警察的AOE手段
        self.ALLOWED_AOE = {"地震", "地动山摇", "电磁步枪", "天星"}
        
        # 警察允许装备的白名单
        # 禁止：电磁步枪（蓄力武器）、磨刀武器、导弹等
        self.POLICE_ALLOWED_WEAPONS = {
            "警棍", "高斯步枪", "地震", "地动山摇"
            # 高斯步枪可以使用不蓄力模式（基础伤害1.0）
        }
        self.POLICE_ALLOWED_ARMOR = {
            "盾牌", "陶瓷护甲", "魔法护盾", "AT力场"
        }

    # ============================================
    #  犯罪检测
    # ============================================

    def check_and_record_crime(self, player_id, crime_type):
        """
        检查并记录犯罪行为。
        crime_type: "伤害玩家" / "无凭证商店" / "无凭证手术" /
                    "进入他人家" / "进入军事基地" / "释放病毒"
        返回：是否构成犯罪（bool）
        """
        player = self.state.get_player(player_id)
        if not player:
            return False

        # 警察（非队长）不能犯罪
        if player.is_police and not player.is_captain:
            return False

        # 不良少年的热那亚之刃：攻击不构成犯罪
        # Phase 4 天赋系统会在这里插入检查
        # 目前基础局直接记录

        self.police.add_crime(player_id, crime_type)
        player.is_criminal = True

        # 队长犯罪扣威信
        if player.is_captain:
            self.police.authority -= 1
            if self.police.authority <= 0:
                self._on_authority_zero()

        self.state.log_event("crime", player=player_id, crime_type=crime_type)
        return True

    # ============================================
    #  举报流程
    # ============================================

    def can_report(self, reporter_id, target_id):
        """
        检查举报合法性。
        返回 (bool, str原因)
        """
        reporter = self.state.get_player(reporter_id)
        target = self.state.get_player(target_id)

        if not reporter or not target:
            return False, "玩家不存在"

        # 有队长时不受理举报
        if self.police.has_captain():
            return False, "警队已有队长，不再受理举报（邮箱直通垃圾桶）"

        # 举报者不能是犯罪者
        if self.police.is_criminal(reporter_id):
            return False, "你有犯罪记录，不能举报"

        # 目标必须有犯罪记录
        if not self.police.is_criminal(target_id):
            return False, f"{target.name} 没有犯罪记录"

        # 举报者位置（基础局需要在警察局，朝阳好市民可远程）
        # Phase 4: 检查朝阳好市民天赋
        if reporter.location != "警察局":
            return False, "需要在警察局才能举报（除非有特殊天赋）"

        # 已有未完成的举报
        if self.police.report_phase != "idle":
            return False, "已有进行中的举报流程"

        return True, ""

    def do_report(self, reporter_id, target_id):
        """执行举报（P1），消耗1行动回合"""
        self.police.reporter_id = reporter_id
        self.police.reported_target_id = target_id
        self.police.report_phase = "reported"

        target = self.state.get_player(target_id)
        self.state.log_event("report", reporter=reporter_id, target=target_id)
        return f"📢 举报成功！目标：{target.name}。接下来需要花1回合「集结」警察。"

    def can_assemble(self, reporter_id):
        """检查能否集结"""
        if self.police.report_phase != "reported":
            return False, "没有待集结的举报"
        if self.police.reporter_id != reporter_id:
            return False, "只有举报者本人才能集结警察"
        return True, ""

    def do_assemble(self, reporter_id):
        """执行集结（P2），消耗1行动回合"""
        self.police.report_phase = "assembled"

        # 举报者获得警察保护
        reporter = self.state.get_player(reporter_id)
        if reporter:
            reporter.has_police_protection = True
            self.state.markers.add(reporter_id, "POLICE_PROTECT")

        self.state.log_event("assemble", reporter=reporter_id)
        return (f"🚔 警察集结完成！举报者获得警察保护。"
                f"\n   警察将在本轮结束时出动！")

    # ============================================
    #  出动与执法（轮次结束时调用）
    # ============================================

    def process_end_of_round(self, game_state):
        """
        R4-1: 轮次结束时的警察处理。
        按顺序：出动 → 执法攻击 → 追踪倒计时。
        返回消息列表。
        """
        messages = []

        # 1. 出动（assembled → dispatched）
        if self.police.report_phase == "assembled":
            msg = self._dispatch_police()
            messages.append(msg)

        # 2. 执法攻击（dispatched 或 enforcing 状态）
        if self.police.report_phase in ("dispatched", "enforcing"):
            atk_msgs = self._enforcement_attack()
            messages.extend(atk_msgs)
            if self.police.report_phase == "dispatched":
                self.police.report_phase = "enforcing"

        # 3. 追踪倒计时
        tracking_msgs = self._process_tracking()
        messages.extend(tracking_msgs)
        
        # 4. 警察反击（新增）
        retaliation_msgs = self._process_police_retaliation()
        messages.extend(retaliation_msgs)

        # 重置本轮拆分计数
        self.police.splits_this_round = 0

        return messages

    def _dispatch_police(self):
        """警察出动：移动到目标位置，建立面对面"""
        target_id = self.police.reported_target_id
        target = self.state.get_player(target_id)
        if not target or not target.is_alive():
            self.police.report_phase = "idle"
            return "🚔 执法目标已不存在，警察撤回。"

        target_loc = target.location
        for team in self.police.teams:
            if not team.is_eliminated():
                team.location = target_loc
                team.enforcement_target = target_id
                team.is_engaged_with_target = True
                team.is_tracking = False

        self.police.report_phase = "dispatched"
        self.state.log_event("police_dispatch", target=target_id, location=target_loc)
        return f"🚔 警察出动！已抵达{target_loc}，与{target.name}面对面！"

    def _enforcement_attack(self):
        """对执法目标执行攻击"""
        messages = []
        target_id = self._get_enforcement_target()
        if not target_id:
            return messages

        target = self.state.get_player(target_id)
        if not target or not target.is_alive():
            messages.append(f"🚔 执法目标已死亡，警察任务完成。")
            self._reset_enforcement()
            return messages

        # 攻击目标：包括警队和独立单位
        all_attackers = []
        for team in self.police.teams:
            if not team.is_eliminated() and team.location == target.location:
                all_attackers.extend(team.get_active_members())
        
        # 独立单位
        for cop in self.police.individual_units:
            if cop.is_active() and cop.location == target.location:
                all_attackers.append(cop)
        
        if not all_attackers:
            return messages

        for cop in all_attackers:
            weapon = make_weapon(cop.weapon_name)
            if not weapon:
                weapon = make_weapon("警棍")

            result = resolve_damage(
                attacker=None,  # 警察不是玩家
                target=target,
                weapon=weapon,
                game_state=self.state,
            )

            if result["success"]:
                detail = f"   {cop.unit_id} 用{weapon.name}攻击 → "
                if result["killed"]:
                    detail += f"💀 击杀！"
                    self.state.markers.on_player_death(target_id)
                elif result["stunned"]:
                    detail += f"💫 眩晕！(HP:{result['target_hp']})"
                else:
                    detail += f"HP:{result['target_hp']}"
                messages.append(detail)

                # 威信检查：攻击无辜者
                if not self.police.is_criminal(target_id):
                    self.police.authority -= 1
                    messages.append(f"   ⚠️ 攻击无辜者！威信-1（当前：{self.police.authority}）")
                    if self.police.authority <= 0:
                        auth_msgs = self._on_authority_zero()
                        messages.extend(auth_msgs)
                        return messages

            if not target.is_alive():
                break
        
        if not target.is_alive():
            messages.append(f"🚔 {target.name} 已被警察击杀。执法完成。")
            self._reset_enforcement()

        return messages

    def _get_enforcement_target(self):
        """获取当前执法目标"""
        # 队长指定优先
        if self.police.has_captain():
            # 检查各警队的 enforcement_target
            for team in self.police.teams:
                if team.enforcement_target:
                    return team.enforcement_target
            # 独立单位没有 enforcement_target，使用举报目标

        return self.police.reported_target_id

    # ============================================
    #  追踪
    # ============================================

    def on_target_moved(self, target_id, new_location):
        """目标移动后触发追踪"""
        for team in self.police.teams:
            if team.enforcement_target == target_id and not team.is_eliminated():
                team.is_engaged_with_target = False
                team.is_tracking = True
                team.tracking_countdown = 2  # 方式B默认2轮
        self.state.log_event("police_tracking", target=target_id)

    def do_tracking_guide(self, reporter_id):
        """
        举报者花1回合指引追踪（方式A）。
        警察立刻到达目标位置。
        """
        target_id = self.police.reported_target_id
        target = self.state.get_player(target_id)
        if not target or not target.is_alive():
            return "执法目标已不存在"

        for team in self.police.teams:
            if team.enforcement_target == target_id and team.is_tracking:
                team.location = target.location
                team.is_tracking = False
                team.tracking_countdown = 0
                team.is_engaged_with_target = True

        return f"🚔 举报者指引追踪！警察立刻抵达{target.location}，恢复围攻{target.name}！"

    def _process_tracking(self):
        """处理追踪倒计时"""
        messages = []
        for team in self.police.teams:
            if not team.is_tracking or team.is_eliminated():
                continue
            team.tracking_countdown -= 1
            if team.tracking_countdown <= 0:
                # 自动追上
                target_id = team.enforcement_target
                target = self.state.get_player(target_id)
                if target and target.is_alive():
                    team.location = target.location
                    team.is_tracking = False
                    team.is_engaged_with_target = True
                    messages.append(
                        f"🚔 {team.team_id}追踪完成，抵达{target.location}恢复围攻！")
                else:
                    team.is_tracking = False
                    messages.append(f"🚔 {team.team_id}追踪目标已消失。")
            else:
                messages.append(
                    f"🚔 {team.team_id}追踪中...（{team.tracking_countdown}轮后到达）")
        return messages

    # ============================================
    #  举报者违法
    # ============================================

    def on_reporter_crime(self):
        """举报者在集结后违法"""
        reporter_id = self.police.reporter_id
        reporter = self.state.get_player(reporter_id)
        if reporter:
            reporter.has_police_protection = False
            self.state.markers.remove(reporter_id, "POLICE_PROTECT")

        if not self.police.has_captain():
            # 无队长：打所有违法者然后撤退
            return "reporter_crime_no_captain"
        else:
            # 有队长：后续由队长指挥
            return "reporter_crime_has_captain"

    # ============================================
    #  加入警察
    # ============================================

    def can_join_police(self, player_id):
        """检查能否加入警察"""
        player = self.state.get_player(player_id)
        if not player:
            return False, "玩家不存在"
        if player.is_police:
            return False, "你已经是警察了"
        if self.police.is_criminal(player_id):
            return False, "你有犯罪记录，不能加入警察"
        if player.location != "警察局":
            return False, "需要在警察局才能加入"
        return True, ""

    def do_join_police(self, player_id, choices):
        """
        加入警察，三选二。
        choices: list of 2 items from ["凭证", "警棍", "盾牌"]
        """
        player = self.state.get_player(player_id)
        player.is_police = True
        self.state.markers.add(player_id, "IS_POLICE")

        rewards = []
        for c in choices:
            if c == "凭证":
                player.vouchers += 1
                rewards.append("购买凭证x1")
            elif c == "警棍":
                from models.equipment import make_weapon
                player.add_weapon(make_weapon("警棍"))
                rewards.append("警棍")
            elif c == "盾牌":
                from models.equipment import make_armor
                armor = make_armor("盾牌")
                success, _ = player.add_armor(armor)
                if success:
                    rewards.append("盾牌")
                else:
                    rewards.append("盾牌(装备失败)")

        self.state.log_event("join_police", player=player_id, rewards=rewards)
        return f"🚔 {player.name} 加入了警察！获得：{', '.join(rewards)}"

    # ============================================
    #  队长系统
    # ============================================

    def can_start_election(self, player_id):
        """检查能否开始/继续竞选"""
        player = self.state.get_player(player_id)
        if not player:
            return False, "玩家不存在"
        if not player.is_police:
            return False, "需要先加入警察"
        if self.police.has_captain():
            return False, "已有队长，不能竞选"
        if player.location != "警察局":
            return False, "需要在警察局竞选"
        return True, ""

    def do_election_progress(self, player_id):
        """
        推进竞选进度。
        需要3回合（朝阳好市民天赋减1→2回合）。
        """
        player = self.state.get_player(player_id)
        required = 3
        # Phase 4: 朝阳好市民减1
        # if player.talent and player.talent.name == "朝阳好市民":
        #     required = 2

        progress_key = "captain_election"
        current = player.progress.get(progress_key, 0)
        current += 1
        player.progress[progress_key] = current

        if current < required:
            return f"🏛️ {player.name} 竞选进度：{current}/{required}"

        # 竞选成功
        del player.progress[progress_key]
        self.police.captain_id = player_id
        self.police.authority = 3
        player.is_captain = True
        self.state.markers.add(player_id, "IS_CAPTAIN")
        
        # 队长上任：所有警察返回警察局等待指令
        self._on_captain_elected()

        self.state.log_event("captain_elected", player=player_id)
        return (f"👑 {player.name} 成为警队队长！威信：3"
                f"\n   队长可指挥警察、指定目标、拆分警队。"
                f"\n   ⚠️ 警局不再受理其他人的举报。")

    def _on_captain_elected(self):
        """队长上任处理：所有警察返回警察局等待指令"""
        # 所有警队返回警察局
        for team in self.police.teams:
            if not team.is_eliminated():
                team.location = "警察局"
                team.enforcement_target = None
                team.is_engaged_with_target = False
                team.is_tracking = False
        
        # 独立单位也返回警察局
        for cop in self.police.individual_units:
            if cop.is_alive():
                cop.location = "警察局"
                cop.current_order = None
        
        # 举报系统暂停（但保留犯罪记录）
        self.police.report_phase = "idle"
        self.police.reporter_id = None
        self.police.reported_target_id = None

    def captain_designate_target(self, captain_id, target_id):
        """队长指定执法目标"""
        target = self.state.get_player(target_id)
        for team in self.police.teams:
            if not team.is_eliminated():
                team.enforcement_target = target_id

        # 如果还没出动，现在标记为需要出动
        if self.police.report_phase == "idle":
            self.police.report_phase = "assembled"
            self.police.reported_target_id = target_id

        self.state.log_event("captain_designate", captain=captain_id, target=target_id)
        return f"👑 队长指定执法目标：{target.name}"

    def captain_split_team(self, captain_id, team_id):
        """
        队长拆分警队为独立单位（修改版）。
        拆分后成为独立个体，不可合并。
        全场最多3个独立单位（police1, police2, police3）。
        """
        # 验证队长权限
        if self.police.captain_id != captain_id:
            return "❌ 只有队长可以拆分警察"

        if self.police.splits_this_round >= 1:
            return "❌ 本轮已拆分过一次"

        source = self.police.get_team(team_id)
        if not source:
            return f"❌ 找不到警队 {team_id}"

        # 检查是否能拆分（至少2个存活成员）
        alive = source.get_alive_members()
        if len(alive) < 2:
            return "❌ 该警队存活人数不足2人，无法拆分"

        # 检查独立单位数量上限（最多3个）
        existing_individuals = len(self.police.individual_units)
        if existing_individuals >= 3:
            return "❌ 独立警察数量已达上限（最多3个）"

        # 拆分第一个成员为独立单位
        cop_to_split = alive[0]
        cop_to_split.is_individual = True
        cop_to_split.original_team_id = team_id
        
        # 生成独立ID
        individual_id = self._generate_individual_id()
        cop_to_split.unit_id = individual_id
        
        # 从原队伍移除，添加到独立单位列表
        source.members.remove(cop_to_split)
        self.police.individual_units.append(cop_to_split)
        
        # 独立单位位置与原队伍相同
        cop_to_split.location = source.location
        
        self.police.splits_this_round += 1

        self.state.log_event("police_split_individual", captain=captain_id,
                             source=team_id, new_unit=individual_id)
        return f"🚔 警察 {individual_id} 已拆分为独立单位！不可合并。"

    def captain_equip_team(self, captain_id, team_id, weapon_name):
        """队长为警队更换装备"""
        team = self.police.get_team(team_id)
        if not team:
            return f"❌ 找不到警队 {team_id}"

        # 验证装备是否允许
        if not self._validate_police_equipment(weapon_name, "weapon"):
            return f"❌ 警察不能装备「{weapon_name}」"

        for cop in team.get_alive_members():
            cop.weapon_name = weapon_name

        return f"🚔 警队{team_id}全员更换武器为「{weapon_name}」"

    # ============================================
    #  威信归零
    # ============================================

    def _on_authority_zero(self):
        """威信归零处理（扩展版：重置独立警察）"""
        messages = []
        captain_id = self.police.captain_id
        captain = self.state.get_player(captain_id)

        messages.append(f"\n  ⚠️⚠️⚠️ 队长 {captain.name} 威信归零！")
        messages.append(f"  队长身份解除！所有警队撤退回警察局！")

        # 解除队长
        if captain:
            captain.is_captain = False
            self.state.markers.remove(captain_id, "IS_CAPTAIN")

        self.police.captain_id = None
        self.police.authority = 0

        # 所有警队撤退
        for team in self.police.teams:
            team.location = "警察局"
            team.enforcement_target = None
            team.is_engaged_with_target = False
            team.is_tracking = False

        # 重置所有独立警察为初始状态
        for cop in self.police.individual_units:
            if cop.is_alive():
                cop.reset_to_initial()
                # 返回原队伍（如果原队伍还存在）
                original_team = self.police.get_team(cop.original_team_id)
                if original_team:
                    cop.is_individual = False
                    original_team.members.append(cop)
                    messages.append(f"  {cop.unit_id} 重置并返回原队伍")
                else:
                    messages.append(f"  {cop.unit_id} 重置为初始状态")
        
        # 清除独立单位列表
        self.police.individual_units = []

        # 原队长成为唯一违法者
        self.police.clear_all_crimes_except(captain_id)
        self.police.add_crime(captain_id, "队长滥权")

        messages.append(f"  {captain.name} 被记录为唯一违法者。其他人犯罪记录清空。")

        # 清除所有玩家的犯罪标记（除原队长）
        for p in self.state.players.values():
            if p.player_id != captain_id:
                p.is_criminal = False

        self.police.report_phase = "idle"
        self.state.log_event("authority_zero", captain=captain_id)

        return messages

    def captain_study(self, captain_id):
        """队长在警察局研究性学习，威信+1"""
        self.police.authority += 1
        return f"📚 队长研究性学习完成！威信+1（当前：{self.police.authority}）"

    # ============================================
    #  辅助
    # ============================================

    def _reset_enforcement(self):
        """重置执法状态"""
        self.police.report_phase = "idle"
        self.police.reporter_id = None
        self.police.reported_target_id = None
        for team in self.police.teams:
            team.enforcement_target = None
            team.is_engaged_with_target = False
            team.is_tracking = False
            team.location = "警察局"

    def is_protected_by_police(self, player_id):
        """检查玩家是否受警察保护"""
        player = self.state.get_player(player_id)
        if not player or not player.has_police_protection:
            return False
        # 必须与至少一支警队同地点
        for team in self.police.teams:
            if not team.is_eliminated() and team.location == player.location:
                return True
        return False

    def wake_police(self, player_id, team_id, cop_id):
        """玩家花1回合唤醒眩晕警察"""
        team = self.police.get_team(team_id)
        if not team:
            return "❌ 找不到警队"
        for cop in team.members:
            if cop.unit_id == cop_id and cop.is_stunned:
                cop.is_stunned = False
                cop.hp = 1.0
                return f"🚔 {cop_id} 被唤醒！HP恢复至1。"
        return "❌ 找不到该眩晕警察"

    # ============================================
    #  新增：警察攻击与反击系统
    # ============================================

    def _find_police_unit(self, police_target):
        """查找警察单位（支持 police, police1, police2, police3）"""
        if police_target.lower() == "police":
            # 返回第一个存活的警察单位（优先独立单位，然后警队）
            if self.police.individual_units:
                for cop in self.police.individual_units:
                    if cop.is_alive():
                        return cop
            for team in self.police.teams:
                if not team.is_eliminated():
                    for cop in team.get_alive_members():
                        return cop
            return None
        
        # 匹配 police1, police2, police3
        if police_target.lower().startswith("police"):
            # 提取编号
            try:
                num = int(police_target.lower().replace("police", ""))
            except ValueError:
                return None
            # 在独立单位中查找
            for cop in self.police.individual_units:
                if cop.unit_id.lower() == police_target.lower() and cop.is_alive():
                    return cop
            # 在警队中查找（理论上独立单位才有这些ID）
            return None
        
        # 匹配原ID（如 cop_1）
        for team in self.police.teams:
            for cop in team.members:
                if cop.unit_id == police_target and cop.is_alive():
                    return cop
        return None

    def _is_valid_aoe_attack(self, attack_method):
        """验证是否为允许攻击警察的AOE手段"""
        # 武器名称匹配
        if attack_method in self.ALLOWED_AOE:
            return True
        
        # 武器对象匹配
        weapon = make_weapon(attack_method)
        if weapon and weapon.name in self.ALLOWED_AOE:
            return True
        
        # 天赋名称匹配（如"天星"）
        attacker_weapon = None
        # 这里可以扩展检查天赋名称
        return False

    def attack_police(self, attacker_id, police_target, attack_method):
        """玩家攻击警察"""
        # 验证攻击者
        attacker = self.state.get_player(attacker_id)
        if not attacker:
            return "❌ 攻击者不存在"
        
        # 验证AOE攻击
        if not self._is_valid_aoe_attack(attack_method):
            return "❌ 警察只能被地震、地动山摇、电磁步枪、天星伤害！"
        
        # 查找警察目标
        police_unit = self._find_police_unit(police_target)
        if not police_unit:
            return f"❌ 找不到警察目标 {police_target}"
        
        # 计算伤害（警察HP=1，直接全额伤害）
        weapon = make_weapon(attack_method) or attacker.get_weapon(attack_method)
        if not weapon:
            # 可能是天赋攻击，默认伤害1.0
            base_damage = 1.0
        else:
            base_damage = weapon.get_effective_damage()
        
        # 施加伤害
        result = police_unit.take_damage(base_damage, attacker_id)
        
        # 记录犯罪
        crime_type = "攻击执法单位"
        self.police.add_crime(attacker_id, crime_type)
        
        # 队长攻击警察扣威信
        if self.police.captain_id == attacker_id:
            self.police.authority -= 1
            auth_msg = f"👑 队长攻击警察，威信-1（当前：{self.police.authority}）"
        else:
            auth_msg = ""
        
        # 返回结果
        if result["killed"]:
            msg = f"💀 {attacker.name} 使用「{attack_method}」击杀警察{police_unit.unit_id}！"
            # 从所在容器中移除死亡警察
            if police_unit.is_individual:
                self.police.individual_units.remove(police_unit)
            else:
                for team in self.police.teams:
                    if police_unit in team.members:
                        team.members.remove(police_unit)
                        break
        else:
            msg = f"⚔️ {attacker.name} 攻击警察{police_unit.unit_id}，造成{result['damage']}伤害！"
        
        if auth_msg:
            msg += f"\n{auth_msg}"
        
        return msg

    def _process_police_retaliation(self):
        """处理警察反击（在R4阶段调用）"""
        messages = []
        
        # 1. 检查警队成员
        for team in self.police.teams:
            for cop in team.get_active_members():
                if cop.was_attacked_this_round and cop.is_alive():
                    messages.extend(self._retaliate(cop))
        
        # 2. 检查独立警察
        for cop in self.police.individual_units:
            if cop.was_attacked_this_round and cop.is_alive():
                messages.extend(self._retaliate(cop))
        
        return messages

    def _retaliate(self, police_unit):
        """单个警察反击"""
        attacker_id = police_unit.last_attacker_id
        attacker = self.state.get_player(attacker_id)
        if not attacker or not attacker.is_alive():
            return []
        
        # 警察反击（使用当前武器）
        weapon = make_weapon(police_unit.weapon_name)
        if not weapon:
            weapon = make_weapon("警棍")
        
        result = resolve_damage(
            attacker=None,
            target=attacker,
            weapon=weapon,
            game_state=self.state,
        )
        
        # 清除标记
        police_unit.was_attacked_this_round = False
        
        # 返回消息
        messages = [f"👮 {police_unit.unit_id} 对 {attacker.name} 进行反击！"]
        if result.get("killed"):
            messages.append(f"   💀 击杀！")
        
        return messages

    def _generate_individual_id(self):
        """生成独立警察ID（police1, police2, police3）"""
        existing = [u.unit_id.lower() for u in self.police.individual_units]
        for i in range(1, 4):
            candidate = f"police{i}"
            if candidate not in existing:
                return candidate
        return "policeX"  # 理论上不会超过3个

    def _validate_police_equipment(self, equipment_name, equipment_type):
        """验证警察装备是否允许"""
        if equipment_type == "weapon":
            return equipment_name in self.POLICE_ALLOWED_WEAPONS
        elif equipment_type == "armor":
            return equipment_name in self.POLICE_ALLOWED_ARMOR
        return False

    def captain_control_police(self, captain_id, police_id, command, **kwargs):
        """队长操控警察（消耗1行动回合）"""
        # 验证队长权限
        if self.police.captain_id != captain_id:
            return "❌ 只有队长可以操控警察"
        
        # 查找警察（包括独立单位和警队成员）
        police_unit = self._find_police_unit(police_id)
        if not police_unit:
            return f"❌ 找不到警察单位 {police_id}"
        
        if command == "move":
            # 移动警察到指定地点
            location = kwargs.get("location")
            if not location:
                return "❌ 请指定目的地"
            police_unit.location = location
            police_unit.current_order = {"type": "move", "destination": location}
            return f"👑 队长移动 {police_id} 到 {location}"
        
        elif command == "equip":
            # 为警察更换装备
            weapon = kwargs.get("weapon")
            armor = kwargs.get("armor")
            
            if weapon:
                if not self._validate_police_equipment(weapon, "weapon"):
                    return f"❌ 警察不能装备「{weapon}」"
                police_unit.weapon_name = weapon
            
            if armor:
                if not self._validate_police_equipment(armor, "armor"):
                    return f"❌ 警察不能装备「{armor}」"
                police_unit.armor_name = armor
            
            police_unit.current_order = {"type": "equip", "weapon": weapon, "armor": armor}
            return f"👑 队长为 {police_id} 更换装备"
        
        elif command == "attack":
            # 命令警察攻击玩家（仍需满足同地点条件）
            target_id = kwargs.get("target")
            if not target_id:
                return "❌ 请指定攻击目标"
            
            target = self.state.get_player(target_id)
            if not target:
                return f"❌ 找不到目标玩家 {target_id}"
            
            # 标记为需要移动并攻击
            police_unit.current_order = {
                "type": "move_and_attack",
                "destination": target.location,
                "target": target_id
            }
            return f"👑 队长命令 {police_id} 前往 {target.location} 攻击 {target.name}"
        
        else:
            return f"❌ 未知的命令类型：{command}"