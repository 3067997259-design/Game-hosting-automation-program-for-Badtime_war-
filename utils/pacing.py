"""AI 演示节奏控制"""

import time


def action_pause(game_state, label=""):
    """
    每次行动后调用。
    根据 game_state 的设置决定：延迟 / 等回车 / 直接跳过。
    """
    if game_state.pause_mode:
        # 按回车继续模式（最方便观看）
        hint = f"  ⏸️ [{label}] " if label else "  ⏸️ "
        input(f"{hint}按回车继续...")
    elif game_state.ai_delay > 0:
        # 自动延迟模式
        time.sleep(game_state.ai_delay)
