"""
RL 可选接入 —— RL 可用性检测
═════════════════════════════
检测 RL 模块和模型文件是否可用。
参考 stats_runner.py 的 try-import 模式。
"""

import os
import glob
from typing import Dict, Any, List


def detect_rl_availability() -> Dict[str, Any]:
    """
    检测 RL 环境是否可用。

    返回:
        {
            "available": bool,
            "models": [path1, path2, ...],
            "has_opponent_controller": bool,
            "has_torchscript_controller": bool,
        }
    """
    result: Dict[str, Any] = {
        "available": False,
        "models": [],
        "has_opponent_controller": False,
        "has_torchscript_controller": False,
    }

    # 尝试导入 RL 控制器
    try:
        from rl.self_play import OpponentRLController
        result["has_opponent_controller"] = True
    except ImportError:
        pass

    try:
        from rl.self_play import TorchScriptRLController
        result["has_torchscript_controller"] = True
    except ImportError:
        pass

    # 扫描模型文件
    models: List[str] = []
    checkpoint_dirs = ["checkpoints/", "rl/checkpoints/"]
    for cdir in checkpoint_dirs:
        if os.path.isdir(cdir):
            models.extend(glob.glob(os.path.join(cdir, "*.zip")))
            models.extend(glob.glob(os.path.join(cdir, "*.pts")))

    result["models"] = sorted(models)
    result["available"] = (
        (result["has_opponent_controller"] or result["has_torchscript_controller"])
        and len(models) > 0
    )
    return result
