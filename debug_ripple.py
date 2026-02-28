"""
涟漪快速测试：跳过所有前期，直接进入可锚定状态
"""
from models.player import Player
from models.equipment import make_weapon
from engine.game_state import GameState
from engine.response_window import ResponseWindowManager
from engine.round_manager import RoundManager
from talents.g5_ripple import Ripple
from talents.g1_blood_fire import BloodFire  # 给对手随便一个天赋


def quick_test():
    gs = GameState()

    # ---- 创建2个玩家 ----
    p1 = Player("p1", "涟漪哥")
    p2 = Player("p2", "沙包")
    gs.add_player(p1)
    gs.add_player(p2)

    # ---- 分配天赋 ----
    ripple = Ripple("p1", gs)
    p1.talent = ripple
    p1.talent_name = "往昔的涟漪"
    ripple.on_register()

    blood = BloodFire("p2", gs)
    p2.talent = blood
    p2.talent_name = "血火"
    blood.on_register()

    # ---- 作弊：全员起床 ----
    for pid, player in gs.players.items():
        player.is_awake = True
        player.hp = player.max_hp
        gs.markers.remove(pid, "SLEEPING")

    # ---- 作弊：追忆拉满 ----
    ripple.reminiscence = getattr(ripple, 'max_memory', 24)
    print(f"✅ {p1.name} 追忆: {ripple.reminiscence}")

    # ---- 作弊：给把武器方便测试攻击 ----
    # 删掉或注释掉这段
    # try:
    #     p1.weapon = make_weapon("小刀")
    #     p2.weapon = make_weapon("小刀")
    # except Exception:
    #     print("⚠️ make_weapon 失败，用默认拳击")

    # ---- 作弊：放到同一个地点方便测试 ----
    p1.location = "商店"
    p2.location = "商店"

    # ---- 跳过前几轮 ----
    gs.current_round = 5  # 假装已经第5轮了

    print()
    print("=" * 60)
    print("  🔧 DEBUG MODE - 涟漪快速测试")
    print(f"  玩家: {p1.name}(涟漪) vs {p2.name}")
    print(f"  追忆: {ripple.reminiscence}")
    print(f"  位置: 都在商店")
    print(f"  轮次: {gs.current_round}")
    print("=" * 60)
    print()

    # ---- 开跑 ----
    rm = RoundManager(gs)
    try:
        rm.run_game_loop()
    except KeyboardInterrupt:
        print("\n\n  测试中断。")


if __name__ == "__main__":
    quick_test()
