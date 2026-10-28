"""Initialize untrained M9 opponent-slot action rows from learned slot 0.

The early no-talent curriculum was 1v1, so only relative opponent slot 0 could
receive policy gradients.  M9's target slots are semantically exchangeable;
copying the learned row to slots 1-4 is a narrower and safer initialization
than behavior-cloning the whole recurrent actor.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch

from engine.experiments import set_profile
from rl.action_space import (
    ACTION_COUNT_M9_FIXED,
    IDX_ATTACK_BASE,
    IDX_FIND_BASE,
    IDX_LOCK_BASE,
    IDX_MOVE_HOME_M9_BASE,
    IDX_SHOOT_M9_BASE,
    M9_TARGET_SLOTS,
)
from rl.maskable_recurrent import MaskableRecurrentPPO


def combat_slot_row_groups() -> list[list[int]]:
    """Rows sharing one combat meaning and differing only by target slot."""
    groups = [
        [IDX_LOCK_BASE + slot for slot in range(M9_TARGET_SLOTS)],
        [IDX_FIND_BASE + slot for slot in range(M9_TARGET_SLOTS)],
        [IDX_SHOOT_M9_BASE + slot for slot in range(M9_TARGET_SLOTS)],
    ]
    groups.extend([
        [IDX_ATTACK_BASE + slot * 10 + weapon for slot in range(M9_TARGET_SLOTS)]
        for weapon in range(10)
    ])
    return groups


@torch.no_grad()
def symmetrize_combat_action_rows(policy, *, merge_random_home: bool = True) -> dict:
    """Copy slot-0 combat logits to slots 1-4; leave every other parameter alone."""
    action_net = policy.action_net
    if action_net.out_features != ACTION_COUNT_M9_FIXED:
        raise ValueError(
            f"expected M9 action head {ACTION_COUNT_M9_FIXED}, "
            f"got {action_net.out_features}"
        )

    copied: list[tuple[int, int]] = []
    for rows in combat_slot_row_groups():
        source = rows[0]
        for target in rows[1:]:
            action_net.weight[target].copy_(action_net.weight[source])
            action_net.bias[target].copy_(action_net.bias[source])
            copied.append((source, target))

    # Before home destinations were stabilized, 1v1 randomly used the first or
    # second home row for the sole opponent.  Their mean preserves both halves
    # of that historical training, then seeds all five opponent homes.
    home_rows = [IDX_MOVE_HOME_M9_BASE + slot for slot in range(M9_TARGET_SLOTS)]
    if merge_random_home:
        template_weight = action_net.weight[home_rows[:2]].mean(dim=0)
        template_bias = action_net.bias[home_rows[:2]].mean(dim=0)
    else:
        template_weight = action_net.weight[home_rows[0]].clone()
        template_bias = action_net.bias[home_rows[0]].clone()
    for target in home_rows:
        action_net.weight[target].copy_(template_weight)
        action_net.bias[target].copy_(template_bias)
        copied.append((home_rows[0], target))

    policy.optimizer.state.clear()
    return {
        "copied_pairs": len(copied),
        "combat_groups": len(combat_slot_row_groups()),
        "home_rows": home_rows,
        "merge_random_home": merge_random_home,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="M9 checkpoint 对手槽位动作头对称初始化",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--no-merge-random-home", action="store_true",
        help="不合并旧随机 home 槽，只复制当前 slot0 home 行",
    )
    args = parser.parse_args()

    set_profile("m9-rfc")
    model = MaskableRecurrentPPO.load(args.model, device=args.device)
    report = symmetrize_combat_action_rows(
        model.policy,
        merge_random_home=not args.no_merge_random_home,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(output))
    metadata = output.with_suffix(".symmetry.json")
    metadata.write_text(json.dumps({
        "source_model": str(args.model),
        "output_model": str(output),
        **report,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    print(f"[SYMMETRY] saved {output} and {metadata}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
