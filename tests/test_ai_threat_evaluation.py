import sys
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace


_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from controllers.ai.controller import BasicAIController
from controllers.ai.minds.threat_mind import ThreatMind


HOSHINO_TALENT_NAME = "大叔我啊，剪短发了"


def _player(name, player_id, talent=None, **attrs):
    values = dict(
        name=name,
        player_id=player_id,
        talent=talent,
        hp=5.0,
        is_alive=lambda: True,
        is_captain=False,
        locked_target=None,
        location="home",
        armor=None,
    )
    values.update(attrs)
    return SimpleNamespace(**values)


def _state(*players, markers=None):
    by_id = {player.player_id: player for player in players}
    return SimpleNamespace(
        player_order=[player.player_id for player in players],
        get_player=lambda player_id: by_id.get(player_id),
        markers=markers,
    )


class _FalseCaptainDangerStrategy:
    def __init__(self):
        self.called = False

    def assess_captain_danger(self, player, state, police_cache, count_outer_armor_fn):
        self.called = True
        return False


class _AnchorMarkers:
    def __init__(self, anchored_id, anchor_id):
        self.anchored_id = anchored_id
        self.anchor_id = anchor_id

    def has_relation(self, source_id, relation, target_id):
        return (
            source_id == self.anchored_id
            and relation == "ANCHORED_BY"
            and target_id == self.anchor_id
        )


def _count_locked_by(player, state):
    count = 0
    for pid in state.player_order:
        if pid == player.player_id:
            continue
        target = state.get_player(pid)
        if target and target.is_alive():
            locked = getattr(target, 'locked_target', None)
            if locked and (locked == player.name or locked == player.player_id):
                count += 1
    return count


def _is_anchored(player, state):
    markers = getattr(state, "markers", None)
    if not markers or not hasattr(markers, "has_relation"):
        return False
    for pid in state.player_order:
        if pid != player.player_id and markers.has_relation(player.player_id, "ANCHORED_BY", pid):
            return True
    return False


class ThreatEvaluationTest(unittest.TestCase):
    def _evaluate_target_score(self, target_talent):
        controller = BasicAIController()
        player = _player("Observer", "p1")
        target = _player("Hoshino", "p2", target_talent)
        controller._estimate_power = MethodType(lambda self, target: 100.0, controller)

        controller._update_threat_scores(player, _state(player, target))

        return controller._threat_scores[target.name]

    def _legacy_captain_critical(self, player, state, police_cache=None):
        controller = BasicAIController()
        controller._strategy = _FalseCaptainDangerStrategy()
        controller._police_cache = police_cache or {}

        return controller._is_critical(player, state), controller._strategy.called

    def _mind_captain_critical(self, player, state, police_cache=None):
        strategy = _FalseCaptainDangerStrategy()
        result = ThreatMind()._is_critical(
            player,
            state,
            strategy,
            polices_cache=police_cache or {},
        )

        return result, strategy.called

    def _assert_captain_critical_in_both_paths(self, player, state, police_cache=None):
        evaluators = (
            ("legacy", self._legacy_captain_critical),
            ("threat_mind", self._mind_captain_critical),
        )
        for path_name, evaluate in evaluators:
            with self.subTest(path=path_name):
                result, strategy_called = evaluate(player, state, police_cache)
                self.assertTrue(strategy_called)
                self.assertTrue(result)

    def test_legacy_arch_preserves_terror_threat_boost(self):
        talent = SimpleNamespace(
            name=HOSHINO_TALENT_NAME,
            is_terror=True,
            self_doubt_pending=False,
            tactical_unlocked=False,
            ammo=[],
        )

        self.assertEqual(
            self._evaluate_target_score(talent),
            60.0,
        )

    def test_legacy_arch_preserves_self_doubt_threat_boost(self):
        talent = SimpleNamespace(
            name=HOSHINO_TALENT_NAME,
            is_terror=False,
            self_doubt_pending=True,
            tactical_unlocked=False,
            ammo=[],
        )

        self.assertEqual(
            self._evaluate_target_score(talent),
            50.0,
        )

    def test_new_arch_terror_threat_matches_legacy_baseline(self):
        talent = SimpleNamespace(
            name=HOSHINO_TALENT_NAME,
            is_terror=True,
            self_doubt_pending=False,
            tactical_unlocked=False,
            ammo=[],
        )

        self.assertEqual(
            self._evaluate_target_score(talent),
            60.0,
        )

    def test_llm_aggression_is_not_added_uniformly_to_legacy_threat_scores(self):
        controller = BasicAIController()
        controller._llm_aggression_mod = 10.0
        controller._estimate_power = MethodType(lambda self, target: 100.0, controller)

        observer = _player("Observer", "p1")
        enemy_a = _player("EnemyA", "p2")
        enemy_b = _player("EnemyB", "p3")

        controller._update_threat_scores(observer, _state(observer, enemy_a, enemy_b))

        self.assertEqual(controller._threat_scores[enemy_a.name], 20.0)
        self.assertEqual(controller._threat_scores[enemy_b.name], 20.0)

    def test_llm_aggression_is_not_added_uniformly_to_threat_mind_scores(self):
        mind = ThreatMind()
        mind._query.estimate_power = lambda target: 100.0

        observer = _player("Observer", "p1")
        enemy_a = _player("EnemyA", "p2")
        enemy_b = _player("EnemyB", "p3")

        assessment = mind.assess(
            observer,
            _state(observer, enemy_a, enemy_b),
            strategy=None,
            llm_aggression_mod=10.0,
        )

        threat_scores = assessment.data["threat_scores"]
        self.assertEqual(threat_scores[enemy_a.name], 20.0)
        self.assertEqual(threat_scores[enemy_b.name], 20.0)

    def test_captain_strategy_does_not_bypass_builtin_critical_checks(self):
        cases = []

        captain = _player("Captain", "captain", is_captain=True)
        cases.append((
            "police_dispatched",
            captain,
            _state(captain),
            {"report_target": "captain", "report_phase": "dispatched"},
        ))

        captain = _player("Captain", "captain", is_captain=True)
        cases.append((
            "locked_by_multiple_players",
            captain,
            _state(
                captain,
                _player("Enemy1", "e1", locked_target="captain"),
                _player("Enemy2", "e2", locked_target="captain"),
            ),
            {},
        ))

        captain = _player("Captain", "captain", is_captain=True)
        cases.append((
            "anchored",
            captain,
            _state(
                captain,
                _player("Anchor", "anchor"),
                markers=_AnchorMarkers("captain", "anchor"),
            ),
            {},
        ))

        captain = _player("Captain", "captain", is_captain=True)
        cases.append((
            "burned_without_armor",
            captain,
            _state(
                captain,
                _player("Burner", "burner", talent=SimpleNamespace(burn_targets={"captain": 1})),
            ),
            {},
        ))

        for case_name, player, state, police_cache in cases:
            with self.subTest(case=case_name):
                self._assert_captain_critical_in_both_paths(player, state, police_cache)

    def test_captain_strategy_false_can_still_resolve_to_safe(self):
        captain = _player("Captain", "captain", is_captain=True)
        state = _state(captain)

        for path_name, evaluate in (
            ("legacy", self._legacy_captain_critical),
            ("threat_mind", self._mind_captain_critical),
        ):
            with self.subTest(path=path_name):
                result, strategy_called = evaluate(captain, state)
                self.assertTrue(strategy_called)
                self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
