"""
标记系统（Phase 2 完整版）
补全：隐身/探测/眩晕/震荡/石化 联动
"""


class MarkerManager:
    def __init__(self):
        self._simple = {}       # player_id -> set("SLEEPING", "STUNNED", ...)
        self._relations = {}    # player_id -> {"LOCKED_BY": set(), "ENGAGED_WITH": set(), ...}

    def init_player(self, player_id):
        self._simple[player_id] = {"SLEEPING"}
        self._relations[player_id] = {
            "LOCKED_BY": set(),
            "ENGAGED_WITH": set(),
            "DETECTED_BY": set(),
        }

    # ---- 简单标记 ----

    def add(self, player_id, marker):
        if player_id in self._simple:
            self._simple[player_id].add(marker)

    def remove(self, player_id, marker):
        if player_id in self._simple:
            self._simple[player_id].discard(marker)

    def has(self, player_id, marker):
        return marker in self._simple.get(player_id, set())

    def get_all_simple(self, player_id):
        """获取某玩家的所有简单标记"""
        return set(self._simple.get(player_id, set()))

    # ---- 关系型标记 ----

    def add_relation(self, player_id, relation_type, related_id):
        if player_id in self._relations:
            if relation_type not in self._relations[player_id]:
                self._relations[player_id][relation_type] = set()
            self._relations[player_id][relation_type].add(related_id)

    def remove_relation(self, player_id, relation_type, related_id):
        if player_id in self._relations:
            rel = self._relations[player_id].get(relation_type, set())
            rel.discard(related_id)

    def clear_relation_type(self, player_id, relation_type):
        """清空某玩家的某种关系标记"""
        if player_id in self._relations:
            self._relations[player_id][relation_type] = set()

    def has_relation(self, player_id, relation_type, related_id=None):
        rel = self._relations.get(player_id, {}).get(relation_type, set())
        if related_id is None:
            return len(rel) > 0
        return related_id in rel

    def get_related(self, player_id, relation_type):
        return set(self._relations.get(player_id, {}).get(relation_type, set()))

    # ---- 联动方法 ----

    def on_player_move(self, player_id):
        """
        玩家移动时自动清理：
        1. 清除所有「被X锁定」
        2. 清除所有「面对面」（双向）
        """
        # 清除别人对我的锁定
        lockers = self.get_related(player_id, "LOCKED_BY")
        for locker_id in list(lockers):
            self.remove_relation(player_id, "LOCKED_BY", locker_id)

        # 清除面对面（双向）
        engaged = self.get_related(player_id, "ENGAGED_WITH")
        for other_id in list(engaged):
            self.remove_relation(player_id, "ENGAGED_WITH", other_id)
            self.remove_relation(other_id, "ENGAGED_WITH", player_id)

    def on_player_wake_up(self, player_id):
        """玩家起床"""
        self.remove(player_id, "SLEEPING")

    def on_player_death(self, player_id):
        """
        玩家死亡时清理所有相关标记：
        1. 清除别人对该玩家的锁定
        2. 清除该玩家对别人的锁定
        3. 清除所有与该玩家相关的面对面（双向）
        4. 清除所有探测关系
        5. 清除该玩家的所有简单标记
        """
        # 1. 别人对我的锁定
        lockers = self.get_related(player_id, "LOCKED_BY")
        for lid in list(lockers):
            self.remove_relation(player_id, "LOCKED_BY", lid)
        # 2. 我对别人的锁定：遍历所有玩家，移除我作为锁定者的记录
        for other_id in list(self._relations.keys()):
            if other_id == player_id:
                continue
            self.remove_relation(other_id, "LOCKED_BY", player_id)
        # 3. 面对面（双向）
        engaged = self.get_related(player_id, "ENGAGED_WITH")
        for eid in list(engaged):
            self.remove_relation(player_id, "ENGAGED_WITH", eid)
            self.remove_relation(eid, "ENGAGED_WITH", player_id)
            # 恢复对方被压制的隐身（如果有）
            if self.has(eid, "INVISIBLE_SUPPRESSED"):
                self.remove(eid, "INVISIBLE_SUPPRESSED")
                self.add(eid, "INVISIBLE")
        # 4. 探测关系
        detected_by = self.get_related(player_id, "DETECTED_BY")
        for did in list(detected_by):
            self.remove_relation(player_id, "DETECTED_BY", did)
        # 我探测别人的记录
        for other_id in list(self._relations.keys()):
            if other_id == player_id:
                continue
            self.remove_relation(other_id, "DETECTED_BY", player_id)
        # 5. 清空该玩家所有简单标记
        self._simple[player_id] = set()

    def on_player_go_invisible(self, player_id, all_players):
        """
        玩家进入隐身时：
        1. 添加 INVISIBLE 标记
        2. 对所有无探测能力的观察者：使其对该玩家的锁定失效
        3. 面对面不因隐身解除（规则明确）
        """
        self.add(player_id, "INVISIBLE")

        for p in all_players:
            if p.player_id == player_id:
                continue
            if not p.has_detection:
                # 该观察者无法探测 → 锁定失效
                self.remove_relation(player_id, "LOCKED_BY", p.player_id)

    def on_player_detected(self, observer_id, target_id):
        """探测者发现隐身目标：添加 DETECTED_BY 关系"""
        self.add_relation(target_id, "DETECTED_BY", observer_id)

    def on_player_lose_invisible(self, player_id):
        """玩家失去隐身"""
        self.remove(player_id, "INVISIBLE")
        # 清除所有 DETECTED_BY（不再需要，因为本身就可见了）
        self.clear_relation_type(player_id, "DETECTED_BY")

    def on_engaged_melee_attack_by_invisible(self, attacker_id, target_id):
        """
        面对面状态下隐身方对对方造成伤害：
        隐身状态在本次面对面关系解除前立刻失效。
        用 INVISIBLE_SUPPRESSED 标记追踪，面对面解除时恢复。
        """
        if self.has(attacker_id, "INVISIBLE"):
            self.remove(attacker_id, "INVISIBLE")
            self.add(attacker_id, "INVISIBLE_SUPPRESSED")

    def on_engaged_broken(self, player_a, player_b):
        """
        面对面关系解除时：
        检查是否有因面对面而被压制的隐身，恢复之。
        """
        self.remove_relation(player_a, "ENGAGED_WITH", player_b)
        self.remove_relation(player_b, "ENGAGED_WITH", player_a)

        # 恢复被压制的隐身
        if self.has(player_a, "INVISIBLE_SUPPRESSED"):
            self.remove(player_a, "INVISIBLE_SUPPRESSED")
            self.add(player_a, "INVISIBLE")
        if self.has(player_b, "INVISIBLE_SUPPRESSED"):
            self.remove(player_b, "INVISIBLE_SUPPRESSED")
            self.add(player_b, "INVISIBLE")

    def on_stun(self, player_id):
        """玩家进入眩晕"""
        self.add(player_id, "STUNNED")

    def on_stun_recover(self, player_id):
        """玩家从眩晕中苏醒"""
        self.remove(player_id, "STUNNED")

    def on_shock(self, player_id):
        """玩家进入震荡"""
        self.add(player_id, "SHOCKED")

    def on_shock_recover(self, player_id):
        """玩家从震荡中苏醒（消耗1行动回合）"""
        self.remove(player_id, "SHOCKED")
        # 震荡和眩晕不叠加，解除一个另一个也解除
        self.remove(player_id, "STUNNED")

    def on_petrify(self, player_id):
        """玩家进入石化"""
        self.add(player_id, "PETRIFIED")

    def on_petrify_recover(self, player_id):
        """玩家解除石化"""
        self.remove(player_id, "PETRIFIED")

    def is_visible_to(self, target_id, observer_id, observer_has_detection):
        """
        判断 target 对 observer 是否可见。
        规则：
          - 默认可见
          - 隐身 → 对无探测者不可见
          - 探测 → 隐身目标视为「被发现」→ 可见
          - INVISIBLE_SUPPRESSED 视为可见（隐身被压制中）
        """
        if not self.has(target_id, "INVISIBLE"):
            return True
        # target 隐身中
        if observer_has_detection:
            return True
        # 被发现也算可见
        if self.has_relation(target_id, "DETECTED_BY", observer_id):
            return True
        return False

    def set_engaged(self, player_a, player_b):
        """建立双向面对面关系"""
        self.add_relation(player_a, "ENGAGED_WITH", player_b)
        self.add_relation(player_b, "ENGAGED_WITH", player_a)

    def disengage(self, player_a, player_b):
        """解除双向面对面关系（含隐身恢复）"""
        self.on_engaged_broken(player_a, player_b)

    def describe_markers(self, player_id):
        """返回某玩家的标记描述字符串"""
        parts = []
        simple = self.get_all_simple(player_id)
        for m in sorted(simple):
            if m == "SLEEPING":
                continue  # 睡眠在别处显示
            display_map = {
                "INVISIBLE": "🫥隐身",
                "INVISIBLE_SUPPRESSED": "🫥隐身(压制中)",
                "STUNNED": "💫眩晕",
                "SHOCKED": "⚡震荡",
                "PETRIFIED": "🗿石化",
                "MISSILE_CTRL": "🚀导弹控制权",
                "POLICE_PROTECT": "🛡️警察保护",
            }
            parts.append(display_map.get(m, m))

        locked_by = self.get_related(player_id, "LOCKED_BY")
        for lid in locked_by:
            parts.append(f"🎯被{lid}锁定")

        engaged = self.get_related(player_id, "ENGAGED_WITH")
        for eid in engaged:
            parts.append(f"👊与{eid}面对面")

        return " ".join(parts) if parts else "无异常"
