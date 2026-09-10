"""T-AA..T-AC, T-AI..T-AJ, T-AL: the ranking arithmetic itself.

Every property here is about the score in isolation: bounded, finite, monotone in the input
that dominates it, blind to which way the market is pointing, and a total order.
"""

from __future__ import annotations

import math
from typing import Any

import opportunity_fixtures as fixtures
import pytest
from quant_engine.opportunity import (
    OpportunityCandidate,
    OpportunityEngine,
    median,
    persistence,
    rank_score,
    sort_key,
)
from quant_engine.opportunity.scoring import (
    MIN_SELECTION_SCORE,
    SUPPORT_FLOOR,
    breadth,
    ramp,
    support_for,
    support_multiplier,
)

STEPS = 21


def candidate(**changes: Any) -> OpportunityCandidate:
    """One candidate, ranked alone, so the score under test is not moved by a cohort."""
    engine = OpportunityEngine()
    result = engine.ingest(fixtures.ensemble(**changes), {1})
    assert result is not None
    return result.board.candidates[0]


# --- T-AA bounds -----------------------------------------------------------------------


def test_the_score_stays_inside_zero_and_one_across_the_whole_input_space() -> None:
    for step in range(STEPS):
        value = step / (STEPS - 1)
        for support in (0.0, value, 1.0):
            score = rank_score(value, support)
            assert 0.0 <= score <= 1.0
            assert math.isfinite(score)


def test_no_combination_of_extremes_produces_nan_or_infinity() -> None:
    for confidence in (0.0, 1.0):
        for agreement in (0.0, 1.0):
            for noise in (0.0, 1.0):
                for disagreement in (0.0, 1.0):
                    support = support_for(
                        agreement=agreement,
                        regime_confidence=confidence,
                        active_strategies=0,
                        direction_persistence3=0.0,
                        disagreement=disagreement,
                        noise_score=noise,
                        analysis_status="DEGRADED",
                    )
                    score = rank_score(confidence, support.score)
                    assert math.isfinite(score) and 0.0 <= score <= 1.0
                    assert 0.0 <= support.score <= 1.0


def test_the_support_multiplier_never_leaves_its_documented_band() -> None:
    # Phase 8 adjusts an ordering; it may not overturn Phase 7. A candidate with poor
    # ensemble confidence cannot reach the top of a board because one diagnostic looked good.
    for step in range(STEPS):
        multiplier = support_multiplier(step / (STEPS - 1))
        assert SUPPORT_FLOOR <= multiplier <= 1.0
    assert support_multiplier(-5.0) == SUPPORT_FLOOR
    assert support_multiplier(5.0) == 1.0


def test_ramp_and_breadth_are_bounded_and_flat_outside_their_named_points() -> None:
    assert ramp(0.5, 0.5, 0.5) == 0.0
    assert ramp(-1.0, 0.0, 1.0) == 0.0
    assert ramp(2.0, 0.0, 1.0) == 1.0
    assert breadth(0) == 0.0
    assert breadth(3) == 1.0
    assert breadth(6) == 1.0


# --- T-AB confidence monotonicity ------------------------------------------------------


def test_higher_ensemble_confidence_never_ranks_lower() -> None:
    # Confidence is the dominant input, so this is the property that makes the whole layer
    # honest: Phase 8 may reorder near-equals, never invert Phase 7's own reading.
    scores = [candidate(confidence=step / (STEPS - 1)).rankScore for step in range(STEPS)]
    assert scores == sorted(scores)
    assert scores[-1] > scores[0]


def test_two_candidates_differing_only_in_confidence_rank_in_confidence_order() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(slot=1, confidence=0.40), {1, 2})
    result = engine.ingest(fixtures.ensemble(slot=2, confidence=0.55), {1, 2})
    assert result is not None
    assert [item.slotId for item in result.board.candidates] == [2, 1]
    assert [item.rank for item in result.board.candidates] == [1, 2]


# --- T-AC up / down symmetry -----------------------------------------------------------


def test_mirrored_up_and_down_evidence_produces_the_same_rank_score() -> None:
    up = candidate(direction="UP")
    down = candidate(direction="DOWN")
    assert up.rankScore == down.rankScore
    assert up.supportScore == down.supportScore
    assert up.candidateStatus == down.candidateStatus


def test_a_stronger_down_setup_outranks_a_weaker_up_one() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(slot=1, direction="UP", confidence=0.60), {1, 2})
    result = engine.ingest(fixtures.ensemble(slot=2, direction="DOWN", confidence=0.80), {1, 2})
    assert result is not None
    board = result.board
    assert board.selectedSlotId == 2
    assert board.selectedDirection == "DOWN"
    assert [item.direction for item in board.candidates] == ["DOWN", "UP"]


def test_direction_is_never_read_by_the_scoring_module() -> None:
    # A stronger guarantee than a mirrored pair: the score cannot prefer a direction if it
    # never sees one. `persistence` compares directions to each other and to nothing else.
    assert persistence(["UP", "UP"], "UP") == persistence(["DOWN", "DOWN"], "DOWN")
    assert persistence(["UP", "DOWN"], "UP") == persistence(["DOWN", "UP"], "DOWN")


# --- T-AI direction persistence --------------------------------------------------------


def test_an_unbroken_run_is_full_persistence() -> None:
    assert persistence(["UP", "UP", "UP"], "UP") == 1.0
    assert persistence(["DOWN", "DOWN", "DOWN"], "DOWN") == 1.0


def test_one_reversal_in_three_is_two_thirds() -> None:
    assert persistence(["UP", "DOWN", "UP"], "UP") == pytest.approx(2 / 3)


def test_a_neutral_or_skip_is_not_counted_as_the_opposite_direction() -> None:
    # M1: an abstention reduces how much directional evidence the window holds. It is not a
    # DOWN merely because the current reading is UP.
    assert persistence(["UP", "NEUTRAL", "UP"], "UP") == 1.0
    assert persistence(["UP", "SKIP", "UP"], "UP") == 1.0
    assert persistence(["UP", "DOWN", "UP"], "UP") < 1.0


def test_a_signal_appearing_for_the_first_time_is_not_punished_for_having_no_past() -> None:
    # M3: a breakout is legitimately new. "Not present three bars ago" may not reject it.
    assert persistence(["UP"], "UP") == 1.0
    assert persistence([], "UP") == 0.0


# --- T-AJ confidence median ------------------------------------------------------------


def test_the_middle_value_is_reported_rather_than_the_mean() -> None:
    assert median([0.20, 0.90, 0.40]) == 0.40
    assert median([0.20, 0.90, 0.40]) != sum([0.20, 0.90, 0.40]) / 3


def test_the_median_is_defined_for_even_windows_and_for_none_at_all() -> None:
    assert median([0.2, 0.4]) == pytest.approx(0.3)
    assert median([0.5]) == 0.5
    assert median([]) == 0.0


def test_one_extreme_spike_cannot_move_the_stability_diagnostic_far() -> None:
    assert median([0.50, 0.52, 1.00]) == 0.52


# --- T-AL deterministic tie-break ------------------------------------------------------


def test_identical_candidates_are_broken_by_the_lower_slot_number() -> None:
    for order in ([2, 4], [4, 2]):
        engine = OpportunityEngine()
        board = None
        for slot in order:
            result = engine.ingest(fixtures.ensemble(slot=slot), {2, 4})
            assert result is not None
            board = result.board
        assert board is not None
        assert [item.slotId for item in board.candidates] == [2, 4]
        assert [item.rank for item in board.candidates] == [1, 2]
        assert board.candidates[0].rankScore == board.candidates[1].rankScore


def test_the_sort_key_orders_on_the_candidate_and_never_on_insertion() -> None:
    engine = OpportunityEngine()
    for slot in (4, 2):
        engine.ingest(fixtures.ensemble(slot=slot), {2, 4})
    board = engine.latest_board("capitalbear")
    assert board is not None
    keys = [sort_key(item) for item in board.candidates]
    assert keys == sorted(keys)


def test_each_tie_break_level_is_reachable() -> None:
    # Every level below rankScore must be able to decide an order, or it is decoration.
    high = fixtures.ensemble(slot=1, confidence=0.60, agreement=0.90, active=3)
    low = fixtures.ensemble(slot=2, confidence=0.60, agreement=0.50, active=3)
    engine = OpportunityEngine()
    engine.ingest(low, {1, 2})
    result = engine.ingest(high, {1, 2})
    assert result is not None
    assert [item.slotId for item in result.board.candidates] == [1, 2]


# --- semantics -------------------------------------------------------------------------


def test_the_selection_gate_is_reachable_by_real_phase_seven_confidence() -> None:
    # A gate nothing can clear would make every board NO_OPPORTUNITY and prove nothing.
    # A gate everything clears would not be a gate. Both ends are asserted here.
    assert candidate(confidence=0.60).candidateStatus == "ACTIONABLE"
    assert candidate(confidence=0.20).candidateStatus == "WATCH"
    assert 0.0 < MIN_SELECTION_SCORE < 0.6


def test_the_score_is_never_presented_as_a_probability() -> None:
    fields = set(candidate().model_dump())
    assert fields.isdisjoint({"winProbability", "winRate", "probability", "edge"})
    assert fields.isdisjoint({"expectedValue", "expectedReturn", "payout", "stake"})
    assert "rankScore" in fields and "ensembleConfidence" in fields
