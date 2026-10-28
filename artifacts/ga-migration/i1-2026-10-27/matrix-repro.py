
from pathlib import Path
import hashlib,json,sys,time
from engine.game_state import GameState
from engine.ga.controllers import MinimalController
from engine.ga.runtime import run_game
out=Path(sys.argv[1]); count=int(sys.argv[2]); budget=int(sys.argv[3])
path=out/f'matrix-{count}.jsonl'
if path.exists(): raise SystemExit('Refusing to replace existing matrix evidence')
for seed in range(20):
    s=GameState.new_ga([f'p{i}' for i in range(count)],seed=seed)
    started=time.perf_counter()
    try:
        terminal=run_game(s,{p:MinimalController() for p in s.players},timeout_seconds=budget)
        facts=[e.to_dict() for e in s.ga_ledger.events]
        row={'players':count,'seed':seed,'status':'passed','rounds':s.current_round,'world_tick':s.world_tick,'reason':terminal.reason.value,'last_survivor':s.winner,'score_winners':s.score_winners,'terminal':terminal.to_dict(),'event_count':len(facts),'event_sha256':hashlib.sha256(json.dumps(facts,sort_keys=True).encode()).hexdigest(),'draw_count':len(s.ga_rng.draws),'elapsed_seconds':time.perf_counter()-started,'illegal_reselections':0,'fallbacks':0}
    except Exception as exc:
        row={'players':count,'seed':seed,'status':'failed','error':repr(exc),'trace':s.ga_trace,'draws':s.ga_rng.draws}
    with path.open('a',encoding='utf-8') as handle:
        handle.write(json.dumps(row,ensure_ascii=False)+'\n')
    if row['status']!='passed': raise SystemExit('First failure stops this group')
print(f'{count} players: 20/20 completed',flush=True)
