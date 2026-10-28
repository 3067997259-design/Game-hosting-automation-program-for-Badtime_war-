"""RL 动作空间 ↔ 引擎动词表 覆盖治理测试（防"第三个 home 槽/shoot"）。

历史教训（同一类 bug 的三次实锤）：
1. home 槽：引擎允许 "move home_pX"，RL 固定 6 格装不下 → 蹲家对手不可达；
2. shoot：validator 一直支持，但可用名单/枚举器/RL 索引三层全缺 →
   RL 面对风筝 archer 徒手追猎枪（700k 步恒 5%）；
3. 弓混叠：m9 武器表 11 件 × 5×10 attack 网格 → "attack X 弓" 编码
   混叠到下一槽拳击/越界进 special 段，mask 点亮错误索引。

本测试的两条不变式：
- 【soundness】mask 点亮的每个索引，解码出的命令必须通过 cli.validator；
- 【completeness】引擎 build_action_options 枚举出的每条命令，必须能在
  mask 点亮位的解码集合中找到（显式白名单豁免除外）。

白名单是"有意不覆盖"的唯一合法出口，新增豁免必须写明原因。
"""

from __future__ import annotations

import contextlib
import io
import unittest

import numpy as np

from engine import experiments


def _make_env():
    """构造 1v1 m9-rfc env 并推进到首个 RL 决策点（构造期输出静音）。"""
    from rl.env import BadtimeWarEnv
    with contextlib.redirect_stdout(io.StringIO()):
        env = BadtimeWarEnv(num_opponents=1, max_rounds=24,
                            enable_talents=False)
        env.reset(seed=7)
    return env


def _arm_bow_scenario(env):
    """场景手术：RL 持弓 3 箭在商店，对手在医院（跨地点、可见、都醒着）。"""
    from models.equipment import make_weapon
    rl = env._rl_player
    state = env._state
    opp = next(state.get_player(pid) for pid in state.player_order
               if pid != rl.player_id)
    if not rl.has_weapon("弓"):
        rl.weapons.append(make_weapon("弓"))
    rl.arrows = 3
    rl.is_awake = True
    opp.is_awake = True
    rl.location = "商店"
    opp.location = "医院"
    return rl, opp, state


# ── completeness 白名单：有意不进 RL 动作空间的命令模式（原因必写）──
def _is_whitelisted(cmd: str) -> bool:
    verb = cmd.split(" ", 1)[0]
    # 弓不进 5×10 attack 网格（会索引混叠）；弓的攻击走 shoot 专属段
    if verb == "attack" and cmd.endswith(" 弓"):
        return True
    return False


class TestRLActionCoverage(unittest.TestCase):
    """m9-rfc：RL mask/映射与引擎动词表的一致性门禁。"""

    @classmethod
    def setUpClass(cls):
        experiments.set_profile("m9-rfc")
        cls.env = _make_env()
        cls.rl, cls.opp, cls.state = _arm_bow_scenario(cls.env)

    @classmethod
    def tearDownClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.env.close()
        experiments.reset()

    # ── 动词层：shoot 必须进入可用名单 ─────────────────────────────
    def test_shoot_in_available_names(self):
        from engine.action_turn import ActionTurnManager
        names, _ = ActionTurnManager(self.state)._get_available_actions(self.rl)
        self.assertIn("shoot", names,
                      "持弓有箭时可用名单必须含 shoot（弓的唯一跨地点动词）")

    # ── soundness：mask 点亮位 → 解码命令必须过 validator ──────────
    def test_mask_soundness(self):
        from rl.action_space import (
            IDX_FORFEIT, IDX_TALENT_T0_TARGET_BASE, IDX_CHOOSE_BASE,
            build_action_mask, idx_to_command,
        )
        from cli.parser import parse
        from cli.validator import validate

        mask = build_action_mask(self.rl, self.state, self.rl.player_id)
        bad: list[str] = []
        for idx in np.flatnonzero(mask):
            idx = int(idx)
            # T0/choose 段不应在普通模式点亮
            if IDX_TALENT_T0_TARGET_BASE <= idx < IDX_CHOOSE_BASE + 16:
                bad.append(f"idx={idx}: T0/choose 段在普通模式点亮")
                continue
            cmd = idx_to_command(idx, self.rl, self.state)
            if cmd == "forfeit":
                if idx != IDX_FORFEIT:
                    bad.append(f"idx={idx}: 解码回退 forfeit（目标槽失效？）")
                continue
            parsed = parse(cmd, self.rl.player_id)
            if parsed is None:
                bad.append(f"idx={idx}: 命令无法解析 {cmd!r}")
                continue
            ok, reason = validate(parsed, self.rl, self.state)
            if not ok:
                bad.append(f"idx={idx}: mask 点亮但 validator 拒绝 "
                           f"{cmd!r} — {reason}")
        self.assertEqual(bad, [], "mask soundness 违规:\n" + "\n".join(bad))

    # ── completeness：引擎枚举命令 → 必须映射进 mask 解码集 ────────
    def test_engine_options_covered_by_mask(self):
        from engine.action_turn import ActionTurnManager
        from engine.action_enumerator import build_action_options
        from rl.action_space import build_action_mask, idx_to_command

        names, _ = ActionTurnManager(self.state)._get_available_actions(self.rl)
        options = build_action_options(self.rl, self.state, names)
        mask = build_action_mask(self.rl, self.state, self.rl.player_id)
        decoded = {idx_to_command(int(i), self.rl, self.state)
                   for i in np.flatnonzero(mask)}

        missing: list[str] = []
        for verb, cmds in options.items():
            for cmd in cmds:
                if cmd in decoded or _is_whitelisted(cmd):
                    continue
                # special 模板族：解码自动填首个目标，族被覆盖即可
                if verb == "special" and any(
                        d.startswith(f"special {cmd.split(' ', 1)[1][:2]}")
                        for d in decoded):
                    continue
                missing.append(cmd)
        self.assertEqual(
            missing, [],
            "引擎可执行但 RL 永远发不出的命令（动作空间失明）:\n"
            + "\n".join(missing))

    # ── 定点回归 1：home 槽必须可达（曾实锤 5%）─────────────────────
    def test_home_move_reachable(self):
        from rl.action_space import build_action_mask, idx_to_command
        mask = build_action_mask(self.rl, self.state, self.rl.player_id)
        decoded = {idx_to_command(int(i), self.rl, self.state)
                   for i in np.flatnonzero(mask)}
        self.assertIn(f"move home_{self.opp.player_id}", decoded,
                      "追入对手住宅的 move 必须在 mask 中可达")

    # ── 定点回归 2：shoot 槽点亮且往返一致 ─────────────────────────
    def test_shoot_roundtrip(self):
        from rl.action_space import (
            ACTION_COUNT_M9, IDX_SHOOT_M9_BASE,
            build_action_mask, idx_to_command,
        )
        mask = build_action_mask(self.rl, self.state, self.rl.player_id)
        shoot_bits = [int(i) for i in np.flatnonzero(mask)
                      if IDX_SHOOT_M9_BASE <= i < ACTION_COUNT_M9]
        self.assertTrue(shoot_bits, "持弓有箭+跨地点可见目标：shoot 槽必须点亮")
        cmds = {idx_to_command(i, self.rl, self.state) for i in shoot_bits}
        self.assertIn(f"shoot {self.opp.name}", cmds)

    # ── 观测侧：能射不等于看得见弹药（mask 说不出"还剩几支箭"）──────
    def test_obs_exposes_bow_and_arrows(self):
        from rl.obs_builder_m9 import (
            M9_BLOCK_DIM, OBS_DIM_M9_TOTAL, build_obs_profile,
            obs_dim_for_profile,
        )
        from rl.obs_builder import OBS_DIM as LEGACY_OBS_DIM

        self.assertEqual(obs_dim_for_profile(self.state), OBS_DIM_M9_TOTAL)
        obs = build_obs_profile(self.rl, self.state, self.rl.player_id)
        self.assertEqual(obs.shape[0], OBS_DIM_M9_TOTAL)

        bow_base = LEGACY_OBS_DIM + M9_BLOCK_DIM - 3   # 27/28/29：持弓/箭量/远程目标
        self.assertEqual(float(obs[bow_base]), 1.0, "持弓位应为 1")
        self.assertGreater(float(obs[bow_base + 1]), 0.0,
                           "箭量位应随 arrows>0 抬起（弹药规划信号）")
        self.assertEqual(float(obs[bow_base + 2]), 1.0,
                         "对手在异地点且可见 → 远程目标位应为 1")

        # 箭打空后必须能被观测到（否则 RL 学不出"该补箭"）
        self.rl.arrows = 0
        try:
            obs0 = build_obs_profile(self.rl, self.state, self.rl.player_id)
            self.assertEqual(float(obs0[bow_base + 1]), 0.0)
            self.assertEqual(float(obs0[bow_base]), 1.0, "弓还在手上")
        finally:
            self.rl.arrows = 3

    # ── 定点回归 3：弓不得混叠进 attack 网格（曾实锤映射污染）───────
    def test_bow_never_aliases_attack_grid(self):
        from rl.action_space import _cmd_to_attack_idx
        idx = _cmd_to_attack_idx(f"attack {self.opp.name} 弓",
                                 {self.opp.name: 0}, self.state)
        self.assertEqual(idx, -1,
                         "弓（武器表第 11 件）编码进 5×10 网格会索引混叠，"
                         "必须返回 -1（弓走 shoot 专属段）")

    def test_future_unmapped_interact_never_aliases_last_action_slot(self):
        """encode=-1 只能表示未映射，绝不能借 Python 负索引点亮末槽。"""
        from unittest.mock import patch

        from engine.action_turn import ActionTurnManager
        from rl.action_space import IDX_FORFEIT, build_action_mask

        with patch.object(
            ActionTurnManager,
            "_get_available_actions",
            return_value=(["interact"], {}),
        ), patch(
            "rl.action_space._enumerate_interact",
            return_value=["interact 未来未登记项目"],
        ):
            mask = build_action_mask(self.rl, self.state, self.rl.player_id)

        self.assertTrue(mask[IDX_FORFEIT])
        self.assertFalse(mask[-1], "未映射 interact 曾通过 mask[-1] 污染最后 home 槽")

    def test_every_m9_menu_item_has_stable_roundtrip_slot(self):
        """M9 六地点菜单全部可表达，且 encode/decode 保持同一条命令。"""
        from locations import (
            home, hospital, magic_institute, military_base, police_station, shop,
        )
        from rl.action_space import _cmd_to_interact_idx, idx_to_command

        commands = {
            f"interact {item}"
            for module in (
                home, shop, magic_institute, hospital, military_base, police_station,
            )
            for item in module.get_menu()
            if not str(item).startswith("_")
        }
        failures = []
        seen_indices: dict[int, str] = {}
        for command in sorted(commands):
            idx = _cmd_to_interact_idx(command)
            if idx < 0:
                failures.append(f"未映射: {command}")
                continue
            decoded = idx_to_command(idx, self.rl, self.state)
            if decoded != command:
                failures.append(f"往返漂移: {command} -> {idx} -> {decoded}")
            previous = seen_indices.setdefault(idx, command)
            if previous != command:
                failures.append(f"索引碰撞: {previous} / {command} -> {idx}")
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
