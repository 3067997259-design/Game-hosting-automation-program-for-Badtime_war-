"""  
rl/watch.py  
───────────  
加载训练好的 MaskablePPO 模型，跑一局可视化对局，  
逐步打印 RL 智能体的决策过程。  
"""  
  
import sys  
import numpy as np  
from pathlib import Path  
from sb3_contrib import MaskablePPO  
  
from rl.env import BadtimeWarEnv  
from rl.action_space import (  
    ACTION_COUNT, LOCATIONS, INTERACT_ITEMS, WEAPONS,  
    SPECIAL_OPS, POLICE_CMDS,  
    IDX_FORFEIT, IDX_WAKE, IDX_MOVE_BASE, IDX_INTERACT_BASE,  
    IDX_LOCK_BASE, IDX_FIND_BASE, IDX_ATTACK_BASE,  
    IDX_SPECIAL_BASE, IDX_POLICE_BASE,  
    get_opponent_slots,  
)  
  
  
def action_name(idx: int, player=None, game_state=None) -> str:  
    """将动作索引翻译为人类可读的描述。"""  
    if idx == IDX_FORFEIT:  
        return "放弃行动 (forfeit)"  
    if idx == IDX_WAKE:  
        return "起床 (wake)"  
    if IDX_MOVE_BASE <= idx < IDX_INTERACT_BASE:  
        return f"移动 → {LOCATIONS[idx - IDX_MOVE_BASE]}"  
    if IDX_INTERACT_BASE <= idx < IDX_LOCK_BASE:  
        return f"交互: {INTERACT_ITEMS[idx - IDX_INTERACT_BASE]}"  
    if IDX_LOCK_BASE <= idx < IDX_FIND_BASE:  
        slot = idx - IDX_LOCK_BASE  
        name = _slot_name(slot, player, game_state)  
        return f"锁定: {name} (slot {slot})"  
    if IDX_FIND_BASE <= idx < IDX_ATTACK_BASE:  
        slot = idx - IDX_FIND_BASE  
        name = _slot_name(slot, player, game_state)  
        return f"寻找: {name} (slot {slot})"  
    if IDX_ATTACK_BASE <= idx < IDX_SPECIAL_BASE:  
        offset = idx - IDX_ATTACK_BASE  
        target_slot = offset // 10  
        weapon_slot = offset % 10  
        name = _slot_name(target_slot, player, game_state)  
        return f"攻击: {name} 使用 {WEAPONS[weapon_slot]}"  
    if IDX_SPECIAL_BASE <= idx < IDX_POLICE_BASE:  
        return f"特殊: {SPECIAL_OPS[idx - IDX_SPECIAL_BASE]}"  
    if IDX_POLICE_BASE <= idx < ACTION_COUNT:  
        return f"警察: {POLICE_CMDS[idx - IDX_POLICE_BASE]}"  
    return f"未知动作 ({idx})"  
  
  
def _slot_name(slot: int, player, game_state) -> str:  
    if player is None or game_state is None:  
        return f"slot{slot}"  
    opponents = get_opponent_slots(player, game_state)  
    opp = opponents[slot]  
    return opp.name if opp else f"slot{slot}(空)"  
  
  
def watch(model_path: str, num_opponents: int = 1, max_rounds: int = 50):  
    print(f"加载模型: {model_path}")  
    model = MaskablePPO.load(model_path)  
  
    env = BadtimeWarEnv(  
        num_opponents=num_opponents,  
        max_rounds=max_rounds,  
        render_mode=None,  # 不显示游戏引擎输出，用我们自己的格式  
    )  
  
    obs, info = env.reset()  
    state = env._state  
    player = env._rl_player  
  
    print(f"\n{'='*60}")  
    print(f"  对局开始: RL_Agent vs {num_opponents} AI")  
    print(f"  最大轮数: {max_rounds}")  
    print(f"{'='*60}\n")  
  
    done = False  
    step_num = 0  
    action_history = []  
  
    while not done:  
        mask = info["action_masks"]  
        valid_actions = mask.nonzero()[0]  
  
        # 模型预测  
        action, _ = model.predict(obs, action_masks=mask, deterministic=True)  
        action = int(action)  
  
        # 获取动作概率分布（可选，用于分析）  
        desc = action_name(action, player, state)  
  
        # 打印当前状态  
        print(f"── 第 {step_num + 1} 步 (轮次 {state.current_round}) ──")  
        print(f"  HP: {player.hp:.1f}/{player.max_hp:.1f}")  
        print(f"  位置: {player.location or '未知'}")  
        print(f"  凭证: {player.vouchers}")  
        weapons = [w.name for w in player.weapons] if player.weapons else []  
        print(f"  武器: {weapons}")  
        armor_names = [a.name for a in player.armor.get_all_active()] if player.armor else []  
        print(f"  护甲: {armor_names}")  
        print(f"  合法动作数: {len(valid_actions)}")  
        print(f"  >>> 选择: {desc}")  
  
        # 打印对手状态  
        opponents = get_opponent_slots(player, state)  
        for i, opp in enumerate(opponents):  
            if opp is not None and opp.is_alive():  
                print(f"  [对手 {opp.name}] HP={opp.hp:.1f} 位置={opp.location or '?'} 击杀={opp.kill_count}")  
  
        action_history.append(desc)  
  
        # 执行动作  
        obs, reward, terminated, truncated, info = env.step(action)  
        done = terminated or truncated  
        step_num += 1  
  
        print(f"  奖励: {reward:.2f}")  
        print()  
  
    # 对局结束  
    print(f"{'='*60}")  
    print(f"  对局结束!")  
    print(f"  总步数: {step_num}")  
    print(f"  胜者: {state.winner}")  
    print(f"  RL HP: {player.hp:.1f}")  
    print(f"  terminated={terminated}, truncated={truncated}")  
    print(f"{'='*60}")  
  
    # 行动统计  
    print(f"\n── 行动统计 ──")  
    from collections import Counter  
    action_types = Counter()  
    for desc in action_history:  
        # 提取动作类型（冒号前的部分）  
        atype = desc.split(":")[0].split("→")[0].split("(")[0].strip()  
        action_types[atype] += 1  
    for atype, count in action_types.most_common():  
        pct = count / len(action_history) * 100  
        print(f"  {atype}: {count} 次 ({pct:.1f}%)")  
  
    env.close()  
  
  
if __name__ == "__main__":  
    import argparse  
    p = argparse.ArgumentParser(description="观战 RL 对局")  
    p.add_argument("--model", type=str, required=True, help="模型路径 (.zip)")  
    p.add_argument("--opponents", type=int, default=1)  
    p.add_argument("--max-rounds", type=int, default=50)  
    args = p.parse_args()  
  
    watch(args.model, args.opponents, args.max_rounds)