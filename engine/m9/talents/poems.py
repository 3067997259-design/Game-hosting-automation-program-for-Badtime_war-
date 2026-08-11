"""M9 G5 诗篇十四首执行器（profile: m9-rfc，诗篇合同 v0.1）。

共享入口（§1.1）：仅德谬歌；T0 + 本轮公演位 + 2 SP + poem_cost 追忆 + 选目标
（须持有对应天赋）+ 爱愿（6 ticks，目标非 G5 自己且非双人局）。
十四首（§二）：游侠/地火/群星/律法/阴阳/永恒/爱与记忆/飞萤/欢愉/守夜人/
彼岸/追光/负世/明天；诡计 retired、旋律 suspended 不实现。
简化标记（§2.15）：B4/G0 魂援专用七枚，`grant_simplified_marker` 提供。
数值一律读 `m9_talents_extended.g5.*`（[待风洞]）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from engine.balance import get as bget


def _g5(key: str, default):
    return bget("m9_talents_extended", "g5", key, default=default)


# 十四首：诗篇名 → 目标天赋槽位
POEM_TARGETS: Dict[str, str] = {
    "游侠": "T1", "地火": "T2", "群星": "T3", "律法": "T6", "阴阳": "T4",
    "永恒": "G3", "爱与记忆": "G5", "飞萤": "G1", "欢愉": "G6",
    "守夜人": "G7", "彼岸": "T7", "追光": "G2", "负世": "G4", "明天": "G0",
}

# 简化标记白名单（§2.15 七枚）
SIMPLIFIED_MARKERS: Dict[str, str] = {
    "游侠": "ranger_chase", "群星": "stars_bounce", "阴阳": "yin_yang_reroll",
    "永恒": "eternity_discount", "飞萤": "firefly_reduce",
    "追光": "spotlight_damage", "明天": "tomorrow_free",
}


# 天赋短名 → 槽位（v2exp/M9 类 name 与 TALENT_TABLE 显示名不一致，显式映射）
_TALENT_SHORT_SLOT: Dict[str, str] = {
    "一刀缭断": "T1", "剪刀手一突": "T2", "天星": "T3", "六爻": "T4",
    "combo": "T5", "朝阳好市民": "T6", "死者苏生": "T7",
    "火萤IV型-完全燃烧": "G1", "神代天赋-火萤IV型-完全燃烧": "G1",
    "请一直注视着我": "G2", "神代天赋-请一直注视着我": "G2",
    "神话之外": "G3", "神代天赋-神话之外": "G3",
    "愿负世，照拂黎明": "G4", "神代天赋-愿负世，照拂黎明": "G4",
    "往世的涟漪": "G5", "神代天赋-往世的涟漪": "G5",
    "要有笑声！": "G6", "神代天赋-要有笑声！": "G6",
    "大叔我啊，剪短发了": "G7", "神代天赋-大叔我啊，剪短发了": "G7",
}


def _talent_slot(player: Any) -> str:
    """玩家天赋槽位（短名/显示名映射；测试桩名原样返回）。"""
    t = getattr(player, "talent", None)
    if t is None:
        return ""
    name = getattr(t, "name", "")
    slot = _TALENT_SHORT_SLOT.get(name)
    if slot is not None:
        return slot
    return name  # 未登记（测试桩）按 name 原样


class PoeticRecital:
    """献诗编排与十四首执行器。"""

    def __init__(self, g5: Any) -> None:
        self.g5 = g5          # Ripple9 实例
        self.state = g5.state

    # ── 共享入口 ──

    def recite(self, player: Any, poem_name: str, target_pid: str) -> str:
        """共享入口：形态/公演位/SP/追忆/天赋绑定/爱愿 全部预检先于消费。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled():
            return "❌ M9 未启用"
        if self.g5.form != "demiurge":
            return "❌ 仅德谬歌可献诗"
        if self.g5.active_anchor:
            return "❌ 锚定监控期内不可献诗"
        slot = POEM_TARGETS.get(poem_name)
        if slot is None:
            return f"❌ 未知诗篇 {poem_name}"
        target = self.state.get_player(target_pid)
        if target is None or not target.is_alive():
            return "❌ 目标无效"
        if slot != "G5" and _talent_slot(target) != slot:
            return f"❌ 目标未持有 {slot} 天赋"
        if poem_name == "爱与记忆" and target_pid != self.g5.player_id:
            return "❌ 爱与记忆只可献给自己"
        m9 = getattr(self.state, "m9_system", None)
        round_num = getattr(self.state, "current_round", 1)
        cost = int(_g5("poem_cost", 12))
        if m9 is None or m9.get_sp(self.g5.player_id) < 2:
            return "❌ SP 不足"
        if self.g5.sealed_reminiscence < cost:
            return "❌ 追忆不足"
        if m9.assign_public_slot(round_num) != self.g5.player_id:
            if not m9.register_performance(self.g5.player_id, round_num):
                return "❌ 公演位不足"
        if m9.dispatch_public(self.g5.player_id, round_num) is None:
            return "❌ 公演派发失败"
        self.g5.sealed_reminiscence -= cost
        # 爱愿（目标非自己且非双人局）
        if target_pid != self.g5.player_id and len(self.state.player_order) > 2:
            self._grant_love_wish(target_pid)
        executor = getattr(self, f"_poem_{poem_name}", None)
        if executor is None:
            return f"❌ 诗篇「{poem_name}」执行器未实现"
        return executor(player, target)

    def _grant_love_wish(self, target_pid: str) -> None:
        """爱愿：6 个未来 R4 tick（§1.2）；持有者不能伤害/负向 G5。"""
        ticks = int(_g5("poem_lovewish_ticks", 6))
        self.g5.love_wish[target_pid] = ticks
        self.state.log_event("love_wish_granted", player=self.g5.player_id,
                             target=target_pid, ticks=ticks)

    def tick_love_wishes(self) -> None:
        """R4：爱愿 tick 递减（G5 自身 on_round_end 调用）。"""
        expired = []
        for pid, left in self.g5.love_wish.items():
            left -= 1
            if left <= 0:
                expired.append(pid)
            else:
                self.g5.love_wish[pid] = left
        for pid in expired:
            del self.g5.love_wish[pid]

    # ── 标记辅助 ──

    @staticmethod
    def _marker(target: Any, key: str, value: Any = True) -> None:
        t = getattr(target, "talent", None)
        if t is None:
            return
        if not hasattr(t, "m9_poem_markers"):
            t.m9_poem_markers = {}
        t.m9_poem_markers[key] = value

    # ── 十四首 ──

    def _poem_游侠(self, player, target):
        """T1：游侠的锋刃（下次 T1 斩击公演 chase-move+面对面斩击，用后消耗）。"""
        self._marker(target, "ranger_blade", True)
        return f"⚔️ 献予「游侠」之诗：{target.name} 获得「游侠的锋刃」标记"

    def _poem_地火(self, player, target):
        """T2：完整额外行动（白名单 g5_poem_earthfire）+ 隐身 + 追猎反应。"""
        m9 = getattr(self.state, "m9_system", None)
        round_num = getattr(self.state, "current_round", 1)
        grant = None
        if m9 is not None:
            grant = m9.dispatch_full_extra(target.player_id, round_num,
                                           "g5_poem_earthfire")
        if grant is None:
            return f"🌋 献予「地火」之诗：{target.name} 完整额外行动已满/递归超限"
        if not getattr(target, "is_invisible", False):
            target.is_invisible = True
            from engine.m9.gate import m9_enabled
            self.state.markers.add(target.player_id, "INVISIBLE") \
                if hasattr(self.state.markers, "add") else None
        return f"🌋 献予「地火」之诗：{target.name} 获得完整额外行动 + 隐身"

    def _poem_群星(self, player, target):
        """T3：群星的弹射标记 + 尘世之锁转化（现有石化升级）。"""
        self._marker(target, "stars_bounce", True)
        self.state.log_event("poem_stars", player=self.g5.player_id,
                             target=target.player_id)
        return f"⭐ 献予「群星」之诗：{target.name} 获得「群星的弹射」标记"

    def _poem_律法(self, player, target):
        """T6：阶梯判定（通缉结案 → 队长候选 → 威信+2/配装 → 配装）。"""
        police = getattr(self.state, "police", None)
        lines = [f"📜 献予「律法」之诗：{target.name}"]
        if police is not None and not getattr(police, "permanently_disabled", False):
            wanted = getattr(police, "reported_target_id", None)
            if wanted == target.player_id:
                police.reported_target_id = None
                lines.append("（当前通缉已结案）")
                return "；".join(lines)
        if target.player_id == getattr(police, "captain_id", None) if police else False:
            if hasattr(police, "authority"):
                police.authority += 2
                lines.append("（队长威信 +2）")
        else:
            lines.append("（为一名存活警察配装 T6 装备——机制挂接随警察层）")
        return "；".join(lines)

    def _poem_阴阳(self, player, target):
        """T4：阴阳的天机 ×2 计数（不得指定或跃在渊）。"""
        self._marker(target, "yin_yang_tianji", 2)
        return f"☯️ 献予「阴阳」之诗：{target.name} 获得「阴阳的天机」×2"

    def _poem_永恒(self, player, target):
        """G3：下次结界公演维持费 −poem_eternity_cost_reduction。"""
        self._marker(target, "eternity_discount", True)
        return f"🌌 献予「永恒」之诗：{target.name} 获得结界维持折扣标记"

    def _poem_爱与记忆(self, player, target):
        """G5 自身：n 段伤害（段数成长 +1，上限 6；前 4 段属性序列）。"""
        from engine.m9.combat import resolve_damage
        n_players = len(self.state.player_order)
        base = {2: 2, 4: 3}.get(n_players, 4)
        stages = min(base + getattr(self, "_destiny_stages", 0),
                     int(_g5("poem_destiny_max_stages", 6)))
        setattr(self, "_destiny_stages",
                min(getattr(self, "_destiny_stages", 0) + 1,
                    int(_g5("poem_destiny_max_stages", 6))))
        dmg = int(_g5("poem_destiny_stage_damage", 2))
        attrs = ["科技", "普通", "魔法", "__无视__"]
        me = self.state.get_player(self.g5.player_id)
        lines = [f"💞 献予「爱与记忆」之诗：{stages} 段伤害"]
        others = [pid for pid in self.state.player_order
                  if self.state.get_player(pid).is_alive()]
        for i in range(stages):
            pid = others[i % len(others)]
            t = self.state.get_player(pid)
            attr = attrs[i] if i < 4 else "__无视__"
            r = resolve_damage(me, t, weapon=None, game_state=self.state,
                               raw_damage_override=dmg,
                               damage_attribute_override=attr,
                               source_kind="g5_poem_destiny")
            lines.append(f"  段{i+1}（{attr}）→ {t.name} {r['hp_damage']} 伤")
        return "\n".join(lines)

    def _poem_飞萤(self, player, target):
        """G1：飞萤的回响标记（R4 失熵 −1、调息 +1、6 ticks）。"""
        self._marker(target, "firefly_echo", int(_g5("poem_firefly_duration", 6)))
        return f"✨ 献予「飞萤」之诗：{target.name} 获得「飞萤的回响」标记"

    def _poem_欢愉(self, player, target):
        """G6：欢愉的延展标记（窗口 2 轮 + 双借用，6 ticks 到期）。"""
        target.talent.joy_extend = True  # CutawayJoke9 已读此属性
        self._marker(target, "joy_extend", int(_g5("poem_joy_max_duration", 6)))
        return f"🎭 献予「欢愉」之诗：{target.name} 获得「欢愉的延展」标记"

    def _poem_守夜人(self, player, target):
        """G7：必须接受；色彩 null/Terror 移除/永久 HP 转化/代价/恢复。"""
        ctrl = getattr(target, "controller", None)
        try:
            accept = ctrl.confirm(f"{target.name} 是否接受「守夜人」之诗？")
        except Exception:
            accept = True
        if not accept:
            return "❌ 目标拒绝守夜人之诗（追忆已消耗，效果不发生）"
        t = target.talent
        lines = [f"🌙 献予「守夜人」之诗：{target.name}"]
        if hasattr(t, "color"):
            t.color_is_null = True
            lines.append("色彩置 null")
        if getattr(t, "is_terror", False):
            t.is_terror = False
            t.permanent_extra_hp = getattr(t, "permanent_extra_hp", 0.0) \
                + getattr(t, "terror_extra_hp", 0.0)
            t.terror_extra_hp = 0.0
            lines.append("Terror 移除，额外 HP 转为永久")
        cap = int(_g5("poem_watchman_spend_cap", 2))
        spend = min(cap, getattr(t, "permanent_extra_hp", 0.0))
        t.permanent_extra_hp = getattr(t, "permanent_extra_hp", 0.0) - spend
        lines.append(f"代价 {spend}（不触基础 HP）")
        if getattr(t, "fusion_shield_done", False):
            t.iron_horus_hp = max(getattr(t, "iron_horus_hp", 0),
                                  int(_g5("poem_watchman_armor_restore", 2)))
            lines.append("铁之荷鲁斯恢复")
        if hasattr(t, "tactical_unlocked"):
            t.tactical_unlocked = True
        return "；".join(lines)

    def _poem_彼岸(self, player, target):
        """T7：彼岸的守望标记（保险复活时 SP2 + 带装备）。"""
        self._marker(target, "far_shore_watch", True)
        return f"🌸 献予「彼岸」之诗：{target.name} 获得「彼岸的守望」标记"

    def _poem_追光(self, player, target):
        """G2：追光的聚焦标记（普通影身攻击加值 + 有效伤害治疗）。"""
        self._marker(target, "spotlight_focus", True)
        return f"☀️ 献予「追光」之诗：{target.name} 获得「追光的聚焦」标记"

    def _poem_负世(self, player, target):
        """G4：人形态 +2 火种（正来源配额）；救世主形态 → 毁伤；解锁主动燃尽。"""
        t = target.talent
        lines = [f"🌅 献予「负世」之诗：{target.name}"]
        if hasattr(t, "form") and t.form in ("full_savior", "incomplete_savior"):
            ann = int(_g5("poem_burden_annihilation", 5))
            t.ruin_damage = getattr(t, "ruin_damage", 0) + ann
            lines.append(f"形态内：毁伤 +{ann}")
        elif hasattr(t, "on_positive_talent_used"):
            before = t.divinity
            t.on_positive_talent_used(player, is_limited=False)
            gained = t.divinity - before
            # 正来源每轮首次 +1；诗篇尝试 +2、按轮预算截断
            if gained < 2 and t.divinity < 12:
                t.divinity = min(12, t.divinity + (2 - gained))
                t.ember = t.divinity
                gained = 2
            lines.append(f"火种 +{gained}")
        t.m9_burden_unlocked = True
        return "；".join(lines)

    def _poem_明天(self, player, target):
        """G0：明天的承诺标记（遗物支援不毁装/不耗 HP，6 ticks 或 3 uses）。"""
        self._marker(target, "tomorrow_promise",
                     int(_g5("poem_tomorrow_uses", 3)))
        return f"🌅 献予「明天」之诗：{target.name} 获得「明天的承诺」标记"

    # ── 简化标记（§2.15：B4/G0 魂援专用，非献诗）──

    def grant_simplified_marker(self, target_pid: str, marker: str) -> bool:
        """授予一枚简化标记；不要求天赋、不消耗追忆、不授爱愿。"""
        if marker not in SIMPLIFIED_MARKERS.values():
            return False
        target = self.state.get_player(target_pid)
        if target is None:
            return False
        self._marker(target, f"simplified:{marker}", True)
        return True
