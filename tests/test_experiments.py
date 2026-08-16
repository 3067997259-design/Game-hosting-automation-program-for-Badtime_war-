"""M0b 实验开关测试：配置加载 / 运行时覆盖 / 缺键默认关。"""
import json
import os
import tempfile
import unittest
from unittest import mock

from engine import experiments


class ExperimentsTest(unittest.TestCase):

    def setUp(self) -> None:
        experiments.reset()

    def tearDown(self) -> None:
        experiments.reset()

    def test_unknown_experiment_defaults_off(self) -> None:
        """未声明的实验必须返回 False（v1 行为不变的保证）。"""
        self.assertFalse(experiments.is_enabled("no_such_experiment"))

    def test_runtime_enable_and_active(self) -> None:
        experiments.enable("k_initiative")
        self.assertTrue(experiments.is_enabled("k_initiative"))
        self.assertIn("k_initiative", experiments.active())
        experiments.disable("k_initiative")
        self.assertFalse(experiments.is_enabled("k_initiative"))
        self.assertNotIn("k_initiative", experiments.active())

    def test_config_file_loading(self) -> None:
        """experiments 分区从配置文件加载；下划线键被忽略；CLI 覆盖优先。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = os.path.join(tmp, "config")
            os.makedirs(cfg_dir)
            cfg = {
                "profile": "m1",
                "experiments": {
                    "_comment": "should be ignored",
                    "from_config_on": True,
                    "k_initiative": False,
                    "from_config_off": False,
                }
            }
            with open(os.path.join(cfg_dir, "game_config.json"), "w",
                      encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False)

            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                experiments.reset()
                self.assertEqual(experiments.current_profile(), "m1")
                self.assertTrue(experiments.is_enabled("from_config_on"))
                self.assertFalse(experiments.is_enabled("k_initiative"))
                self.assertFalse(experiments.is_enabled("from_config_off"))
                self.assertNotIn("_comment", experiments.active())
                # 运行时覆盖优先于配置
                experiments.disable("from_config_on")
                self.assertFalse(experiments.is_enabled("from_config_on"))
            finally:
                os.chdir(cwd)

    def test_missing_experiments_section(self) -> None:
        """配置文件没有 experiments 分区时一切默认关闭、不报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = os.path.join(tmp, "config")
            os.makedirs(cfg_dir)
            with open(os.path.join(cfg_dir, "game_config.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"ai_disabled_talents": []}, f)
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                experiments.reset()
                self.assertFalse(experiments.is_enabled("anything"))
                self.assertEqual(experiments.current_profile(), "legacy")
                self.assertEqual(experiments.active(), [])
            finally:
                os.chdir(cwd)

    def test_runtime_profile_then_experiment_override(self) -> None:
        experiments.set_profile("m3")
        self.assertEqual(experiments.current_profile(), "m3")
        self.assertTrue(experiments.is_enabled("k_initiative"))
        self.assertTrue(experiments.is_enabled("hp20"))
        self.assertTrue(experiments.is_enabled("m3_accuracy"))
        self.assertFalse(experiments.is_enabled("m4_gear"))
        experiments.disable("hp20")
        experiments.enable("m4_gear")
        self.assertFalse(experiments.is_enabled("hp20"))
        self.assertTrue(experiments.is_enabled("m4_gear"))


if __name__ == "__main__":
    unittest.main()
