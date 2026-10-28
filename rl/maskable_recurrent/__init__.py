"""
rl/maskable_recurrent
─────────────────────
MaskableRecurrentPPO —— 把 Invalid Action Masking（sb3-contrib MaskablePPO）
与 RecurrentPPO（LSTM 策略 + episode-starts 回滚缓冲）合并的标准库。

结构：薄 delta subclass——掩码只在分布接触点注入（forward / evaluate_actions /
get_distribution / _predict / predict 五处），LSTM 状态管理、_process_sequence、
episode 重置、GAE/padding 数值安全全部继承/沿用上游 RecurrentPPO 语义。

支持：
- 观测：Box（Dict 未支持——buffer 显式 NotImplementedError，故不导出 MultiInput 别名）；
- 动作：Discrete / MultiDiscrete（MultiBinary 刻意不支持，buffer 显式拒绝）；
- CLI 入口：MlpLstmPolicy / CnnLstmPolicy。

版本耦合：get()/_get_samples 依赖 sb3-contrib 2.7.x 内部细节（create_sequencers/
swap_and_flatten），发布时须 pin `sb3-contrib>=2.7,<3`。

验证：tests/test_maskable_recurrent_lib.py 提供等价性/掩码服从/稳定性回归门禁；
tools/phase0_maskable_recurrent.py 为开发期逐项验证脚本（全 True 掩码下与官方
RecurrentPPO 逐数字等价、掩码 100% 服从、10k 步无 NaN）。

导出：
    MaskableRecurrentActorCriticPolicy / MlpLstmPolicy / CnnLstmPolicy
    MaskableRecurrentRolloutBuffer / MaskableRecurrentRolloutBufferSamples
    MaskableRecurrentPPO
"""

from rl.maskable_recurrent.policies import (
    CnnLstmPolicy,
    MaskableRecurrentActorCriticPolicy,
    MlpLstmPolicy,
)
from rl.maskable_recurrent.buffers import (
    MaskableRecurrentRolloutBuffer,
    MaskableRecurrentRolloutBufferSamples,
)
from rl.maskable_recurrent.ppo import MaskableRecurrentPPO

__all__ = [
    "MaskableRecurrentActorCriticPolicy",
    "MlpLstmPolicy",
    "CnnLstmPolicy",
    "MaskableRecurrentRolloutBuffer",
    "MaskableRecurrentRolloutBufferSamples",
    "MaskableRecurrentPPO",
]

__version__ = "0.2.0"