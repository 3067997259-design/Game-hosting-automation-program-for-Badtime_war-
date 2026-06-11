"""prompts.json 数值冻结 lint —— 防止新增嵌死数字的文案模板。

规则：模板中包含「数字 + 游戏单位」（点/轮/回合/HP/伤害/护甲/血/火种等）
模式的 key 必须登记在白名单中。新增嵌数模板将导致测试失败，
促使开发者把数字参数化（改用 {n} 变量从 balance 读取）。
"""
import json
import os
import re
import unittest

_PROMPTS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "prompts.json"
)

# 匹配嵌入游戏数值的模式（中文数字 + 常见单位）
_NUMERIC_PATTERN = re.compile(
    r'\d+\s*(?:点|轮|回合|HP|血|火种|护甲|伤害|行动|耐久|防御|颗心|层|次|把|件|格|人|回合|票|分)',
    re.UNICODE
)

# ── 白名单（已登记的嵌数模板 key，V2.0 改到对应数值时逐条参数化）──
_WHITELIST = {
    # g2reset — 舞台系统
    "g2reset.snap.triggered",
    "g2reset.song.dolente",
    "g2reset.song.riposato",
    "g2reset.stage.max_duration",
    # talent — G1 火萤
    "talent.g1mythfire.ardent_wish_gain",
    "talent.g1mythfire.debuff_delayed_low_activity",
    # talent — G2 全息影像
    "talent.g2eternity.shock_from_stay",
    # talent — G5 往世的涟漪
    "talent.g5ripple.dm_confirm_acquisition",
    "talent.g5ripple.anchor_countdown",
    "talent.g5ripple.monitoring_period",
    "talent.g5ripple.poem_law_election_progress",
    "talent.g5ripple.poem_bear",
    "talent.g5ripple.poem_destiny_header",
    "talent.g5ripple.poem_destiny_damage_selection",
    "talent.g5ripple.poem_rhythm_immediate_action",
    "talent.g5ripple.destructive_action_list",
    "talent.g5ripple.poem_ranger_oneslash_fallback",
    "talent.g5ripple.poem_strife_completion",
    # talent — G6 要有笑声
    "talent.g6cutaway.poem_joy_enhanced",
    # talent — G7 星野
    "talent.g7hoshino.macro_dash_limit",
    "talent.g7hoshino.macro_reorder_limit",
    "talent.g7hoshino.med_adrenaline_used",
    "talent.g7hoshino.med_chocolate_restore",
    "talent.g7hoshino.shield_filter_overflow",
    "talent.g7hoshino.terror_attack_desc",
    "talent.g7hoshino.terror_petrify_break",
    "talent.g7hoshino.wakeup_halo",
    # talent — T3 天星
    "talent.t3star.damage",
    "talent.t3star.police_damage",
    "talent.t3star.ripple_bounce_damage",
    "talent.t3star.ripple_bounce_header",
    "talent.t3star.ripple_bounce_police_damage",
    # talent — T4 六爻
    "talent.t4hexagram.thunder_damage",
    # talent — T5 combo
    "talent.t5combo.bonus_round",
    # talent — duet
    "talent.duet.heat.round_end",
    # 以下为新增时在此登记（说明数值含义 + 关联的 balance 键）
}


def _walk_prompts(data, path=""):
    """递归遍历 prompts.json，返回 {(key_path, value)} 中嵌数的条目。"""
    findings = []
    if isinstance(data, dict):
        for k, v in data.items():
            p = f"{path}.{k}" if path else k
            findings.extend(_walk_prompts(v, p))
    elif isinstance(data, str):
        if _NUMERIC_PATTERN.search(data):
            findings.append((path, data))
    return findings


class TestPromptsNumericFreeze(unittest.TestCase):
    """确保 prompts.json 中所有嵌死数值的 key 均在白名单内。"""

    @classmethod
    def setUpClass(cls):
        with open(_PROMPTS_PATH, "r", encoding="utf-8") as f:
            cls._prompts = json.load(f)

    def test_all_numeric_keys_whitelisted(self):
        """扫描所有模板，嵌数的 key 必须在白名单中。"""
        findings = _walk_prompts(self._prompts)
        found_keys = {key for key, _ in findings}

        unregistered = found_keys - _WHITELIST
        if unregistered:
            self.fail(
                "prompts.json 中以下 key 嵌入了游戏数值但未在白名单登记：\n"
                + "\n".join(f"  - {k}" for k in sorted(unregistered))
                + "\n请将数值参数化（改用 {n} 从 balance 读取），"
                  "或确认后加入 _WHITELIST。"
            )

    def test_no_stale_whitelist_entries(self):
        """白名单中不应有不存在或不再嵌数的 key。"""
        findings = _walk_prompts(self._prompts)
        found_keys = {key for key, _ in findings}

        stale = _WHITELIST - found_keys
        if stale:
            self.fail(
                "白名单中的以下 key 已不再嵌数或不存在，应从 _WHITELIST 移除：\n"
                + "\n".join(f"  - {k}" for k in sorted(stale))
            )


if __name__ == "__main__":
    unittest.main()
