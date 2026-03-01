"""
天赋6：朝阳好市民（原初）
常驻效果：
  - 远程举报：不需在警察局即可举报
  - 扩展犯罪名单：进入他人家、进入军事基地、释放病毒
  - 竞选队长所需行动回合-1
"""

from talents.base_talent import BaseTalent


class GoodCitizen(BaseTalent):
    name = "朝阳好市民"
    description = "远程举报。扩展犯罪(进入他人家/军事基地/释放病毒)。竞选-1回合。"
    tier = "原初"

    def on_register(self):
        """开局扩展犯罪名单"""
        self.state.crime_types.add("进入他人家")
        self.state.crime_types.add("进入军事基地")
        self.state.crime_types.add("释放病毒")

    def get_election_rounds_reduction(self):
        """竞选回合减免"""
        return 1

    def allows_remote_report(self):
        """允许远程举报"""
        return True

    def describe_status(self):
        return "常驻生效中。扩展犯罪：进入他人家/军事基地/释放病毒。"
