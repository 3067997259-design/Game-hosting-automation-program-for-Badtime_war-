"""
rl/obs_builder_m9.py
────────────────────
M9-rfc 观测构建器：让 RL 在 M9 下"看见"关键决策状态。

布局（m9-rfc 专属，legacy/v2exp 不用）：
    ┌─ 基底 [0..539)：复用 legacy build_obs（539 维；其尾部 choose 指示位在
    │                M9 下不采用，置 0，choose 指示改放全局尾部）
    ├─ M9 摘要块 [539..569)：30 维，见 _fill_m9_block
    ├─ choose 契约 [569..828)：头 3 维 + 16 个选项 × 16 维语义
    └─ 天赋块 [828..918)：自身天赋 one-hot + 按稳定对手槽的天赋 one-hot，
                         见 _fill_talent_block（P2 追加，append-only）
    OBS_DIM_M9_TOTAL = 918

天赋块之前的 828 维是 **冻结的 v5 核心宽度**（OBS_DIM_M9_V5_CORE）：旧
checkpoint 经 `rl/obs_compat.LegacyObsDimWrapper(…, OBS_DIM_M9_V5_CORE)`
尾部截断后继续可用——追加只发生在绝对尾部，截断不破坏旧语义。

所有 m9 字段一律 getattr 兜底 + 索引断言，防止字段改名/缺失再度崩坏作业
（教训：legacy 的 Hologram/melody 字段漂移曾炸过 build_obs）。
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

import numpy as np

from engine.m9.talent_registry import M9_ACTIVE_SLOT_IDS
from rl.obs_builder import OBS_DIM as _LEGACY_OBS_DIM  # noqa: F401 (仅引用)
from rl.obs_builder import build_obs as _build_obs_legacy

# M9 摘要块与 choose 候选语义布局
M9_BLOCK_DIM = 30
M9_CHOOSE_HEADER_DIM = 3
M9_CHOOSE_MAX_OPTIONS = 16
M9_CHOOSE_OPTION_DIM = 16
M9_CHOOSE_OBS_DIM = (
    M9_CHOOSE_HEADER_DIM + M9_CHOOSE_MAX_OPTIONS * M9_CHOOSE_OPTION_DIM
)
# v5 核心宽度（冻结）：828 = 539 基底 + 30 摘要 + 259 choose 契约。
# 旧 checkpoint（obs=828）经 LegacyObsDimWrapper 截断到该宽度继续服役。
OBS_DIM_M9_V5_CORE = _LEGACY_OBS_DIM + M9_BLOCK_DIM + M9_CHOOSE_OBS_DIM

# ── 天赋块（P2，append-only）：自身 + 5 个稳定对手槽 ──
# 对手天赋当前是公开信息（引擎无天赋隐藏机制）；若未来引入 concealment，
# one-hot 应改按可见性门控（对接 choose_visibility 语义），布局不变。
M9_TALENT_SLOT_IDS: tuple[str, ...] = M9_ACTIVE_SLOT_IDS          # 14 槽稳定序
M9_TALENT_SLOT_COUNT = len(M9_TALENT_SLOT_IDS)
M9_TALENT_BLOCK_BASE = OBS_DIM_M9_V5_CORE                         # 828
M9_TALENT_OWN_DIM = 1 + M9_TALENT_SLOT_COUNT                       # 有天赋标志 + one-hot
M9_TALENT_PER_OPPONENT_DIM = 1 + M9_TALENT_SLOT_COUNT             # 在席标志 + one-hot
M9_TALENT_BLOCK_DIM = M9_TALENT_OWN_DIM + 5 * M9_TALENT_PER_OPPONENT_DIM  # 90
OBS_DIM_M9_TOTAL = OBS_DIM_M9_V5_CORE + M9_TALENT_BLOCK_DIM       # 918
_M9_TALENT_SLOT_INDEX = {slot: i for i, slot in enumerate(M9_TALENT_SLOT_IDS)}
_M9_BLOCK_BASE = _LEGACY_OBS_DIM          # 539
_M9_INJECT = _LEGACY_OBS_DIM + M9_BLOCK_DIM   # 569（choose 契约区）
_LEGACY_CHOOSE_OBS_DIM = 3

# 弓配置专家只在普通 T1 决策读取这段；它复用 choose 候选区的前 44 维，
# 因而不扩大 828 维 checkpoint。普通策略默认不注入，旧 checkpoint 仍看到全零；
# choose 决策则继续由 fill_choose_obs_m9 覆盖整段，两种语义不会同时出现。
M9_BOW_CONTEXT_BASE = _M9_INJECT + M9_CHOOSE_HEADER_DIM
M9_BOW_CONTEXT_DIM = 44

# 归一化容器（避免魔法数；数值上限按 m9 平衡量纲，保守足够大）
_SP_MAX = 2.0
_PP_NORM = 20.0
_ARC_CAP = 3.0
_QUEUE_NORM = 6.0
_POLICE_NORM = 6.0
_ARROW_NORM = 3.0   # 箭量归一化上限（商店单次补给量级；超出饱和即可）
_DEFENSE_NORM = 12.0
_DURABILITY_NORM = 24.0
_BURN_NORM = 3.0


def _g(state: Any, path: str, default):
    """getattr 属性链兜底：按 '.' 分段取属性（不含方法调用），缺省 default。"""
    cur: Any = state
    for part in path.split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return default
    return cur


def _call(obj: Any, name: str, *args, default=None):
    """调用 obj.name(*args)，方法缺失/抛错 → default。"""
    fn = getattr(obj, name, None)
    if not callable(fn):
        return default
    try:
        return fn(*args)
    except Exception:
        return default


def build_obs_m9(
    player: Any,
    game_state: Any,
    rl_player_id: str,
    long_term_stats: Optional[dict] = None,
    **kwargs: Any,
) -> np.ndarray:
    """返回 m9-rfc 的完整观测（含逐选项语义）。

    kwargs 透传 choose 三参（choose_mode/choose_situation/choose_n_options，供
    BC 收集器等造 obs 时把 choose 契约写进 m9 尾部）。
    """
    base = _build_obs_legacy(player, game_state, rl_player_id,
                             long_term_stats=long_term_stats)
    assert base.shape[0] == _LEGACY_OBS_DIM, base.shape
    # M9 不采用 legacy 尾部的 choose 指示位（[536:539]）——清 0，改放全局尾部
    base = base.copy()
    base[_LEGACY_OBS_DIM - _LEGACY_CHOOSE_OBS_DIM:_LEGACY_OBS_DIM] = 0.0

    obs = np.zeros(OBS_DIM_M9_TOTAL, dtype=np.float32)
    obs[:_LEGACY_OBS_DIM] = base
    _fill_m9_block(obs, player, game_state)
    if kwargs.get("bow_context") and not kwargs.get("choose_mode"):
        fill_bow_context_m9(obs, player, game_state)
    if kwargs.get("choose_mode"):
        fill_choose_obs_m9(obs, choose_mode=True,
                           situation=kwargs.get("choose_situation", ""),
                           n_options=kwargs.get("choose_n_options", 0),
                           options=kwargs.get("choose_options", ()),
                           player=player, game_state=game_state)
    _fill_talent_block(obs, player, game_state)
    return obs


def _talent_slot_id(talent: Any) -> str:
    """天赋实例 → 稳定槽位 ID（T1..G7）；无法解析返回空串。

    与 controllers.ai.decision.snapshot._slot_id_for 同源逻辑；此处本地实现
    以避免观测热路径拉起整个 controllers.ai 决策链。
    """
    if talent is None:
        return ""
    try:
        from engine.m9.talent_registry import (
            M9_TALENT_REGISTRY, registration_for_legacy_class,
        )
        reg = registration_for_legacy_class(type(talent))
        if reg is None:
            cls_path = f"{type(talent).__module__}.{type(talent).__name__}"
            reg = next(
                (item for item in M9_TALENT_REGISTRY.values()
                 if getattr(item, "m9_class_path", None) == cls_path),
                None,
            )
        if reg is not None:
            return reg.slot_id
    except Exception:  # noqa: BLE001 —— 注册表异常时退回 slot_id 属性
        pass
    return str(getattr(talent, "slot_id", "") or "")


def _fill_talent_block(raw_obs: np.ndarray, player: Any, game_state: Any) -> None:
    """填充天赋块（P2）：自身 one-hot + 5 个稳定对手槽 one-hot。

    布局（90 维，追加在 v5 核心 828 之后）：
        [0]        自身有天赋标志
        [1..15]    自身天赋 one-hot（M9_TALENT_SLOT_IDS 序）
        每 15 维/对手槽：[0] 在席标志 + [1..15] one-hot（None 槽全零）
    """
    from rl.action_space import get_opponent_slots

    idx = M9_TALENT_BLOCK_BASE
    own_slot = _talent_slot_id(getattr(player, "talent", None))
    raw_obs[idx] = float(bool(own_slot))
    if own_slot in _M9_TALENT_SLOT_INDEX:
        raw_obs[idx + 1 + _M9_TALENT_SLOT_INDEX[own_slot]] = 1.0
    idx += M9_TALENT_OWN_DIM

    for opponent in get_opponent_slots(player, game_state):
        raw_obs[idx] = float(opponent is not None)
        if opponent is not None:
            opp_slot = _talent_slot_id(getattr(opponent, "talent", None))
            if opp_slot in _M9_TALENT_SLOT_INDEX:
                raw_obs[idx + 1 + _M9_TALENT_SLOT_INDEX[opp_slot]] = 1.0
        idx += M9_TALENT_PER_OPPONENT_DIM

    assert idx == OBS_DIM_M9_TOTAL, (idx, OBS_DIM_M9_TOTAL)


def fill_bow_context_m9(
    raw_obs: np.ndarray,
    player: Any,
    game_state: Any,
) -> None:
    """在非 choose 决策中注入统一弓配置上下文，不改变 observation 宽度。

    布局（44 维）：自身六模块计数、全图六模块剩余供应、箭量/空箭袋，
    再按稳定五个对手槽编码普通/魔法/科技防御、护甲耐久、灼烧和本轮移动。
    地点、信用点、通行证、锁定与隐身已在基底/M9 摘要块中表达；旧基底
    实际没有箭量，因此这里必须显式补入，才能区分无限模块的适用场景。
    """
    from combat.numeric_v2 import compute_defense
    from engine.bow_modules import module_specs
    from rl.action_space import M9_BOW_MODULE_NAMES, get_opponent_slots

    start = M9_BOW_CONTEXT_BASE
    end = start + M9_BOW_CONTEXT_DIM
    if raw_obs.shape[0] < end:
        raise ValueError(
            f"弓配置上下文需要 observation 至少 {end} 维，实际 {raw_obs.shape[0]}"
        )
    raw_obs[start:end] = 0.0
    idx = start

    installed = list(getattr(player, "bow_modules", []) or [])
    for name in M9_BOW_MODULE_NAMES:
        raw_obs[idx] = min(installed.count(name), 2) / 2.0
        idx += 1

    specs = module_specs()
    supply = getattr(game_state, "module_supply", {}) or {}
    for name in M9_BOW_MODULE_NAMES:
        initial = max(int(specs.get(name, {}).get("supply", 1) or 1), 1)
        raw_obs[idx] = np.clip(float(supply.get(name, 0)) / initial, 0.0, 1.0)
        idx += 1

    from engine.balance import get as bget
    max_arrows = max(int(bget("bow", "max_arrows", default=6) or 6), 1)
    raw_obs[idx] = np.clip(
        float(getattr(player, "arrows", 0) or 0) / max_arrows, 0.0, 1.0,
    )
    idx += 1
    raw_obs[idx] = float(int(getattr(player, "arrows", 0) or 0) <= 0)
    idx += 1

    for opponent in get_opponent_slots(player, game_state):
        if opponent is None or not opponent.is_alive():
            idx += 6
            continue
        for attribute in ("普通", "魔法", "科技"):
            defense, _pieces = compute_defense(opponent, attribute)
            raw_obs[idx] = np.clip(float(defense) / _DEFENSE_NORM, 0.0, 1.0)
            idx += 1
        armor = getattr(opponent, "armor", None)
        pieces = armor.get_all_active() if armor is not None else []
        durability = sum(max(float(getattr(piece, "durability", 0) or 0), 0.0)
                         for piece in pieces)
        raw_obs[idx] = np.clip(durability / _DURABILITY_NORM, 0.0, 1.0)
        idx += 1
        raw_obs[idx] = np.clip(
            float(getattr(opponent, "burn_stacks", 0) or 0) / _BURN_NORM,
            0.0,
            1.0,
        )
        idx += 1
        raw_obs[idx] = float(bool(getattr(opponent, "moved_this_round", False)))
        idx += 1

    assert idx == end, f"弓配置上下文越界: {idx} != {end}"


def fill_choose_obs_m9(
    raw_obs: np.ndarray,
    *,
    choose_mode: bool,
    situation: str,
    n_options: int,
    options: Sequence[str] = (),
    player: Any = None,
    game_state: Any = None,
) -> None:
    """写入 choose 头及每个选项的可泛化语义，避免只凭菜单序号决策。"""
    from rl.obs_builder import _CHOOSE_SITUATION_MAP, _MAX_CHOOSE_SITUATIONS
    base = _M9_INJECT
    raw_obs[base:base + M9_CHOOSE_OBS_DIM] = 0.0
    if choose_mode:
        raw_obs[base] = 1.0
        raw_obs[base + 1] = _CHOOSE_SITUATION_MAP.get(situation, 0) / max(_MAX_CHOOSE_SITUATIONS, 1)
        raw_obs[base + 2] = min(n_options, 16) / 16.0
        features = encode_choose_options(options, player, game_state)
        start = base + M9_CHOOSE_HEADER_DIM
        raw_obs[start:start + features.size] = features.reshape(-1)


def encode_choose_options(
    options: Sequence[str], player: Any = None, game_state: Any = None,
) -> np.ndarray:
    """把最多 16 个自然语言选项编码为 16 维/项的关系与功能特征。

    位布局：present, self, opponent[5], accept, decline, offense,
    defense/support, movement/location, weapon/item, mode/form,
    numeric-value, ordinal。situation ID 另在 choose 头中提供。
    """
    result = np.zeros(
        (M9_CHOOSE_MAX_OPTIONS, M9_CHOOSE_OPTION_DIM), dtype=np.float32,
    )
    opponents = []
    if player is not None and game_state is not None:
        from rl.action_space import get_opponent_slots
        opponents = get_opponent_slots(player, game_state)

    # “报名公演/保留”是 M9 R0 最重要的一组战略二选一。此前两项都只留下
    # ordinal，策略只能记“第二个按钮”，并把该偏好通过 LSTM 污染到紧随其后的
    # talent_t0。把它们分别编码为接受/拒绝本次公演，不改变 obs 维度或 checkpoint
    # schema，同时让“报名→发动”共享同一功能语义而不是相反的菜单序号。
    accept_tokens = (
        "是", "确认", "接受", "发动", "继续", "加入", "同意", "重试", "激活",
        "报名", "yes", "accept",
    )
    decline_tokens = (
        "否", "拒绝", "跳过", "取消", "放弃", "结束", "保留", "no", "skip",
    )
    offense_tokens = ("攻击", "打击", "伤害", "进攻", "追猎", "偷", "缴械", "雷霆")
    defense_tokens = ("防御", "守", "治疗", "恢复", "护盾", "保护", "援助", "苏生")
    move_tokens = ("移动", "地点", "前往", "home", "商店", "魔法所", "医院", "军事基地", "警察局")
    weapon_item_tokens = (
        "武器", "物品", "拳击", "小刀", "警棍", "魔法弹幕", "地震",
        "电磁步枪", "高斯步枪", "导弹", "弓", "模块", "护甲", "药",
    )
    mode_tokens = ("模式", "形态", "即演", "普通", "水着", "临战", "节奏", "曲")

    for i, raw in enumerate(list(options)[:M9_CHOOSE_MAX_OPTIONS]):
        text = str(raw)
        lower = text.lower()
        row = result[i]
        row[0] = 1.0
        own_name = str(getattr(player, "name", "") or "")
        row[1] = float("自己" in text or bool(own_name and own_name in text))
        for slot, opponent in enumerate(opponents[:5]):
            if opponent is None:
                continue
            tokens = (
                str(getattr(opponent, "name", "") or ""),
                str(getattr(opponent, "player_id", "") or ""),
            )
            row[2 + slot] = float(any(token and token in text for token in tokens))
        row[7] = float(any(token in lower for token in accept_tokens))
        row[8] = float(any(token in lower for token in decline_tokens))
        row[9] = float(any(token in text for token in offense_tokens))
        row[10] = float(any(token in text for token in defense_tokens))
        row[11] = float(any(token in text for token in move_tokens))
        row[12] = float(any(token in text for token in weapon_item_tokens))
        row[13] = float(any(token in text for token in mode_tokens))
        numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
        row[14] = float(np.clip(float(numbers[0]) / 20.0, -1.0, 1.0)) if numbers else 0.0
        row[15] = i / max(M9_CHOOSE_MAX_OPTIONS - 1, 1)
    return result


def _fill_m9_block(obs: np.ndarray, player: Any, game_state: Any) -> None:
    """填充 30 维 M9 摘要块。索引语义写死；任何字段缺失 → 0（不崩）。"""
    idx = _M9_BLOCK_BASE
    m9 = getattr(game_state, "m9_system", None)
    pp = getattr(game_state, "m9_pp", None)
    arc = getattr(game_state, "m9_arc", None)
    police = getattr(game_state, "m9_police", None)
    pid = player.player_id

    # ── 0-6：SP / 当前授予（ActionGrant）──
    obs[idx] = min(float(_call(m9, "get_sp", pid, default=0)), _SP_MAX) / _SP_MAX; idx += 1
    cur_grant = _call(m9, "current_grant", default=None)
    grant_for_me = bool(cur_grant is not None and getattr(cur_grant, "actor_id", None) == pid)
    obs[idx] = float(grant_for_me); idx += 1
    obs[idx] = float(grant_for_me and getattr(cur_grant, "kind", "") == "standard"); idx += 1
    obs[idx] = float(grant_for_me and getattr(cur_grant, "kind", "") == "full_extra"); idx += 1
    obs[idx] = float(grant_for_me and getattr(cur_grant, "restricted", False)); idx += 1
    obs[idx] = float(grant_for_me and getattr(cur_grant, "allow_instant", False)); idx += 1
    obs[idx] = float(grant_for_me and getattr(cur_grant, "allow_public", False)); idx += 1

    # ── 7-10：公演队列 / 本轮公演位 ──
    queue = _g(m9, "queue", None)
    obs[idx] = float(bool(queue and _call(queue, "is_in_queue", pid, default=False))); idx += 1  # 7 已报名
    round_ = getattr(game_state, "current_round", 0)
    holder = _g(m9, "_public_holder_by_round", None)
    holder_me = bool(holder and holder.get(round_) == pid)
    obs[idx] = float(holder_me); idx += 1                                        # 8 本轮公演位归属
    obs[idx] = float(bool(queue and _call(queue, "head", default=None) == pid)); idx += 1  # 9 队首是我
    members = _call(queue, "members", default=[])
    obs[idx] = min(int(len(members)), _QUEUE_NORM) / _QUEUE_NORM; idx += 1        # 10 队列长度

    # ── 11-15：PP / 往世层 / 剧情分 ──
    obs[idx] = min(float(_call(pp, "balance", pid, default=0.0)), _PP_NORM) / _PP_NORM; idx += 1   # 11
    obs[idx] = float(_call(pp, "is_frozen", pid, default=False)); idx += 1        # 12
    obs[idx] = float(_call(pp, "is_blackhorse", pid, default=False)); idx += 1    # 13
    chapters = _call(arc, "chapters_of", pid, default=set())
    obs[idx] = min(len(chapters), _ARC_CAP) / _ARC_CAP; idx += 1                  # 14 剧情章数
    obs[idx] = float(_call(arc, "has_debut", pid, default=False)); idx += 1       # 15 首演

    # ── 16-20：警察 ──
    obs[idx] = float(_call(police, "is_disabled", default=False)); idx += 1       # 16
    obs[idx] = float(bool(police and getattr(police, "captain_id", None) == pid)); idx += 1  # 17 我当队长
    candidates = _call(police, "candidates", default=[])
    obs[idx] = float(bool(pid in candidates)); idx += 1                           # 18 队长候选
    units = _call(police, "alive_units", default=[])
    obs[idx] = min(int(len(units)), _POLICE_NORM) / _POLICE_NORM; idx += 1        # 19 警力
    wanted = _call(police, "open_wanted", default=None)
    obs[idx] = float(wanted is not None); idx += 1                                # 20 有通缉目标

    # ── 21-27：关键天赋 flag 概览（getattr 兜底；逐步实填）──
    talent = getattr(player, "talent", None)
    obs[idx] = float(bool(talent and getattr(talent, "is_terror", False))); idx += 1       # 21 Terror
    obs[idx] = float(bool(talent and getattr(talent, "is_terror", False)
                          and float(_g(talent, "terror_extra_hp", 0) or 0) > 0)); idx += 1  # 22 Terror 残血
    obs[idx] = float(bool(talent and getattr(talent, "anchor_active", False))); idx += 1   # 23 G5 锚定中
    obs[idx] = float(bool(talent and getattr(talent, "active", False))); idx += 1          # 24 G2/G3 区域激活
    obs[idx] = float(bool(talent and _call(talent, "free_find_available",
                                           getattr(game_state, "current_round", 1),
                                           default=False))); idx += 1                       # 25 卸甲免费find 就绪
    obs[idx] = float(bool(m9 and _g(m9, "g6_template_pool", None) is not None)); idx += 1  # 26 G6 模板池存在

    # ── 27-29：弓/射击态势 ──
    # 基底 539 维的武器 one-hot 是冻结的 10 件窗口（不含弓），且没有任何"箭"计数位：
    # mask 只能告诉 RL"此刻能不能射"，说不出"还剩几支箭"——弹药规划必须自己看得见，
    # 否则会把箭在无价值目标上打空（shoot 段刚补上，配套观测不能缺）。
    obs[idx] = float(_call(player, "has_weapon", "弓", default=False)); idx += 1        # 27 持弓
    obs[idx] = min(float(getattr(player, "arrows", 0) or 0), _ARROW_NORM) / _ARROW_NORM
    idx += 1                                                                            # 28 箭量
    obs[idx] = float(_has_remote_visible_target(player, game_state)); idx += 1          # 29 有跨地点可见目标

    assert idx == _M9_BLOCK_BASE + M9_BLOCK_DIM, f"M9 摘要块越界: {idx}"


def _has_remote_visible_target(player: Any, game_state: Any) -> bool:
    """是否存在"不同地点且对我可见"的存活对手（弓的专属射程价值判断）。

    只做可见性 + 地点比较，不复刻 validator 的全部拒绝条件：精确合法性由
    action mask 承担，本位只提供"值不值得留箭/追不追"的态势信号。
    """
    from engine.visibility import can_see
    for pid in getattr(game_state, "player_order", []) or []:
        if pid == player.player_id:
            continue
        opp = game_state.get_player(pid)
        if opp is None or not opp.is_alive() or opp.location == player.location:
            continue
        try:
            if can_see(player, opp, game_state):
                return True
        except Exception:
            continue
    return False


def build_obs_profile(
    player: Any,
    game_state: Any,
    rl_player_id: str,
    long_term_stats: Optional[dict] = None,
    **kwargs: Any,
) -> np.ndarray:
    """按 profile 选择观测构建器：m9-rfc → build_obs_m9；否则 legacy build_obs。

    kwargs（choose 三参等）透传给对应构建器（BC 收集/M9 评估等非 env 造 obs 场景）。
    """
    from engine.m9.gate import m9_enabled
    if m9_enabled(game_state):
        return build_obs_m9(player, game_state, rl_player_id,
                            long_term_stats=long_term_stats, **kwargs)
    return _build_obs_legacy(player, game_state, rl_player_id,
                             long_term_stats=long_term_stats, **kwargs)


def obs_dim_for_profile(game_state=None) -> int:
    """m9-rfc → OBS_DIM_M9_TOTAL；legacy/v2exp → 539。与观察空间/堆叠同源。"""
    from engine.m9.gate import m9_enabled
    if m9_enabled(game_state):
        return OBS_DIM_M9_TOTAL
    return _LEGACY_OBS_DIM
