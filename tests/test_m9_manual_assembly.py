"""M9 手册装配器回归测试。

验证 tools/m9_handbook.py 与 docs/m9/manual/manifest.json：
1. 模块装配与生成产物一致性（同 tools/handbook.py 的 check 纪律）；
2. `⟦bal:...⟧` 数值渲染自 data/balance.json，玩家版不留占位符。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from handbook import HandbookError, check_outputs, load_manifest  # noqa: E402


class M9ManualAssemblyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest_path = ROOT / "docs" / "m9" / "manual" / "manifest.json"
        self.source_output = self.manifest_path.parent / "complete.generated.src.md"
        self.rendered_output = self.manifest_path.parent / "complete.generated.md"
        self.balance_path = ROOT / "data" / "balance.json"

    def test_manifest_and_generated_outputs_consistent(self) -> None:
        manifest = load_manifest(self.manifest_path)
        check_outputs(
            self.manifest_path,
            manifest,
            None,
            self.source_output,
            self.rendered_output,
            self.balance_path,
        )

    def test_rendered_output_has_no_balance_placeholder(self) -> None:
        text = self.rendered_output.read_text(encoding="utf-8")
        self.assertNotIn("⟦bal:", text)
        balance = json.loads(self.balance_path.read_text(encoding="utf-8"))
        expected_hp = balance["hp20"]["player_max_hp"]
        self.assertIn(f"HP 上限 `{expected_hp}`", text)

    def test_missing_module_is_detected(self) -> None:
        manifest = load_manifest(self.manifest_path)
        fake = self.manifest_path.parent / "core" / "__missing_module__.md"
        original = manifest["modules"][0]["path"]
        manifest["modules"][0]["path"] = fake.name
        try:
            with self.assertRaises(HandbookError):
                check_outputs(
                    self.manifest_path,
                    manifest,
                    None,
                    self.source_output,
                    self.rendered_output,
                    self.balance_path,
                )
        finally:
            manifest["modules"][0]["path"] = original


if __name__ == "__main__":
    unittest.main()
