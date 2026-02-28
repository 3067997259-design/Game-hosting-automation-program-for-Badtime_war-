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

        for team in self.police.teams:
            if team.is_eliminated():
                continue
            if team.location != target.location:
                # 不在同一地点，跳过
                continue
            if team.needs_search:
                team.search_countdown -= 1
                if team.search_countdown <= 0:
                    team.needs_search = False
                    team.is_engaged_with_target = True
                    messages.append(f"🔍 {team.team_id}搜查完成，恢复执法。")
                else:
                    messages.append(f"🔍 {team.team_id}搜查中...（剩余{team.search_countdown}轮）")
                continue
            if not team.is_engaged_with_target:
                continue

            # 执行攻击：每支活跃警队的每个活跃个体各打一次
            for cop in team.get_active_members():
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
                break

        return messages

    def _get_enforcement_target(self):
        """获取当前执法目标"""
        # 队长指定优先
        if self.police.has_captain():
            # 检查各警队的 enforcement_target
            for team in self.police.teams:
                if team.enforcement_target:
                    return team.enforcement_target

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

        self.state.log_event("captain_elected", player=player_id)
        return (f"👑 {player.name} 成为警队队长！威信：3"
                f"\n   队长可指挥警察、指定目标、拆分警队。"
                f"\n   ⚠️ 警局不再受理其他人的举报。")

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
        """队长拆分警队"""
        active_teams = [t for t in self.police.teams if not t.is_eliminated()]
        if len(active_teams) >= self.police.max_teams:
            return f"❌ 警队数量已达上限（{self.police.max_teams}支）"

        if self.police.splits_this_round >= 1:
            return "❌ 本轮已拆分过一次"

        source = self.police.get_team(team_id)
        if not source:
            return f"❌ 找不到警队 {team_id}"

        alive = source.get_alive_members()
        if len(alive) < 2:
            return "❌ 该警队存活人数不足2人，无法拆分"

        # 拆分：一半分到新队
        split_count = len(alive) // 2
        new_team_id = f"team_{len(self.police.teams) + 1}"
        new_team = PoliceTeam(new_team_id, initial_size=0)
        new_team.location = source.location

        for i in range(split_count):
            member = alive[-(i + 1)]
            source.members.remove(member)
            new_team.members.append(member)

        self.police.teams.append(new_team)
        self.police.splits_this_round += 1

        self.state.log_event("police_split", captain=captain_id,
                             source=team_id, new=new_team_id)
        return f"🚔 警队拆分！{team_id}({source.alive_count()}人) → 新建{new_team_id}({new_team.alive_count()}人)"

    def captain_equip_team(self, captain_id, team_id, weapon_name):
        """队长为警队更换装备"""
        team = self.police.get_team(team_id)
        if not team:
            return f"❌ 找不到警队 {team_id}"

        for cop in team.get_alive_members():
            cop.weapon_name = weapon_name

        return f"🚔 警队{team_id}全员更换武器为「{weapon_name}」"

    # ============================================
    #  威信归零
    # ============================================

    def _on_authority_zero(self):
        """威信归零处理"""
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
