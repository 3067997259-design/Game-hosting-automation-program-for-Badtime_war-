"""M9 T2 剪刀手一突天赋（profile: m9-rfc，T2 合同 v1.0）。

继承 v2exp ScissorRush（伤人免罪 / 攻击回盾 / 零击杀隐身入口），覆写 M9 差异：
- 伤人未杀免罪：最近一次攻击未击杀 → {"immune": True}；击杀 → 正常记罪
  （犯罪再动 / 响应窗口 / 警觉额外回合全部退役，不设任何 extra-turn 标志）；
- 攻击回盾：m9_on_attack 偶数次攻击命中护甲（击碎名单优先，吸收量回退）→
  恢复自身同名外甲耐久（数值读 `m9_talents_extended.t2.shield_recovery_durability`，
  默认 4；铁之荷鲁斯排除）；
- 零击杀隐身：stealth_on_zero_kills=True，M9 战斗钩子自动豁免隐身压制；
- 警觉：find/found 只登记普通关注（mark_attention +1 SP，每触发类型每轮一次），
  不设额外回合；
- 追猎反应：他人公演根行动完成后一次合法 find/lock（全局一次 _hunt_used；
  地火诗 free_hunt_reaction 免费通道不消耗额度）；
- 即演（−1 SP）/ 公演（−2 SP）：对已找到（ENGAGED_WITH）或已锁定
  （LOCKED_BY）的存活目标核心攻击；公演可先追演移动（actions.move）；
- G6 借用核心：core_attack(target_id) 直接攻击（无追演 / 无 find 前置）。
"""

from __future__ import annotations

from typing import Any, Optional

from engine.balance import get as bget
from engine.m9.text import m9_text
from talents.t2_scissor_rush import ScissorRush


def _t2(key: str, default):
    return bget("m9_talents_extended", "t2", key, default=default)


class ScissorRush9(ScissorRush):
    """M9 T2（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。"""

    name = "剪刀手一突"
    description = m9_text("talents.t2.description")

    def __init__(self, player_id: str, game_state: Any) -> None:
        super().__init__(player_id, game_state)
        from engine.m9.gate import m9_enabled

        if m9_enabled(game_state):
            # 退役字段删除（合同：无犯罪再动/响应窗口/警觉次数语义；
            # m9 禁用时保留，保证 v2exp 钩子回归不漂）
            for attr in (
                "triggered_crime_types",
                "response_uses_remaining",
                "response_triggered_locations",
                "vigilance_uses",
            ):
                if hasattr(self, attr):
                    delattr(self, attr)
        self.attack_count = 0  # 攻击计数器（攻击回盾，偶数次触发）
        self.stealth_on_zero_kills = True  # 零击杀隐身豁免（M9 战斗钩子读取）
        self._hunt_used = False  # 追猎反应全局一次额度
        self._find_attention_round = -1  # find 警觉每轮一次（按触发类型）
        self._found_attention_round = -1  # found 警觉每轮一次（按触发类型）

    # ════════════════════════════════════════════════════════
    #  常驻被动：伤人未杀免罪（无犯罪再动）
    # ════════════════════════════════════════════════════════

    def on_crime_check(self, player_id, crime_type):
        from engine.m9.gate import m9_enabled

        if not m9_enabled(self.state):
            return super().on_crime_check(player_id, crime_type)
        if player_id != self.player_id:
            return None
        if crime_type == "伤害玩家":
            # 最近一次攻击未击杀 → 免罪；击杀 → 正常记罪（无额外回合）
            killed = self._last_attack_killed()
            if not killed:
                return {"immune": True}
            return None
        # 非攻击类犯罪：犯罪再动退役 → 正常记罪
        return None

    # ════════════════════════════════════════════════════════
    #  常驻被动：攻击回盾（m9_on_attack，偶数次命中护甲）
    # ════════════════════════════════════════════════════════

    def m9_on_attack(self, hit: Any, target: Any) -> None:
        """M9 结算路径攻击方钩子：攻击计数 + 偶数次命中护甲 → 回盾。"""
        from engine.m9.gate import m9_enabled

        if not m9_enabled(self.state):
            return
        self.attack_count += 1
        if self.attack_count % 2 != 0:
            return  # 奇数不计
        hit_piece = self._find_hit_piece(target, hit)
        if hit_piece is None:
            return
        self._recover_shield(hit_piece)

    def _find_hit_piece(self, target: Any, hit: Any) -> Optional[Any]:
        """定位被命中的护甲：击碎名单（broken 甲名）优先，吸收量回退。"""
        broken = getattr(hit, "broken", None) or []
        if broken:
            for name in broken:
                piece = self._find_piece_by_name(target, name)
                if piece is not None:
                    return piece
            return None
        if getattr(hit, "a_phase_absorbed", 0) > 0:
            # 吸收回退：外甲 → 内甲，首个耐久>0 的活跃护甲
            for layer in ("outer", "inner"):
                for piece in getattr(target.armor, layer, None) or []:
                    if getattr(piece, "durability", 0) > 0:
                        return piece
            return None
        return None

    @staticmethod
    def _find_piece_by_name(target: Any, name: str) -> Optional[Any]:
        for layer in ("outer", "inner"):
            for piece in getattr(target.armor, layer, None) or []:
                if getattr(piece, "name", None) == name:
                    return piece
        return None

    def _recover_shield(self, hit_piece: Any) -> None:
        """攻击回盾（M9 hp20 耐久路径）：恢复自身同名外甲耐久。"""
        EXCLUDED_ARMORS = {"铁之荷鲁斯"}
        if getattr(hit_piece, "name", "") in EXCLUDED_ARMORS:
            return
        attacker = self.state.get_player(self.player_id)
        if attacker is None:
            return
        amount = int(_t2("shield_recovery_durability", 4))
        for piece in getattr(attacker.armor, "outer", []) or []:
            if (
                getattr(piece, "name", None) == hit_piece.name
                and getattr(piece, "durability", 0) > 0
                and piece.durability < piece.max_durability
            ):
                piece.durability = min(piece.max_durability, piece.durability + amount)
                self.state.log_event(
                    "scissor_rush_shield_recovery",
                    player=self.player_id,
                    armor=piece.name,
                    durability=amount,
                )
                return

    # ════════════════════════════════════════════════════════
    #  警觉：find/found 只登记普通关注（无额外回合）
    # ════════════════════════════════════════════════════════

    def _mark_attention_once(self, key: str) -> None:
        """每触发类型每轮至多一次关注登记（guard 由 can_attend 内部保证）。"""
        round_num = getattr(self.state, "current_round", 1)
        if getattr(self, key) == round_num:
            return
        setattr(self, key, round_num)
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None or not m9.can_attend(round_num, self.player_id):
            return
        m9.mark_attention(round_num, self.player_id)

    def on_find_someone(self, player, target_id):
        """主动找到他人：M9 只登记普通关注（+1 SP），不设额外回合。"""
        from engine.m9.gate import m9_enabled

        if not m9_enabled(self.state):
            return super().on_find_someone(player, target_id)
        self._mark_attention_once("_find_attention_round")
        self.state.log_event(
            "scissor_rush_vigilance",
            player=self.player_id,
            trigger="find",
            target=target_id,
        )

    def on_found_by_someone(self, player, finder_id):
        """被他人找到：M9 只登记普通关注（+1 SP），不设额外回合。"""
        from engine.m9.gate import m9_enabled

        if not m9_enabled(self.state):
            return super().on_found_by_someone(player, finder_id)
        self._mark_attention_once("_found_attention_round")
        self.state.log_event(
            "scissor_rush_vigilance",
            player=self.player_id,
            trigger="found_by",
            finder=finder_id,
        )

    # ════════════════════════════════════════════════════════
    #  响应窗口（退役：M9 不参与，返回 False / 空操作）
    # ════════════════════════════════════════════════════════

    def check_response_window(self, actor, action_type):
        from engine.m9.gate import m9_enabled

        if not m9_enabled(self.state):
            return super().check_response_window(actor, action_type)
        return False

    def execute_response(self, player):
        from engine.m9.gate import m9_enabled

        if not m9_enabled(self.state):
            return super().execute_response(player)
        return ""

    # ════════════════════════════════════════════════════════
    #  追猎反应（他人公演根行动完成后；全局一次 + 地火诗免费通道）
    # ════════════════════════════════════════════════════════

    def m9_on_public_root_completed(self, performer_id: str) -> None:
        """他人公演根行动完成后：合法 find/lock 一次（全局一次额度）。

        不创建 ActionGrant、不进 T0、无移动/攻击；find/lock 自身的
        费用/犯罪/关注/结果归属执行动作机制。异常吞掉，不打断轮次循环。
        """
        from engine.m9.gate import m9_enabled

        if not m9_enabled(self.state):
            return
        if self._hunt_used:
            return
        me = self.state.get_player(self.player_id)
        if me is None or not me.is_alive():
            return
        if not self._hunt_legal(me, performer_id):
            return
        self._hunt_used = True
        try:
            self._hunt_execute(me, performer_id)
        except Exception:
            pass

    def free_hunt_reaction(self, performer_id: Optional[str] = None) -> Optional[str]:
        """地火诗 full_extra 免费追猎：同 find/lock，不消耗全局一次额度。"""
        me = self.state.get_player(self.player_id)
        if me is None or not me.is_alive():
            return None
        if performer_id is None:
            candidates = self._hunt_candidates(me)
            if not candidates:
                return None
            if len(candidates) == 1:
                performer_id = candidates[0].player_id
            else:
                names = [p.name for p in candidates]
                try:
                    choice = me.controller.choose(
                        m9_text("talents.t2.choose_earthfire_hunt_prompt"), names,
                        context={"phase": "R3", "situation": "t2_earthfire_hunt"})
                except (AttributeError, TypeError, ValueError):
                    choice = names[0]
                target = next((p for p in candidates if p.name == choice),
                              candidates[0])
                performer_id = target.player_id
        if not self._hunt_legal(me, performer_id):
            return None
        try:
            return self._hunt_execute(me, performer_id)
        except Exception:
            return None

    def _hunt_candidates(self, me: Any) -> list:
        actors = self.state.iter_targetable_actors() if hasattr(
            self.state, "iter_targetable_actors") else self.state.iter_actors()
        result = []
        for actor in actors:
            actor_id = getattr(actor, "player_id", "")
            if actor_id == self.player_id:
                continue
            if self._hunt_legal(me, actor_id):
                result.append(actor)
        return result

    def _hunt_legal(self, me: Any, performer_id: str) -> bool:
        performer = self.state.get_actor(performer_id)
        if performer is None or not performer.is_alive():
            return False
        if hasattr(performer, "is_on_map") and not performer.is_on_map():
            return False
        from engine.visibility import can_see
        if not can_see(me, performer, self.state):
            return False
        return self._hunt_can_find(me, performer) or self._hunt_can_lock(me, performer)

    def _hunt_can_find(self, me: Any, performer: Any) -> bool:
        """find 前置（actions/find_target.py 同构）：同地点 + 尚未面对面。"""
        if getattr(performer, "location", None) != getattr(me, "location", None):
            return False
        if self.state.markers.has_relation(
            me.player_id, "ENGAGED_WITH", performer.player_id
        ):
            return False
        return True

    def _hunt_can_lock(self, me: Any, performer: Any) -> bool:
        """lock 前置（actions/lock_target.py 同构）：有远程武器 + 尚未锁定。"""
        from models.equipment import WeaponRange

        has_ranged = any(
            getattr(w, "weapon_range", None) == WeaponRange.RANGED
            and not getattr(w, "_hexagram_disabled", False)
            for w in (getattr(me, "weapons", None) or [])
            if w
        )
        if not has_ranged:
            return False
        if self.state.markers.has_relation(
            performer.player_id, "LOCKED_BY", me.player_id
        ):
            return False
        return True

    def _hunt_execute(self, me: Any, performer_id: str) -> Optional[str]:
        """执行一次 find（优先）或 lock；返回动作结果描述。"""
        performer = self.state.get_player(performer_id)
        result: Optional[str] = None
        if self._hunt_can_find(me, performer):
            from actions import find_target as _find

            result = _find.execute(me, performer_id, self.state)
        elif self._hunt_can_lock(me, performer):
            from actions import lock_target as _lock

            result = _lock.execute(me, performer_id, self.state)
        if result is not None:
            self.state.log_event("t2_hunt_reaction", player=self.player_id,
                                 target=performer_id)
        return result

    # ════════════════════════════════════════════════════════
    #  T0 入口：即演（−1 SP）/ 公演（−2 SP）核心攻击
    # ════════════════════════════════════════════════════════

    def _core_targets(self, player: Any) -> list:
        """核心攻击目标：已找到（ENGAGED_WITH）或已被我方锁定（LOCKED_BY）的存活目标。"""
        targets = []
        seen = set()
        for eid in self.state.markers.get_related(player.player_id, "ENGAGED_WITH"):
            ep = self.state.get_actor(eid)
            if ep and ep.is_alive():
                targets.append(ep)
                seen.add(eid)
        actors = self.state.iter_actors() if hasattr(
            self.state, "iter_actors") else (
                self.state.get_player(pid) for pid in self.state.player_order)
        for ep in actors:
            pid = getattr(
                ep, "player_id", getattr(ep, "unit_id", "")) if ep else ""
            if pid == self.player_id or pid in seen:
                continue
            if (
                ep
                and ep.is_alive()
                and self.state.markers.has_relation(pid, "LOCKED_BY", self.player_id)
            ):
                targets.append(ep)
        return targets

    def _pick_core_target(self, player: Any) -> Optional[Any]:
        targets = self._core_targets(player)
        if not targets:
            return None
        if len(targets) == 1:
            return targets[0]
        try:
            names = [t.name for t in targets]
            choice = player.controller.choose(
                m9_text("talents.t2.choose_target_prompt"),
                names,
                context={"phase": "T0", "situation": "t2_core_target"},
            )
            return next((t for t in targets if t.name == choice), targets[0])
        except Exception:
            return targets[0]

    def get_t0_option(self, player: Any) -> Optional[dict]:
        from engine.m9.gate import m9_enabled

        if not m9_enabled(self.state):
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        if not self._core_targets(player):
            return None
        sp = m9.get_sp(self.player_id)
        if sp >= 2:
            return {
                "name": m9_text("talents.t2.t0.name_public"),
                "description": m9_text("talents.t2.t0.description_public"),
                "m9_kind": "t2_public",
            }
        if sp >= 1:
            return {
                "name": m9_text("talents.t2.t0.name_improvise"),
                "description": m9_text("talents.t2.t0.description_improvise"),
                "m9_kind": "t2_improvise",
            }
        return None

    def execute_t0(self, player: Any):
        from engine.m9.gate import m9_enabled

        if not m9_enabled(self.state):
            return super().execute_t0(player)
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return m9_text("talents.t2.err_m9_not_mounted"), False
        round_num = getattr(self.state, "current_round", 1)

        # 预检先于任何 SP/公演位消费
        target = self._pick_core_target(player)
        if target is None:
            return m9_text("talents.t2.err_no_core_target"), False

        sp = m9.get_sp(self.player_id)
        if sp >= 2:
            public_ready = m9.assign_public_slot(round_num) == player.player_id
            option_public = m9_text("talents.t2.option_public")
            option_improvise = m9_text("talents.t2.option_improvise")
            options = [option_public, option_improvise] if public_ready \
                else [option_improvise]
            try:
                mode = player.controller.choose(
                    m9_text("talents.t2.choose_performance_mode_prompt"), options,
                    context={"phase": "T0", "situation": "t2_performance_mode"})
            except (AttributeError, TypeError, ValueError):
                mode = options[0]
            if "公演" in mode:
                if not self._ensure_public_seat(player, m9, round_num):
                    return m9_text("talents.t2.err_sp_or_public_seat"), False
                self._chase_to(player, target)
                return self._core_attack(player, target), True
            if m9.dispatch_improvise(self.player_id, round_num) is None:
                return m9_text("talents.t2.err_sp_insufficient_cancel"), False
            return self._core_attack(player, target), True
        if sp >= 1:
            if m9.dispatch_improvise(self.player_id, round_num) is None:
                return m9_text("talents.t2.err_sp_insufficient_cancel"), False
            return self._core_attack(player, target), True
        return m9_text("talents.t2.err_sp_insufficient"), False

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        """只消费 R0 已固化的公演位；T0 不允许临时报名。"""
        if m9.assign_public_slot(round_num) != player.player_id:
            return False
        return m9.dispatch_public(player.player_id, round_num) is not None

    def _chase_to(self, player: Any, target: Any) -> None:
        """公演追演：移动到目标地点（走 actions.move 正规移动）。"""
        if getattr(player, "location", None) == getattr(target, "location", None):
            return
        from actions import move as _move

        try:
            _move.execute(player, target.location, self.state)
        except Exception:
            pass  # 追演被阻碍（架盾/借机等）不阻断核心攻击

    def _pick_weapon(self, player: Any) -> Optional[Any]:
        """选最强武器；并列时走 controller.choose（失败取首个，确定性兜底）。"""
        weapons = [w for w in getattr(player, "weapons", []) if w]
        if not weapons:
            return None
        best = max(weapons, key=lambda w: w.get_effective_damage())
        usable = [
            w
            for w in weapons
            if w.get_effective_damage() == best.get_effective_damage()
        ]
        if len(usable) == 1:
            return usable[0]
        try:
            names = [w.name for w in usable]
            choice = player.controller.choose(
                m9_text("talents.t2.choose_weapon_prompt"),
                names,
                context={"phase": "T0", "situation": "t2_pick_weapon"},
            )
            return next(w for w in usable if w.name == choice)
        except Exception:
            return usable[0]

    def _core_attack(self, player: Any, target: Any) -> str:
        """核心攻击（即演/公演同一结算）：resolve_damage 普通武器攻击。"""
        from engine.m9.combat import resolve_damage

        weapon = self._pick_weapon(player)
        if weapon is None:
            return m9_text("talents.t2.err_no_weapon")
        result = resolve_damage(
            attacker=player,
            target=target,
            weapon=weapon,
            game_state=self.state,
            source_kind="t2_core_attack",
        )
        lines = [m9_text("talents.t2.attack_header", player=player.name,
                         weapon=weapon.name, target=target.name)]
        for detail in result.get("details", []):
            lines.append(f"   {detail}")
        if result.get("killed"):
            lines.append(m9_text("talents.t2.attack_killed", name=target.name))
        self.state.log_event(
            "attack",
            attacker=self.player_id,
            target=target.player_id,
            weapon=weapon.name,
            result=result,
        )
        return "\n".join(lines)

    # ════════════════════════════════════════════════════════
    #  G6 借用核心：直接攻击（无追演移动 / 无 find 前置）
    # ════════════════════════════════════════════════════════

    def core_attack(self, target_id: str):
        """G6 借用核心：直接攻击目标，返回 (msg, ok)。

        不进行追演移动、不消费 SP、不做 found→free-hunt 前置处理。
        """
        from engine.m9.combat import resolve_damage

        me = self.state.get_player(self.player_id)
        if me is None or not me.is_alive():
            return m9_text("talents.t2.err_self_absent"), False
        target = self.state.get_player(target_id)
        if target is None or not target.is_alive():
            return m9_text("talents.t2.err_target_invalid"), False
        weapon = self._pick_weapon(me)
        if weapon is None:
            return m9_text("talents.t2.err_no_weapon"), False
        result = resolve_damage(
            attacker=me,
            target=target,
            weapon=weapon,
            game_state=self.state,
            source_kind="t2_core_attack",
        )
        lines = [m9_text("talents.t2.core_attack_header", player=me.name,
                         weapon=weapon.name, target=target.name)]
        for detail in result.get("details", []):
            lines.append(f"   {detail}")
        if result.get("killed"):
            lines.append(m9_text("talents.t2.attack_killed", name=target.name))
        self.state.log_event(
            "attack",
            attacker=self.player_id,
            target=target.player_id,
            weapon=weapon.name,
            result=result,
        )
        return "\n".join(lines), True

    # ════════════════════════════════════════════════════════
    #  状态描述
    # ════════════════════════════════════════════════════════

    def describe_status(self) -> str:
        from engine.m9.gate import m9_enabled

        if not m9_enabled(self.state):
            return super().describe_status()
        player = self.state.get_player(self.player_id)
        kills = getattr(player, "kill_count", 0) if player else 0
        hunt = m9_text("talents.t2.hunt_used") if self._hunt_used \
            else m9_text("talents.t2.hunt_unused")
        return m9_text("talents.t2.status", attack_count=self.attack_count,
                       kills=kills, hunt=hunt)
