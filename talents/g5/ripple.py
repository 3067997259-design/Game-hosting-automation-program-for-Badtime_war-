"""
Ripple —— 神代天赋5「往世的涟漪」主类

追忆系统 + T0入口 + 状态描述。
锚定逻辑在 AnchorMixin，献诗逻辑在 PoemMixin。

V1.92 改动：
  - 追忆积攒速度：行动了 +0.5，未行动 +1（原 +1/+2）
  - 打破"只能用一次"限制：最多发动2次，每种诗篇最多1次
  - 首次发动需24层，之后12层
  - 释放消耗12层追忆
"""

from talents.base_talent import BaseTalent
from talents.g5.anchor_mixin import AnchorMixin
from talents.g5.poem_mixin import PoemMixin
from cli import display
from controllers.human import HumanController
from engine.prompt_manager import prompt_manager


class Ripple(AnchorMixin, PoemMixin, BaseTalent):
    name = "往世的涟漪"
    description = "追忆满后发动：锚定命运或献诗增强。V1.92: 最多两次。"
    tier = "神代"

    POEM_MAP = {
        "一刀缭断": "游侠",
        "你给路打油": "隐者",
        "神话之外": "永恒",
        "天星": "群星",
        "朝阳好市民": "律法",
        "不良少年": "诡计",
        "六爻": "阴阳",
        "死者苏生": "彼岸",
        "火萤IV型-完全燃烧": "飞萤",
        "请一直，注视着我": "追光",
        "愿负世，照拂黎明": "负世",
        "往世的涟漪": "爱与记忆",
    }

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)

        # === 追忆 ===
        self.reminiscence = 0
        self.max_reminiscence = 24       # V1.92: 首次24，之后降为12
        self.acted_last_round = False
        self.only_extra_turn = False

        # === V1.92: 使用次数追踪（替代原 self.used 布尔值）===
        self.used = False                # 向后兼容：全部用完时为 True
        self.total_uses = 0              # 已发动次数（上限2）
        self.max_uses = 2                # 最多发动2次
        self.used_poems = set()          # 已使用的诗篇类型（每种最多1次）

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
        self._target_round_start_location = None
        self._target_round_start_armor = None
        self._caster_round_start_stunned = False
        self._caster_round_start_shocked = False
        self._caster_round_start_petrified = False

    # ================================================================
    #  V1.92: 消耗使用次数（替代原来的 self.used = True）
    # ================================================================

    def _consume_use(self):
        """消耗一次发动机会 + 12层追忆"""
        self.total_uses += 1
        self.reminiscence = max(0, self.reminiscence - 12)
        if self.total_uses >= self.max_uses:
            self.used = True              # 向后兼容
        # V1.92: 首次发动后，下次只需12层
        self.max_reminiscence = 12

    def _can_activate(self):
        """检查是否还能发动"""
        return self.total_uses < self.max_uses

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

    def _get_caster(self):
        """获取发动者 Player 对象"""
        return self.state.get_player(self.player_id)

    # ================================================================
    #  追忆积累（R0）
    # ================================================================

    def on_round_start(self, round_num):
        # V1.92: 用 _can_activate() 替代 self.used 检查
        if not self._can_activate():
            return
        if self.anchor_active:
            # 锚定期间保存轮初快照（用于破坏性行动判定）
            self._anchor_save_round_snapshots()
            return
        if round_num <= 1:
            return

        # V1.92: 追忆积攒速度削弱（原 2/1 → 1/0.5）
        if not self.acted_last_round or self.only_extra_turn:
            gain = 1        # 原来是 2
        else:
            gain = 0.5      # 原来是 1

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

        if self.reminiscence >= self.max_reminiscence:
            display.show_info(
                prompt_manager.get_prompt(
                    "talent", "g5ripple.reminiscence_full",
                    default="🌊✨ {name} 的追忆已满！可以发动「往世的涟漪」！"
                ).format(name=name)
            )

        self.acted_last_round = False
        self.only_extra_turn = False

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
        # V1.92: 用 _can_activate() 替代 self.used
        if not self._can_activate():
            return None
        if self.anchor_active:
            return None
        if self.reminiscence < self.max_reminiscence:
            return None

        return {
            "name": "往世的涟漪",
            "description": (
                f"追忆已满（{self.reminiscence}/{self.max_reminiscence}）\n"
                f"  已使用 {self.total_uses}/{self.max_uses} 次\n"
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
            msg = self._execute_poem(player)
            return msg, True

        else:
            return prompt_manager.get_prompt(
                "talent", "g5ripple.cancel_activation",
                default="取消发动。"
            ), False

    # ================================================================
    #  描述（V1.92 更新）
    # ================================================================

    def describe_status(self):
        parts = []
        if self.anchor_active:
            parts.append(f"🌊锚定中：{self.anchor_detail}")
            parts.append(f"剩余{self.anchor_rounds_left}轮")
            parts.append(
                f"破坏{self.anchor_destructive_count}/{self.anchor_variance}")
        elif not self._can_activate():
            parts.append("已用完（{}/{}）".format(self.total_uses, self.max_uses))
        else:
            parts.append(f"追忆：{self.reminiscence}/{self.max_reminiscence}")
            parts.append(f"已用{self.total_uses}/{self.max_uses}次")
            if self.reminiscence >= self.max_reminiscence:
                parts.append("✨可发动")
        return " | ".join(parts)

    def describe(self):
        return (
            f"【{self.name}】"
            f"\n  追忆：{self.reminiscence}/{self.max_reminiscence}"
            f"（R0每轮+0.5或+1）"
            f"\n  首次发动需24层，之后12层"
            f"\n  释放消耗12层追忆"
            f"\n  最多发动{self.max_uses}次（已用{self.total_uses}次）"
            f"\n    方式一：锚定命运（不消耗行动回合）"
            f"\n    方式二：献诗增强（消耗行动回合，每种诗最多1次）")