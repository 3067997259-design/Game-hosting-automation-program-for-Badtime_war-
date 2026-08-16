"""M9 G0 砂狼白子*Terror 天赋适配器（profile: m9-rfc，G0 RFC v0.3）。

独立天赋类（不继承 v2exp Combo——T5 已退役，禁止移植 combo 任何机制）。
基于 `M9TalentStub` 提供 v2exp 钩子安全空实现，核心机制：

- **BLACK FANG 465（AR）**：开局替换弓；真实攻击扣弹，find/交互/掉落箭矢按比例装弹；
  与无人机协同时属性变「神秘」——三属性试算取目标侧最高 H，并用于伤害结算、
  护甲耐久与事件登记（探针磨耐久会快照还原，只让结算磨一次）。
- **无人机**：即演（1 SP + 当前 HP 20%）召唤；固定轮数、未来 R4 tick
  （建立轮不 tick）；跟随 G0 地点；可被攻击（非犯罪，为 G0 记高影响关注）；
  协同攻击追加科技属性伤害；十字炮火后消失；G0 死亡/退场立即消失。
- **公演（2 SP + 20% HP）**：选项 A 十字炮火（DIRECT_DAMAGE 全员含自身，
  无人机消失）/ 选项 B 遗物支援技（摧毁遗物装备释放原持有者天赋的增强支援技，
  无人机保留；13 槽 T1-T7/G1-G7，T5/G0 不可）。
- **遗物追忆池**：G5 遗物 +6（cap 12）；满 12 可在遗物支援公演中花费全部
  追忆为自己兑换一枚简化标记诗篇（游侠/群星/阴阳/永恒/飞萤/追光/明天白名单）。
- **调整呼吸**：非 absolute_death 致死自动触发（每局一次）：HP 锁
  breath_min_hp、免疫普通攻击伤害与普通 debuff（ACTION_SUPPRESSED/自我怀疑
  不豁免；直达伤害不豁免）；到期 HP 未满 → 退场（撤退，非死亡）。
- **撤退**：不能行动、不可被指定为目标、PP 冻结、装备以带原槽位的遗留登记、
  无击杀关系。

数值一律读 `m9_talents_extended.g0.*`（[待风洞]）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from engine.balance import get as bget
from engine.m9.talents.stub import M9TalentStub
from engine.m9.text import m9_text
from models.equipment import Attribute as EqAttr, Weapon, WeaponRange

AR_WEAPON_NAME = "BLACK FANG 465"

# 简化标记诗篇白名单（G5 诗篇 RFC v0.1 §2.15；与 poems.SIMPLIFIED_MARKERS 键一致）
REDUCED_POEM_WHITELIST: Tuple[str, ...] = (
    "游侠", "群星", "阴阳", "永恒", "飞萤", "追光", "明天",
)

# 神秘属性试算顺序（并列取首个，确定性）
_MYSTERY_ATTRIBUTES: Tuple[str, ...] = ("科技", "魔法", "普通")


class G0DroneActor:
    """无人机的只读 actor 视图。

    无人机可作为攻击目标，但没有标准行动槽，也不进入 ``iter_actors``，从而
    不会被 G3 计入捕捉人数/维持费。生命值权威数据始终留在 G0 adapter。
    """

    _m9_drone_actor = True
    is_auxiliary_actor = True
    talent = None
    is_invisible = False
    has_detection = False

    def __init__(self, talent: "ShirokoTerror9") -> None:
        self.owner_talent = talent
        self.owner_pid = talent.player_id
        self.player_id = f"g0_drone:{talent.player_id}"

    @property
    def name(self) -> str:
        owner = self.owner_talent.state.get_player(self.owner_pid)
        owner_name = getattr(owner, "name", self.owner_pid)
        return m9_text("talents.g0.drone_actor_name", owner=owner_name)

    @property
    def location(self) -> Optional[str]:
        return self.owner_talent.drone_location()

    @property
    def hp(self) -> int:
        drone = self.owner_talent.drone
        return int(drone["hp"]) if drone is not None else 0

    @property
    def max_hp(self) -> int:
        drone = self.owner_talent.drone
        return int(drone["max_hp"]) if drone is not None else 0

    def is_alive(self) -> bool:
        return self.owner_talent.is_drone_present() and self.hp > 0

    def is_on_map(self) -> bool:
        return self.is_alive() and self.location is not None

    def can_be_targeted(self) -> bool:
        return self.is_on_map()


def _g0(key: str, default):
    return bget("m9_talents_extended", "g0", key, default=default)


def _half_up(value: float) -> int:
    """half-up 四舍五入（§6.3：整数伤害/治疗/护盾/掩体耐久）。"""
    return int(math.floor(float(value) + 0.5))


class ShirokoTerror9(M9TalentStub):
    """M9 G0（m9-rfc 实例化；独立类，不继承 legacy Combo）。"""

    name = "砂狼白子*Terror"

    def __init__(self, player_id: str, game_state: Any) -> None:
        self.player_id = player_id
        self.state = game_state

        # ── BLACK FANG 465（AR 替换弓）──
        self.magazine = int(_g0("ar_magazine", 30))
        self._install_ar()

        # ── 无人机 ──
        self.drone: Optional[Dict[str, Any]] = None   # hp/max_hp/duration_left/…
        self.drone_established_round: Optional[int] = None
        self._drone_actor = G0DroneActor(self)

        # ── 遗物支援技 ──
        self.relics: List[Dict[str, str]] = []        # [{"name", "slot"}]
        self.relic_memory: int = 0
        self.m9_poem_markers: Dict[str, Any] = {}     # 简化诗篇等标记
        self.t1_relic_armed = False                   # 下次 AR 攻击无视护甲 ×mult
        self.t2_stealth_rounds = 0
        self.t4_half_next = False                     # 否卦：下次受伤减半
        self.t7_revive = False                        # 临时复活（一次性）
        self.g3_projection_rounds = 0                 # 螺旋剑（伪）剩余轮
        self.g3_projection_bonus = 0
        self.g4_ash_layers = 0                        # 余烬护甲层数
        self.g7_cover_hp = 0                          # 临时掩体耐久
        self.g7_cover_rounds = 0

        # ── 调整呼吸 / 撤退 ──
        self.breath_uses_left = int(_g0("breath_max_uses", 1))
        self.breath_active = False
        self.breath_rounds = 0
        self.breath_established_round: Optional[int] = None
        self.breath_deadline_round: Optional[int] = None  # 触发+4 轮：HP>50% 否则退场
        self.breath_min_hp = int(_g0("breath_min_hp", 1))
        self.breath_duration = int(_g0("breath_duration", 2))
        self.retreated = False

        # 世界援助状态机（机制遗产；gate 不实例化，adapter 挂自身属性）
        self.world_aid: Any = None
        pp = getattr(self.state, "m9_pp", None)
        if pp is not None:
            from engine.m9.g0_world_poem import WorldPoemAid
            self.world_aid = WorldPoemAid(has_g0_in_pool=True, pp=pp)

    # ════════════════════════════════════════════════════════
    #  常驻被动：BLACK FANG 465 / 箭矢转化
    # ════════════════════════════════════════════════════════

    def _install_ar(self) -> None:
        """开局 AR 替换弓：移除起始弓，装备 BLACK FANG 465。"""
        me = self.state.get_player(self.player_id)
        if me is None:
            return
        for w in list(getattr(me, "weapons", [])):
            if w is not None and getattr(w, "name", "") == "弓":
                me.weapons.remove(w)
        if not any(getattr(w, "name", "") == AR_WEAPON_NAME
                   for w in getattr(me, "weapons", []) if w is not None):
            me.weapons.append(Weapon(
                AR_WEAPON_NAME, EqAttr.TECH,
                int(_g0("ar_base_damage", 3)), WeaponRange.RANGED))

    def convert_arrow_gain(self, arrows: int) -> int:
        """箭矢→子弹数量换算；不直接改弹匣，供所有资源入口共用。"""
        ratio = int(_g0("arrow_to_bullet_ratio", 3))
        bullets = max(0, int(arrows)) * ratio
        self.state.log_event("g0_arrow_to_bullet", player=self.player_id,
                             arrows=int(arrows), ratio=ratio, bullets=bullets)
        return bullets

    def receive_arrows(self, arrows: int, *,
                       source: str = "unknown") -> Dict[str, int]:
        """按弹匣空间转换箭矢，返回实际消耗箭数与装入子弹数。"""
        available = max(0, int(arrows))
        ratio = int(_g0("arrow_to_bullet_ratio", 3))
        capacity = int(_g0("ar_magazine", 30))
        space = max(0, capacity - self.magazine)
        loaded = min(available * ratio, space)
        consumed = min(
            available,
            int(math.ceil(loaded / ratio)) if loaded > 0 and ratio > 0 else 0,
        )
        if consumed:
            self.convert_arrow_gain(consumed)
        self.magazine += loaded
        self.state.log_event(
            "g0_ammo_loaded",
            player=self.player_id,
            source=source,
            arrows=consumed,
            bullets_loaded=loaded,
            magazine=self.magazine,
            capacity=capacity,
        )
        return {"arrows_consumed": consumed, "bullets_loaded": loaded}

    def has_ar(self) -> bool:
        me = self.state.get_player(self.player_id)
        if me is None:
            return False
        return any(getattr(w, "name", "") == AR_WEAPON_NAME
                   for w in getattr(me, "weapons", []) if w is not None)

    # ════════════════════════════════════════════════════════
    #  无人机
    # ════════════════════════════════════════════════════════

    def is_drone_present(self) -> bool:
        return self.drone is not None and not self.retreated

    @property
    def drone_target_id(self) -> str:
        return f"g0_drone:{self.player_id}"

    def drone_actor(self) -> Optional[G0DroneActor]:
        """返回当前可指定的无人机 actor；不在场时 fail-closed。"""
        if not self.is_drone_present():
            return None
        return self._drone_actor

    def get_auxiliary_actor(self, actor_id: str) -> Optional[G0DroneActor]:
        if actor_id != self.drone_target_id:
            return None
        return self.drone_actor()

    def drone_location(self) -> Optional[str]:
        """无人机位置 = G0 当前位置（G0 每次重定位自动同步，权威信源）。"""
        me = self.state.get_player(self.player_id)
        return getattr(me, "location", None) if me is not None else None

    def attack_drone(self, attacker: Any, damage: int) -> Dict[str, Any]:
        """他人攻击无人机：不构成犯罪；为 G0 登记高影响关注（走关注/SP 结算）。
        摧毁（HP≤0）后无人机立即消失，G0 不受伤害。"""
        if self.drone is None:
            return {"success": False, "reason": "no_drone"}
        dmg = max(0, int(damage))
        self.drone["hp"] = max(0, self.drone["hp"] - dmg)
        m9 = getattr(self.state, "m9_system", None)
        attended = False
        if m9 is not None:
            round_num = getattr(self.state, "current_round", 1)
            if m9.can_attend(round_num, self.player_id):
                attended = m9.mark_attention(round_num, self.player_id)
        self.state.log_event(
            "g0_drone_attacked", player=self.player_id,
            attacker=getattr(attacker, "player_id",
                             getattr(attacker, "name", "?")),
            damage=dmg, not_a_crime=True, attention=attended)
        if self.drone["hp"] <= 0:
            self._vanish_drone("destroyed")
            return {"success": True, "destroyed": True, "attention": attended}
        return {"success": True, "destroyed": False, "attention": attended}

    def _vanish_drone(self, reason: str) -> None:
        if self.drone is None:
            return
        self.drone = None
        self.state.log_event("g0_drone_vanish", player=self.player_id,
                             reason=reason)

    # ════════════════════════════════════════════════════════
    #  即演（1 SP）：召唤无人机
    # ════════════════════════════════════════════════════════

    def _do_summon(self, player: Any, m9: Any, round_num: int) -> Tuple[str, bool]:
        if self.drone is not None:
            return m9_text("talents.g0.err_drone_already_present"), False
        if getattr(player, "hp", 0) <= 0:
            return m9_text("talents.g0.err_cannot_pay_hp_cost"), False
        cost = self._hp_cost_pct(player, "drone_hp_cost")
        grant = m9.dispatch_improvise(self.player_id, round_num,
                                      source_id="g0_drone")
        if grant is None:
            return m9_text("talents.g0.err_sp_insufficient"), False
        if not self._pay_hp_cost(player, cost, "g0_drone_hp_cost"):
            return m9_text("talents.g0.summon_hp_cost_death", name=player.name), True
        from engine.m9.talent_registry import g0_drone_stats
        stats = g0_drone_stats()
        duration = int(_g0("drone_duration", 3))
        self.drone = {
            "hp": stats["max_hp"],
            "max_hp": stats["max_hp"],
            "duration_left": duration,
            "rounds_left": duration,
            "established_round": round_num,
        }
        self.drone_established_round = round_num
        self.state.log_event("g0_drone_summon", player=self.player_id,
                             hp_cost=cost, sp=1, round=round_num)
        return (m9_text("talents.g0.summon_success", name=player.name, cost=cost),
                True)

    # ════════════════════════════════════════════════════════
    #  公演（2 SP）：十字炮火 / 遗物支援技
    # ════════════════════════════════════════════════════════

    def _do_performance(self, player: Any, m9: Any,
                        round_num: int) -> Tuple[str, bool]:
        if self.drone is None:
            return m9_text("talents.g0.err_drone_absent"), False
        if getattr(player, "hp", 0) <= 0:
            return m9_text("talents.g0.err_cannot_pay_hp_cost"), False
        ctrl = getattr(player, "controller", None)
        options = [
            m9_text("talents.g0.performance_option_crossfire"),
            m9_text("talents.g0.performance_option_relic"),
        ]
        try:
            choice = ctrl.choose(
                m9_text("talents.g0.performance_choose_prompt"), options)
        except Exception:
            choice = options[0]
        if choice not in options:
            choice = options[0]
        if "遗物" in choice or "支援" in choice:
            return self._do_relic_support(player, m9, round_num)
        return self._do_crossfire(player, m9, round_num)

    def _do_crossfire(self, player: Any, m9: Any,
                      round_num: int) -> Tuple[str, bool]:
        """选项 A：十字炮火——地点全员（含 G0）受伤，无人机消失。

        2026-09 风洞 R7：由 DIRECT_DAMAGE 改为普通属性 + 1000 命中加值
        （必中但护甲可减免），压顶 G0 头部胜率。
        """
        if self.drone is None:
            return m9_text("talents.g0.err_drone_absent"), False
        if not self._ensure_public_seat(player, m9, round_num):
            return m9_text("talents.g0.err_sp_or_public_seat"), False
        cost = self._hp_cost_pct(player, "crossfire_hp_cost")
        if not self._pay_hp_cost(player, cost, "g0_crossfire_hp_cost"):
            return m9_text("talents.g0.crossfire_hp_cost_death",
                           name=player.name), True
        dmg = int(_g0("crossfire_damage", 3))
        loc = getattr(player, "location", None)
        lines = [m9_text("talents.g0.crossfire_cast",
                         name=player.name, cost=cost)]
        for target in self._units_at_location(loc):
            from engine.m9.combat import resolve_damage
            r = resolve_damage(player, target, weapon=None,
                               game_state=self.state,
                               raw_damage_override=dmg,
                               damage_attribute_override="普通",
                               accuracy_bonus=1000,  # 必中；护甲照常减免
                               source_kind="g0_crossfire")
            name = getattr(target, "name", getattr(target, "unit_id", "?"))
            lines.append(m9_text("talents.g0.damage_line",
                                 name=name, damage=r["hp_damage"]))
            if r.get("killed"):
                self._finalize_root_kill(player, target, r, "g0_crossfire")
        self._vanish_drone("crossfire")
        self.state.log_event("g0_crossfire", player=self.player_id,
                             location=loc, damage=dmg)
        return "\n".join(lines), True

    def _do_relic_support(self, player: Any, m9: Any,
                          round_num: int) -> Tuple[str, bool]:
        """选项 B：遗物支援技——摧毁一件遗物装备释放原天赋增强支援（无人机保留）。

        明天的承诺（诗篇 v0.1 §14）：遗物不摧毁（装备保留）、不支付 HP 成本
        （仍需 2 SP），每遗物对象限一次、共 poem_tomorrow_uses 次。"""
        if not self.relics:
            return m9_text("talents.g0.err_no_relics"), False
        if not self._ensure_public_seat(player, m9, round_num):
            return m9_text("talents.g0.err_sp_or_public_seat"), False
        tomorrow = self._tomorrow_available()
        if not tomorrow:
            cost = self._hp_cost_pct(player, "relic_support_hp_cost")
            if not self._pay_hp_cost(player, cost, "g0_relic_hp_cost"):
                return m9_text("talents.g0.relic_support_hp_cost_death",
                               name=player.name), True
        # 追忆满 12：可花费全部追忆兑换简化标记诗篇（不摧毁遗物）
        memory_cost = int(_g0("relic_memory_cost", 12))
        if self.relic_memory >= memory_cost:
            ctrl = getattr(player, "controller", None)
            options = [
                m9_text("talents.g0.option_redeem_poem", cost=memory_cost),
                m9_text("talents.g0.option_use_relic_support"),
            ]
            try:
                choice = ctrl.choose(
                    m9_text("talents.g0.relic_support_choose_prompt"), options)
            except Exception:
                choice = options[1]
            if choice not in options:
                choice = options[1]
            if "诗篇" in choice:
                poem = self._pick_poem(player)
                return self._grant_reduced_poem(player, poem), True
        relic = self._pick_relic(player)
        if relic is None:
            return m9_text("talents.g0.err_no_selectable_relic"), False
        if tomorrow:
            # 明天的承诺：遗物装备保留（标记为已消耗），不摧毁、不支付 HP
            self._consume_tomorrow_use()
            self.state.log_event("g0_tomorrow_free", player=self.player_id,
                                 item=relic["name"], slot=relic["slot"])
        else:
            self._consume_relic(player, relic)
        return self._apply_relic_effect(player, relic)

    def _tomorrow_available(self) -> bool:
        """明天的承诺标记是否仍有可用次数。"""
        markers = getattr(self, "m9_poem_markers", None)
        return bool(markers and int(markers.get("tomorrow_promise", 0)) > 0)

    def _consume_tomorrow_use(self) -> None:
        """消费一次明天的承诺；次数归零后标记消失。"""
        markers = getattr(self, "m9_poem_markers", None)
        if not markers:
            return
        uses = int(markers.get("tomorrow_promise", 0)) - 1
        if uses <= 0:
            markers.pop("tomorrow_promise", None)
        else:
            markers["tomorrow_promise"] = uses

    # ── 公演基础设施 ──

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        """T0 只消费 R0 已固化 holder；不得临时报名或改写队列。"""
        if m9.assign_public_slot(round_num) != player.player_id:
            return False
        return m9.dispatch_public(player.player_id, round_num) is not None

    def _hp_cost_pct(self, player: Any, key: str) -> int:
        """当前 HP 百分比代价：half-up 取整；HP>0 时至少 1。"""
        hp = getattr(player, "hp", 0)
        if hp <= 0:
            return 0
        pct = int(_g0(key, 20))
        return max(1, _half_up(float(hp) * pct / 100.0))

    def _pay_hp_cost(self, player: Any, cost: int, source_kind: str) -> bool:
        """支付显式 HP 成本；成本致死不属于攻击，不触发调整呼吸。"""
        player.hp = max(0, getattr(player, "hp", 0) - max(0, int(cost)))
        if player.hp > 0:
            return True
        from engine.m9.combat import finalize_death
        finalize_death(
            self.state,
            player,
            attacker=None,
            source_kind=source_kind,
            cause="g0_hp_cost",
        )
        return False

    # ════════════════════════════════════════════════════════
    #  遗物支援技（13 槽）
    # ════════════════════════════════════════════════════════

    def mark_relic(self, item_name: str, source_slot: str,
                   kind: str = "item") -> bool:
        """G0 拾取遗留时标记遗物装备（记录原持有者天赋槽位；T5/G0 不可）。"""
        if source_slot in ("T5", "G0"):
            return False
        self.relics.append(
            {"name": item_name, "slot": source_slot, "kind": kind})
        self.state.log_event("g0_relic_marked", player=self.player_id,
                             item=item_name, slot=source_slot)
        return True

    def _pick_relic(self, player: Any) -> Optional[Dict[str, str]]:
        if not self.relics:
            return None
        ctrl = getattr(player, "controller", None)
        names = [m9_text("talents.g0.relic_option_format",
                         name=r["name"], slot=r["slot"])
                 for r in self.relics]
        choice = names[0]
        if ctrl is not None:
            try:
                picked = ctrl.choose(
                    m9_text("talents.g0.relic_choose_prompt"), names)
                if picked in names:
                    choice = picked
            except Exception:
                pass
        for relic in self.relics:
            if m9_text("talents.g0.relic_option_format",
                       name=relic["name"], slot=relic["slot"]) == choice:
                return relic
        return self.relics[0]

    def _consume_relic(self, player: Any, relic: Dict[str, str]) -> None:
        self.relics.remove(relic)
        kind = relic.get("kind", "")
        if kind in ("", "weapon"):
            for weapon in list(getattr(player, "weapons", [])):
                if getattr(weapon, "name", "") == relic["name"]:
                    player.weapons.remove(weapon)
                    break
        if kind in ("", "armor"):
            armor = getattr(player, "armor", None)
            if armor is not None:
                pieces = (list(getattr(armor, "outer", []))
                          + list(getattr(armor, "inner", [])))
                for piece in pieces:
                    if getattr(piece, "name", "") != relic["name"]:
                        continue
                    layer = (armor.outer if piece in armor.outer
                             else armor.inner)
                    layer.remove(piece)
                    break
        for it in list(getattr(player, "items", [])):
            if getattr(it, "name", "") == relic["name"]:
                player.items.remove(it)
                break
        self.state.log_event("g0_relic_destroyed", player=self.player_id,
                             item=relic["name"], slot=relic["slot"])

    def _pick_poem(self, player: Any) -> str:
        ctrl = getattr(player, "controller", None)
        choice = REDUCED_POEM_WHITELIST[0]
        if ctrl is not None:
            try:
                picked = ctrl.choose(
                    m9_text("talents.g0.poem_choose_prompt"),
                    list(REDUCED_POEM_WHITELIST))
                if picked in REDUCED_POEM_WHITELIST:
                    choice = picked
            except Exception:
                pass
        return choice

    def _grant_reduced_poem(self, player: Any, poem_name: str) -> str:
        """花费全部追忆兑换一枚简化标记诗篇（白名单；目标 = G0 自己）。"""
        from engine.m9.talents.poems import SIMPLIFIED_MARKERS
        marker = SIMPLIFIED_MARKERS.get(poem_name)
        if marker is None:
            return m9_text("talents.g0.err_poem_not_in_whitelist",
                           poem=poem_name)
        self.relic_memory = max(0, self.relic_memory
                                - int(_g0("relic_memory_cost", 12)))
        self.m9_poem_markers[f"simplified:{marker}"] = True
        self.state.log_event("g0_reduced_poem", player=self.player_id,
                             poem=poem_name, marker=marker)
        return m9_text("talents.g0.reduced_poem_granted",
                       name=player.name, poem=poem_name)

    def _apply_relic_effect(self, player: Any,
                            relic: Dict[str, str]) -> Tuple[str, bool]:
        """按遗物原持有者天赋槽位释放增强支援技（13 槽分支）。"""
        slot = relic["slot"]
        self.state.log_event("g0_relic_effect", player=self.player_id,
                             item=relic["name"], slot=slot)
        if slot == "T1":
            self.t1_relic_armed = True
            return (m9_text("talents.g0.relic_t1_effect",
                            mult=_g0("relic_t1_mult", 1.5)), True)
        if slot == "T2":
            dur = int(_g0("relic_t2_duration", 2))
            self.t2_stealth_rounds = dur
            player.is_invisible = True
            markers = getattr(self.state, "markers", None)
            if markers is not None and hasattr(markers, "add"):
                try:
                    markers.add(self.player_id, "INVISIBLE")
                except Exception:
                    pass
            return m9_text("talents.g0.relic_t2_effect", duration=dur), True
        if slot == "T3":
            return self._relic_t3(player), True
        if slot == "T4":
            self.t4_half_next = True
            return m9_text("talents.g0.relic_t4_effect"), True
        if slot == "T6":
            enforcement = self._temp_police_enforcement(player)
            if not enforcement:
                return m9_text("talents.g0.relic_t6_no_enforcement"), True
            return m9_text("talents.g0.relic_t6_enforcement",
                           enforcement=enforcement), True
        if slot == "T7":
            self.t7_revive = True
            return m9_text("talents.g0.relic_t7_effect"), True
        if slot == "G1":
            return self._relic_g1(player), True
        if slot == "G2":
            return self._relic_g2(player), True
        if slot == "G3":
            self.g3_projection_rounds = int(_g0("relic_g3_duration", 2))
            self.g3_projection_bonus = int(_g0("relic_g3_bonus", 1))
            return (m9_text("talents.g0.relic_g3_effect",
                            rounds=self.g3_projection_rounds,
                            bonus=self.g3_projection_bonus), True)
        if slot == "G4":
            self.g4_ash_layers += int(_g0("relic_g4_stacks", 4))
            return m9_text("talents.g0.relic_g4_effect",
                           layers=self.g4_ash_layers), True
        if slot == "G5":
            cap = int(_g0("relic_memory_cap", 12))
            gain = int(_g0("relic_g5_memory", 6))
            self.relic_memory = min(cap, self.relic_memory + gain)
            return m9_text("talents.g0.relic_g5_effect",
                           gain=gain, memory=self.relic_memory, cap=cap), True
        if slot == "G6":
            return self._relic_g6(player), True
        if slot == "G7":
            ratio = float(_g0("relic_g7_ratio", 0.5))
            mult = 1.0 + int(_g0("g7_synergy_bonus", 20)) / 100.0
            dur = int(_g0("relic_g7_duration", 2))
            self.g7_cover_hp = _half_up(
                getattr(player, "hp", 0) * ratio * mult)
            self.g7_cover_rounds = dur
            return m9_text("talents.g0.relic_g7_effect",
                           cover=self.g7_cover_hp, duration=dur), True
        return m9_text("talents.g0.err_unknown_relic_slot", slot=slot), False

    def _relic_t3(self, player: Any) -> str:
        """天星：对 G0 地点所有其他单位造成普通伤害并施加石化。"""
        from engine.m9.combat import resolve_damage
        dmg = int(_g0("relic_t3_damage", 2))
        loc = getattr(player, "location", None)
        lines = [m9_text("talents.g0.relic_t3_header",
                         location=loc, damage=dmg)]
        for target in self._units_at_location(loc):
            if getattr(target, "player_id", getattr(target, "unit_id", "")) \
                    == self.player_id:
                continue
            r = resolve_damage(player, target, weapon=None,
                               game_state=self.state,
                               raw_damage_override=dmg,
                               damage_attribute_override="普通",
                               source_kind="g0_relic_t3")
            name = getattr(target, "name", getattr(target, "unit_id", "?"))
            lines.append(m9_text("talents.g0.damage_line",
                                 name=name, damage=r["hp_damage"]))
            if r.get("killed"):
                self._finalize_root_kill(player, target, r, "g0_relic_t3")
            if getattr(target, "is_alive", lambda: True)():
                petrify = getattr(self.state, "m9_petrify", None)
                if petrify is not None and hasattr(petrify, "apply"):
                    try:
                        petrify.apply(self.state, target,
                                      source_pid=self.player_id)
                    except Exception:
                        pass
        return "\n".join(lines)

    def _relic_g1(self, player: Any) -> str:
        """火萤 IV 型：对 G0 地点除自己外所有合法单位伤害 + 灼烧层数。"""
        from engine.m9.combat import resolve_damage
        dmg = int(_g0("relic_g1_damage", 2))
        burn = int(_g0("relic_g1_burn", 2))
        loc = getattr(player, "location", None)
        lines = [m9_text("talents.g0.relic_g1_header",
                         location=loc, damage=dmg, burn=burn)]
        for target in self._units_at_location(loc):
            if getattr(target, "player_id", getattr(target, "unit_id", "")) \
                    == self.player_id:
                continue
            r = resolve_damage(player, target, weapon=None,
                               game_state=self.state,
                               raw_damage_override=dmg,
                               damage_attribute_override="普通",
                               source_kind="g0_relic_g1")
            name = getattr(target, "name", getattr(target, "unit_id", "?"))
            lines.append(m9_text("talents.g0.damage_line",
                                 name=name, damage=r["hp_damage"]))
            if r.get("killed"):
                self._finalize_root_kill(player, target, r, "g0_relic_g1")
            elif getattr(target, "is_alive", lambda: True)():
                target.burn_stacks = getattr(target, "burn_stacks", 0) + burn
        return "\n".join(lines)

    def _relic_g2(self, player: Any) -> Tuple[str, bool]:
        """shadow_echo 追演：G0 以当前身体执行一次合法基础攻击后回声消失。"""
        from engine.m9.combat import resolve_damage
        target = self._pick_echo_target(player)
        weapon = self._echo_weapon(player)
        if target is None or weapon is None:
            return m9_text("talents.g0.err_no_echo_target_weapon"), False
        r = resolve_damage(player, target, weapon, game_state=self.state,
                           source_kind="g0_shadow_echo")
        if r.get("killed"):
            self._finalize_root_kill(player, target, r, "g0_shadow_echo")
        self.state.log_event("g0_shadow_echo", player=self.player_id,
                             target=getattr(target, "player_id", ""),
                             weapon=weapon.name)
        return (m9_text("talents.g0.relic_g2_result",
                        name=target.name, damage=r["hp_damage"]), True)

    def _pick_echo_target(self, player: Any) -> Optional[Any]:
        loc = getattr(player, "location", None)
        candidates = []
        for actor in self._units_at_location(loc):
            if getattr(actor, "player_id", getattr(actor, "unit_id", "")) \
                    == self.player_id:
                continue
            if getattr(actor, "is_alive", lambda: True)():
                candidates.append(actor)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        ctrl = getattr(player, "controller", None)
        names = [getattr(a, "name", getattr(a, "unit_id", "?"))
                 for a in candidates]
        try:
            picked = ctrl.choose(
                m9_text("talents.g0.echo_choose_prompt"), names)
        except Exception:
            picked = names[0]
        for actor in candidates:
            if getattr(actor, "name", getattr(actor, "unit_id", "?")) == picked:
                return actor
        return candidates[0]

    def _echo_weapon(self, player: Any) -> Optional[Any]:
        weapons = [w for w in getattr(player, "weapons", [])
                   if w is not None]
        for w in weapons:
            if getattr(w, "name", "") == AR_WEAPON_NAME:
                return w
        for w in weapons:
            if getattr(w, "get_effective_damage", lambda: 0)() > 0:
                return w
        return None

    def _relic_g6(self, player: Any) -> Tuple[str, bool]:
        """要有笑声：从本轮模板池选一个合法类别以 G0 自身状态重演。"""
        pool = getattr(self.state, "g6_template_pool", None)
        if pool is None:
            return m9_text("talents.g0.err_template_pool_unavailable"), False
        round_num = getattr(self.state, "current_round", 1)
        entries = pool.categories(round_num)
        if not entries:
            return m9_text("talents.g0.err_no_legal_template_category"), False
        names = [e["category"] for e in entries]
        ctrl = getattr(player, "controller", None)
        try:
            choice = ctrl.choose(
                m9_text("talents.g0.template_choose_prompt"), names)
        except Exception:
            choice = names[0]
        if choice not in names:
            choice = names[0]
        from engine.m9.executor import execute_category
        msg, ok = execute_category(player, self.state, choice)
        self.state.log_event("g0_template_replay", player=self.player_id,
                             category=choice, ok=ok)
        return m9_text("talents.g0.template_replay_result",
                       choice=choice, msg=msg), ok

    def _temp_police_enforcement(self, player: Any) -> str:
        """T6 遗物：临时演出警察——只对当前通缉（且同地点）执法一次后消散。"""
        station = getattr(self.state, "m9_police", None)
        self.state.log_event("g0_temp_police", player=self.player_id)
        if station is None:
            return ""
        wanted = station.open_wanted()
        if wanted is None:
            return ""
        target = self.state.get_player(wanted.suspect_id)
        if target is None or not target.is_alive():
            return ""
        if getattr(target, "location", None) != getattr(player, "location",
                                                        None):
            return ""
        from engine.m9.combat import resolve_damage
        dmg = int(station._enforcement_damage())
        r = resolve_damage(None, target, weapon=None, game_state=self.state,
                           raw_damage_override=dmg,
                           damage_attribute_override="__无视__",
                           source_kind="g0_temp_police")
        if r.get("killed"):
            self._finalize_root_kill(None, target, r, "g0_temp_police")
        self.state.log_event("g0_temp_police_enforcement",
                             player=self.player_id,
                             target=wanted.suspect_id, damage=dmg)
        return m9_text("talents.g0.temp_police_result",
                       name=target.name, damage=dmg)

    # ════════════════════════════════════════════════════════
    #  协同攻击（神秘属性）/ T1 遗物强化
    # ════════════════════════════════════════════════════════

    def m9_prepare_outgoing(self, attacker: Any, target: Any, weapon: Any,
                            raw: float) -> Optional[Dict[str, Any]]:
        """把 AR 攻击编译为公共 outgoing plan，禁止 adapter 内部递归结算。

        主 hit 负责神秘属性/T1 穿防；无人机科技追加作为 ``bonus_hits`` 独立
        A/H 段。公共 combat 聚合 result，并在完整死亡收尾后调用 callback。
        """
        if self.retreated:
            return None
        if weapon is None or getattr(weapon, "name", "") != AR_WEAPON_NAME:
            return None
        if attacker is None or getattr(attacker, "player_id", None) \
                != self.player_id:
            return None
        me = self.state.get_player(self.player_id)
        if me is None or not me.is_alive():
            return None
        if self.magazine <= 0:
            self.state.log_event("g0_ar_dry", player=self.player_id,
                                 target=getattr(target, "player_id", ""))
            return {"raw": 0.0, "attribute": "科技",
                    "armor_pierce_factor": 1.0, "bonus_hits": []}
        t1 = self.t1_relic_armed
        mult = float(_g0("relic_t1_mult", 1.5)) if t1 else 1.0
        pierce = 0.0 if t1 else 1.0
        if self.g3_projection_rounds > 0:
            raw = raw + self.g3_projection_bonus
        raw_int = max(1, _half_up(raw * mult))
        chosen = "科技"
        probe_damage = raw_int
        bonus_hits: List[Dict[str, Any]] = []
        if self.drone is not None:
            chosen, probe_damage = self._pick_mystery_attribute(
                target, raw_int, pierce)
            bonus_hits.append({
                "raw": int(_g0("drone_bonus_damage", 1)),
                "attribute": "科技",
                "armor_pierce_factor": 1.0,
                "source_kind": "g0_drone_bonus",
            })

        def after_settlement(result: Dict[str, Any]) -> None:
            self.magazine = max(0, self.magazine - 1)
            self.state.log_event("g0_ar_ammo_spent", player=self.player_id,
                                 magazine=self.magazine)
            if self.drone is not None:
                self.state.log_event(
                    "g0_cooperative_attack", player=self.player_id,
                    target=getattr(target, "player_id", ""),
                    attribute=chosen, probe_damage=probe_damage,
                    raw=raw_int,
                    bonus=int(_g0("drone_bonus_damage", 1)),
                    hp_damage=result.get("hp_damage", 0),
                    final_damage=result.get("final_damage", 0),
                    killed=bool(result.get("killed")), t1=t1)
            elif t1:
                self.state.log_event(
                    "g0_t1_attack", player=self.player_id,
                    target=getattr(target, "player_id", ""), raw=raw_int,
                    hp_damage=result.get("hp_damage", 0),
                    killed=bool(result.get("killed")))
            if t1:
                self.t1_relic_armed = False

        return {
            "raw": raw_int,
            "attribute": chosen,
            "armor_pierce_factor": pierce,
            "bonus_hits": bonus_hits,
            "after_settlement": after_settlement,
        }

    def _pick_mystery_attribute(self, target: Any, raw_int: int,
                                pierce: float) -> Tuple[str, int]:
        """神秘属性：科技/魔法/普通各试算一次目标侧最终 H，取最高者
        （并列取试算顺序首个，确定性）。探针会磨损护甲耐久——结算前后
        快照还原，保证只由真实结算消耗一次耐久。"""
        from engine.m9.combat import resolve_hit_probe
        armor = getattr(target, "armor", None)
        snapshot: List[Tuple[Any, int]] = []
        if armor is not None:
            for piece in (list(getattr(armor, "outer", []))
                          + list(getattr(armor, "inner", []))):
                snapshot.append((piece, getattr(piece, "durability", 0)))
        try:
            best_attr, best_damage = _MYSTERY_ATTRIBUTES[0], -1
            for attr in _MYSTERY_ATTRIBUTES:
                hit = resolve_hit_probe(target, raw_int, attr,
                                        pierce_factor=pierce)
                if hit.damage > best_damage:
                    best_attr, best_damage = attr, hit.damage
        finally:
            for piece, durability in snapshot:
                piece.durability = durability
        return best_attr, best_damage

    # ════════════════════════════════════════════════════════
    #  调整呼吸 / 受击协议
    # ════════════════════════════════════════════════════════

    def m9_on_lethal(self, target: Any, attacker: Any,
                     source_kind: Optional[str]) -> Optional[str]:
        """致死替代：T7 临时复活标记优先；否则调整呼吸（非 absolute_death；
        absolute_death 致死由死亡裁决直走，不消耗呼吸次数）。"""
        if target is None or getattr(target, "player_id", None) \
                != self.player_id:
            return None
        from engine.m9.combat import is_absolute_death_source
        if is_absolute_death_source(source_kind):
            return None
        if self.retreated:
            return None
        if self.t7_revive:
            self.t7_revive = False
            target.hp = max(1, self.breath_min_hp)
            self.state.log_event("g0_t7_revive", player=self.player_id)
            return "g0_t7_revive"
        if self.breath_uses_left > 0 and not self.breath_active:
            self.breath_uses_left -= 1
            self._enter_breath(target)
            return "g0_breath"
        return None

    def _enter_breath(self, player: Any) -> None:
        """进入调整呼吸：HP 锁 min、免疫全部伤害 2 轮；期间每次 forfeit
        恢复 breath_forfeit_heal HP；触发 4 轮后 HP 未超过最大 HP 的
        breath_recovery_threshold_pct → 立刻退场（呼吸重设计，2026-09 风洞）。"""
        self.breath_active = True
        self.breath_rounds = self.breath_duration
        self.breath_established_round = getattr(self.state, "current_round", 1)
        self.breath_deadline_round = self.breath_established_round + 4
        player.hp = self.breath_min_hp
        self.state.log_event("g0_breath_enter", player=self.player_id,
                             hp=player.hp, rounds=self.breath_rounds,
                             deadline=self.breath_deadline_round)

    def m9_modify_incoming(self, hit: Any) -> None:
        """受击钩子：T4 否卦减半；调整呼吸免疫全部伤害
        （含 DIRECT_DAMAGE；绝对死亡来源不经过本钩子的伤害段、
        终焉真伤由 round_manager 直扣 HP 亦不经本钩子）。"""
        if self.t4_half_next:
            self.t4_half_next = False
            hit.damage = _half_up(float(hit.damage) / 2.0)
            return
        if not self.breath_active:
            return
        hit.damage = 0

    def m9_on_forfeit(self, player: Any) -> str:
        """调整呼吸免疫期内：每选择一次 forfeit 恢复 breath_forfeit_heal HP。

        重设计（2026-09）：duration=2、heal=4、40% 止损线——两次 forfeit
        从 1 → 5 → 9 刚好跨过 8 的止损线。呼吸期外 forfeit 无效果。
        """
        if player is None or not self.breath_active:
            return ""
        heal = int(_g0("breath_forfeit_heal", 4))
        if heal <= 0:
            return ""
        before = float(getattr(player, "hp", 0) or 0)
        player.hp = min(getattr(player, "max_hp", 20), before + heal)
        gained = player.hp - before
        self.state.log_event("g0_breath_forfeit_heal", player=self.player_id,
                             heal=gained)
        return m9_text("talents.g0.breath_forfeit_heal", gained=gained)

    def receive_damage_to_temp_hp(self, damage: float,
                                  is_embrace: bool = False) -> float:
        """临时吸收链：G7 临时掩体先吸收，再余烬护甲（每层 absorb 点整数
        伤害后消耗），剩余进入 HP。"""
        remaining = max(0.0, float(damage))
        if remaining <= 0:
            return 0.0
        if self.g7_cover_hp > 0:
            take = min(self.g7_cover_hp, int(remaining))
            self.g7_cover_hp -= take
            remaining -= take
        if self.g4_ash_layers > 0 and remaining > 0:
            absorb_per = max(1, int(_g0("relic_g4_absorb", 1)))
            consumed = 0
            while self.g4_ash_layers > 0 and remaining > 0:
                take = min(absorb_per, int(remaining))
                remaining -= take
                self.g4_ash_layers -= 1
                consumed += 1
            if consumed:
                self.state.log_event("g0_ash_absorb", player=self.player_id,
                                     layers_consumed=consumed)
        return max(0.0, remaining)

    def is_immune_to_damage(self, damage_type: str) -> bool:
        """调整呼吸期间免疫普通 debuff 伤害（灼烧等）。"""
        return self.breath_active

    # ════════════════════════════════════════════════════════
    #  撤退（退场语义：非死亡）
    # ════════════════════════════════════════════════════════

    def is_retreated(self) -> bool:
        return bool(self.retreated)

    def _retreat(self, me: Any, reason: str = "breath_end") -> None:
        """撤退：不能行动/不可被指定、PP 冻结、装备遗留登记、无击杀关系。"""
        if self.retreated:
            return
        self.retreated = True
        self.breath_active = False
        self._vanish_drone("retreat")
        pp = getattr(self.state, "m9_pp", None)
        if pp is not None and hasattr(pp, "freeze"):
            try:
                pp.freeze(self.player_id)
            except Exception:
                pass
        scoring = getattr(self.state, "m9_scoring", None)
        if scoring is not None and hasattr(scoring, "mark_retreat"):
            try:
                scoring.mark_retreat(self.player_id)
            except Exception:
                pass
        m9 = getattr(self.state, "m9_system", None)
        if m9 is not None and hasattr(m9, "queue") \
                and hasattr(m9.queue, "remove_permanently"):
            m9.queue.remove_permanently(self.player_id)
        if me is not None:
            me.is_awake = False
            me._m9_exit_round = int(getattr(self.state, "current_round", 0) or 0)
            weapon_names = [
                getattr(w, "name", "?")
                for w in getattr(me, "weapons", [])
                if w is not None and getattr(w, "name", "")
                not in ("拳击", "弓")]
            self.state.log_event("g0_retreat_leavings", player=self.player_id,
                                 weapons=weapon_names)
            if hasattr(self.state, "drop_loot_on_retreat"):
                self.state.drop_loot_on_retreat(me)
        self.state.log_event("g0_retreat", player=self.player_id,
                             reason=reason, no_kill_credit=True)

    # ════════════════════════════════════════════════════════
    #  R4 结算
    # ════════════════════════════════════════════════════════

    def on_round_end(self, round_num):
        """R4 tick：无人机倒计时（建立轮不 tick）、调整呼吸倒计时（到期
        不立即退场；T+4 止损线判定失败才撤退）、临时效果轮数递减。"""
        if self.retreated:
            return
        me = self.state.get_player(self.player_id)
        if me is None:
            return
        if not me.is_alive():
            self._vanish_drone("death")
            return
        if self.drone is not None:
            if self.drone_established_round != round_num:
                self.drone["duration_left"] -= 1
                self.drone["rounds_left"] -= 1
                if self.drone["duration_left"] <= 0:
                    self._vanish_drone("expired")
        if self.breath_active:
            if self.breath_established_round != round_num:
                self.breath_rounds -= 1
                if self.breath_rounds <= 0:
                    self._end_breath(me)
        # 呼吸止损（重设计）：触发 4 轮后 HP 未超过最大 HP 的
        # breath_recovery_threshold_pct（默认 40%）→ 立刻退场。
        deadline = getattr(self, "breath_deadline_round", None)
        if deadline is not None and round_num >= deadline:
            self.breath_deadline_round = None
            max_hp = float(getattr(me, "max_hp", 20) or 20)
            threshold_pct = float(_g0("breath_recovery_threshold_pct", 40))
            if float(getattr(me, "hp", 0) or 0) <= max_hp * threshold_pct / 100.0:
                self._retreat(me, reason="breath_recovery_failed")
                return
            self.state.log_event("g0_breath_recovered", player=self.player_id,
                                 hp=me.hp)
        self._tick_temp_effects(me)

    def _end_breath(self, me: Any) -> None:
        """免疫期结束：不再立即退场，止损改由 4 轮后的阈值 HP 检查裁决。"""
        self.breath_active = False
        self.breath_rounds = 0
        self.state.log_event("g0_breath_end", player=self.player_id,
                             hp=getattr(me, "hp", 0))

    def _tick_temp_effects(self, me: Any) -> None:
        """遗物临时效果轮数递减（隐身/螺旋剑投影/临时掩体）。"""
        if self.t2_stealth_rounds > 0:
            self.t2_stealth_rounds -= 1
            if self.t2_stealth_rounds <= 0:
                me.is_invisible = False
                markers = getattr(self.state, "markers", None)
                if markers is not None and hasattr(markers, "remove"):
                    try:
                        markers.remove(self.player_id, "INVISIBLE")
                    except Exception:
                        pass
        if self.g3_projection_rounds > 0:
            self.g3_projection_rounds -= 1
            if self.g3_projection_rounds <= 0:
                self.g3_projection_bonus = 0
        if self.g7_cover_rounds > 0:
            self.g7_cover_rounds -= 1
            if self.g7_cover_rounds <= 0:
                self.g7_cover_hp = 0

    def _on_any_player_death(self, victim_id: str, killer_id: Optional[str] = None):
        """G0 死亡 → 无人机立即消失。"""
        if victim_id == self.player_id:
            self._vanish_drone("death")
            self.breath_active = False

    # ════════════════════════════════════════════════════════
    #  目标枚举 / 击杀清理
    # ════════════════════════════════════════════════════════

    def _units_at_location(self, location: Optional[str]) -> List[Any]:
        """地点全部存活单位：玩家（含 G0）+ M9 影身 + 警察单位
        （已退场 G0 排除出目标枚举）。"""
        units: List[Any] = []
        state = self.state
        for p in getattr(state, "players", {}).values():
            if not getattr(p, "is_alive", lambda: False)():
                continue
            if getattr(p, "location", None) != location:
                continue
            if self.retreated and getattr(p, "player_id", None) \
                    == self.player_id:
                continue
            units.append(p)
        for shadow in getattr(state, "m9_shadows", {}).values():
            if not getattr(shadow, "is_alive", lambda: False)():
                continue
            if getattr(shadow, "location", None) != location:
                continue
            units.append(shadow)
        m9_police = getattr(state, "m9_police", None)
        if m9_police is not None and hasattr(m9_police, "units_at"):
            for unit in m9_police.units_at(location or ""):
                if getattr(unit, "is_alive", lambda: False)():
                    units.append(unit)
        return units

    def _finalize_root_kill(self, killer: Any, target: Any, result: Dict[str, Any],
                            source_kind: str) -> None:
        """兼容旧调用点；公共 finalizer 是唯一死亡写边界且自身幂等。"""
        if not result.get("killed"):
            return
        from engine.m9.combat import finalize_death
        finalize_death(
            self.state, target, killer,
            source_kind=source_kind, cause="m9_g0_attack")

    # ════════════════════════════════════════════════════════
    #  T0 入口
    # ════════════════════════════════════════════════════════

    def get_t0_option(self, player: Any) -> Optional[dict]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return None
        if self.retreated:
            return None
        if player is None or not getattr(player, "is_alive", lambda: False)():
            return None
        if self.breath_active:
            return None  # 调整呼吸：不能召唤新无人机、不能公演
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        sp = m9.get_sp(self.player_id)
        if self.drone is None:
            if sp >= 1:
                return {"name": m9_text("talents.g0.t0_improvise_name"),
                        "description": m9_text(
                            "talents.g0.t0_improvise_description"),
                        "m9_kind": "g0_drone_summon"}
            return None
        round_num = getattr(self.state, "current_round", 1)
        phase = getattr(self.state, "current_phase", "")
        public_ready = sp >= 2 and (
            phase != "r3_actions"
            or m9._public_holder_by_round.get(round_num) == self.player_id)
        if public_ready:
            return {"name": m9_text("talents.g0.t0_public_name"),
                    "description": m9_text("talents.g0.t0_public_description"),
                    "m9_kind": "g0_performance"}
        return None

    def execute_t0(self, player: Any):
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return m9_text("talents.g0.err_m9_disabled"), False
        if self.retreated:
            return m9_text("talents.g0.err_retreated"), False
        if player is None or not getattr(player, "is_alive", lambda: False)():
            return m9_text("talents.g0.err_cannot_act"), False
        if self.breath_active:
            return m9_text("talents.g0.err_breath_active_no_t0"), False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return m9_text("talents.g0.err_m9_not_mounted"), False
        round_num = getattr(self.state, "current_round", 1)
        if self.drone is None:
            return self._do_summon(player, m9, round_num)
        if m9.get_sp(self.player_id) < 2:
            return m9_text("talents.g0.err_drone_exists_need_2sp"), False
        return self._do_performance(player, m9, round_num)

    # ── 状态展示 ──

    def describe_status(self) -> str:
        parts = []
        if self.drone is not None:
            parts.append(m9_text(
                "talents.g0.status_drone",
                hp=self.drone["hp"], max_hp=self.drone["max_hp"],
                ticks=self.drone["duration_left"]))
        if self.relic_memory > 0:
            parts.append(m9_text(
                "talents.g0.status_relic_memory",
                memory=self.relic_memory,
                cap=_g0("relic_memory_cap", 12)))
        if self.breath_active:
            parts.append(m9_text("talents.g0.status_breath",
                                 rounds=self.breath_rounds))
        if self.retreated:
            parts.append(m9_text("talents.g0.status_retreated"))
        if self.relics:
            parts.append(m9_text("talents.g0.status_relics",
                                 count=len(self.relics)))
        return " ".join(parts)
