"""I2固定矩阵；失败即停且保留证据，不加载任何模型。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent / "final-runtime-2"
sys.path.insert(0, str(ROOT))

from engine.game_state import GameState
from engine.ga.controllers import MinimalController
from engine.ga.runtime import run_game
from engine.ga.setup import TALENTS


def group(count: int) -> None:
    """每人数20固定seed；混合阵容按seed轮转覆盖九天赋。"""
    target = HERE / f"matrix-{count}.jsonl"
    if target.exists():
        raise FileExistsError(target)
    slots = tuple(TALENTS)
    with target.open("x", encoding="utf-8") as output:
        for seed in range(20):
            lineup = [slots[(seed + i) % len(slots)] for i in range(count)]
            started = time.monotonic()
            state = GameState.new_ga([f"p{i}" for i in range(count)], seed=seed,
                                     talents={f"p{i}": s for i, s in enumerate(lineup)})
            row = {"players": count, "seed": seed, "slots": lineup}
            try:
                terminal = run_game(state, {p: MinimalController() for p in state.players},
                                    timeout_seconds=10)
                row.update(status="passed", rounds=state.current_round,
                           elapsed_seconds=time.monotonic() - started,
                           terminal=terminal.to_dict(), score_winners=state.score_winners)
            except Exception:
                row.update(status="failed", traceback=traceback.format_exc(),
                           random_draws=state.ga_rng.draws, trace=state.ga_trace)
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
                output.flush()
                raise
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
    print(json.dumps({"players": count, "passed": 20}), flush=True)


def main() -> None:
    """启动前写完整协议与源码散列，子进程另设120秒外层墙钟。"""
    if len(sys.argv) == 2:
        group(int(sys.argv[1]))
        return
    HERE.mkdir(exist_ok=True)
    sources = [*ROOT.glob("engine/ga/**/*.py"), ROOT / "engine/game_state.py",
               ROOT / "tools/ga_play.py"]
    protocol = {"players": [2, 4, 6], "seeds": list(range(20)),
                "slots": list(TALENTS), "lineup": "slots[(seed + i) % 9]",
                "games": 60, "per_game_timeout_seconds": 10,
                "per_group_process_timeout_seconds": 120,
                "budget_basis": "57-game probe completed; mixed six-player games below 10s",
                "first_failure_stops": True, "automatic_retry": False,
                "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(
                    p.read_bytes()).hexdigest() for p in sources}}
    with (HERE / "protocol.json").open("x", encoding="utf-8") as output:
        json.dump(protocol, output, indent=2)
    for count in (2, 4, 6):
        subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), str(count)],
                       cwd=ROOT, check=True, timeout=120)


if __name__ == "__main__":
    main()
