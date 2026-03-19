"""  
rl/watch_all.py  
───────────────  
观战脚本（完整版）：同时显示 RL 智能体和所有 BasicAI 对手的行动。  
  
用法：  
    python -m rl.watch_all --model <模型路径> --opponents 3 --max-rounds 50  
"""  
  
import sys  
import threading  
import numpy as np  
from sb3_contrib import MaskablePPO  
  
from rl.env import BadtimeWarEnv  
from rl.watch import action_name  # 复用 RL 动作名翻译  
from rl.action_space import get_opponent_slots  
  
  
# ─────────────────────────────────────────────────────────────  
#  线程安全的行动日志  
# ─────────────────────────────────────────────────────────────  
  
class _ActionLog:  
    """线程安全的行动日志，用于捕获后台线程中 AI 的行动。"""  
  
    def __init__(self):  
        self._lock = threading.Lock()  
        self._entries: list[dict] = []  
  
    def append(self, entry: dict):  
        with self._lock:  
            self._entries.append(entry)  
  
    def drain(self) -> list[dict]:  
        """取出并清空所有日志条目。"""  
        with self._lock:  
            entries = self._entries[:]  
            self._entries.clear()  
            return entries  
  
  
# ─────────────────────────────────────────────────────────────  
#  包装 AI 控制器  
# ─────────────────────────────────────────────────────────────  
  
def _wrap_ai_controller(player, action_log):  
    ctrl = player.controller                      # ← 加这一行  
    original_get_command = ctrl.get_command        # 现在 ctrl 才是 controller  
  
    def logged_get_command(player, game_state, available_actions, context=None):  
        cmd = original_get_command(  
            player=player,  
            game_state=game_state,  
            available_actions=available_actions,  
            context=context,  
        )  
        attempt = context.get("attempt", 1) if context else 1  
        if attempt == 1:  
            action_log.append({  
                "round": game_state.current_round,  
                "player": player.name,  
                "player_id": player.player_id,  
                "cmd": cmd,  
                "hp": player.hp,  
                "max_hp": player.max_hp,  
                "location": player.location,  
                "vouchers": getattr(player, "vouchers", 0),  
                "weapons": [w.name for w in getattr(player, "weapons", [])],  
                "armor": [a.name for a in player.armor.get_all_active()] if hasattr(player.armor, "get_all_active") else [],  
                "personality": getattr(ctrl, "personality", "?"),  
                "virus_immunity": any(  
                    getattr(a, "name", "") == "防毒面具"  
                    for a in (player.armor.get_all_active() if hasattr(player.armor, "get_all_active") else [])  
                ) or getattr(player, "has_seal", False),  
                "kills": getattr(player, "kills", 0),  
            })
        return cmd  
  
    ctrl.get_command = logged_get_command          # 替换 controller 上的方法
  
  
# ─────────────────────────────────────────────────────────────  
#  打印 AI 行动日志  
# ─────────────────────────────────────────────────────────────  
  
def _print_ai_actions(actions):  
    if not actions:  
        return  
    print(f"\n  📋 期间 AI 行动 ({len(actions)} 条):")  
    current_round = None  
    for e in actions:  
        r = e.get("round", "?")  
        if r != current_round:  
            if current_round is not None:  
                print("    └─")  
            print(f"    ┌─ 轮次 {r} ─")  
            current_round = r  
  
        name = e.get("player", "?")  
        personality = e.get("personality", "?")  
        hp = e.get("hp", 0)  
        max_hp = e.get("max_hp", 0)  
        loc = e.get("location", "?")  
        cmd = e.get("cmd", "?")  
        vouchers = e.get("vouchers", 0)  
        weapons = e.get("weapons", [])  
        armor = e.get("armor", [])  
        immunity = "🛡️免疫" if e.get("virus_immunity", False) else ""  
  
        print(  
            f"    │ [{name}] ({personality}) "  
            f"HP={hp:.1f}/{max_hp:.1f} "  
            f"位置={loc} "  
            f"凭证={vouchers} "  
            f"武器={weapons} "  
            f"护甲={armor} "  
            f"{immunity}"  
        )  
        print(f"    │   >>> {cmd}")  
    print("    └─")
  
  
# ─────────────────────────────────────────────────────────────  
#  主观战函数  
# ─────────────────────────────────────────────────────────────  
  
def watch_all(model_path: str, num_opponents: int = 1, max_rounds: int = 50):  
    print(f"加载模型: {model_path}")  
    model = MaskablePPO.load(model_path)  
  
    env = BadtimeWarEnv(  
        num_opponents=num_opponents,  
        max_rounds=max_rounds,  
        render_mode=None,  
    )  
  
    obs, info = env.reset()  
    state = env._state  
    player = env._rl_player  
    assert state is not None  
    assert player is not None  
  
    # 包装所有 AI 控制器  
    action_log = _ActionLog()  
    for pid in state.player_order:  
        p = state.get_player(pid)  
        if p and p.player_id != "rl_0":  
            _wrap_ai_controller(p, action_log)  
  
    print(f"\n{'=' * 60}")  
    print(f"  对局开始: RL_Agent vs {num_opponents} AI")  
    print(f"  最大轮数: {max_rounds}")  
    print(f"  AI 人格: ", end="")  
    for pid in state.player_order:  
        p = state.get_player(pid)  
        if p and p.player_id != "rl_0":  
            personality = getattr(p.controller, 'personality', '?')  
            print(f"{p.name}({personality}) ", end="")  
    print(f"\n{'=' * 60}\n")  
  
    done = False  
    step_num = 0  
    action_history = []  
    last_round = state.current_round  
  
    # 清空 reset 期间产生的 AI 日志（起床等）  
    initial_ai_actions = action_log.drain()  
    if initial_ai_actions:  
        print("  📋 开局阶段 AI 行动:")  
        _print_ai_actions(initial_ai_actions)  
        print()  
  
    while not done:  
        mask = info["action_masks"]  
        valid_actions = mask.nonzero()[0]  
  
        # 模型预测  
        action, _ = model.predict(obs, action_masks=mask, deterministic=True)  
        action = int(action)  
  
        desc = action_name(action, player, state)  
  
        # 打印 RL 状态  
        print(f"── 第 {step_num + 1} 步 (轮次 {state.current_round}) ──")  
        print(f"  🤖 RL_Agent:")  
        print(f"    HP: {player.hp:.1f}/{player.max_hp:.1f}")  
        print(f"    位置: {player.location or '未知'}")  
        print(f"    凭证: {player.vouchers}")  
        weapons = [w.name for w in player.weapons] if player.weapons else []  
        print(f"    武器: {weapons}")  
        armor_names = [a.name for a in player.armor.get_all_active()] if player.armor else []  
        print(f"    护甲: {armor_names}")  
        print(f"    合法动作数: {len(valid_actions)}")  
        print(f"    >>> 选择: {desc}")  
  
        # 打印对手摘要  
        opponents = get_opponent_slots(player, state)  
        for i, opp in enumerate(opponents):  
            if opp is not None and opp.is_alive():  
                personality = getattr(opp.controller, 'personality', '?')  
                print(f"    [对手 {opp.name}({personality})] HP={opp.hp:.1f} 位置={opp.location or '?'} 击杀={opp.kill_count}")  
  
        action_history.append(desc)  
  
        # 执行动作  
        obs, reward, terminated, truncated, info = env.step(action)  
        done = terminated or truncated  
        step_num += 1  
  
        print(f"    奖励: {reward:.2f}")  
  
        # 打印两步之间的 AI 行动  
        ai_actions = action_log.drain()  
        if ai_actions:  
            _print_ai_actions(ai_actions)  
  
        # 病毒状态  
        if state.virus.is_active:  
            print(f"  🦠 病毒状态: 激活 (倒计时={state.virus.countdown})")  
  
        print()  
  
    # 对局结束  
    print(f"{'=' * 60}")  
    print(f"  对局结束!")  
    print(f"  总步数: {step_num}")  
    print(f"  胜者: {state.winner}")  
    print(f"  RL HP: {player.hp:.1f}")  
    print(f"  terminated={terminated}, truncated={truncated}")  
  
    # 存活玩家状态  
    print(f"\n  最终玩家状态:")  
    for pid in state.player_order:  
        p = state.get_player(pid)  
        if p:  
            status = "存活" if p.is_alive() else "死亡"  
            personality = getattr(p.controller, 'personality', '?') if pid != "rl_0" else "RL"  
            print(f"    {p.name}({personality}): HP={p.hp:.1f} 位置={p.location or '?'} 击杀={p.kill_count} [{status}]")  
  
    print(f"{'=' * 60}")  
  
    # 行动统计  
    print(f"\n── RL 行动统计 ──")  
    from collections import Counter  
    action_types = Counter()  
    for d in action_history:  
        atype = d.split(":")[0].split("→")[0].split("(")[0].strip()  
        action_types[atype] += 1  
    for atype, count in action_types.most_common():  
        pct = count / len(action_history) * 100  
        print(f"  {atype}: {count} 次 ({pct:.1f}%)")  
  
    env.close()  
  
  
if __name__ == "__main__":  
    import argparse  
    p = argparse.ArgumentParser(description="观战 RL 对局（完整版，含 AI 行动）")  
    p.add_argument("--model", type=str, required=True, help="模型路径 (.zip)")  
    p.add_argument("--opponents", type=int, default=1)  
    p.add_argument("--max-rounds", type=int, default=50)  
    args = p.parse_args()  
  
    watch_all(args.model, args.opponents, args.max_rounds)