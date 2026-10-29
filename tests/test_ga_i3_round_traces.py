"""I3真实R0～R4多轮三章路线和世界法则组合，不手填章节/行动结果。"""

from collections.abc import Mapping
from typing import Any

from engine.ga.contracts import ActionRequest, Performance, TurnStage
from engine.ga.runtime import run_round
from tests.ga_i2_helpers import Decisions, execute, ready, turn
from tests.test_ga_g7 import Steps
from tests.test_ga_i2_round_traces import choose


def test_g2_three_chapters_from_actual_a_keys_and_first_dusk_slot() -> None:
    state = ready({"a": "G2"})

    class Singer(Decisions):
        def choose(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
                   stage: TurnStage) -> ActionRequest | None:
            if stage is TurnStage.T0:
                if view["round"] == 2:
                    return choose(actions, "g2_a", mode=Performance.INSTANT)
                return None
            if view["round"] == 2:
                return choose(actions, "move", location="magic")
            return choose(actions, "forfeit")

    state.ga_controllers["a"] = Singer()
    for _ in range(10):
        run_round(state, state.ga_controllers)
    assert state.current_round == state.world_tick == 11
    assert state.ga_roles["a"].form == "rain"
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
    starts = [x for x in state.ga_trace if x["label"] == "slot_start"]
    assert len(starts) == 30 and all(x["kind"] == "standard" for x in starts)


def test_g3_three_chapters_through_real_guard_r0_magic_public_chain_and_collapse() -> None:
    state = ready({"a": "G3"})

    class Projection(Decisions):
        def choose(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
                   stage: TurnStage) -> ActionRequest | None:
            rnd = view["round"]
            if stage is TurnStage.T0:
                return choose(actions, "g3_trace", kind="guard") if rnd == 2 else None
            if rnd == 2:
                return choose(actions, "attack", target="b", weapon="knife")
            if rnd == 3:
                return choose(actions, "g3_domain", target="b", mode=Performance.PUBLIC)
            if rnd == 4:
                return choose(actions, "g3_spiral", target="b", mode=Performance.NONE)
            return choose(actions, "forfeit")

    class Harass(Decisions):
        def choose(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
                   stage: TurnStage) -> ActionRequest | None:
            if stage is TurnStage.T0:
                return None
            if view["round"] == 2:
                return choose(actions, "attack", target="a", weapon="knife")
            return choose(actions, "forfeit")

    state.ga_controllers["a"] = Projection(projection_chain=["b", "b"],
                                           projection_finish=["collapse"])
    state.ga_controllers["b"] = Harass()
    for _ in range(3):
        run_round(state, state.ga_controllers)
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
    assert state.ga_roles["a"].realm is None
    assert sum(state.players["a"].resources.values()) == 0
    assert len([x for x in state.ga_trace if x["label"] == "slot_start"]) == 9


def test_g5_three_chapters_can_stay_stage2_three_distinct_rounds_of_accepted_poems() -> None:
    state = ready({"a": "G5", "c": "T6"})

    class Writer(Decisions):
        def choose(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
                   stage: TurnStage) -> ActionRequest | None:
            rnd = view["round"]
            if stage is TurnStage.T0:
                return choose(actions, "g5_stage2" if rnd == 6 else "g5_write")
            # 讲故事不返还关注 SP，第 5 轮付不起第二次，改为攻击。
            if rnd in (2, 4, 5):
                return choose(actions, "attack", target="b", weapon="knife")
            if rnd == 3:
                return choose(actions, "g5_story", target="b", mode=Performance.MAJOR_INSTANT)
            return choose(actions, "g5_poem", target="c", mode=Performance.MAJOR_INSTANT,
                          paid_sp=0 if rnd == 7 else 1)

    state.ga_controllers["a"] = Writer()
    state.ga_controllers["c"] = Decisions(poem_offer=["accept"] * 3, poem_law=["wall"] * 3)
    for _ in range(7):
        run_round(state, state.ga_controllers)
    role = state.ga_roles["a"]
    assert role.stage == 2 and role.poetry_rounds == {6, 7, 8}
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
    assert not state.ga_clock_paused and state.ga_police.captain is None


def test_g6_three_chapters_real_sp_feedback_and_four_actual_recipient_t0_openings() -> None:
    state = ready({"a": "G6"})

    class Joker(Decisions):
        def choose(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
                   stage: TurnStage) -> ActionRequest | None:
            rnd = view["round"]
            if stage is TurnStage.T0:
                return choose(actions, "g6_counter") if rnd == 2 else None
            if rnd == 2:
                return choose(actions, "g6_borrow", target="c", mode=Performance.INSTANT,
                              victim="b", weapon="knife", source="borrow")
            if rnd in (4, 6):
                return choose(actions, "g6_gifts", mode=Performance.PUBLIC)
            return next((a for a in actions if a.verb.value == "attack" and
                         a.parameters.get("weapon") == "bow"), choose(actions, "forfeit"))

    class Opener(Decisions):
        def choose(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
                   stage: TurnStage) -> ActionRequest | None:
            if stage is TurnStage.T0:
                return next((a for a in actions if a.operation == "g6_open_box"), None)
            return choose(actions, "forfeit")

    state.ga_controllers["a"] = Joker()
    state.ga_controllers["b"] = Opener()
    state.ga_controllers["c"] = Opener()
    for _ in range(7):
        run_round(state, state.ga_controllers)
    assert state.ga_roles["a"].boxes_opened == 4
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
    assert not state.ga_gifts


def test_g7_three_chapters_real_purchases_macro_active_block_then_ex() -> None:
    state = ready({"a": "G7"})
    state.players["a"].credits = 30

    class Hoshino(Steps):
        def choose(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
                   stage: TurnStage) -> ActionRequest | None:
            if stage is TurnStage.T0:
                return None
            rnd = view["round"]
            if rnd == 2:
                return choose(actions, "move", location="military")
            if rnd == 3:
                return choose(actions, "buy_armor", kind="at_field")
            if rnd in (4, 5):
                weapon = "electromagnetic_rifle" if rnd == 4 else "gauss_rifle"
                return choose(actions, "buy_weapon", weapon=weapon)
            if rnd == 6:
                return choose(actions, "g7_macro")
            if rnd == 8:
                return choose(actions, "g7_ex", target="b", mode=Performance.PUBLIC,
                              location="military")
            return choose(actions, "forfeit")

    class Harass(Decisions):
        def choose(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
                   stage: TurnStage) -> ActionRequest | None:
            if stage is TurnStage.T0:
                return None
            rnd = view["round"]
            if rnd == 2:
                return choose(actions, "move", location="military")
            if rnd in (6, 7):
                return choose(actions, "attack", target="a", weapon="knife")
            return choose(actions, "forfeit")

    state.ga_controllers["a"] = Hoshino(["g7_macro_shield"])
    state.ga_controllers["b"] = Harass()
    for _ in range(7):
        run_round(state, state.ga_controllers)
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
    assert not state.ga_roles["a"].magazine
    assert not state.ga_extra_used


def test_actual_reversal_pauses_reactor_clock_but_not_g1_absolute_countdown() -> None:
    state = ready({"a": "G5", "b": "G1"})
    singer = state.ga_roles["a"]
    singer.stage, singer.pointer = 3, 8
    g1 = state.ga_roles["b"]
    g1.form, g1.full_opened = "full", True
    state.players["b"].hp = 1
    turn(state, "c")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert g1.form == "propagation"
    state.ga_clock_accelerated = True
    state.ga_world.begin_reversal(state, "a")
    for _ in range(3):
        run_round(state, state.ga_controllers)
    assert state.world_tick == 1 and state.players["b"].absolute_dead
    assert state.ga_clock_accelerated and state.ga_clock_paused
