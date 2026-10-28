"""I3预先固定60局、首失败停机与最终源码散列，不加载模型。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent / "final-runtime-3"
sys.path.insert(0, str(ROOT))

from engine.game_state import GameState
from engine.ga.controllers import MinimalController
from engine.ga.runtime import run_game
from engine.ga.setup import TALENTS


def group(count: int) -> None:
    """每组固定20seed，阵容轮转覆盖十四个真实注册槽位。"""
    slots = tuple(TALENTS)
    with (HERE / f"matrix-{count}.jsonl").open("x", encoding="utf-8") as output:
        for seed in range(20):
            lineup = [slots[(seed + i) % len(slots)] for i in range(count)]
            state = GameState.new_ga([f"p{i}" for i in range(count)], seed=seed,
                                     talents={f"p{i}": s for i, s in enumerate(lineup)})
            row = {"players": count, "seed": seed, "slots": lineup}
            started = time.monotonic()
            try:
                terminal = run_game(state, {p: MinimalController() for p in state.players},
                                    timeout_seconds=15)
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
    """协议先写、源码固定、外层子进程120秒；禁止覆盖证据与自动重试。"""
    if len(sys.argv) == 2:
        group(int(sys.argv[1]))
        return
    HERE.mkdir(exist_ok=True)
    paths = [*ROOT.glob("engine/ga/**/*.py"), ROOT / "engine/game_state.py",
             ROOT / "tools/ga_play.py"]
    protocol = {"players": [2, 4, 6], "seeds": list(range(20)), "slots": list(TALENTS),
                "lineup": "slots[(seed + i) % 14]", "games": 60,
                "per_game_timeout_seconds": 15, "per_group_process_timeout_seconds": 120,
                "budget_basis": "five new role duels and six-player sample passed; sample 0.75s",
                "first_failure_stops": True, "automatic_retry": False,
                "scope": "I3 runtime, not I4 strategy or I5 balance acceptance",
                "source_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(
                    p.read_bytes()).hexdigest() for p in paths}}
    with (HERE / "protocol.json").open("x", encoding="utf-8") as output:
        json.dump(protocol, output, indent=2)
    for count in (2, 4, 6):
        subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), str(count)],
                       cwd=ROOT, check=True, timeout=120)


if __name__ == "__main__":
    main()
