"""
Ripple —— 神代天赋5「往世的涟漪」主类

追忆系统 + T0入口 + 状态描述。
锚定逻辑在 AnchorMixin，献诗逻辑在 PoemMixin。

V1.92+ 改动：
  - 追忆积攒速度：未行动 +0.5，行动了 +1（原 +2/+1）
  - 无次数上限，首次发动需24层消耗12，之后12层消耗12
  - 爱与记忆之诗消耗递增：min(24, 12+3×已用次数)
  - 同一首诗可重复使用
"""

from talents.base_talent import BaseTalent
from talents.g5.anchor_mixin import AnchorMixin
from talents.g5.poem_mixin import PoemMixin
from cli import display
from controllers.human import HumanController
from engine.prompt_manager import prompt_manager


class Ripple(AnchorMixin, PoemMixin, BaseTalent):
    name = "往世的涟漪"
    description = "追忆满后发动：锚定命运或献诗增强。无次数限制，爱与记忆逐次成长"
    tier = "神代"

    POEM_MAP = {
        "一刀缭断": "游侠",
        "剪刀手一突": "地火",
        "神话之外": "永恒",
        "天星": "群星",
        "朝阳好市民": "律法",
        "combo": "旋律",
        "六爻": "阴阳",
        "死者苏生": "彼岸",
        "火萤IV型-完全燃烧": "飞萤",
        "请一直，注视着我": "追光",
        "愿负世，照拂黎明": "负世",
        "往世的涟漪": "爱与记忆",
        "要有笑声！": "欢愉",
        "大叔我啊，剪短发了": "守夜人",
    }

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)

        # === 追忆 ===
        self.reminiscence = 0
        self.max_reminiscence = 24       # V1.92: 首次24，之后降为12
        self.activation_threshold = 24   # 发动门槛（首次24，之后降为12）
        self.acted_last_round = False
        self.only_extra_turn = False

        # === V1.92+: 使用次数追踪（无上限） ===
        self.used = False                # 向后兼容：不再设为 True
        self.total_uses = 0              # 已发动总次数（无上限）
        self.poem_use_counts = {}        # {poem_type: count} — tracks per-poem usage
        self.destiny_use_count = 0       # 爱与记忆之诗使用次数（用于递增消耗和段数成长）

        # === 锚定状态（由 AnchorMixin 使用）===
        self.anchor_active = False
        self.anchor_type = None
        self.anchor_target_id = None
        self.anchor_detail = ""
        self.anchor_path = []
        self.anchor_fate = 0
        self.anchor_variance = 0
        self.anchor_rounds_left = 0
        self.anchor_destructive_count = 0
        self.anchor_caster_backup = None
        self.anchor_target_snapshot = None
        self.anchor_revealed_step = None
        self.was_paused_by_barrier = False

        # === V1.92: 轮初快照（由 AnchorMixin._auto_judge_destructive 使用）===
        self._target_round_start_location: object | None = None
        self._target_round_start_armor: list | None = None
        self._caster_round_start_stunned: bool = False
        self._caster_round_start_shocked: bool = False
        self._caster_round_start_petrified: bool = False

        # === V1.92: 锚定D4加成（来自「一页永恒的善见天」）===
        # 成功锚定后，双方在后续5个判定轮次中D4点数+2（不超过4）
        self._anchor_d4_bonus_rounds: int = 0  # 剩余加成轮次
        self._anchor_d4_target_id: str | None = None  # 持久化目标ID（不被_anchor_cleanup清除）

        # === 爱愿系统 ===
        self.love_wish = {}  # {target_player_id: remaining_rounds}

    # ================================================================
    #  V1.92+: 消耗使用次数
    # ================================================================

    def _consume_use(self, cost=12):
        """消耗一次发动机会 + 指定层追忆"""
        self.total_uses += 1
        self.reminiscence = max(0, self.reminiscence - cost)
        # 首次发动后，发动门槛降为12（但积累上限保持24）
        if self.total_uses == 1:
            self.activation_threshold = 12  # 原来是 self.max_reminiscence = 12

    def _can_activate(self):
        """检查是否还能发动（无次数上限）"""
        return True

    def get_destiny_cost(self):
        """爱与记忆之诗的递增消耗：min(24, 12 + 3 × 已用次数)"""
        return min(24, 12 + 4 * self.destiny_use_count)

    # ================================================================
    #  辅助
    # ================================================================

    def _has_human_players(self):
        """如果全场没有人类玩家，DM 判定自动处理"""
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and isinstance(p.controller, HumanController):
                return True
        return False

    def grant_love_wish(self, target_pid, rounds=12):
        """给目标施加爱愿效果"""
        if target_pid == self.player_id:
            return  # 不给自己加爱愿
        self.love_wish[target_pid] = rounds
        target = self.state.get_player(target_pid)
        me = self._get_caster()
        target_name = target.name if target else target_pid
        my_name = me.name if me else self.player_id
        display.show_info(
            f"💝 {target_name} 获得了「爱愿」！（12轮内无法对 {my_name} 造成伤害或施加debuff）"
        )

    def has_love_wish(self, player_pid):
        """检查某玩家是否对本G5持有者持有爱愿"""
        return self.love_wish.get(player_pid, 0) > 0

    def break_love_wish(self, target_pid):
        """G5持有者攻击了某玩家，破除该玩家的爱愿"""
        if target_pid in self.love_wish:
            del self.love_wish[target_pid]
            target = self.state.get_player(target_pid)
            me = self._get_caster()
            target_name = target.name if target else target_pid
            my_name = me.name if me else self.player_id
            display.show_info(
                f"💔 {my_name} 攻击了 {target_name}，「爱愿」破碎！"
            )

    def _get_caster(self):
        """获取发动者 Player 对象"""
        return self.state.get_player(self.player_id)

    # ================================================================
    #  追忆积累（R0）
    # ================================================================

    def on_round_start(self, round_num):
        if self.anchor_active:
            # 锚定期间保存轮初快照（用于破坏性行动判定）
            self._anchor_save_round_snapshots()
            return

        if round_num <= 1:
            return

        # V1.92: 追忆积攒速度调整（未行动 +0.5，行动了 +1）
        if not self.acted_last_round or self.only_extra_turn:
            gain = 0.5        # 未行动/仅额外行动 +0.5
        else:
            gain = 1.0      # 行动了 +1

        old = self.reminiscence
        self.reminiscence = min(self.max_reminiscence, self.reminiscence + gain)

        me = self._get_caster()
        name = me.name if me else self.player_id
        display.show_info(
            prompt_manager.get_prompt(
                "talent", "g5ripple.reminiscence_gain",
                default="🌊 {name} 获得 {gain} 层追忆（{old}→{reminiscence}/{max_reminiscence}）"
            ).format(
                name=name, gain=gain, old=old,
                reminiscence=self.reminiscence,
                max_reminiscence=self.max_reminiscence
            )
        )

        if self.reminiscence >= self.activation_threshold:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.reminiscence_full",
                    default="🌊✨ {name} 的追忆已满！可以发动「往世的涟漪」！"
                ).format(name=name)
            )

        self.acted_last_round = False
        self.only_extra_turn = False

    def on_round_end(self, round_num):
        """轮末处理：委托给 AnchorMixin 的锚定监控"""
        self._anchor_on_round_end(round_num)
        # V1.92: 递减锚定D4加成轮次
        if self._anchor_d4_bonus_rounds > 0:
            self._anchor_d4_bonus_rounds -= 1
            # 同步递减目标玩家上的D4加成轮次
            if self._anchor_d4_target_id:
                target = self.state.get_player(self._anchor_d4_target_id)
                if target and getattr(target, '_anchor_d4_bonus_rounds', 0) > 0:
                    target._anchor_d4_bonus_rounds -= 1
                    if target._anchor_d4_bonus_rounds <= 0:
                        target._anchor_d4_bonus_amount = 0
            if self._anchor_d4_bonus_rounds <= 0:
                self._anchor_d4_target_id = None
        # 爱愿倒计时
        expired = []
        for pid, remaining in self.love_wish.items():
            self.love_wish[pid] = remaining - 1
            if self.love_wish[pid] <= 0:
                expired.append(pid)
        for pid in expired:
            del self.love_wish[pid]
            p = self.state.get_player(pid)
            if p:
                display.show_info(f"💝 {p.name} 的「爱愿」已到期消散。")

    def on_turn_end(self, player, action_type):
        if player.player_id != self.player_id:
            return
        if action_type and action_type not in ("shock_recover", "petrify_skip"):
            if hasattr(player, 'hexagram_extra_turn') and player.hexagram_extra_turn:
                self.only_extra_turn = True
            else:
                self.acted_last_round = True
                self.only_extra_turn = False

    # ================================================================
    #  T0 选项
    # ================================================================

    def get_t0_option(self, player):
        if player.player_id != self.player_id:
            return None
        if self.anchor_active:
            return None
        if self.reminiscence < self.activation_threshold:
            return None

        return {
            "name": "往世的涟漪",
            "description": (
                f"追忆已满（{self.reminiscence}/{self.activation_threshold}）\n"
                f"  已发动 {self.total_uses} 次\n"
                f"  方式一：锚定命运（不消耗行动回合）\n"
                f"  方式二：献诗增强（消耗行动回合）"),
        }

    def execute_t0(self, player):
        # ══ CONTROLLER 改动 1：选方式 ══
        choice = player.controller.choose(
            "选择涟漪的发动方式：",
            ["方式一：锚定命运（不消耗行动回合）",
             "方式二：献诗增强（消耗行动回合）",
             "取消"],
            context={"phase": "T0", "situation": "ripple_choose_method"}
        )
        # ══ CONTROLLER 改动 1 结束 ══

        if "锚定" in choice:
            msg = self._execute_anchor(player)
            if msg is None:
                return prompt_manager.get_prompt(
                    "talent", "g5ripple.anchor_not_established",
                    default="锚定未成立，追忆已返还。"
                ), False
            return msg, False

        elif "献诗" in choice:
            msg, consumed = self._execute_poem(player)
            return msg, consumed

        else:
            return prompt_manager.get_prompt(
                "talent", "g5ripple.cancel_activation",
                default="取消发动。"
            ), False

    # ================================================================
    #  描述（V1.92+ 更新）
    # ================================================================

    def describe_status(self):
        parts = []
        if self.anchor_active:
            parts.append(f"🌊锚定中：{self.anchor_detail}")
            parts.append(f"剩余{self.anchor_rounds_left}轮")
            parts.append(
                f"破坏{self.anchor_destructive_count}/{self.anchor_variance}")
        else:
            parts.append(f"追忆：{self.reminiscence}/{self.activation_threshold}（上限{self.max_reminiscence}）")
            parts.append(f"已发动{self.total_uses}次")
            if self.reminiscence >= self.activation_threshold:
                parts.append("✨可发动")
        if self.love_wish:
            parts.append(f"💝爱愿：{len(self.love_wish)}人")
        return " | ".join(parts)

    def describe(self):
        return (
            f"【{self.name}】"
            f"\n  追忆：{self.reminiscence}/{self.activation_threshold}（积累上限{self.max_reminiscence}）"
            f"（R0每轮+1或+0.5）"
            f"\n  首次发动需24层，之后{self.activation_threshold}层"
            f"\n  释放消耗12层追忆（爱与记忆递增：当前{self.get_destiny_cost()}层）"
            f"\n  无次数限制（已发动{self.total_uses}次，爱与记忆{self.destiny_use_count}次）")

    # ================================================================
    #  V1.92: 锚定D4加成（「一页永恒的善见天」）
    # ================================================================

    def on_d4_bonus(self, player):
        """锚定成功后，双方在后续5个判定轮次中D4点数+2（不超过4）"""
        if self._anchor_d4_bonus_rounds <= 0:
            return 0
        # 检查是否是发动者或被锚定者
        if player.player_id == self.player_id:
            return 2
        if player.player_id == self._anchor_d4_target_id:
            return 2
        return 0

    def _apply_anchor_d4_bonus(self):
        """锚定成功后应用D4加成"""
        if not self.anchor_target_id:
            return
        # 持久化目标ID（anchor_target_id 会被 _anchor_cleanup 清除）
        self._anchor_d4_target_id = self.anchor_target_id
        # 设置5轮加成
        self._anchor_d4_bonus_rounds = 5

        me = self._get_caster()
        target = self.state.get_player(self._anchor_d4_target_id)
        if me and target:
            # 在目标玩家上设置D4加成属性，使 get_d4_bonus 能直接读取
            target._anchor_d4_bonus_rounds = 5
            target._anchor_d4_bonus_amount = 2
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.anchor_d4_bonus_applied",
                    default="  🎲 「一页永恒的善见天」生效！{caster_name} 和 {target_name} 的D4点数+2（不超过4），持续5轮！"
                ).format(caster_name=me.name, target_name=target.name)
            )