"""engine/ish_bosheth.py
ish-bosheth 舞台结界状态管理器（G2 Reset v0.6）

负责：
- 结界展开 / R4 衰减（含阵营胜利检查）/ R0 废墟谢幕 / 统一清理
- 三声部系统（Accarezzevole / Indifferenza / Strappando 固定阵营）
- ma non troppo 开场 2/2/2 分配
- Regard 新公式（Ind 正向 / Str 负向 / Acc 中性）
- 物料牌系统集成
- 旋律（Melody）声部特效（狂热 / 回声 / 裂音）
- 曲目执行辅助
"""

from __future__ import annotations
import random
from typing import TYPE_CHECKING, List, Optional, Set

from cli import display
from engine.prompt_manager import prompt_manager
from models.chorus import ChorusUnit

if TYPE_CHECKING:
    from engine.game_state import GameState
    from models.player import Player

# ── 声部常量（复用旧情绪常量名）───────────────────────────────────
ACCAREZZEVOLE = "accarezzevole"   # 入戏者
INDIFFERENZA  = "indifferenza"    # 抽离者
STRAPPANDO    = "strappando"      # 反抗者

VOICE_ORDER = [STRAPPANDO, INDIFFERENZA, ACCAREZZEVOLE]  # 低→高
EMOTION_ORDER = VOICE_ORDER  # 向后兼容别名

VOICE_LABELS = {
    ACCAREZZEVOLE: "入戏 (Accarezzevole)",
    INDIFFERENZA:  "抽离 (Indifferenza)",
    STRAPPANDO:    "反抗 (Strappando)",
}
EMOTION_LABELS = VOICE_LABELS  # 向后兼容别名

# ── 阵营胜利结束原因 ──────────────────────────────────────────────
END_ACC_WIN     = "acc_win"
END_STR_WIN     = "str_win"
END_IND_WIN     = "ind_win"
END_SILENT      = "silent"
END_BREAK       = "break"
END_EMPTY       = "empty"
END_CURTAIN     = "curtain"
END_MAX_DURATION = "max_duration"
END_FORCED      = "forced"
END_DEATH       = "death"
END_DUET        = "duet"          # v2.0: G2×G5 双人演出谢幕

# ── 声部特效标记 ──────────────────────────────────────────────────
MARK_FERVOR     = "fervor"       # 狂热（Acc 被旋律命中）
MARK_CRACK      = "crack"        # 裂音（Str 被旋律命中）


class ButtonDummy:
    """双人演出大红按钮 dummy（v2.0）。

    每轮两个，出现于随机座位。被攻击时记录伤害为声部热力，不扣血。
    复用 ChorusUnit 的注册模式（register_chorus）参与 D4 和 targeting。
    """
    is_button: bool = True
    is_chorus: bool = True    # 兼容现有 D4/攻击系统

    def is_alive(self) -> bool:
        return True  # 永不死亡

    def __init__(self, seat: str, index: int):
        self.player_id: str = f"__button_{index}__"
        self.name: str = f"🔴 大红按钮 #{index}"
        self.location: str = seat
        self.hp: float = 999.0
        self.max_hp: float = 999.0
        self.is_awake: bool = True
        self.emotion: str = ""     # 无阵营
        self.armor = None
        self.talent = None
        self.controller = None     # 按钮不参与 choose/get_command，显式置 None 以防遍历误触
        self.weapons = []
        self.stage_statuses: set = set()
        self.encore_layers: int = 0
        self.temp_hp_g2: float = 0.0
        self.temp_atk_g2: float = 0.0

    def __repr__(self):
        return f"ButtonDummy(seat={self.location}, id={self.player_id})"


class IshBosheth:
    """舞台结界实例（v0.6）。同一时间最多一个。"""

    def __init__(self, g2_owner_id: str):
        self.g2_owner_id: str = g2_owner_id
        self.g2_home: str = f"home_{g2_owner_id}"

        self.regard: float = 0.0
        self.regard_cap: float = 8.0
        self.r4_count: int = 0
        self.phase: str = "active"   # "active" / "pending_curtain" / "ended"

        self.chorus_list: List[ChorusUnit] = []
        self.submerged_list: list = []
        self.seat_assignments: dict = {}  # pid/chorus_id → seat

        self.before_light: Optional[str] = None   # "riposato" / "dolente" / None

        # v0.6 安定値: 累计 |ΔRegard| 解锁三间章
        self.cumulative_delta_regard: float = 0.0
        # v0.6: 三间章使用追踪（不再由 r4_count 控制解锁）
        self.melody_1_used: bool = False
        self.melody_2_used: bool = False
        self.melody_3_used: bool = False

        self.participants: Set[str] = set()

        # v0.6: 物料牌系统（open() 中创建）
        self.deck: Optional[Any] = None

        # v0.6: G2 投影（后台通行证生成）
        self.projection_seat: Optional[str] = None
        self.projection_round: int = -1

        # v0.7 安定値交互标记
        self._pivot_override: Optional[float] = None  # Riposato/Dolente 覆盖 pivot

        # ==================================================================
        #  v2.0: G2×G5 双人演出 TE 状态
        # ==================================================================
        self.duet_g5_pid: Optional[str] = None       # 上台的 G5 玩家 ID
        self.duet_heat: dict[str, float] = {}         # {voice: total_heat}
        self.duet_round: int = 0                      # duet 当前轮次（最大 8）
        self.duet_buttons: list = []                  # 当前轮按钮实体
        self.duet_encores: int = 0                    # 安可触发次数
        self.harmonize_active: bool = False           # G5 本轮是否伴唱
        self.duet_curtain_triggered: bool = False     # 谢幕是否已触发
        self._duet_prev_heat: dict[str, float] = {}   # 上轮累计热力（用于当轮增量计算）
        # v2.0 duet 歌曲效果状态（每轮在 _spawn_duet_buttons 中重置）
        self._duet_voice_button_mult: dict[str, float] = {}  # {voice: multiplier}
        self._duet_displacement_immune: set[str] = set()     # player_ids
        self._duet_pooled_heat: bool = False                 # Riposato 公共池
        self._duet_heat_conversion_mult: float = 1.0         # Riposato ×1.5
        self._duet_button_dmg_mult: float = 1.0              # Dolente ×1.3

    # ── 累计解锁阈值 ────────────────────────────────────────────
    MELODY_1_THRESHOLD = 3.0
    MELODY_2_THRESHOLD = 7.0
    MELODY_3_THRESHOLD = 11.0

    def adjust_regard(self, delta: float):
        """修改 Regard 并自动追踪累计变化绝对值。"""
        old = self.regard
        self.regard = max(0.0, min(self.regard_cap, self.regard + delta))
        actual = abs(self.regard - old)
        self.cumulative_delta_regard += actual

    # ================================================================
    #  v2.0: G2×G5 双人演出 TE 入口
    # ================================================================
    def enter_duet(self, g5_player_id: str, game_state: GameState):
        """G5 献诗上台通过投票 → 进入双人演出模式。"""
        g2_player = game_state.get_player(self.g2_owner_id)
        g5_player = game_state.get_player(g5_player_id)

        # ── 切换阶段 ──
        self.phase = "duet"

        # ── G5 注册 ──
        self.duet_g5_pid = g5_player_id

        # ── Regard 重置（保证第一轮演出顺利进行）──
        self.regard = max(4.0, self.regard)

        # ── 热力值初始化 ──
        self.duet_heat = {
            ACCAREZZEVOLE: 0.0,
            INDIFFERENZA:  0.0,
            STRAPPANDO:    0.0,
        }
        self._duet_prev_heat = dict(self.duet_heat)  # 与 duet_heat 同步，首轮增量=0
        self.duet_round = 0
        self.duet_buttons = []
        self.duet_encores = 0
        self.harmonize_active = False
        self.duet_curtain_triggered = False

        # ── G5 移动到舞台 ──
        if g5_player:
            old_loc = g5_player.location
            g5_player.location = self.g2_home
            game_state.markers.on_player_move(g5_player_id)
            display.show_info(
                prompt_manager.get_prompt(
                    "duet", "enter.move_to_stage",
                    default="  🏠 {name} 从 {old_loc} 移动到舞台中心（{stage_home}）。"
                ).format(name=g5_player.name, old_loc=old_loc, stage_home=self.g2_home)
            )

        # ── G5 追忆预算初始化（从 talent 读取或默认 12）──
        if g5_player and g5_player.talent:
            talent = g5_player.talent
            if not getattr(talent, 'duet_joined', False):
                talent.duet_joined = True
                talent.reminiscence_budget = 12.0
                talent.harmonize_count = 0
        elif g5_player and not g5_player.talent:
            # 防御：无天赋玩家不可能出现在此路径，但兜底设 budget 确保不崩
            g5_player._g5_duet_fallback = True

        # ── 冻结 G2 独唱系统 ──
        # 旋律（在 duet 中禁用）
        # faction_victory_check（R4 不再检查阵营胜利）
        # Embrace 自动标记（伤害流水线中暂停）

        g2_name = g2_player.name if g2_player else "??"
        g5_name = g5_player.name if g5_player else "??"
        budget = getattr(g5_player.talent, 'reminiscence_budget', 12.0) if g5_player and g5_player.talent else 12.0
        display.show_info(
            prompt_manager.get_prompt(
                "duet", "enter.info",
                default="\n==================================================\n"
                        "  🎤🌊 双人演出模式 —— {g2} & {g5}\n"
                        "  Regard 初始：{regard}\n"
                        "  G5 追忆预算：{budget}/12\n"
                        "  最大轮次：8\n"
                        "  热力计数器已就绪\n"
                        "=================================================="
            ).format(g2=g2_name, g5=g5_name, regard=self.regard, budget=budget)
        )

    # ================================================================
    #  v2.0: 大红按钮管理
    # ================================================================
    def _spawn_duet_buttons(self, game_state: GameState):
        """R0：在两个随机座位召唤按钮。"""
        available = sorted(self.SEATS)
        if len(available) < 2:
            return
        chosen = random.sample(available, 2)
        self.duet_buttons = []
        for i, seat in enumerate(chosen, 1):
            btn = ButtonDummy(seat, i)
            self.duet_buttons.append(btn)
            game_state.register_chorus(btn)
        # 重置 duet 歌曲效果（每轮清零）
        self._duet_voice_button_mult.clear()
        self._duet_displacement_immune.clear()
        self._duet_pooled_heat = False
        self._duet_heat_conversion_mult = 1.0
        self._duet_button_dmg_mult = 1.0
        display.show_info(
            prompt_manager.get_prompt(
                "duet", "button.spawn",
                default="\n🔴🔴 两个大红按钮出现在 {seat1} 和 {seat2}！"
            ).format(seat1=chosen[0], seat2=chosen[1])
        )

    def _despawn_duet_buttons(self, game_state: GameState):
        """R3 结束：移除本轮按钮。"""
        for btn in self.duet_buttons:
            game_state.unregister_chorus(btn.player_id)
        self.duet_buttons.clear()

    def record_heat(self, attacker, damage: float):
        """攻击按钮成功 → 记录热力值（含 duet 歌曲效果倍率）。"""
        voice = getattr(attacker, 'emotion', None)
        if voice not in self.duet_heat:
            return
        # v2.0: duet 歌曲效果 — 声部倍率 + 全局按钮倍率
        damage *= self._duet_voice_button_mult.get(voice, 1.0)
        damage *= self._duet_button_dmg_mult
        if self._duet_pooled_heat:
            # Riposato 公共池：三等分到三个声部
            split = round(damage / 3, 2)
            for v in self.duet_heat:
                self.duet_heat[v] += split
            display.show_info(
                prompt_manager.get_prompt(
                    "duet", "button.hit",
                    default="🔴 {name} 按下了按钮！+{heat} 热力（公共池 三等分）"
                ).format(name=attacker.name, heat=damage)
            )
        else:
            self.duet_heat[voice] += damage
            display.show_info(
                prompt_manager.get_prompt(
                    "duet", "button.hit",
                    default="🔴 {name} 按下了按钮！+{heat} 热力 → {voice}"
                ).format(name=attacker.name, heat=damage, voice=voice)
            )

    def offer_heat(self, player, amount: float, card_name: str = ""):
        """v2.0 Plan B: 上供舞台 → 低保热力。

        物料牌打给 G2/G5 时调用，为使用者的声部贡献固定热力。
        """
        if self.phase != "duet":
            return
        voice = getattr(player, 'emotion', None)
        if voice not in self.duet_heat:
            return
        self.duet_heat[voice] += amount
        card_info = f"（{card_name}）" if card_name else ""
        display.show_info(
            prompt_manager.get_prompt(
                "duet", "offer.heat",
                default="🎁 {name} 向舞台献上 {card}！+{heat} 热力 → {voice}"
            ).format(name=player.name, card=card_info, heat=amount, voice=voice)
        )

    # ================================================================
    #  v2.0: duet 轮次结算
    # ================================================================
    def _duet_on_r4(self, game_state: GameState):
        """duet 模式 R4：当轮热力→Regard 折算 + 检查谢幕条件。"""
        CONVERSION = 0.5
        # 当轮热力增量 = 当前累计 - 上轮累计（避免全赛程累积导致 Regard 快速饱和）
        round_heat = {
            v: self.duet_heat[v] - self._duet_prev_heat.get(v, 0)
            for v in self.duet_heat
        }
        self._duet_prev_heat = dict(self.duet_heat)
        total_round = sum(round_heat.values())
        regen = total_round * CONVERSION * self._duet_heat_conversion_mult
        self.regard = min(self.regard_cap, self.regard + regen)

        # 重置伴唱标记
        self.harmonize_active = False
        self.duet_round += 1

        display.show_info(
            prompt_manager.get_prompt(
                "duet", "heat.round_end",
                default="\n🎤 第 {round}/8 轮 — 当轮热力: Acc=+{acc} Ind=+{ind} Str=+{str_} → Regard +{regen} = {regard}"
            ).format(
                round=self.duet_round,
                acc=round_heat.get(ACCAREZZEVOLE, 0),
                ind=round_heat.get(INDIFFERENZA, 0),
                str_=round_heat.get(STRAPPANDO, 0),  # str_ 避免覆写 Python str 内建类型
                regen=regen,
                regard=self.regard,
            )
        )

        # 检查谢幕条件
        if self.regard <= 0 or self.duet_round >= 8:
            self._duet_curtain(game_state)

    # ================================================================
    #  展开
    # ================================================================
    def open(self, game_state: GameState, g2_player: Player):
        """展开分布式 ish-bosheth（v0.6）。"""
        lines: list[str] = []

        # 1. G2 传送回家
        g2_player.location = self.g2_home
        lines.append(f"  🏠 {g2_player.name} 的家成为舞台中心。")

        # 2. 收集参与者（排除 G3 结界内、已死亡）
        g3_inside: set[str] = set()
        if game_state.active_barrier:
            barrier = game_state.active_barrier
            if hasattr(barrier, 'inside_players'):
                g3_inside = set(barrier.inside_players)

        for pid in game_state.player_order:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            if pid in g3_inside:
                lines.append(f"  {p.name} 身处幻想乡结界，未参与舞台。")
                continue
            self.participants.add(pid)

        # 2.5. 强制起床（草案 §3.2：未起床者强制起床且不算主动起床）
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p and not p.is_awake:
                p.is_awake = True
                game_state.markers.on_player_wake_up(pid)
                lines.append(f"  💤 {p.name} 被舞台强制唤醒！")
                # 天赋起床被动（G7 等）在 execute_t0 完整展开后、R3 额外回合中触发

        # 3. 解除隐身
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p and p.is_invisible:
                p.is_invisible = False
                lines.append(f"  {p.name} 的隐身被解除。")
        if g2_player.is_invisible:
            g2_player.is_invisible = False
            lines.append(f"  {g2_player.name} 的隐身被解除。")

        # 4. 清除即时关系
        all_pids_in_stage = list(self.participants) + [self.g2_owner_id]
        for pid in all_pids_in_stage:
            game_state.markers.clear_all_relations(pid)

        # 5. 星野架盾/持盾终止
        for pid in all_pids_in_stage:
            p = game_state.get_player(pid)
            if p and p.talent and hasattr(p.talent, 'shield_mode') and p.talent.shield_mode:
                if hasattr(p.talent, '_end_shield_mode'):
                    p.talent._end_shield_mode(p)
                    lines.append(f"  {p.name} 的架盾/持盾状态被终止。")

        # 6. 座位分配
        seat_lines = self._assign_seats(game_state, g2_player)
        lines.extend(seat_lines)

        # 6.5. Submerge 非玩家单位
        if hasattr(game_state, 'police') and game_state.police:
            for unit in game_state.police.units:
                if unit.is_alive() and unit.is_on_map():
                    unit.is_submerged = True
                    self.submerged_list.append(unit)
            if self.submerged_list:
                lines.append(prompt_manager.get_prompt(
                    "g2reset", "stage.submerge_count",
                    count=len(self.submerged_list)))

        # 7. liberamente_vivace
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p:
                p.stage_statuses = getattr(p, 'stage_statuses', set())
                p.stage_statuses.add("liberamente_vivace")
        # v0.6: G2 固定 D4=0 且强制首位行动，不需要 liberamente_vivace 的 D4+1

        # 8. 真实观众选择期望声部
        from controllers.human import HumanController
        voice_prefs: dict[str, str] = {}
        for pid in self.participants:
            p = game_state.get_player(pid)
            if not p:
                continue
            if isinstance(p.controller, HumanController):
                display.show_info(
                    f"\n{'='*50}\n"
                    f"  🎭 ish-bosheth 展开！请将屏幕交给 {p.name}\n"
                    f"{'='*50}")
                input(f"  [仅 {p.name} 可看] 按回车选择期望声部...")
            choice = p.controller.choose(
                "选择你在 ish-bosheth 舞台中的期望声部：",
                ["入戏 (Accarezzevole)", "抽离 (Indifferenza)", "反抗 (Strappando)"],
                context={"phase": "T0", "situation": "g2_voice_choice"},
            )
            voice_prefs[pid] = self._parse_emotion_choice(choice)
            lines.append(f"  {p.name} 期望 {VOICE_LABELS.get(voice_prefs[pid], voice_prefs[pid])}。")

        # 9. Chorus 随机声部
        for c in self.chorus_list:
            c.emotion = random.choice([ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO])

        # 10. ma non troppo 开场声部分配
        self.ma_non_troppo(game_state, voice_prefs)

        # 11. 创建物料牌系统
        from engine.material_deck import MaterialDeck
        self.deck = MaterialDeck()
        real_participants = [game_state.get_player(pid) for pid in self.participants
                            if game_state.get_player(pid) and game_state.get_player(pid).is_alive()]
        self.deck.opening_deal(real_participants, self.chorus_list, self.seat_assignments)

        # 12. 初始 Regard
        P = len(self.participants)
        C = len(self.chorus_list)
        N = len(self.submerged_list)
        self.regard = max(4.0, min(8.0, 3.0 + P + 0.5 * C + 0.5 * N))
        lines.append(f"  初始 Regard: {self.regard}/{self.regard_cap}")

        game_state.ish_bosheth = self
        return lines

    # ================================================================
    #  座位分配（同 v0.5，保留）
    # ================================================================
    SEATS = {"商店", "魔法所", "警察局", "医院", "军事基地"}

    def _assign_seats(self, game_state, g2_player) -> list[str]:
        lines: list[str] = []
        from controllers.chorus_controller import ChorusController

        seats = sorted(self.SEATS - {g2_player.location})

        assigned: dict[str, list] = {}
        unassigned = []

        for pid in self.participants:
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            loc = p.location
            if loc and loc in seats and loc not in assigned:
                assigned[loc] = [p]
            else:
                unassigned.append(p)

        empty_seats = [s for s in seats if s not in assigned]
        for p in unassigned:
            if empty_seats:
                seat = random.choice(empty_seats)
                assigned[seat] = [p]
                empty_seats.remove(seat)
            else:
                seat = random.choice(seats)
                assigned.setdefault(seat, []).append(p)
            if p.location != seat:
                p.location = seat
                game_state.markers.on_player_move(p.player_id)

        # 空座位各填 1 个 Chorus
        for seat in empty_seats:
            c = ChorusUnit()
            c.location = seat
            c.controller = ChorusController()
            self.chorus_list.append(c)
            game_state.markers.register_unit(c.player_id)
            game_state.register_chorus(c)
            assigned.setdefault(seat, []).append(c)

        # v0.6: 总观众数不足 6 时，在随机座位上追加 Chorus（补齐 2/2/2 三声部）
        total_audience = len(self.participants) + len(self.chorus_list)
        needed = max(0, 6 - total_audience)
        for _ in range(needed):
            c = ChorusUnit()
            seat = random.choice(seats)
            c.location = seat
            c.controller = ChorusController()
            self.chorus_list.append(c)
            game_state.markers.register_unit(c.player_id)
            game_state.register_chorus(c)
            assigned.setdefault(seat, []).append(c)

        for seat, units in assigned.items():
            names = []
            for u in units:
                pid = getattr(u, 'player_id', None)
                if pid:
                    self.seat_assignments[pid] = seat
                label = getattr(u, 'name', '?')
                names.append(label)
            lines.append(f"  🪑 {seat}: {', '.join(names)}")

        return lines

    # ================================================================
    #  R4 衰减（v0.6：含阵营胜利检查）
    # ================================================================
    def on_r4(self, game_state: GameState):
        if self.phase == "duet":
            self._duet_on_r4(game_state)
            return
        if self.phase != "active":
            return

        # ── 阵营胜利检查 ──
        faction_result = self._check_faction_victory(game_state)
        if faction_result:
            return  # end_ish_bosheth 已在 _check_faction_victory 中调用

        # 空场检查
        remaining_real = [pid for pid in self.participants
                          if pid != self.g2_owner_id
                          and game_state.get_player(pid)
                          and game_state.get_player(pid).is_alive()]
        if not remaining_real:
            self.end_ish_bosheth(END_EMPTY, game_state)
            return

        # Regard 变化（v0.6 公式 + 安定値追踪）
        total_delta = -1.0

        # Indifferenza 维持演出（v0.7 翻倍）
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if p and p.is_alive() and getattr(p, 'emotion', None) == INDIFFERENZA:
                total_delta += 1.0   # was 0.5
        for c in self.chorus_list:
            if c.is_alive() and c.emotion == INDIFFERENZA:
                total_delta += 0.5   # was 0.25

        # Strappando 撕裂演出
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if p and p.is_alive() and getattr(p, 'emotion', None) == STRAPPANDO:
                total_delta -= 0.5
        for c in self.chorus_list:
            if c.is_alive() and c.emotion == STRAPPANDO:
                total_delta -= 0.25

        self.adjust_regard(total_delta)
        self.r4_count += 1

        # 谢幕检查
        if self.regard <= 0:
            self.phase = "pending_curtain"
        elif self.r4_count >= 8:
            self.phase = "pending_curtain"

        # ── 清理过期效果 ──
        # 聚光灯 + Sognando 锁 + 临时增益
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p and "spotlight" in getattr(p, 'stage_statuses', set()):
                granted_r4 = getattr(p, '_spotlight_granted_r4', -1)
                if granted_r4 < self.r4_count:
                    p.stage_statuses.discard("spotlight")
                    p.stage_statuses.discard("sognando_lock")
                    p.temp_hp_g2 = 0.0
                    p.temp_atk_g2 = 0.0
        for c in self.chorus_list:
            c._g2_spotlight_target_id = None
            if "spotlight" in getattr(c, 'stage_statuses', set()):
                granted_r4 = getattr(c, '_spotlight_granted_r4', -1)
                if granted_r4 < self.r4_count:
                    c.stage_statuses.discard("spotlight")
                    c.stage_statuses.discard("sognando_lock")
                    c.temp_hp_g2 = 0.0
                    c.temp_atk_g2 = 0.0
        self.before_light = None
        self._pivot_override = None  # v0.7: Before light 效果仅持续一个 R4

        # 清理物料牌临时效果
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if p:
                for attr in ('_card_damage_bonus', '_card_damage_bonus_target_id',
                             '_card_damage_bonus_voice_filter',
                             '_card_debuff_damage_taken', '_card_no_attack_until_r4',
                             '_card_temp_hp_until_r4'):
                    if hasattr(p, attr):
                        if attr in ('_card_no_attack_until_r4',
                                   '_card_damage_bonus_target_id',
                                   '_card_damage_bonus_voice_filter'):
                            setattr(p, attr, None)
                        else:
                            setattr(p, attr, 0.0)
                if hasattr(p, '_card_earplug'):
                    p._card_earplug = False
                if hasattr(p, '_card_tear_ticket_active'):
                    p._card_tear_ticket_active = False
                if hasattr(p, '_card_d6_bonus_rounds') and getattr(p, '_card_d6_bonus_rounds', 0) > 0:
                    p._card_d6_bonus_rounds -= 1
        for c in self.chorus_list:
            for attr in ('_card_damage_bonus', '_card_damage_bonus_target_id',
                         '_card_damage_bonus_voice_filter',
                         '_card_debuff_damage_taken', '_card_no_attack_until_r4',
                         '_card_temp_hp_until_r4'):
                if hasattr(c, attr):
                    if attr in ('_card_no_attack_until_r4',
                               '_card_damage_bonus_target_id',
                               '_card_damage_bonus_voice_filter'):
                        setattr(c, attr, None)
                    else:
                        setattr(c, attr, 0.0)
            if hasattr(c, '_card_earplug'):
                c._card_earplug = False
            if hasattr(c, '_card_tear_ticket_active'):
                c._card_tear_ticket_active = False
            if hasattr(c, '_card_d6_bonus_rounds') and getattr(c, '_card_d6_bonus_rounds', 0) > 0:
                c._card_d6_bonus_rounds -= 1
            if hasattr(c, '_card_extra_play'):
                c._card_extra_play = False
            if hasattr(c, '_g2_commanded_target_id'):
                delattr(c, '_g2_commanded_target_id')

        # 清理声部特效标记
        for pid in self.participants:
            p = game_state.get_player(pid)
            if p:
                p.stage_statuses.discard(MARK_FERVOR)
                p.stage_statuses.discard(MARK_CRACK)
        for c in self.chorus_list:
            c.stage_statuses.discard(MARK_FERVOR)
            c.stage_statuses.discard(MARK_CRACK)

        # 清理 G2 投影
        self.projection_seat = None

        # 重置物料牌轮次追踪
        if self.deck:
            self.deck.reset_round_tracking()

    # ── 阵营胜利检查 ──────────────────────────────────────────────
    def _check_faction_victory(self, game_state: GameState) -> bool:
        """R4 检查阵营胜利条件。返回 True 表示游戏已因阵营胜利结束。"""
        # 统计存活的各声部单位
        acc_alive = False
        str_alive = False
        ind_alive = False

        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            voice = getattr(p, 'emotion', None)
            if voice == ACCAREZZEVOLE:
                acc_alive = True
            elif voice == STRAPPANDO:
                str_alive = True
            elif voice == INDIFFERENZA:
                ind_alive = True

        for c in self.chorus_list:
            if not c.is_alive():
                continue
            if c.emotion == ACCAREZZEVOLE:
                acc_alive = True
            elif c.emotion == STRAPPANDO:
                str_alive = True
            elif c.emotion == INDIFFERENZA:
                ind_alive = True

        # Acc 胜利：消灭所有 Str
        if not str_alive and acc_alive:
            prompt_manager.show("g2reset", "stage.acc_win")
            self.end_ish_bosheth(END_ACC_WIN, game_state)
            return True

        # Str 胜利：消灭所有 Acc
        if not acc_alive and str_alive:
            prompt_manager.show("g2reset", "stage.str_win")
            self.end_ish_bosheth(END_STR_WIN, game_state)
            return True

        # 静默终幕：Acc 和 Str 同时不存在，但 Ind 存在
        if not acc_alive and not str_alive and ind_alive:
            prompt_manager.show("g2reset", "stage.silent_end")
            self.end_ish_bosheth(END_SILENT, game_state)
            return True

        return False

    # ================================================================
    #  R0 废墟谢幕 / 完整谢幕
    # ================================================================
    def on_r0_curtain(self, game_state: GameState):
        if self.phase != "pending_curtain":
            return

        # 完整谢幕条件检查：Acc 和 Str 都没被彻底消灭
        acc_alive = False
        str_alive = False
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            v = getattr(p, 'emotion', None)
            if v == ACCAREZZEVOLE:
                acc_alive = True
            elif v == STRAPPANDO:
                str_alive = True
        for c in self.chorus_list:
            if not c.is_alive():
                continue
            if c.emotion == ACCAREZZEVOLE:
                acc_alive = True
            elif c.emotion == STRAPPANDO:
                str_alive = True

        # 完整谢幕
        if acc_alive and str_alive:
            self.end_ish_bosheth(END_IND_WIN, game_state)
            return

        # 废墟谢幕（旧路径：一方已灭、regard 归零或超时）
        # 1. 聚光灯/安可观众受 0.5 伤害
        for pid in list(self.participants):
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            ss = getattr(p, 'stage_statuses', set())
            if "spotlight" in ss or getattr(p, 'encore_layers', 0) > 0:
                p.hp = round(max(0, p.hp - 0.5), 2)
                prompt_manager.show("g2reset", "stage.curtain_damage",
                                   player_name=p.name, hp=p.hp)

        # 2. Strappando engage G2 检查
        has_strappando_engage = False
        g2p = game_state.get_player(self.g2_owner_id)
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if (p and p.is_alive()
                    and getattr(p, 'emotion', None) == STRAPPANDO
                    and game_state.markers.has_relation(
                        pid, "ENGAGED_WITH", self.g2_owner_id)):
                has_strappando_engage = True
                p._g2_curtain_d4_bonus = True
                display.show_info(f"  {p.name} 获得破幕未遂：下轮 D4+1。")

        if not has_strappando_engage and g2p and g2p.is_alive():
            game_state.markers.clear_all_relations(self.g2_owner_id)
            g2p.is_invisible = True

        self.end_ish_bosheth(END_CURTAIN, game_state)

    # ================================================================
    #  v2.0: duet 谢幕结算（排名 + Embrace + 安可）
    # ================================================================
    def _duet_curtain(self, game_state: GameState):
        """duet 谢幕：排名结算 + Embrace + 安可 + 清理。"""
        if self.duet_curtain_triggered:
            return
        self.duet_curtain_triggered = True

        reached_max = self.duet_round >= 8
        sep = "=" * 50
        display.show_info(
            prompt_manager.get_prompt(
                "duet", "curtain.header",
                default=f"\n{sep}\n  🎤 双人演出谢幕！\n{sep}"
            )
        )

        # ── 安可判定 ──
        self._check_duet_encore(game_state)

        # ── 热力排名 ──
        ranked = sorted(self.duet_heat.items(), key=lambda x: x[1], reverse=True)
        if len(ranked) >= 3:
            display.show_info(
                prompt_manager.get_prompt(
                    "duet", "curtain.ranking",
                    default="🏆 热力排名：第1 — {first}({hf}) / 第2 — {second}({hs}) / 第3 — {third}({ht})"
                ).format(
                    first=VOICE_LABELS.get(ranked[0][0], ranked[0][0]), hf=ranked[0][1],
                    second=VOICE_LABELS.get(ranked[1][0], ranked[1][0]), hs=ranked[1][1],
                    third=VOICE_LABELS.get(ranked[2][0], ranked[2][0]), ht=ranked[2][1],
                )
            )

        # ── 各声部奖励 ──
        self._award_duet_rank_rewards(game_state, ranked, reached_max)

        # ── Embrace 谢幕拥抱 ──
        self._duet_embrace_phase(game_state, ranked)

        # ── 清理 ──
        self.end_ish_bosheth(END_DUET, game_state)

    def _check_duet_encore(self, game_state: GameState):
        """检查安可条件：第 8 轮 + G5 追忆完全未使用。"""
        if self.duet_round < 8 or self.duet_encores > 0:
            return
        g5 = game_state.get_player(self.duet_g5_pid)
        if not g5 or not g5.talent:
            return
        talent = g5.talent
        budget = getattr(talent, 'reminiscence_budget', 0)
        # 预算仅通过 max(0, budget - 2) 递减，2 的倍数路径简单，
        # 浮点精度不影响等值判定（budget 不会是 12.0 - ε）
        if budget < 12.0:
            return

        self.duet_encores += 1
        display.show_info(
            prompt_manager.get_prompt(
                "duet", "encore.trigger",
                default="\n🎉🎉 安可！安可！G5 的 12 追忆分毫未动！\n"
                        "   G2 与 G5 合唱双人曲 —— 所有观众自选一件物品！"
            )
        )

        # 全员自选物品（两轮选择：地点 → 物品）
        from models.equipment import make_weapon, make_armor, make_item
        LOCATION_ITEMS = {
            "商店": ["小刀", "盾牌", "陶瓷护甲", "隐身衣", "防毒面具", "通行证"],
            "警察局": ["警棍", "防毒面具"],
            "军事基地": ["高斯步枪", "电磁步枪", "导弹", "AT力场", "热成像仪"],
            "魔法所": ["魔法弹幕", "远程魔法弹幕", "魔法护盾"],
            "医院": ["防毒面具"],
            "家": ["小刀", "磨刀石", "通行证"],
        }
        locations = list(LOCATION_ITEMS.keys())

        for pid in list(self.participants):
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            # 第一轮：选地点
            loc_choice = p.controller.choose(
                prompt_manager.get_prompt("duet", "encore.pick_location", default="选择获取地点："),
                locations,
                context={"phase": "duet_encore", "situation": "pick_location"}
            )
            if loc_choice not in LOCATION_ITEMS:
                loc_choice = locations[0]
            # 第二轮：选物品
            items = LOCATION_ITEMS.get(loc_choice, ["小刀"])
            item_choice = p.controller.choose(
                prompt_manager.get_prompt("duet", "encore.pick_item", default="选择要获取的物品："),
                items,
                context={"phase": "duet_encore", "situation": "pick_item", "location": loc_choice}
            )
            if item_choice not in items:
                item_choice = items[0]
            # 发放
            granted = False
            for factory in (make_weapon, make_armor, make_item):
                obj = factory(item_choice)
                if obj:
                    if factory is make_weapon:
                        p.add_weapon(obj)
                    elif factory is make_armor:
                        p.add_armor(obj)
                    else:
                        p.add_item(obj)
                    granted = True
                    break
            if not granted:
                display.show_info(f"  ⚠️ 无法创建「{item_choice}」，请手动发放。")
            else:
                display.show_info(
                    prompt_manager.get_prompt(
                        "duet", "encore.grant",
                        default="✅ {name} 获得了 {item}！"
                    ).format(name=p.name, item=item_choice)
                )

    def _award_duet_rank_rewards(self, game_state: GameState, ranked: list, reached_max: bool):
        """按热力排名发放三等奖励。"""
        multiplier = 1.0
        if not reached_max:
            total_rounds = self.duet_round
            if total_rounds >= 6:
                multiplier = 0.75
            elif total_rounds >= 4:
                multiplier = 0.5
            else:
                multiplier = 0.25

        for rank_idx, (voice, heat) in enumerate(ranked):
            # 收集该声部的真实玩家
            members = []
            for pid in self.participants:
                p = game_state.get_player(pid)
                if p and p.is_alive() and getattr(p, 'emotion', None) == voice:
                    members.append(p)
            for c in self.chorus_list:
                if c.is_alive() and c.emotion == voice:
                    members.append(c)

            if rank_idx == 0:
                for m in members:
                    m._duet_d4_bonus = True
                    m._duet_d6_bonus = True
                    m._duet_damage_bonus = getattr(m, '_duet_damage_bonus', 0) + 0.5 * multiplier
                display.show_info(
                    prompt_manager.get_prompt("duet", "curtain.reward_1st",
                        default="🥇 第1声部({voice}): D4/D6+1，下次伤害+0.5").format(voice=voice))
            elif rank_idx == 1:
                for m in members:
                    m._duet_d4_bonus = True
                    m._duet_d6_bonus = True
                    m.temp_hp_g2 += 0.5 * multiplier
                display.show_info(
                    prompt_manager.get_prompt("duet", "curtain.reward_2nd",
                        default="🥈 第2声部({voice}): D4/D6+1，tempHP+0.5").format(voice=voice))
            else:
                for m in members:
                    m._duet_d4_bonus = True
                display.show_info(
                    prompt_manager.get_prompt("duet", "curtain.reward_3rd",
                        default="🥉 第3声部({voice}): D4+1").format(voice=voice))

    def _duet_embrace_phase(self, game_state: GameState, ranked: list):
        """谢幕拥抱阶段：可选 embrace G2 或 G5。"""
        g2 = game_state.get_player(self.g2_owner_id)
        g5 = game_state.get_player(self.duet_g5_pid)
        if not g2 or not g5:
            return

        # 真实玩家
        for pid in list(self.participants):
            p = game_state.get_player(pid)
            if not p or not p.is_alive():
                continue
            self._embrace_player(p, g2, g5, ranked)
        # v2.0: Chorus 也可参与 Embrace
        for c in self.chorus_list:
            if c.is_alive():
                self._embrace_player(c, g2, g5, ranked)

    def _embrace_player(self, p, g2, g5, ranked):
        """单个单位（真实玩家或 Chorus）的 Embrace 逻辑。"""
        voice = getattr(p, 'emotion', None)
        rank_idx = next((i for i, (v, _) in enumerate(ranked) if v == voice), 2)
        embrace_mult = 2.0 if rank_idx == 0 else (1.0 if rank_idx == 1 else 0.5)

        choice = p.controller.choose(
            f"谢幕拥抱 —— {p.name}，选择拥抱：",
            [f"拥抱 {g2.name}", f"拥抱 {g5.name}", "不拥抱"],
            context={"phase": "duet_curtain", "situation": "embrace"}
        )
        if "不抱" in choice:
            return
        if g2.name in choice:
            p._embrace_g2_buff = embrace_mult
            display.show_info(
                prompt_manager.get_prompt("duet", "embrace.g2",
                    default="🤗 {name} 拥抱了 G2！获得「歌者的祝福」：下次攻击伤害+{mult}"
                ).format(name=p.name, mult=embrace_mult))
        elif g5.name in choice:
            p._embrace_g5_buff = embrace_mult
            display.show_info(
                prompt_manager.get_prompt("duet", "embrace.g5",
                    default="🤗 {name} 拥抱了 G5！获得「涟漪的余韵」：下次被攻击免伤{mult}"
                ).format(name=p.name, mult=embrace_mult))

    # ================================================================
    #  统一清理（v0.6）
    # ================================================================
    def end_ish_bosheth(self, reason: str, game_state: GameState,
                        breaker_id: Optional[str] = None):
        g2p = game_state.get_player(self.g2_owner_id)

        display.show_info(f"\n{'='*50}")
        prompt_manager.show("g2reset", "stage.end_header", reason=reason)
        display.show_info(f"{'='*50}")

        # 阵营胜利奖励说明
        self._award_faction_rewards(reason, game_state, breaker_id)

        # 破幕特殊
        if reason == END_BREAK and breaker_id:
            breaker = game_state.get_player(breaker_id)
            if breaker:
                breaker._g2_curtain_d4_bonus = True

        # 空场 / 谢幕 / 完整演出 G2 隐身
        if reason in (END_EMPTY, END_CURTAIN, END_SILENT, END_IND_WIN, END_DUET) and g2p and g2p.is_alive():
            game_state.markers.clear_all_relations(self.g2_owner_id)
            g2p.is_invisible = True

        # 通用清理：参与者
        for pid in list(self.participants):
            p = game_state.get_player(pid)
            if p:
                p.emotion = None
                if hasattr(p, 'stage_statuses'):
                    p.stage_statuses.clear()
                p.encore_layers = 0
                if hasattr(p, 'stage_entangle'):
                    p.stage_entangle.clear()
                p.temp_hp_g2 = 0.0
                p.temp_atk_g2 = 0.0
                # 清理物料牌临时效果
                p._card_damage_bonus = 0.0
                p._card_damage_bonus_target_id = None
                p._card_damage_bonus_voice_filter = None
                p._card_debuff_damage_taken = 0.0
                p._card_no_attack_until_r4 = None
                p._card_temp_hp_until_r4 = 0.0
                p._card_earplug = False
                p._card_tear_ticket_active = False
                p._card_d6_bonus_rounds = 0
                p._card_extra_play = False

        # G2 清理
        if g2p:
            if hasattr(g2p, 'stage_statuses'):
                g2p.stage_statuses.clear()
            g2p.emotion = None
            g2p.encore_layers = 0
            if hasattr(g2p, 'stage_entangle'):
                g2p.stage_entangle.clear()
            g2p.temp_hp_g2 = 0.0
            g2p.temp_atk_g2 = 0.0

        # Chorus 消散
        for c in self.chorus_list:
            game_state.unregister_chorus(c.player_id)
        self.chorus_list.clear()

        # Submerged 解除
        for unit in self.submerged_list:
            unit.is_submerged = False
        self.submerged_list.clear()

        # 物料牌系统清理
        if self.deck:
            self.deck.clear_all()
            self.deck = None

        self.phase = "ended"
        game_state.ish_bosheth = None

    def _award_faction_rewards(self, reason: str, game_state: GameState,
                               breaker_id: Optional[str] = None):
        """阵营胜利时发放奖励。"""
        if reason == END_ACC_WIN:
            # Acc 狂热终幕
            for pid in self.participants:
                p = game_state.get_player(pid)
                if p and p.is_alive() and getattr(p, 'emotion', None) == ACCAREZZEVOLE:
                    p._g2_curtain_d4_bonus = True
                    card_bonus = getattr(p, '_card_damage_bonus', 0.0)
                    p._card_damage_bonus = max(card_bonus, 0.5)
                    display.show_info(f"  🎭 {p.name} (Acc) 获得狂热余温：下轮 D4/D6+1，下次伤害+0.5")

        elif reason == END_STR_WIN:
            # Str 撕幕终幕
            for pid in self.participants:
                p = game_state.get_player(pid)
                if p and p.is_alive() and getattr(p, 'emotion', None) == STRAPPANDO:
                    p._g2_curtain_d4_bonus = True
                    # 清除自身标记
                    game_state.markers.clear_all_relations(pid)
                    display.show_info(f"  🎭 {p.name} (Str) 获得撕幕余烬：下轮 D4/D6+1，清除标记")
            if breaker_id:
                b = game_state.get_player(breaker_id)
                if b:
                    b._g2_curtain_d4_bonus = True  # 额外 D4+1

        elif reason == END_IND_WIN:
            # 完整谢幕：G2 + Ind 奖励
            for pid in self.participants:
                p = game_state.get_player(pid)
                if p and p.is_alive() and getattr(p, 'emotion', None) == INDIFFERENZA:
                    p._g2_curtain_d4_bonus = True
                    p.hp = min(p.max_hp, round(p.hp + 0.5, 2))
                    display.show_info(f"  🎭 {p.name} (Ind) 获得谢幕回声：D4/D6+1，恢复 0.5 HP")
            # G2 奖励（在 end_ish_bosheth 中处理隐身，这里加 D4/D6）
            g2p = game_state.get_player(self.g2_owner_id)
            if g2p and g2p.is_alive():
                g2p._g2_curtain_d4_bonus = True
                # Chorus 存活率奖励
                if self.chorus_list:
                    initial = len(self.chorus_list)  # 已 clear，需在清理前计算
                else:
                    initial = 0
                # 这里 chorus_list 还没清空，存活率在 end_ish_bosheth 之前计算
                alive_chorus = sum(1 for c in self.chorus_list if c.is_alive())
                if initial > 0:
                    survival = alive_chorus / initial
                    if survival >= 1.0:
                        g2p.hp = min(g2p.max_hp, round(g2p.hp + 1.0, 2))
                        display.show_info(f"  🌟 Chorus 全存活(100%)！{g2p.name} 恢复 1 HP，D4/D6+1")
                    elif survival >= 0.5:
                        g2p.hp = min(g2p.max_hp, round(g2p.hp + 0.5, 2))
                        display.show_info(f"  🌟 Chorus 存活率 {survival:.0%}！{g2p.name} 恢复 0.5 HP，D6+1")
                        g2p._card_d6_bonus_rounds = max(g2p._card_d6_bonus_rounds, 1)
                    elif survival > 0:
                        g2p.hp = min(g2p.max_hp, round(g2p.hp + 0.5, 2))
                        display.show_info(f"  🌟 Chorus 存活率 {survival:.0%}！{g2p.name} 恢复 0.5 HP")

    # ================================================================
    #  声部分配（v0.6 ma non troppo，仅开场）
    # ================================================================
    @staticmethod
    def _parse_emotion_choice(choice_str: str) -> str:
        if not choice_str:
            return INDIFFERENZA
        low = choice_str.lower()
        if "入戏" in low or "accarezzevole" in low:
            return ACCAREZZEVOLE
        if "反抗" in low or "strappando" in low:
            return STRAPPANDO
        return INDIFFERENZA

    def ma_non_troppo(self, game_state: GameState,
                      voice_prefs: Optional[dict[str, str]] = None):
        """v0.6: 开场分配三声部，目标 2/2/2 结构。

        1. 先按真实观众意愿分配
        2. 若某声部真实玩家 > 2，随机保留 2 人，其余分配到缺口
        3. Chorus 补足缺口
        """
        real_observers: List[Player] = []
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if p and p.is_alive():
                real_observers.append(p)

        # 1. 按意愿初步分配
        acc_real = []; ind_real = []; str_real = []
        for p in real_observers:
            pref = (voice_prefs or {}).get(p.player_id, INDIFFERENZA)
            p.emotion = pref
            if pref == ACCAREZZEVOLE:
                acc_real.append(p)
            elif pref == STRAPPANDO:
                str_real.append(p)
            else:
                ind_real.append(p)

        # 2. 若某声部 > 2，溢出者重新分配到缺口
        target = 2
        for group, voice in [(acc_real, ACCAREZZEVOLE),
                             (ind_real, INDIFFERENZA),
                             (str_real, STRAPPANDO)]:
            while len(group) > target:
                overflow = group.pop()
                # 找到数量最少的声部（排除当前声部以避免无限循环）
                counts = {ACCAREZZEVOLE: len(acc_real),
                          INDIFFERENZA: len(ind_real),
                          STRAPPANDO: len(str_real)}
                other_counts = {v: c for v, c in counts.items() if v != voice}
                min_voice = min(other_counts, key=other_counts.get)
                overflow.emotion = min_voice
                if min_voice == ACCAREZZEVOLE:
                    acc_real.append(overflow)
                elif min_voice == STRAPPANDO:
                    str_real.append(overflow)
                else:
                    ind_real.append(overflow)

        # 3. Chorus 补足至 2/2/2
        alive_chorus = [c for c in self.chorus_list if c.is_alive()]
        counts = {ACCAREZZEVOLE: len(acc_real),
                  INDIFFERENZA: len(ind_real),
                  STRAPPANDO: len(str_real)}
        for voice, current in counts.items():
            needed = target - current
            # 从已有其他声部的 Chorus 中改，或从未分配的 Chorus 中选
            available = [c for c in alive_chorus if c.emotion != voice]
            for _ in range(max(0, needed)):
                if available:
                    c = random.choice(available)
                    c.emotion = voice
                    available.remove(c)

    # ================================================================
    #  曲目辅助
    # ================================================================
    def get_available_songs(self) -> list[dict]:
        songs = []

        # v2.0 duet 模式：仅基础三首歌（旋律禁用）
        if self.phase == "duet":
            if self.regard >= 1:
                songs.append({
                    "name": "追寻那道光",
                    "cost": 1,
                    "desc": "选声部 → 按钮伤害×1.5 / 位移至按钮座",
                    "rhythms": self._get_rhythms_for_song("追寻那道光"),
                })
                songs.append({
                    "name": "拼接遗憾",
                    "cost": 1,
                    "desc": "位移免疫+临时HP / 互换两座位全员+复活Chorus",
                    "rhythms": self._get_rhythms_for_song("拼接遗憾"),
                })
                # Before light duet 消耗不同：Riposato=2, Dolente=3
                bl_rhythms = self._get_rhythms_for_song("Before light")
                for r in bl_rhythms:
                    if r.get("duet_key") == "riposato":
                        r["cost"] = 2
                    elif r.get("duet_key") == "dolente":
                        r["cost"] = 3
                songs.append({
                    "name": "Before light",
                    "cost": 2,
                    "desc": "转化率×1.5公共池 / 生成第3按钮+全局×1.3",
                    "rhythms": bl_rhythms,
                })
            return songs

        # 正常模式
        if self.regard >= 1:
            songs.append({
                "name": "追寻那道光",
                "cost": 1,
                "desc": "选择演员：聚光灯+摸牌",
                "rhythms": self._get_rhythms_for_song("追寻那道光"),
            })
            songs.append({
                "name": "拼接遗憾",
                "cost": 1,
                "desc": "修补物料与观众",
                "rhythms": self._get_rhythms_for_song("拼接遗憾"),
            })
            songs.append({
                "name": "Before light",
                "cost": 1,
                "desc": "改变本轮规则",
                "rhythms": self._get_rhythms_for_song("Before light"),
            })
        # v0.7 旋律：累计 ΔRegard 解锁（而非固定轮次）
        if self.cumulative_delta_regard >= self.MELODY_1_THRESHOLD and not self.melody_1_used:
            songs.append({
                "name": "旋律·第一音节",
                "cost": 0,
                "desc": "双座位 1.0/0.5/0.5/0.5 安定値修正",
                "rhythms": [{"name": "第一音节", "cost": 0}],
            })
        if self.cumulative_delta_regard >= self.MELODY_2_THRESHOLD and not self.melody_2_used:
            songs.append({
                "name": "旋律·第二间章",
                "cost": 0,
                "desc": "双座位 1/1/0.5/0.5 安定値修正",
                "rhythms": [{"name": "第二间章", "cost": 0}],
            })
        if self.cumulative_delta_regard >= self.MELODY_3_THRESHOLD and not self.melody_3_used:
            songs.append({
                "name": "旋律·第三间章",
                "cost": 0,
                "desc": "双座位 2/2/1/1 安定値修正",
                "rhythms": [{"name": "第三间章", "cost": 0}],
            })
        return songs

    def _get_rhythms_for_song(self, song_name: str) -> list[dict]:
        if song_name == "追寻那道光":
            rhythms = [
                {"name": "温柔 (Soave)", "cost": 1, "duet_key": "soave"},
            ]
            if self.regard >= 2:
                rhythms.append({"name": "追寻 (Sognando)", "cost": 2, "duet_key": "sognando"})
            return rhythms
        elif song_name == "Before light":
            rhythms = [
                {"name": "休息 (Riposato)", "cost": 1, "duet_key": "riposato"},
            ]
            if self.regard >= 2:
                rhythms.append({"name": "悲伤 (Dolente)", "cost": 2, "duet_key": "dolente"})
            return rhythms
        elif song_name == "拼接遗憾":
            rhythms = [
                {"name": "平静 (Placido)", "cost": 1, "duet_key": "placido"},
            ]
            if self.regard >= 2:
                rhythms.append({"name": "遗憾 (Zeffiroso)", "cost": 2, "duet_key": "zeffiroso"})
            return rhythms
        return []

    def get_legal_sing_targets(self, game_state: GameState,
                                song_name: str, rhythm_name: str) -> list:
        """返回合法听者列表。"""
        targets = []
        for pid in self.participants:
            if pid == self.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if p and p.is_alive():
                targets.append(p)
        for c in self.chorus_list:
            if c.is_alive():
                targets.append(c)
        return targets

    # ================================================================
    #  旋律（v0.7：双座位 + 安定値修正）
    # ================================================================
    def execute_melody(self, game_state: GameState, g2_player: Player,
                       base_dmg_seq: list = None):
        """旋律 v0.7：G2 选 1-2 座位 → 最多 4 目标 → 安定値修正伤害/治疗。"""
        if base_dmg_seq is None:
            base_dmg_seq = [1.0, 1.0, 0.5, 0.5]

        occupied = self._get_occupied_seats(game_state)
        if not occupied:
            prompt_manager.show("g2reset", "melody.no_targets")
            return

        seat_names = sorted(occupied.keys())

        # 选座位 1（必选）
        chosen1 = g2_player.controller.choose(
            "选择旋律目标座位 1：",
            seat_names,
            context={"situation": "g2_melody_seat"},
        )
        if chosen1 not in occupied:
            chosen1 = seat_names[0]

        # 选座位 2（可选"不选"）
        remaining = [s for s in seat_names if s != chosen1]
        remaining.append("不选")
        chosen2 = g2_player.controller.choose(
            "选择旋律目标座位 2（或不选）：",
            remaining,
            context={"situation": "g2_melody_seat2"},
        )
        if chosen2 not in occupied:
            chosen2 = None

        # 收集目标
        all_targets = list(occupied.get(chosen1, []))
        if chosen2:
            all_targets += occupied.get(chosen2, [])
        targets = [t for t in all_targets if t.is_alive()][:4]
        if not targets:
            prompt_manager.show("g2reset", "melody.no_targets")
            return

        # 衰减序列
        decays = [1.0, 0.6, 0.4, 0.2]
        from combat.damage_resolver import resolve_damage

        for i, target in enumerate(targets):
            dmg = base_dmg_seq[i] if i < len(base_dmg_seq) else 0.5
            decay = decays[i] if i < len(decays) else 0.1
            stability = _calc_stability(target, self.cumulative_delta_regard, decay, ish=self)
            raw = _calc_melody_damage(dmg, stability, target.max_hp, target.hp)
            # v0.7: 安定值临时标记清除（单次效果）
            for attr in ('_stability_armor_mult', '_stability_force_decay',
                         '_stability_defense_offset'):
                if hasattr(target, attr):
                    delattr(target, attr)

            prompt_manager.show("g2reset", "melody.hit",
                               index=i+1, target_name=target.name)
            if raw > 0:
                result = resolve_damage(
                    g2_player, target, None, game_state,
                    raw_damage_override=raw,
                    damage_attribute_override="无视属性克制",
                    is_talent_attack=True,
                )
                for detail in result.get("details", []):
                    display.show_info(f"   {detail}")
                if (result.get("killed") and hasattr(game_state, 'police_engine')
                        and game_state.police_engine):
                    game_state.police_engine.check_and_record_crime(
                        g2_player.player_id, "伤害玩家")
            else:
                heal = min(-raw, target.max_hp - target.hp)
                if heal > 0:
                    target.hp = round(target.hp + heal, 2)
                    display.show_info(
                        f"   🎵 旋律共鸣：{target.name} 恢复 {heal:.1f} HP → {target.hp}")

            # 声部特效（命中且存活后触发）
            if target.is_alive():
                self._apply_melody_voice_effect(target, game_state)

    def _apply_melody_voice_effect(self, target, game_state: GameState):
        """旋律命中存活目标后，根据声部触发特效。"""
        voice = getattr(target, 'emotion', None)
        ss = getattr(target, 'stage_statuses', set())

        if voice == ACCAREZZEVOLE:
            # 狂热：下次攻击 Str 伤害 +0.5
            ss.add(MARK_FERVOR)
            target._card_damage_bonus = max(target._card_damage_bonus, 0.5)
            target._card_damage_bonus_voice_filter = STRAPPANDO
            prompt_manager.show("g2reset", "melody.fervor",
                               player_name=target.name)

        elif voice == INDIFFERENZA:
            # 回声：摸 1 张物料牌
            if self.deck:
                if getattr(target, 'is_chorus', False):
                    self.deck.chorus_draw(target.player_id)
                else:
                    card = self.deck._draw_one()
                    if card:
                        hand = self.deck.hands.setdefault(target.player_id, [])
                        if len(hand) < 3:
                            hand.append(card)
            prompt_manager.show("g2reset", "melody.echo",
                               player_name=target.name)

        elif voice == STRAPPANDO:
            # 裂音：Regard -0.25，下次攻击 G2 伤害 +0.5
            self.adjust_regard(-0.25)
            ss.add(MARK_CRACK)
            target._card_damage_bonus = max(target._card_damage_bonus, 0.5)
            target._card_damage_bonus_target_id = self.g2_owner_id
            prompt_manager.show("g2reset", "melody.crack",
                               player_name=target.name, regard=self.regard)

    def _get_occupied_seats(self, game_state) -> dict:
        occupied: dict[str, list] = {}
        for seat in self.SEATS:
            units = []
            for pid in self.participants:
                if pid == self.g2_owner_id:
                    continue
                p = game_state.get_player(pid)
                if p and p.is_alive() and p.location == seat:
                    units.append(p)
            for c in self.chorus_list:
                if c.is_alive() and c.location == seat:
                    units.append(c)
            if units:
                occupied[seat] = units
        return occupied

    # ================================================================
    #  v0.6 G2 投影（后台通行证）
    # ================================================================
    def create_projection(self, seat: str) -> bool:
        """在指定座位生成 G2 舞台投影。"""
        self.projection_seat = seat
        self.projection_round = self.r4_count
        return True

    def get_projection_seat(self) -> Optional[str]:
        """获取当前投影所在座位。"""
        return self.projection_seat


# ══════════════════════════════════════════════════════════════════
#  v0.7 安定値计算（模块级函数）
# ══════════════════════════════════════════════════════════════════

def get_total_defense_hp(unit) -> float:
    """总防御HP = base_HP + 护甲当前HP + 天赋特殊防御。
    不计入 G2 临时增益（temp_hp_g2 / _card_temp_hp_until_r4）。"""
    hp = unit.max_hp
    t = getattr(unit, 'talent', None)
    if t:
        hp += getattr(t, 'temp_hp', 0.0)
        hp += getattr(t, 'ardent_wish_charges', 0) * 0.5
        hp += getattr(t, 'iron_horus_hp', 0.0)
    armor = getattr(unit, 'armor', None)
    if armor and hasattr(armor, 'get_all_active'):
        for p in armor.get_all_active():
            hp += p.current_hp
    return hp


def _calc_stability(unit, cumulative_delta: float, decay_factor: float = 1.0,
                   ish=None) -> float:
    """每目标独立安定値。正=增伤，负=治疗。

    受以下临时标记影响（旋律命中后立即清除，不会跨目标叠加）：
    - _stability_armor_mult: Placido(×0.5) / Zeffiroso(×2)
    - _stability_force_decay: 反光板(强制decay=1.0)
    - _stability_defense_offset: 耳返(total_defense偏移)
    - ish._pivot_override: Riposato(5.0) / Dolente(2.0)"""
    base = max(-0.5, min(1.5, cumulative_delta / 6.0 - 0.5))
    pivot = ish._pivot_override if (ish and ish._pivot_override is not None) else 3.5
    offset = getattr(unit, '_stability_defense_offset', 0.0)
    total_def = get_total_defense_hp(unit) + offset
    armor_mod = (total_def - pivot) * 0.4
    stability = (base + armor_mod)
    mult = getattr(unit, '_stability_armor_mult', 1.0)
    stability *= mult
    forced = getattr(unit, '_stability_force_decay', None)
    if forced is not None:
        decay_factor = forced
    return stability * decay_factor


def _calc_melody_damage(base_dmg: float, stability: float,
                        unit_max_hp: float, unit_current_hp: float) -> float:
    """安定値修正后的旋律伤害。≤0 → 负值表示治疗量。
    注意：衰减已在 _calc_stability 中生效，此处不再重复。"""
    raw = round(base_dmg * (1.0 + stability), 2)
    if raw <= 0:
        return max(raw, unit_current_hp - unit_max_hp)
    return max(0.5, raw)


# ══════════════════════════════════════════════════════════════════
#  情绪调整（保留兼容，v0.6 中不再被旋律调用）
# ══════════════════════════════════════════════════════════════════

def adjust_emotion_up(unit, game_state=None, ish=None):
    """情绪上调一级（v0.6 保留兼容，不再自动触发）。"""
    if not hasattr(unit, 'emotion') or not unit.emotion:
        return
    idx = VOICE_ORDER.index(unit.emotion) if unit.emotion in VOICE_ORDER else 1
    if idx < 2:
        unit.emotion = VOICE_ORDER[idx + 1]


def adjust_emotion_down(unit, game_state=None, ish=None):
    """情绪下调一级（v0.6 保留兼容）。"""
    if not hasattr(unit, 'emotion') or not unit.emotion:
        return
    idx = VOICE_ORDER.index(unit.emotion) if unit.emotion in VOICE_ORDER else 1
    if idx > 0:
        unit.emotion = VOICE_ORDER[idx - 1]
    else:
        trigger_snap(unit, game_state, ish)


def trigger_snap(unit, game_state=None, ish=None):
    """断弦（v0.6 保留兼容，不再自动触发）。"""
    unit.hp = round(max(0, unit.hp - 0.5), 2)
    if hasattr(unit, 'stage_statuses'):
        unit.stage_statuses.add("imbalance")
    prompt_manager.show("g2reset", "snap.triggered",
                       player_name=unit.name, hp=unit.hp)
