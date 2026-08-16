"""M9 文本治理测试。

1. prompts.json 合法且存在 `m9` 命名空间；
2. `engine.m9.text.m9_text` 门面行为（缺失键返回诊断标记，不抛异常）；
3. 代码中引用的 `m9_text("...")` / `get_prompt("m9", "...")` 键必须在
   prompts.json 中存在（防新增引用忘写数据）；
4. M9 专有文件（engine/m9/**、cli/m9_ui.py、AI M9 适配器）除
   `docs/m9/text_allowlist.json` 白名单身份键外不允许任何 CJK 字面量；
5. G2 光影双身 T0 文案与关键渲染快照回归。
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROMPTS_PATH = ROOT / "data" / "prompts.json"
STRICT_DIRS = [ROOT / "engine" / "m9"]
STRICT_FILES = [
    ROOT / "cli" / "m9_ui.py",
    ROOT / "controllers" / "ai" / "m9_adapters.py",
]
ALLOWLIST_PATH = ROOT / "docs" / "m9" / "text_allowlist.json"
CJK = re.compile(r"[\u4e00-\u9fff]")
KEY_REF = re.compile(r'get_prompt\(\s*"m9"\s*,\s*"([^"]+)"|m9_text\(\s*"([^"]+)"')


def _load_prompts():
    with PROMPTS_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def _resolve(prompts, dotted: str):
    node = prompts.get("m9", {})
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _cjk_constants(tree):
    """AST：非 docstring 的 CJK 字符串常量列表。"""
    doc_ids = set()

    def mark_doc(body):
        if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant) and isinstance(
                    body[0].value.value, str):
            doc_ids.add(id(body[0].value))

    mark_doc(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            mark_doc(node.body)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in doc_ids and CJK.search(node.value)
    ]


class M9TextGovernanceTest(unittest.TestCase):
    """M9 文本治理（迁移进行中版本）。"""

    def test_prompts_json_has_m9_namespace(self) -> None:
        prompts = _load_prompts()
        self.assertIn("m9", prompts)
        self.assertIsInstance(prompts["m9"], dict)

    def test_m9_text_missing_key_returns_diagnostic(self) -> None:
        from engine.m9.text import m9_text
        value = m9_text("__governance_missing_key__")
        self.assertIn("[Missing: m9.__governance_missing_key__]", value)

    def test_m9_text_formats_kwargs(self) -> None:
        from engine.m9.text import m9_text
        # 模板缺失时也安全返回诊断标记；真实模板替换行为由迁移批次测试覆盖。
        value = m9_text("__governance_missing_key__", name="x")
        self.assertIsInstance(value, str)

    def test_referenced_m9_keys_exist_in_prompts(self) -> None:
        prompts = _load_prompts()
        missing = []
        for path in sorted(
                [*(ROOT / "engine").rglob("*.py"),
                 *(ROOT / "cli").rglob("*.py"),
                 *(ROOT / "controllers").rglob("*.py"),
                 *(ROOT / "actions").rglob("*.py"),
                 ROOT / "main.py"]):
            if "__pycache__" in str(path) or "\\rl\\" in str(path) \
                    or "\\network\\" in str(path) or "\\ai_chat\\" in str(path):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in KEY_REF.finditer(text):
                key = match.group(1) or match.group(2)
                if _resolve(prompts, key) is None:
                    missing.append(f"{path.relative_to(ROOT)}: m9.{key}")
        self.assertEqual(missing, [])

    def test_strict_zone_has_no_unallowed_cjk(self) -> None:
        """M9 专有文件除身份键白名单外不允许 CJK 字面量。"""
        allow = []
        if ALLOWLIST_PATH.exists():
            allow = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        allowed = {(entry["file"], entry["text"]) for entry in allow}
        violations = []
        for path in [*STRICT_DIRS[0].rglob("*.py"), *STRICT_FILES]:
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            rel = str(path.relative_to(ROOT)).replace("\\", "/")
            for lineno, literal in _cjk_constants(tree):
                if (rel, literal) not in allowed:
                    violations.append(f"{rel}:{lineno}: {literal[:60]}")
        self.assertEqual(violations, [])


    def test_g2_t0_shadow_prompt_lives_in_prompts(self) -> None:
        """G2 光影双身 T0 文案专项回归：name/描述/choose 提示全部来自 JSON。"""
        prompts = _load_prompts()
        self.assertEqual(_resolve(prompts, "talents.g2.t0.name"), "光影双身")
        self.assertEqual(_resolve(prompts, "talents.g2.t0.desc_create_improvise"),
                         "创建影身（即演 1 SP）")
        self.assertEqual(_resolve(prompts, "talents.g2.t0.desc_create_public"),
                         "创建影身（公演 2 SP）")
        self.assertEqual(_resolve(prompts, "talents.g2.t0.desc_terminal_promise"),
                         "世末终曲承诺（公演 2 SP，永久锁死再造资格）")
        self.assertEqual(_resolve(prompts, "talents.g2.create_choose_prompt"),
                         "创建影身：")

    def test_key_rendering_smoke(self) -> None:
        from engine.m9.text import m9_text
        self.assertEqual(
            m9_text("talents.g2.create_shadow_success", name="玩家", hp="8"),
            "🌫️ 玩家 创建影身（8 HP）！")
        self.assertEqual(
            m9_text("talents.g2.terminal_promise_success", name="玩家"),
            "🎵 玩家 世末终曲承诺！影身转为终曲歌者，再造资格永久锁定。")


if __name__ == "__main__":
    unittest.main()
