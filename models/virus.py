"""
病毒系统。
释放后全体感染，5次轮次结束倒计时后未免疫者死亡。
病毒期间商店免费（不需凭证）。
"""


class VirusSystem:
    def __init__(self):
        self.is_active = False
        self.countdown = 0          # 已经过的倒计时次数（0→1→2→3→4→5，到5判死）
        self.released_by = None     # 释放者 player_id
        self.released_on_round = 0  # 释放时的轮次号

    def release(self, player_id, current_round):
        """释放病毒"""
        self.is_active = True
        self.released_by = player_id
        self.released_on_round = current_round
        self.countdown = 0

    def tick(self):
        """
        轮次结束时调用。
        返回：是否到达致死判定（countdown >= 5）
        """
        if not self.is_active:
            return False
        self.countdown += 1
        return self.countdown >= 5

    def get_dead_players(self, players):
        """返回本次应判死的玩家列表（未免疫且存活）"""
        dead = []
        for p in players:
            if p.is_alive() and not self._is_immune(p):
                dead.append(p)
        return dead

    def _is_immune(self, player):
        """检查玩家是否免疫病毒"""
        # 封闭法术
        if player.has_seal:
            return True
        # 防毒面具
        if any(i.name == "防毒面具" for i in player.items):
            return True
        return False

    def describe(self):
        """返回病毒状态描述"""
        if not self.is_active:
            return "未激活"
        remaining = 5 - self.countdown
        if remaining <= 0:
            return "☠️ 已致死判定"
        return f"🦠 病毒倒计时：{self.countdown}/5（还剩{remaining}轮）"
