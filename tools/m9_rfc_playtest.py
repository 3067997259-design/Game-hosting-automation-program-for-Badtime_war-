"""M9-rfc 剧本验收（阶段 9）：确定性剧本驱动真实引擎（GameState + RoundManager，
m9-rfc profile），覆盖六天赋机制在真实回合流内的表现。全部剧本通过 exit 0。

用法: python tools/m9_rfc_playtest.py [--verbose]

剧本：
- A G1 燃烧循环：着装→完全燃烧→致死繁育→倒计时绝对死亡
- B G4 救世主轮回：火种 12→完整形态（SP2）→形态内致死消耗→tick→退场
- C G5 轮回锚定：归家非死亡→转世→德谬歌→K 槽锚定→逐槽监控→未来闭合+窄回溯
- D G7 战术压制（附）：Terror 攻击 = DIRECT_DAMAGE + absolute_dead（T7 不赔付）
- E G2 光影双身（附）：创建影身 → R3 代理标准槽 → 消散归还
- F G6 模板池（附）：R3 记录 → 即演重演（SP 扣减 + 真实重演）
"""
import os
import random
import sys
from typing import Any, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine import experiments
from engine.game_state import GameState
from engine.round_manager import RoundManager
from models.player import Player
from controllers.base import PlayerController

VERBOSE = "--verbose" in sys.argv
FAILURES: List[str] = []


class ScriptController(PlayerController):
    """预置动作序列控制器（确定性）。"""

    def __init__(self, commands: Optional[List[str]] = None):
        self.commands = list(commands or [])
        self.choices = []

    def enqueue_command(self, cmd: str) -> None:
        self.commands.append(cmd)

    def enqueue_choices(self, *choices: str) -> None:
        self.choices.extend(choices)

    def get_command(self, player, game_state, available_actions, context=None):
        if self.commands:
            cmd = self.commands.pop(0)
            if cmd in available_actions or cmd.split()[0] in available_actions:
                return cmd
        return "forfeit"

    def choose(self, prompt, options, context=None):
        if self.choices:
            c = self.choices.pop(0)
            if c in options:
                return c
        return options[0]

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return options[:max_count]

    def confirm(self, prompt, context=None):
        return True


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{name}: {detail}")


def _state(players: List[tuple]) -> tuple:
    """players: [(pid, talent_cls, controller, location)]。"""
    state = GameState()
    from engine.m9.gate import ensure_state_mechanisms
    ensure_state_mechanisms(state)
    for pid, talent_cls, ctrl, loc in players:
        p = Player(pid, pid.upper(), controller=ctrl)
        p.location = loc
        p.max_hp = 20
        p.hp = 20
        p.is_awake = True
        state.add_player(p)
        if talent_cls is not None:
            t = talent_cls(pid, state)
            p.talent = t
            t.on_register()
    return state, RoundManager(state)


def _run_rounds(state, rm, n: int) -> None:
    for _ in range(n):
        if state.check_victory():
            break
        rm.run_one_round()


def _manual_turn(state, rm, player) -> None:
    """手动驱动一轮：R0/R2（供 R0 转世/追忆与 R4 钩子所在相位）+ p1 的 T0/T1
    （绕过先攻随机性）+ R4（逐槽监控/失熵/听众 tick）。"""
    state.current_round += 1
    rm._phase_r0()
    rm._phase_r2()
    from engine.action_turn import ActionTurnManager
    tm = ActionTurnManager(state)
    if player.is_alive() and player.is_awake:
        tm._phase_t0(player)
        if player.is_alive() and player.is_awake:
            tm._phase_t1(player)
    rm._phase_r4()


def script_a_g1_burn() -> None:
    """G1：着装 → 完全燃烧 → 致死繁育 → 倒计时绝对死亡。"""
    from engine.m9.talents.g1 import G1MythFire9
    ctrl = ScriptController()
    ctrl.enqueue_choices(
        "保留",
        "发动天赋",
        "报名公演",
        "发动天赋",
        "完全燃烧（公演 2 SP）",
    )
    state, rm = _state([
        ("p1", G1MythFire9, ctrl, "商店"),
        ("p2", None, ScriptController(), "商店"),
        ("p3", None, ScriptController(), "医院"),
    ])
    t = state.get_player("p1").talent
    state.m9_system.set_sp("p1", 2)

    # R1：着装（T0 即演 1 SP）→ secondary
    _manual_turn(state, rm, state.get_player("p1"))
    check("A.1 着装成次级", t.form == "secondary",
          f"form={t.form}")
    check("A.2 着装扣 1 SP", state.m9_system.get_sp("p1") == 1,
          f"sp={state.m9_system.get_sp('p1')}")

    # R2：完全燃烧（公演 2 SP）→ full_burn 窗口
    state.m9_system.set_sp("p1", 2)
    _manual_turn(state, rm, state.get_player("p1"))
    check("A.3 完全燃烧", t.form == "full_burn", f"form={t.form}")

    # R3：模拟致死 → 繁育替代
    p1 = state.get_player("p1")
    p1.hp = 0
    kind = t.m9_on_lethal(p1, None, "normal")
    check("A.4 繁育替代", kind == "g1_propagation" and t.form == "propagation",
          f"kind={kind} form={t.form}")
    check("A.5 繁育 HP", p1.hp == 1 and p1.is_alive(), f"hp={p1.hp}")

    # R4-R6：倒计时归零 → 绝对死亡（跳过 T7）
    for _ in range(4):
        _manual_turn(state, rm, p1)
    check("A.6 繁育倒计时绝对死", not p1.is_alive(), f"hp={p1.hp}")


def script_b_g4_savior() -> None:
    """G4：火种 12 → 完整形态（SP2）→ 形态内致死消耗 → tick → 退场不落幕。"""
    from engine.m9.talents.g4 import Savior9
    ctrl = ScriptController()
    state, rm = _state([
        ("p1", Savior9, ctrl, "商店"),
        ("p2", None, ScriptController(), "商店"),
    ])
    t = state.get_player("p1").talent
    p1 = state.get_player("p1")

    # 火种喂到 12（跨轮 W2）
    attacker = state.get_player("p2")
    for r in range(1, 8):
        state.current_round = r
        t.on_being_attacked(attacker, None)
        t.on_positive_talent_used(attacker)
    check("B.1 火种封顶 12", t.divinity == 12, f"ember={t.divinity}")

    # 致死 → 完整形态（SP 置 2）
    p1.hp = 0
    result = t.on_death_check(p1, None)
    check("B.2 完整形态进入", result is not None and t.form == "full_savior",
          f"form={t.form}")
    check("B.3 SP 置 2", state.m9_system.get_sp("p1") == 2,
          f"sp={state.m9_system.get_sp('p1')}")

    # 形态内致死 → 余烬生命消耗（非死亡）
    p1.hp = 0
    kind = t.m9_on_lethal(p1, None, "normal")
    check("B.4 形态内消耗", kind == "g4_savior_consume" and p1.is_alive(),
          f"kind={kind} hp={p1.hp}")

    # tick：建立轮不 tick → 之后每 R4 −1 → 退场回人形态（不 spent）
    state.current_round = 5
    t.on_round_end(5)
    check("B.5 建立轮不 tick", t.form_ticks == 6, f"ticks={t.form_ticks}")
    for r in range(6, 12):
        state.current_round = r
        t.on_round_end(r)
    check("B.6 到期退场不落幕", t.form == "human" and not t.spent,
          f"form={t.form} spent={t.spent}")


def script_c_g5_anchor() -> None:
    """G5：归家非死亡 → 转世 → 德谬歌 → K 槽锚定 → 未来闭合 + 窄回溯。"""
    from engine.m9.talents.g5 import Ripple9
    from models.equipment import Weapon, WeaponRange
    from utils.attribute import Attribute
    ctrl = ScriptController()
    state, rm = _state([
        ("p1", Ripple9, ctrl, "商店"),
        ("p2", None, ScriptController(), "商店"),
    ])
    t = state.get_player("p1").talent
    p1 = state.get_player("p1")

    # 小昔涟致死 → 归家（非死亡）
    p1.hp = 0
    kind = t.m9_on_lethal(p1, None, "normal")
    check("C.1 归家非死亡", kind == "g5_homecoming" and t.form == "home",
          f"kind={kind} form={t.form}")

    # R0 转世 → 德谬歌（追忆足够）
    t.sealed_reminiscence = 20.0
    _manual_turn(state, rm, state.get_player("p1"))
    check("C.2 德谬歌诞生", t.form == "demiurge", f"form={t.form}")

    # K=3 锚定：attack 小刀 + move + attack（目标 hp 2）
    p2 = state.get_player("p2")
    p2.hp = 2
    p1.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
    p1.hp = 5
    state.m9_system.set_sp("p1", 2)
    state.m9_system.begin_round(state.current_round)
    state.m9_system.register_performance("p1", state.current_round)
    state.m9_system.allocate_public_slot(state.current_round)
    msg, ok = t.execute_anchor(p1, [("attack", "p2", "小刀"),
                                    ("move", "医院"),
                                    ("attack", "p2", "小刀")])
    check("C.3 锚定建立", ok and t.active_anchor, f"msg={msg}")
    check("C.4 追忆扣 K", t.sealed_reminiscence == 17,  # 20-3
          f"sealed={t.sealed_reminiscence}")

    # 目标被自然击杀 → 逐槽监控 → 未来闭合 + 窄回溯
    p2.hp = 0
    state.current_round = 6
    t.on_round_end(6)
    t.on_round_end(7)
    t.on_round_end(8)
    check("C.5 未来闭合", t.anchor_results == ["未来闭合"],
          f"results={t.anchor_results}")
    check("C.6 窄回溯恢复 HP", p1.hp == 5, f"hp={p1.hp}")
    check("C.7 水晶花 arc", t.flower_arc_granted, "flower not granted")


def script_d_g7_terror() -> None:
    """G7（附）：Terror 攻击 = DIRECT_DAMAGE + absolute_dead（T7 保险不赔付）。"""
    from engine.m9.talents.g7 import Hoshino9
    from talents.t7_resurrection import Resurrection
    ctrl = ScriptController()
    state, rm = _state([
        ("p1", Hoshino9, ctrl, "商店"),
        ("p2", None, ScriptController(), "商店"),
    ])
    t = state.get_player("p1").talent
    t.is_terror = True
    t.terror_extra_hp = 20
    # p2 挂 T7 保险（保险会 prevent_death——absolute_death 必须跳过）
    t7 = Resurrection("p1", state)
    t7.learned = True
    t7.mounted_on = "p2"
    state.get_player("p2").talent = t7
    state.get_player("p2").hp = 3
    msg = t._terror_attack(state.get_player("p1"))
    check("D.1 Terror 绝对死", not state.get_player("p2").is_alive(),
          f"hp={state.get_player('p2').hp}")
    check("D.2 T7 不赔付", not t7.used, "T7 保险被绝对死亡绕过时应不触发")


def script_e_g2_dualbody() -> None:
    """G2（附）：创建影身 → 代理槽行动 → 消散归还。"""
    from engine.m9.talents.g2 import Hologram9
    ctrl = ScriptController()
    ctrl.enqueue_choices("创建影身（即演 1 SP）")
    state, rm = _state([
        ("p1", Hologram9, ctrl, "商店"),
        ("p2", None, ScriptController(), "商店"),
    ])
    t = state.get_player("p1").talent
    state.m9_system.set_sp("p1", 2)
    _manual_turn(state, rm, state.get_player("p1"))
    shadow = t._shadow()
    check("E.1 影身创建", shadow is not None, "shadow is None")
    if shadow is not None:
        check("E.2 影身 HP", shadow.max_hp == 8, f"hp={shadow.max_hp}")
        # 代理槽：手动驱动影身标准槽（受限菜单 → forfeit）+ 槽收尾
        from engine.action_turn import ActionTurnManager
        tm = ActionTurnManager(state)
        m9 = state.m9_system
        slot_id = m9.assign_slot(shadow.actor_id)
        action_type = tm._phase_t1_shadow(shadow)
        m9.resolve_slot(slot_id, root_action=False, kind="forfeit")
        check("E.3 影身槽收尾",
              m9.outcome(slot_id) is not None
              and m9.outcome(slot_id).slot_resolved,
              "no slot resolved")
        # 消散归还
        item = type("Item", (), {"name": "小刀"})()
        shadow.held_items.append(item)
        t.dissipate(shadow)
        check("E.4 消散归还", t._shadow() is None
              and item in state.get_player("p1").items, "return failed")


def script_f_g6_template() -> None:
    """G6（附）：R3 记录模板池 → 即演重演（扣 1 SP + 真实重演 move）。"""
    from engine.m9.talents.g6 import CutawayJoke9
    ctrl = ScriptController()
    ctrl.enqueue_choices("发动天赋", "即演", "医院")
    state, rm = _state([
        ("p1", CutawayJoke9, ctrl, "医院"),
        ("p2", None, ScriptController(), "商店"),
    ])
    t = state.get_player("p1").talent
    state.m9_system.set_sp("p1", 2)
    # 上一轮 p2 完成 move → 模板池有 move 记录（_manual_turn 自增到 R2，窗口 [1,2] 含之）
    state.g6_template_pool.record(1, "move", "商店", "p2")
    _manual_turn(state, rm, state.get_player("p1"))
    pool = state.g6_template_pool
    check("F.1 模板池记录", len(pool.categories(state.current_round)) >= 1,
          f"pool={pool.categories(state.current_round)}")
    # 即演执行由 T0 触发（本剧本 controller 已在 T0 选即演→重演 move）
    check("F.2 即演扣 SP", state.m9_system.get_sp("p1") == 1,
          f"sp={state.m9_system.get_sp('p1')}")


SCRIPTS = [
    ("A-G1燃烧循环", script_a_g1_burn),
    ("B-G4救世主轮回", script_b_g4_savior),
    ("C-G5轮回锚定", script_c_g5_anchor),
    ("D-G7-Terror附局", script_d_g7_terror),
    ("E-G2-双身附局", script_e_g2_dualbody),
    ("F-G6-模板附局", script_f_g6_template),
]


def main() -> int:
    # 验收必须经过真实 profile；剧本内部仍以固定输入隔离先攻随机性。
    experiments.reset()
    experiments.set_profile("m9-rfc")
    for name, fn in SCRIPTS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"{name}: 异常 {exc!r}")
    experiments.reset()
    if FAILURES:
        print("M9-rfc 剧本验收 FAIL:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"M9-rfc 剧本验收 PASS：{len(SCRIPTS)} 个剧本全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
