"""文档渲染防漂移测试（信源统一：balance.json → 文档）。

守护：每个 docs/*.src.md 重新渲染后必须等于已提交的 docs/*.md，且所有 ⟦bal:⟧ 占位可解析。
若改了 balance 没跑 render_docs.py，本测试会逮住。无 *.src.md 时空过（手册写好后自动生效）。
"""
import glob
import importlib.util
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_render_docs():
    path = os.path.join(_ROOT, "tools", "render_docs.py")
    spec = importlib.util.spec_from_file_location("render_docs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DocsRenderTest(unittest.TestCase):
    def test_rendered_docs_in_sync_with_balance(self):
        rd = _load_render_docs()
        balance = rd.load_balance()
        srcs = glob.glob(os.path.join(_ROOT, "docs", "*" + rd.SRC_SUFFIX))
        for src in srcs:
            with open(src, encoding="utf-8") as f:
                text = f.read()
            # 缺键/畸形占位 → render_text 抛 KeyError，测试即失败
            rendered = rd.render_text(text, balance, os.path.basename(src))
            out = rd.src_to_out(src)
            self.assertTrue(
                os.path.exists(out),
                f"缺渲染版 {os.path.basename(out)}，跑 python tools/render_docs.py")
            with open(out, encoding="utf-8") as f:
                existing = f.read()
            self.assertEqual(
                existing, rendered,
                f"{os.path.basename(out)} 与 balance 不同步，跑 python tools/render_docs.py")


if __name__ == "__main__":
    unittest.main()
