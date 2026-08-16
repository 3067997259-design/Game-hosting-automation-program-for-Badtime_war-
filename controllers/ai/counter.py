"""BasicAI counter 层骨架：capabilities 威胁类目声明 + 类目反制模板。

分层原则（2026-08-12 设计裁决）：
- **capabilities**：每个天赋（slot_id）声明自己的**威胁类目**（对手侧视角）——
  快照/评估层据此推断"谁对我构成哪类威胁"，反制知识按类目复用，不写 N×M 专属。
- **类目反制模板**：对每个威胁类目给出适用于任意反制者的动作候选（含条件），
  由 orchestrator 反制产出点消费（追加到候选列表）。
- 首例：G3 结界类目 → 被困 AI 发起 `special 破界`（反制者可以是任意天赋/白板）。

`capabilities_of` 从快照的对手层（M9Facts/天赋）解析；`counter_candidates`
返回当前决策点应追加的反制命令（去重、限定在快照合法动作内）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from engine.m9.text import m9_text

# ── 威胁类目（关闭列举第一版）──
BARRIER = "barrier"      # 固有结界/区域（G3 结界、G2 终曲区域）
RITUAL = "ritual"        # 慢速仪式（G4 焚诏拉条、G5 锚定、T3 天星公演）
TEMP_HP = "temp_hp"      # 临时 HP/免死（G4 形态、G7 Terror）
MULTI_BODY = "multi_body"  # 分身/附属（G2 影身、G0 无人机）
BURST_WINDOW = "burst_window"  # 高输出窗口（G1 完全燃烧、G7 战术宏）

# ── 天赋槽位 → 威胁类目声明（第一版；策略本体批次可扩展）──
CAPABILITY_DECL: Dict[str, Tuple[str, ...]] = {
    "G0": (MULTI_BODY,),
    "G1": (BURST_WINDOW,),
    "G2": (MULTI_BODY, BARRIER),
    "G3": (BARRIER,),
    "G4": (RITUAL, TEMP_HP),
    "G5": (RITUAL,),
    "G7": (TEMP_HP, BURST_WINDOW),
    "T3": (RITUAL,),
    "T7": (TEMP_HP,),   # 保险
}

# ── 类目反制模板：{类目: [(条件说明, 命令生成函数或静态命令)]}──
# 命令必须是快照目录内合法形式（orchestrator 追加后经 catalog 校验）。
CATEGORY_COUNTERS: Dict[str, List[Dict[str, Any]]] = {
    BARRIER: [
        {"condition": "in_barrier", "command": "special 破界",
         "note": m9_text("ai.counter.note_barrier_in")},
        {"condition": "not_in_barrier", "command": None,
         "note": m9_text("ai.counter.note_barrier_out")},
    ],
    RITUAL: [
        {"condition": "g4_divinity_high", "command": "attack <G4持有者> <最佳武器>",
         "note": m9_text("ai.counter.note_ritual_g4")},
    ],
}


def capabilities_of(snapshot: Any, pid: str) -> Set[str]:
    """对手天赋的威胁类目（brief.slot_id 优先；无则显示名回退）。"""
    if snapshot is None:
        return set()
    brief = snapshot.opponent_briefs.get(pid)
    if brief is None:
        return set()
    slot = brief.slot_id or _slot_of_name(brief.name)
    return set(CAPABILITY_DECL.get(slot, ())) if slot else set()


def _slot_of_name(name: str) -> Optional[str]:
    mapping = {
        "砂狼白子*Terror": "G0", "火萤IV型-完全燃烧": "G1",
        "神代天赋-请一直注视着我": "G2", "神话之外": "G3",
        "愿负世，照拂黎明": "G4", "神代天赋-往世的涟漪": "G5",
        "大叔我啊，剪短发了": "G7", "天星": "T3", "死者苏生": "T7",
        "请一直，注视着我": "G2", "请一直注视着我": "G2",
        "往世的涟漪": "G5", "神代天赋-神话之外": "G3",
    }
    return mapping.get(name)


# G4 蓄力打断阈值（火种 0-12，燃尽门槛 12）
_G4_DIVINITY_PRESS = 8


def counter_candidates(player: Any, snapshot: Any,
                       available: List[str],
                       state: Any = None) -> List[str]:
    """当前决策点的反制命令候选（追加到 orchestrator 候选列表）。

    仅产出"条件成立 + 命令非 None + 未超出 available 类型"的模板项；
    去重由 orchestrator finalize 承担。
    首例：G3 结界内 → 破界；RITUAL：G4 火种高 → 施压打断。
    """
    out: List[str] = []
    if snapshot is None:
        return out
    m9 = snapshot.m9
    if m9 is not None and m9.barrier_active:
        # 捕捉是展开瞬间的身份快照，不等同于“当前恰好在同一地点”。读取
        # 真实 captured，避免把结界建立后新到达原地点的局外人误判为被困者。
        if snapshot.actor_id in m9.barrier_captured:
            out.append("special 破界")
    # RITUAL：对手 G4 火种≥阈值 → 施压打断燃尽蓄力；
    #         对手 G5 激活锚定中 → 施压锚定者（击杀/迫其改道 → 槽位因果改写）
    # BURST_WINDOW：对手 G1 持超新星 / 完全燃烧窗口 → 压制（风洞 R29 解剖：
    #         对手对 G1 仅 0.12-0.20 攻击/存活轮，散开逃跑却不反打）
    if state is not None and "attack" in available:
        from controllers.ai.decision.value import _best_weapon
        weapon = _best_weapon(player)
        if weapon is not None:
            for pid, brief in snapshot.opponent_briefs.items():
                if not brief.alive:
                    continue
                press = False
                if brief.slot_id == "G4":
                    opp = state.get_player(pid)
                    press = getattr(getattr(opp, "talent", None),
                                    "divinity", 0) >= _G4_DIVINITY_PRESS
                elif brief.slot_id == "G5":
                    opp = state.get_player(pid)
                    press = bool(getattr(getattr(opp, "talent", None),
                                         "active_anchor", False))
                elif brief.slot_id == "G1":
                    opp = state.get_player(pid)
                    talent = getattr(opp, "talent", None)
                    press = bool(getattr(talent, "has_supernova", False)) \
                        or getattr(talent, "form", "") == "full_burn"
                elif brief.slot_id == "G0":
                    # Terror：无人机在场（十字炮火链在线）且不在呼吸免疫期
                    # → 压制（击杀 Terror 连坐无人机；免疫期内攻击无效）
                    opp = state.get_player(pid)
                    talent = getattr(opp, "talent", None)
                    press = (getattr(talent, "drone", None) is not None
                             and not getattr(talent, "breath_active", False))
                if press:
                    cmd = f"attack {brief.name} {weapon.name}"
                    if cmd not in out:
                        out.append(cmd)
    return out
