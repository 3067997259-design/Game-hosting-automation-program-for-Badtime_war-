"""信源一致性测试 —— 确保 AI 常量表与引擎规范表无分歧。

每当你修改 engine/action_tables.py 或 utils/attribute.py 的世界数据，
本测试会捕获 AI constants.py 中的派生物是否仍然一致。
"""
import unittest


class TestTableConsistency(unittest.TestCase):
    """验证 controllers/ai/constants.py 的表与引擎信源一致。"""

    def test_locations_from_engine(self):
        """LOCATIONS 应直接从 engine.action_tables 导入。"""
        from engine.action_tables import LOCATIONS as ENG_LOCATIONS
        from controllers.ai.constants import LOCATIONS as AI_LOCATIONS
        self.assertIs(
            AI_LOCATIONS, ENG_LOCATIONS,
            "AI 的 LOCATIONS 应该是 engine.action_tables.LOCATIONS 的同一对象"
        )

    def test_effective_against_from_attribute(self):
        """EFFECTIVE_AGAINST 应直接从 utils.attribute 导入。"""
        from utils.attribute import EFFECTIVE_AGAINST as ATTR_EFF
        from controllers.ai.constants import EFFECTIVE_AGAINST as AI_EFF
        self.assertIs(
            AI_EFF, ATTR_EFF,
            "AI 的 EFFECTIVE_AGAINST 应该是 utils.attribute.EFFECTIVE_AGAINST 的同一对象"
        )

    def test_spell_prerequisites_derived_from_engine(self):
        """SPELL_PREREQUISITES 应从 engine.action_tables 推导且覆盖所有魔法所物品。"""
        from engine.action_tables import ITEM_LOCATIONS, SPELL_PREREQUISITES as ENG_SPELL
        from controllers.ai.constants import SPELL_PREREQUISITES as AI_SPELL

        # 所有魔法所物品都应在 AI 表中
        magic_items = {item for item, locs in ITEM_LOCATIONS.items() if "魔法所" in locs}
        self.assertEqual(set(AI_SPELL.keys()), magic_items,
                         "AI SPELL_PREREQUISITES 的键集应与引擎魔法所物品一致")

        # 前置关系一致
        for item, prereqs in AI_SPELL.items():
            eng_prereq = ENG_SPELL.get(item, "")
            expected = [eng_prereq] if eng_prereq else []
            self.assertEqual(
                prereqs, expected,
                f"'{item}' 的前置应为 {expected}，实际 {prereqs}"
            )

    def test_location_items_consistent_with_item_locations(self):
        """LOCATION_ITEMS 中每个物品都应在 engine ITEM_LOCATIONS 中有记录。"""
        from engine.action_tables import ITEM_LOCATIONS
        from controllers.ai.constants import LOCATION_ITEMS

        # 别名映射（AI 用语 → 引擎规范名）
        ai_to_engine = {"通行证": "办理通行证"}
        # AI 层独有（非交互物品，特殊操作）
        ai_only = {"释放病毒"}

        for loc, items in LOCATION_ITEMS.items():
            for item in items:
                if item in ai_only:
                    continue
                engine_item = ai_to_engine.get(item, item)
                engine_locs = ITEM_LOCATIONS.get(engine_item)
                self.assertIsNotNone(
                    engine_locs,
                    f"'{item}' 在 engine.action_tables.ITEM_LOCATIONS 中不存在"
                )
                self.assertIn(
                    loc, engine_locs,
                    f"LOCATION_ITEMS 称 '{item}' 在 '{loc}'，但引擎记录为 {engine_locs}"
                )


if __name__ == "__main__":
    unittest.main()
