"""M9 槽位机制探针（供 test_m9_final_acceptance 复用；非测试收集模块）。

每个探针在真实对象上驱动真实管线：

- GameState + ``ensure_state_mechanisms``（m9_system / 石化注册表 / 保险 /
  警察局 / 影身 / 模板池全部挂载）；
- 真实 adapter 实例（``engine.m9.talents.<slot>`` 具体类）；
- 真实 ``m9_system`` SP / 公演位 / 派发（``dispatch_improvise`` /
  ``dispatch_public`` / ActionGrant ledger）；
- 真实 ``RoundManager`` R0（报名窗口 + 公演位固化）→ R1（标准槽派发）
  → R3（T0 演出在真实槽上执行）。

断言一律落在世界状态（HP / 石化 / 保险 / 影身 / 结界 / 案件 / 装备移交 /
事件日志 / SP 消费），不落在返回字符串。空 stub 换入后同一编排必须不产生
任何效果（见 test_m9_final_acceptance 的负向控制）。
"""
from __future__ import annotations

from controllers.forfeit_controller import ForfeitController
from engine.game_state import GameState
from engine.m9.gate import ensure_state_mechanisms
from engine.round_manager import RoundManager
from models.equipment import make_weapon
from models.player import Player


def make_player(player_id: str, controller=None) -> Player:
    player = Player(player_id, player_id.upper(),
                    controller=controller or ForfeitController())
    player.is_awake = True
    player.location = "商店"
    player.hp = 20
    player.max_hp = 20
    return player


def probe_world():
    """真实 2 人 M9 局骨架（机制层已挂载；玩家 SP 由 add_player 注册为 1）。"""
    state = GameState()
    ensure_state_mechanisms(state)
    p1 = make_player("p1")
    p2 = make_player("p2")
    state.add_player(p1)
    state.add_player(p2)
    return state, p1, p2


class RegistrationController(ForfeitController):
    """R0 报名窗口选择「报名公演」的控制器。"""

    def choose(self, prompt, options, context=None):
        if context and context.get("phase") == "M9_PUBLIC_REGISTRATION":
            return "报名公演"
        return super().choose(prompt, options, context)


def grant_public_seat(state, pid: str, round_num: int = 1) -> None:
    """在真实 R0 窗口固化唯一公演位（SP≥2 → 报名 → 固化）。"""
    m9 = state.m9_system
    m9.set_sp(pid, 2)
    m9.register_performance(pid, round_num)
    m9.begin_round(round_num)
    m9.allocate_public_slot(round_num)
    assert m9.assign_public_slot(round_num) == pid, f"{pid} 未获得公演位"


def drive_rounds(state, monkeypatch, public_register: bool = False):
    """真实 R0（含报名窗口）→ R1（标准槽派发）→ R3（T0 执行）。

    roll_d6 固定 3 保证先攻/判定确定性；强制槽玩家 p1 稳定先行动。
    """
    if public_register:
        state.get_player("p1").controller = RegistrationController()
    monkeypatch.setattr("engine.round_manager.roll_d6", lambda: 3)
    manager = RoundManager(state)
    state.current_round = 1
    manager._phase_r0()
    manager._phase_r1()
    manager._phase_r3()
    return manager


def slot_outcome(state, pid: str):
    grant = next(g for g in state.m9_round_grants if g.actor_id == pid)
    return state.m9_system.outcome(grant.grant_id)


# ════════════════════════════════════════════════════════════
#  T1 一刀缭断：即演斩击 → 目标 HP 真实下降 + SP 真实消费
# ════════════════════════════════════════════════════════════

def probe_t1(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.t1 import OneSlash9

    p1.talent = OneSlash9("p1", state)
    p1.weapons.append(make_weapon("小刀"))
    state.markers.add_relation("p1", "ENGAGED_WITH", "p2")
    state.m9_system.set_sp("p1", 1)

    drive_rounds(state, monkeypatch)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    assert state.m9_system.get_sp("p1") == 0          # 即演 −1 SP（真实派发）
    assert p2.hp < p2.max_hp                          # 斩击真实伤害（世界状态）
    assert any(e["type"] == "oneslash_attack" for e in state.event_log)


# ════════════════════════════════════════════════════════════
#  T2 剪刀手一突：对已锁定目标核心攻击 → HP 下降 + SP 消费
# ════════════════════════════════════════════════════════════

def probe_t2(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.t2 import ScissorRush9

    p1.talent = ScissorRush9("p1", state)
    p1.weapons.append(make_weapon("小刀"))
    # 语义：p2 的 LOCKED_BY 集合含 p1 → 「p2 被 p1 锁定」
    # （注意：t27 e2e 里 add_relation("p1", LOCKED_BY, "p2") 方向反了，
    #   那是对手锁定自己，T0 从不出现——正是本探针要堵住的空转。）
    state.markers.add_relation("p2", "LOCKED_BY", "p1")
    state.m9_system.set_sp("p1", 1)

    drive_rounds(state, monkeypatch)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    assert state.m9_system.get_sp("p1") == 0
    assert p2.hp < p2.max_hp
    assert any(e["type"] == "attack" and e["attacker"] == "p1"
               for e in state.event_log)


# ════════════════════════════════════════════════════════════
#  T3 天星：公演 AOE → 目标受伤 + 统一石化注册表登记
# ════════════════════════════════════════════════════════════

def probe_t3(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.t3 import Star9

    p1.talent = Star9("p1", state)
    state.m9_system.set_sp("p1", 2)

    drive_rounds(state, monkeypatch, public_register=True)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    assert state.m9_system.get_sp("p1") == 0          # 公演 −2 SP
    assert p2.hp < p2.max_hp                          # 星陨 AOE 真实伤害
    assert state.m9_petrify.is_petrified("p2")        # 石化注册表真实登记
    assert p2.is_petrified is True


# ════════════════════════════════════════════════════════════
#  T4 六爻：石头 vs 石头 → 飞龙在天夺甲 → 玩家真实获得护甲副本
# ════════════════════════════════════════════════════════════

def probe_t4(state, p1, p2, monkeypatch) -> None:
    from models.equipment import ArmorLayer, ArmorPiece

    from engine.m9.talents.t4 import Hexagram9
    from utils.attribute import Attribute

    p1.talent = Hexagram9("p1", state)
    p2.armor.outer.append(ArmorPiece(
        "盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
        defense_map={"普通": 5}, durability=8))
    state.m9_system.set_sp("p1", 1)

    drive_rounds(state, monkeypatch)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    assert state.m9_system.get_sp("p1") == 0
    assert any(e["type"] == "hexagram_cast" for e in state.event_log)
    # ForfeitController：石头 vs 石头 → 飞龙在天（复制外甲）
    assert any(piece.name == "盾牌" for piece in p1.armor.outer)
    assert any(piece.name == "盾牌" for piece in p2.armor.outer)  # 复制非夺取


# ════════════════════════════════════════════════════════════
#  T6 朝阳好市民：联防整备移交真实装备 + 市民热线真实建档
# ════════════════════════════════════════════════════════════

def arrange_t6(state, p1, p2) -> None:
    """T6 探针前置：同地点存活警察 + 白名单武器 + SP。"""
    station = state.m9_police
    station.ensure_roster()
    station.units()[0].location = "商店"
    p1.weapons.append(make_weapon("高斯步枪"))
    state.m9_system.set_sp("p1", 2)


def probe_t6(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.t6 import GoodCitizen9

    p1.talent = GoodCitizen9("p1", state)
    arrange_t6(state, p1, p2)
    station = state.m9_police
    unit = station.units()[0]

    drive_rounds(state, monkeypatch)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    assert unit.weapon_name == "高斯步枪"             # 联防整备真实移交
    assert not p1.has_weapon("高斯步枪")
    assert state.m9_system.get_sp("p1") == 1          # 即演 −1 SP
    assert any(e["type"] == "t6_equip" for e in state.event_log)

    # 市民热线：特别线索 → 真实举报 → 案件建档 + 唯一通缉
    p1.talent.record_special_clue("p2", "线索")
    msg = p1.talent.hotline_report("p2")
    assert "被登记为通缉" in msg
    assert station.has_open_wanted()
    assert station.open_wanted().suspect_id == "p2"


# ════════════════════════════════════════════════════════════
#  T7 死者苏生：即演挂载 → 全局唯一保险伏笔真实登记
# ════════════════════════════════════════════════════════════

def probe_t7(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.t7 import Resurrection9

    p1.talent = Resurrection9("p1", state)
    state.m9_system.set_sp("p1", 1)

    drive_rounds(state, monkeypatch)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    assert state.m9_insurance.is_mounted()            # 保险伏笔真实挂载
    assert state.m9_insurance.mounted_target() == "p1"
    # 挂载双向关注 +1 SP（即演 −1 后关注补回）
    assert state.m9_system.get_sp("p1") == 1
    assert any(e["type"] == "resurrection_mount" for e in state.event_log)


# ════════════════════════════════════════════════════════════
#  G0 砂狼白子*Terror：即演召唤无人机 → 无人机实体 + HP 代价 + AR
# ════════════════════════════════════════════════════════════

def arrange_g0(state, p1, p2) -> None:
    """G0 探针前置：SP 就绪（召唤即演）。"""
    state.m9_system.set_sp("p1", 1)


def probe_g0(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.g0 import AR_WEAPON_NAME, ShirokoTerror9

    p1.talent = ShirokoTerror9("p1", state)
    arrange_g0(state, p1, p2)
    before_hp = p1.hp

    drive_rounds(state, monkeypatch)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    assert p1.talent.drone is not None                 # 无人机真实召唤
    assert p1.talent.is_drone_present()
    assert p1.hp < before_hp                          # 20% 当前 HP 代价真实支付
    assert state.m9_system.get_sp("p1") == 0
    assert p1.has_weapon(AR_WEAPON_NAME)              # BLACK FANG 465 常驻
    assert state.get_actor("g0_drone:p1") is not None
    assert any(e["type"] == "g0_drone_summon" for e in state.event_log)


# ════════════════════════════════════════════════════════════
#  G1 火萤：着装即演 → 形态状态机真实推进到次级燃烧
# ════════════════════════════════════════════════════════════

def probe_g1(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.g1 import FORM_SECONDARY, G1MythFire9

    p1.talent = G1MythFire9("p1", state)
    state.m9_system.set_sp("p1", 1)

    drive_rounds(state, monkeypatch)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    assert p1.talent.form == FORM_SECONDARY          # 着装：形态真实推进
    assert state.m9_system.get_sp("p1") == 0


# ════════════════════════════════════════════════════════════
#  G2 光影双身：即演创建影身 → state.m9_shadows 真实登场
# ════════════════════════════════════════════════════════════

def probe_g2(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.g2 import Hologram9

    p1.talent = Hologram9("p1", state)
    state.m9_system.set_sp("p1", 1)

    drive_rounds(state, monkeypatch)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    shadow_id = p1.talent.current_shadow_id
    assert shadow_id is not None
    assert shadow_id in state.m9_shadows               # 影身真实登场
    shadow = state.m9_shadows[shadow_id]
    assert shadow.is_alive()
    assert shadow.location == p1.location
    assert state.m9_system.get_sp("p1") == 0
    assert any(e["type"] == "SHADOW_CREATED" for e in state.event_log)


# ════════════════════════════════════════════════════════════
#  G3 神话之外：公演展开固有结界 → 结界 + 临时魔力 + 捕捉快照
# ════════════════════════════════════════════════════════════

def arrange_g3(state, p1, p2) -> None:
    """G3 探针前置：SP 就绪（公演展开）。"""
    state.m9_system.set_sp("p1", 2)


def probe_g3(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.g3 import Mythland9

    p1.talent = Mythland9("p1", state)
    arrange_g3(state, p1, p2)

    drive_rounds(state, monkeypatch, public_register=True)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    assert p1.talent.barrier_active                    # 固有结界真实展开
    assert p1.talent.barrier_location == "商店"
    assert p1.talent.temp_magic >= 4                   # 2 SP → 临时超额魔力
    assert state.m9_system.get_sp("p1") == 0
    assert "p2" in p1.talent.captured                  # 快照捕捉真实登记
    assert any(e["type"] == "m9_g3_expand" for e in state.event_log)


# ════════════════════════════════════════════════════════════
#  G4 救世主：负世主动燃尽 → 完整形态 + 真实完整额外行动派发
# ════════════════════════════════════════════════════════════

def probe_g4(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.g4 import FORM_FULL, Savior9

    p1.talent = Savior9("p1", state)
    p1.talent.divinity = 12
    p1.talent.ember = 12
    p1.talent.m9_burden_unlocked = True
    state.m9_system.set_sp("p1", 1)

    drive_rounds(state, monkeypatch)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    assert p1.talent.form == FORM_FULL                # 负世燃尽 → 完整形态
    assert p1.talent.is_savior
    assert p1.talent.ember_hp > 0                     # 余烬生命池建立
    assert state.m9_system.get_sp("p1") == 2          # 形态进入 SP 置 2
    extra = [g for g in state.m9_system.ledger._grants.values()
             if g.kind == "full_extra"
             and g.source_id == "g4_savior_active_burn"]
    assert extra                                      # 真实完整额外行动派发


# ════════════════════════════════════════════════════════════
#  G5 往世的涟漪：德谬歌锚定 → active_anchor + K 追忆真实扣除
#  （真实管线：R0 报名/公演位固化 → R3 T0 真实派发 → execute_t0
#    收集脚本（controller 无脚本接口时回退「真实预言」兜底脚本：
#    可落空预言 + move 垫槽）→ execute_anchor。不再直接调用 execute_anchor。）
# ════════════════════════════════════════════════════════════

def probe_g5(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.g5 import FORM_DEMIURGE, Ripple9
    from models.equipment import make_item

    p1.talent = Ripple9("p1", state)
    p1.talent.form = FORM_DEMIURGE
    p1.talent.sealed_reminiscence = 10.0
    grant_public_seat(state, "p1")                     # 真实 R0 公演位
    # 无武器世界里的唯一可落空预言：商店地面遗落物拾取（ACQUIRE）。
    state.ground_loot["商店"] = {
        "credits": 0, "arrows": 0, "weapons": [], "armor": [],
        "items": [{"name": "防毒面具", "kind": "item",
                   "source_slot": "", "object": make_item("防毒面具")}]}

    option = p1.talent.get_t0_option(p1)
    assert option is not None and option["m9_kind"] in (
        "g5_anchor", "g5_anchor_or_poem")

    drive_rounds(state, monkeypatch, public_register=True)

    outcome = slot_outcome(state, "p1")
    assert outcome.slot_resolved
    assert p1.talent.active_anchor                     # 锚定经真实 T0 建立
    from engine.balance import get as _bget
    min_k = int(_bget("m9_talents_extended", "g5", "anchor_min_k", default=3))
    assert p1.talent.anchor_k == min_k                 # 兜底脚本 K=anchor_min_k
    assert p1.talent.anchor_script[0] == ("interact", "防毒面具", "pickup")
    assert sum(1 for slot in p1.talent.anchor_script if slot[0] == "move") == min_k - 1
    assert p1.talent.sealed_reminiscence == 10.0 - min_k  # K 追忆真实扣除
    assert state.m9_system.get_sp("p1") == 0           # 公演 −2 SP
    assert any(e["type"] == "anchor_script_committed"
               for e in state.event_log)


# ════════════════════════════════════════════════════════════
#  G6 要有笑声：两轮真实闭环 —— 轮 1 普通行动真实入池（记录接线），
#  轮 2 T0 即演真实重演（重演接线）。全程不做任何手工 pool 写入。
# ════════════════════════════════════════════════════════════

class _LockCommandController(ForfeitController):
    """在正常行动菜单发出真实「锁定 p1」命令（M9 普通行动路径）。"""

    def get_command(self, player, game_state, available_actions, context=None):
        return "锁定 p1"


def probe_g6(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.g6 import CutawayJoke9

    p1.talent = CutawayJoke9("p1", state)
    p1.weapons.append(make_weapon("小刀"))
    p2.controller = _LockCommandController()
    state.m9_system.set_sp("p1", 1)

    # 轮 1：p2 真实普通行动「锁定」→ round_manager 行动循环真实入池
    # （若 pool.record 接线被删，本断言失败——不再手工预写池子）。
    drive_rounds(state, monkeypatch)
    pool = state.g6_template_pool
    assert any(e["round"] == 1 and e["category"] == "lock"
               and e["actor"] == "p2" for e in pool._log)

    # 轮 2：G6 T0 即演重演「lock」→ 真实锁定关系 + 真实 SP 消费
    state.current_round = 2
    manager = RoundManager(state)
    manager._phase_r0()
    manager._phase_r1()
    manager._phase_r3()
    assert state.m9_system.get_sp("p1") == 1          # 关注 +1 → 2；即演 −1 → 1
    assert state.markers.has_relation("p2", "LOCKED_BY", "p1")
    grant = [g for g in state.m9_round_grants
             if g.actor_id == "p1"][-1]
    assert state.m9_system.outcome(grant.grant_id).slot_resolved


# ════════════════════════════════════════════════════════════
#  G7 大叔我啊：临战-Archer 起床追演标记 + Terror 真实伤害
#  （G7 合同无 T0 入口：T0 通道是 wake_followup / Terror 钩子。）
# ════════════════════════════════════════════════════════════

def probe_g7(state, p1, p2, monkeypatch) -> None:
    from engine.m9.talents.g7 import Hoshino9

    p1.talent = Hoshino9("p1", state)

    # 起床：临战-Archer → 同槽受限追演标记（真实 wake 通道）
    p1.talent.form = "临战-Archer"
    p1.talent.on_wakeup(p1, state)
    assert p1.talent.wake_followup_available
    assert p1.vouchers >= 1

    # Terror 攻击：经真实命令分发入口（action_turn._execute_action 的
    # attack 分支——玩家命令统一走这里，is_terror 分支由此触发），
    # 而不是直接调用私有 _terror_attack。若该接线被删，本探针失败。
    p1.talent.is_terror = True
    p1.talent.terror_extra_hp = 20.0
    before = p2.hp
    manager = RoundManager(state)
    state.current_round = 1
    msg, kind, ok = manager.turn_manager._execute_action(
        {"action": "attack"}, p1)
    assert kind == "attack" and ok, msg
    from engine.balance import get as _bget
    terror_damage = int(_bget(
        "m9_talents_extended", "g7", "terror_attack_damage", default=4))
    assert p2.hp == before - terror_damage          # terror_attack_damage
    assert p1.talent.terror_extra_hp == 14.0          # 未全灭 → 扣 6 点额外 HP


# 槽位 → 探针
PROBES = {
    "T1": probe_t1,
    "T2": probe_t2,
    "T3": probe_t3,
    "T4": probe_t4,
    "T6": probe_t6,
    "T7": probe_t7,
    "G0": probe_g0,
    "G1": probe_g1,
    "G2": probe_g2,
    "G3": probe_g3,
    "G4": probe_g4,
    "G5": probe_g5,
    "G6": probe_g6,
    "G7": probe_g7,
}
