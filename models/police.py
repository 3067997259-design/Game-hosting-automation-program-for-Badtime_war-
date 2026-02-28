"""
警察数据模型：警队、警察个体、队长信息。
执法逻辑在 engine/police_system.py 中。
"""

from models.equipment import make_weapon


class PoliceUnit:
    """单个警察个体"""

    def __init__(self, unit_id):
        self.unit_id = unit_id
        self.hp = 1.0
        self.weapon_name = "警棍"   # 可被队长更换
        self.is_stunned = False

    def is_alive(self):
        return self.hp > 0

    def is_active(self):
        return self.is_alive() and not self.is_stunned

    def __repr__(self):
        status = "活跃"
        if not self.is_alive():
            status = "已击杀"
        elif self.is_stunned:
            status = "眩晕"
        return f"警察{self.unit_id}({status} HP:{self.hp} 武器:{self.weapon_name})"


class PoliceTeam:
    """一支警队（群体单位）"""

    _next_member_id = 0

    def __init__(self, team_id, initial_size=3):
        self.team_id = team_id
        self.members = []
        self.location = "警察局"

        # 执法状态
        self.enforcement_target = None      # 当前执法目标 player_id
        self.is_engaged_with_target = False # 是否与目标面对面
        self.needs_search = False           # 是否需要搜查（六爻解面对面后）
        self.search_countdown = 0

        # 追踪状态
        self.is_tracking = False
        self.tracking_countdown = 0

        # 生成初始警察个体
        for _ in range(initial_size):
            PoliceTeam._next_member_id += 1
            uid = f"cop_{PoliceTeam._next_member_id}"
            self.members.append(PoliceUnit(uid))

    def alive_count(self):
        return sum(1 for m in self.members if m.is_alive())

    def active_count(self):
        return sum(1 for m in self.members if m.is_active())

    def is_eliminated(self):
        return self.alive_count() == 0

    def get_active_members(self):
        return [m for m in self.members if m.is_active()]

    def get_alive_members(self):
        return [m for m in self.members if m.is_alive()]

    def __repr__(self):
        return (f"警队{self.team_id}(位置:{self.location} "
                f"存活:{self.alive_count()} 目标:{self.enforcement_target})")


class PoliceData:
    """
    警察系统数据层。
    存储所有警队、队长信息、举报状态。
    逻辑操作在 engine/police_system.py 中。
    """

    def __init__(self):
        # 警队列表（初始1支，每支3人）
        self.teams = [PoliceTeam("alpha", initial_size=3)]

        # 举报信息
        self.reporter_id = None
        self.reported_target_id = None
        self.report_phase = "idle"
        # phases: "idle" → "reported" → "assembled" → "dispatched" → "enforcing"

        # 队长
        self.captain_id = None
        self.authority = 0              # 威信

        # 犯罪记录
        self.crime_records = {}         # player_id → set of 犯罪类型字符串

        # 全局限制
        MAX_TEAMS = 3
        self.max_teams = MAX_TEAMS
        self.splits_this_round = 0

    def get_team(self, team_id):
        for t in self.teams:
            if t.team_id == team_id:
                return t
        return None

    def all_teams_at(self, location):
        """获取某地点的所有未被消灭的警队"""
        return [t for t in self.teams if t.location == location and not t.is_eliminated()]

    def has_captain(self):
        return self.captain_id is not None

    def add_crime(self, player_id, crime_type):
        """记录犯罪"""
        if player_id not in self.crime_records:
            self.crime_records[player_id] = set()
        self.crime_records[player_id].add(crime_type)

    def is_criminal(self, player_id):
        """检查是否有犯罪记录"""
        return bool(self.crime_records.get(player_id))

    def get_crimes(self, player_id):
        """获取犯罪记录"""
        return self.crime_records.get(player_id, set())

    def clear_all_crimes_except(self, except_id):
        """清空除指定玩家外所有人的犯罪记录"""
        for pid in list(self.crime_records.keys()):
            if pid != except_id:
                self.crime_records[pid] = set()

    def describe(self):
        """返回警察系统状态描述"""
        lines = []
        lines.append(f"  警队数量：{len([t for t in self.teams if not t.is_eliminated()])}/{self.max_teams}")
        for t in self.teams:
            if not t.is_eliminated():
                lines.append(f"    {t}")
        if self.captain_id:
            lines.append(f"  队长：{self.captain_id}（威信：{self.authority}）")
        else:
            lines.append(f"  队长：无")
        lines.append(f"  举报状态：{self.report_phase}")
        if self.reported_target_id:
            lines.append(f"  执法目标：{self.reported_target_id}")
        return "\n".join(lines)
