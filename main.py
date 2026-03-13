"""
《起闯战争》CLI DM ver1.6
程序入口
"""

from engine.game_setup import setup_game
from engine.round_manager import RoundManager


def main():
    # 游戏初始化
    game_state = setup_game()

    # 创建轮次管理器并启动主循环
    round_mgr = RoundManager(game_state)

    try:
        round_mgr.run_game_loop()
    except KeyboardInterrupt:
        print("\n\n  游戏被手动中断。")
        print("  感谢游玩《起闯战争》！")


if __name__ == "__main__":
    main()

