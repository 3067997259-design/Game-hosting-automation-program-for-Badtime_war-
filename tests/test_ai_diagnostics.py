"""BasicAI 批量诊断的胜负关联回归测试。"""

import json

from controllers.ai.diagnostics import DiagReport


def test_diag_report_saves_lightweight_game_outcomes(tmp_path) -> None:
    report = DiagReport()
    result = {
        "seed": 20260813,
        "draw": False,
        "draw_reason": "",
        "rounds": 17,
        "winner_pid": "p2",
        "survival_winner": "p1",
        "final_scores": {"p1": 2.0, "p2": 7.5},
        "talent_nums_picked": [7, 14],
        "event_counts": {"attack": 3, "death": 1},
        "player_event_counts": {"p1": {"g0_drone_summon": 1}},
        "players": [
            {
                "pid": "p1",
                "personality": "balanced",
                "talent_num": 7,
                "talent_name": "砂狼白子*Terror",
                "is_winner": False,
                "is_survival_winner": True,
                "alive": True,
                "kill_count": 0,
                "final_score": 2.0,
                "talent_usage": {"drone_summons": 1},
                "decision_stats": {"t0_activated": 1},
                "sp_end": 0,
                "unrelated_large_field": ["must not be copied"],
            },
        ],
        "diagnostics": {},
    }

    report.add_game(3, result)
    output = tmp_path / "diag.json"
    report.save_raw(str(output))

    data = json.loads(output.read_text(encoding="utf-8"))
    game = data["games"][0]
    assert game["game_idx"] == 3
    assert game["seed"] == 20260813
    assert game["winner_pid"] == "p2"
    assert game["survival_winner"] == "p1"
    assert game["players"][0]["talent_num"] == 7
    assert game["players"][0]["talent_usage"] == {"drone_summons": 1}
    assert game["players"][0]["event_counts"] == {"g0_drone_summon": 1}
    assert game["event_counts"]["death"] == 1
    assert "unrelated_large_field" not in game["players"][0]
