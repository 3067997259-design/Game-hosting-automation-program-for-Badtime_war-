"""模块化手册与文档治理检查。"""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from tools import check_doc_governance, handbook


ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "docs" / "handbook" / "manifest.json"
LEGACY_MANIFEST_PATH = ROOT / "docs" / "legacy" / "manifest.json"
ARCHIVED_SOURCE_DOCX = (
    ROOT / "docs" / "archive" / "v2-migration" / "完全游玩手册V2.0-exp.src.docx"
)
ARCHIVED_SOURCE_SHA256 = (
    "ae5cc2ad709fae8bc432354510b70700038eb1b57adc721ead11394e9713c10a"
)
ARCHIVED_LEGACY_SOURCE = (
    ROOT / "docs" / "archive" / "legacy-migration" / "完全游玩手册.md"
)
ARCHIVED_LEGACY_SHA256 = (
    "972df15fe443b55b4fb7efdf70d31b4a1dd2c689d97c2be24bbb4680300f6809"
)


class HandbookRoundTripTest(unittest.TestCase):
    """验证拆分、回组装与主题检索。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = handbook.load_manifest(MANIFEST_PATH)

    def test_batches_have_at_most_five_modules(self) -> None:
        counts = {}
        for module in self.manifest["modules"]:
            batch = module["batch"]
            counts[batch] = counts.get(batch, 0) + 1
        self.assertTrue(counts)
        self.assertTrue(all(count <= 5 for count in counts.values()))

    def test_archived_migration_source_is_unchanged(self) -> None:
        digest = hashlib.sha256(ARCHIVED_SOURCE_DOCX.read_bytes()).hexdigest()
        self.assertEqual(ARCHIVED_SOURCE_SHA256, digest)

    def test_generated_outputs_are_current(self) -> None:
        assembled = handbook.assemble(MANIFEST_PATH, self.manifest)
        source_output = ROOT / "docs" / "handbook" / "complete.generated.src.md"
        rendered_output = ROOT / "docs" / "handbook" / "complete.generated.md"
        self.assertEqual(
            handbook.GENERATED_BANNER + assembled,
            source_output.read_text(encoding="utf-8"),
        )
        rendered = handbook.render_balance(assembled, ROOT / "data" / "balance.json")
        self.assertEqual(
            handbook.GENERATED_BANNER + rendered,
            rendered_output.read_text(encoding="utf-8"),
        )

    def test_g2_context_includes_required_modules(self) -> None:
        selected = handbook.select_context_modules(self.manifest, "g2")
        doc_ids = {module["doc_id"] for module in selected}
        self.assertIn("talents.g2.overview", doc_ids)
        self.assertIn("talents.g2.songs", doc_ids)
        self.assertIn("talents.g2.materials", doc_ids)
        self.assertIn("core.rounds_actions", doc_ids)


class LegacyHandbookTest(unittest.TestCase):
    """验证 Legacy 手册模块、冻结原件和生成合订本。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = handbook.load_manifest(LEGACY_MANIFEST_PATH)

    def test_batches_have_at_most_five_modules(self) -> None:
        counts = {}
        for module in self.manifest["modules"]:
            batch = module["batch"]
            counts[batch] = counts.get(batch, 0) + 1
        self.assertEqual(34, len(self.manifest["modules"]))
        self.assertTrue(all(count <= 5 for count in counts.values()))

    def test_archived_migration_source_is_unchanged(self) -> None:
        digest = hashlib.sha256(ARCHIVED_LEGACY_SOURCE.read_bytes()).hexdigest()
        self.assertEqual(ARCHIVED_LEGACY_SHA256, digest)

    def test_generated_output_is_current(self) -> None:
        assembled = handbook.assemble(LEGACY_MANIFEST_PATH, self.manifest)
        generated = ROOT / "docs" / "legacy" / "complete.generated.md"
        self.assertEqual(
            handbook.GENERATED_BANNER + assembled,
            generated.read_text(encoding="utf-8"),
        )


class GovernanceTest(unittest.TestCase):
    """验证治理登记表没有硬错误。"""

    def test_registry_has_no_errors(self) -> None:
        findings = check_doc_governance.run_checks(
            ROOT / "docs" / "document_registry.json"
        )
        errors = [finding for finding in findings if finding.severity == "ERROR"]
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
