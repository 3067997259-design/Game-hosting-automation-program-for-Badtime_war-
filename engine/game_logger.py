"""
engine/game_logger.py
─────────────────────
游戏日志记录器 — watchall 风格的选择性日志。

参考：
  - rl/watch_all.py 的 controller 包装模式（捕获玩家状态快照+指令）
  - rl/env.py 的 display 函数替换模式（选择性 hook）

不记录的内容：行动菜单、help、status 查询、清屏、横幅、输入提示
"""

import sys
import os
import io
from datetime import datetime
from typing import Optional, Any, List, Dict

from cli import display as _display_module
from engine.prompt_manager import prompt_manager


class GameLogger:
    """
    选择性游戏日志记录器。

    三层捕获：
    1. Controller 包装 — 捕获每个玩家的状态快照 + 指令（watchall 风格）
    2. 选择性 display hook — 只 hook 有意义的 display 函数
    3. prompt_manager._output hook — 捕获天赋/战斗通过 show() 输出的内容
    """

    # 要 hook 的 display 函数（有意义的游戏内容）
    LOGGED_DISPLAY_FUNCS = [
        "show_round_header",
        "show_phase",
        "show_d4_results",
        "show_action_turn_header",
        "show_result",
        "show_info",
        "show_death",
        "show_victory",
        "show_police_enforcement",
        "show_virus_deaths",
        "show_virus_status",
        "show_error",
    ]

    # 不 hook 的 display 函数（UI 噪音）：
    # show_available_actions, show_help, show_player_status,
    # show_all_players_status, show_police_status, clear_screen,
    # show_banner, prompt_input, prompt_choice, prompt_secret,
    # show_critical, show_warning, show_prompt

    def __init__(self, num_human: int, num_ai: int, talent_enabled: bool):
        self.log_dir = "logs"
        os.makedirs(self.log_dir, exist_ok=True)

        self.start_time = datetime.now()
        timestamp = self.start_time.strftime("%Y%m%d_%H%M%S")
        talent_str = "talent_on" if talent_enabled else "talent_off"
        filename = f"{timestamp}_{num_human}H_{num_ai}AI_{talent_str}.log"
        self.log_path = os.path.join(self.log_dir, filename)

        self._log_file = open(self.log_path, "w", encoding="utf-8")
        self._active = True

        # 保存原始函数引用（用于 close 时恢复）
        self._original_display_funcs: Dict[str, Any] = {}
        self._original_pm_output = None
        self._original_get_commands: Dict[str, tuple] = {}  # {player_id: (ctrl, original_func)}

    # ══════════════════════════════════════════════════════
    #  日志头部
    # ══════════════════════════════════════════════════════

    def write_header(self, game_state, ai_players_info=None):
        """写入日志头部：游戏元信息摘要"""
        from controllers.human import HumanController

        ai_pids = {info[0] for info in (ai_players_info or [])}
        ai_personality_map = {info[0]: info[2] for info in (ai_players_info or [])}

        personality_cn_map = {
            "balanced": "均衡型",
            "aggressive": "进攻型",
            "defensive": "防守型",
            "political": "政治型",
            "assassin": "暗杀型",
            "builder": "发育型",
        }

        lines = [
            "=" * 60,
            "  起闯战争 游戏日志",
            f"  {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
            "",
            "── 玩家列表 ──",
        ]

        for pid in game_state.player_order:
            p = game_state.get_player(pid)
            if not p:
                continue
            is_human = isinstance(p.controller, HumanController)
            icon = "👤" if is_human else "🤖"

            # 天赋名称
            talent_str = "无"
            if p.talent and hasattr(p.talent, "name"):
                talent_str = p.talent.name
            elif hasattr(p, "talent_name") and p.talent_name:
                talent_str = p.talent_name

            # AI 人格
            personality_str = ""
            if pid in ai_pids:
                personality = ai_personality_map.get(pid, "?")
                personality_str = f" ({personality_cn_map.get(personality, personality)})"

            lines.append(f"  {icon} {p.name}{personality_str} — 天赋: {talent_str}")

        lines.extend(["", "=" * 60, ""])

        self._write("\n".join(lines) + "\n")

    # ══════════════════════════════════════════════════════
    #  激活（hook display + prompt_manager + controllers）
    # ══════════════════════════════════════════════════════

    def activate(self, game_state):
        """
        激活日志记录。在 write_header 之后、游戏循环开始之前调用。
        """
        self._hook_display_funcs()
        self._hook_prompt_manager_output()
        self._wrap_all_controllers(game_state)

    # ── Display 函数 Hook ──

    def _hook_display_funcs(self):
        """
        Hook 指定的 display 函数。
        对每个函数：调用原函数（终端输出不变）+ 捕获其 print 输出写入日志。
        """
        for func_name in self.LOGGED_DISPLAY_FUNCS:
            if hasattr(_display_module, func_name):
                original = getattr(_display_module, func_name)
                self._original_display_funcs[func_name] = original
                setattr(
                    _display_module,
                    func_name,
                    self._make_display_wrapper(original),
                )

    def _make_display_wrapper(self, original_func):
        """
        创建 display 函数的包装器。
        临时重定向 stdout 到 StringIO 来捕获 print 输出，
        然后同时写入终端和日志文件。
        """
        log_file = self._log_file

        def wrapper(*args, **kwargs):
            # 临时重定向 stdout 来捕获 print 输出
            buffer = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buffer
            try:
                original_func(*args, **kwargs)
            finally:
                sys.stdout = old_stdout

            output = buffer.getvalue()
            # 写入终端（保持原有行为）
            if output:
                old_stdout.write(output)
            # 写入日志文件
            if output:
                log_file.write(output)
                log_file.flush()

        return wrapper

    # ── prompt_manager._output Hook ──

    def _hook_prompt_manager_output(self):
        """
        Hook prompt_manager._output()。
        天赋代码（如 g1_firefly.py）通过 prompt_manager.show() → _output() 输出，
        不经过 display.* 函数，所以需要单独 hook。
        """
        self._original_pm_output = prompt_manager._output
        log_file = self._log_file
        original = self._original_pm_output

        def logged_output(text, level):
            original(text, level)
            log_file.write(text + "\n")
            log_file.flush()

        prompt_manager._output = logged_output

    # ── Controller 包装（watchall 风格）──

    def _wrap_all_controllers(self, game_state):
        """包装所有玩家的 controller，捕获指令和状态快照"""
        for pid in game_state.player_order:
            p = game_state.get_player(pid)
            if p and p.controller:
                self._wrap_controller(p)

    def _wrap_controller(self, player):
        """
        包装单个 controller 的 get_command 方法。
        参考 rl/watch_all.py 的 _wrap_ai_controller。
        在每次行动前记录玩家状态快照和发出的指令。
        """
        from controllers.human import HumanController

        ctrl = player.controller
        original_get_command = ctrl.get_command
        self._original_get_commands[player.player_id] = (ctrl, original_get_command)
        log_file = self._log_file
        is_human = isinstance(ctrl, HumanController)

        def logged_get_command(player, game_state, available_actions, context=None):
            cmd = original_get_command(
                player=player,
                game_state=game_state,
                available_actions=available_actions,
                context=context,
            )

            # 只记录第一次尝试（重试不重复记录）
            attempt = context.get("attempt", 1) if context else 1
            if attempt == 1:
                icon = "👤" if is_human else "🤖"
                personality = getattr(ctrl, "personality", "human") if not is_human else "human"

                # 武器列表
                weapons = [w.name for w in getattr(player, "weapons", [])] if getattr(player, "weapons", None) else []

                # 护甲列表
                armor = []
                if hasattr(player, "armor") and hasattr(player.armor, "get_all_active"):
                    armor = [a.name for a in player.armor.get_all_active()]

                # 病毒免疫
                virus_immune = any(
                    getattr(a, "name", "") == "防毒面具" for a in (
                        player.armor.get_all_active()
                        if hasattr(player, "armor") and hasattr(player.armor, "get_all_active")
                        else []
                    )
                ) or getattr(player, "has_seal", False)
                immunity_str = " 🛡️免疫" if virus_immune else ""

                # 写入日志
                snapshot = (
                    f"    {icon} [{player.name}]"
                    f" ({personality})"
                    f" HP={player.hp:.1f}/{player.max_hp:.1f}"
                    f" 位置={player.location or '未知'}"
                    f" 凭证={getattr(player, 'vouchers', 0)}"
                    f" 武器={weapons}"
                    f" 护甲={armor}"
                    f" 击杀={getattr(player, 'kills', 0)}"
                    f"{immunity_str}\n"
                    f"      >>> {cmd}\n"
                )
                log_file.write(snapshot)
                log_file.flush()

            return cmd

        ctrl.get_command = logged_get_command

    # ══════════════════════════════════════════════════════
    #  关闭
    # ══════════════════════════════════════════════════════

    def close(self):
        """关闭日志，恢复所有 hook"""
        if not self._active:
            return
        self._active = False

        # 写入尾部
        end_time = datetime.now()
        duration = end_time - self.start_time
        footer = (
            f"\n{'=' * 60}\n"
            f"  游戏结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"  游戏时长: {duration}\n"
            f"  日志文件: {self.log_path}\n"
            f"{'=' * 60}\n"
        )
        self._write(footer)

        # 恢复 display 函数
        for func_name, original in self._original_display_funcs.items():
            setattr(_display_module, func_name, original)
        self._original_display_funcs.clear()

        # 恢复 prompt_manager._output
        if self._original_pm_output is not None:
            prompt_manager._output = self._original_pm_output
            self._original_pm_output = None

        # 恢复 controller.get_command
        for pid, (ctrl, original_func) in self._original_get_commands.items():
            ctrl.get_command = original_func
        self._original_get_commands.clear()

        # 关闭文件
        self._log_file.close()

        print(f"\n  📝 游戏日志已保存: {self.log_path}")

    # ── 内部工具 ──

    def _write(self, text: str):
        """写入日志文件（不写入终端）"""
        if self._active and self._log_file and not self._log_file.closed:
            self._log_file.write(text)
            self._log_file.flush()