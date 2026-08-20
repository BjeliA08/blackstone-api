"""Rules governing partial fallback coverage.

These are pure functions, so they run without a database.
    python -m pytest tests/test_partial_coverage.py -q
"""
import sys
from datetime import time

sys.path.insert(0, ".")

from app.business import (Candidate, covered_window, narrows_shift,
                          remainder_after_partial, select_candidates,
                          shift_window_minutes, shifts_are_consecutive)

MORNING = (time(7, 0), time(15, 0))
EVENING = (time(15, 0), time(23, 0))
OVERNIGHT = (time(23, 0), time(7, 0))
PARKADE = (time(6, 0), time(16, 30))


# ── Shift windows, including the midnight wrap ────────────────────────────────

def test_overnight_window_extends_past_midnight():
    assert shift_window_minutes(*OVERNIGHT) == (1380, 1860)  # 2300 -> 0700 next day


def test_day_shift_window_is_plain():
    assert shift_window_minutes(*MORNING) == (420, 900)


# ── Consecutiveness ───────────────────────────────────────────────────────────

def test_morning_and_evening_are_consecutive():
    assert shifts_are_consecutive(MORNING[1], EVENING[0])


def test_evening_and_overnight_are_consecutive_across_midnight():
    assert shifts_are_consecutive(EVENING[1], OVERNIGHT[0])


def test_parkade_is_not_consecutive_with_evening():
    assert not shifts_are_consecutive(PARKADE[1], EVENING[0])


# ── Does the operator's limit narrow the shift? ───────────────────────────────

def test_leaving_at_1900_narrows_the_evening_shift():
    assert narrows_shift(*EVENING, None, time(19, 0))


def test_working_to_the_scheduled_end_is_not_narrowing():
    assert not narrows_shift(*EVENING, None, time(23, 0))


def test_no_limits_is_not_narrowing():
    assert not narrows_shift(*EVENING, None, None)


def test_starting_late_also_narrows():
    assert narrows_shift(*EVENING, time(17, 0), None)


def test_leaving_at_0300_narrows_the_overnight_shift():
    # 0300 is inside a 2300-0700 shift, not before it.
    assert narrows_shift(*OVERNIGHT, None, time(3, 0))


# ── The absolute priority rule ────────────────────────────────────────────────

def _full(name):
    return Candidate(operator_id=name, operator_name=name, coverage_type="full")


def _partial(name, latest_end=time(19, 0)):
    return Candidate(operator_id=name, operator_name=name,
                     coverage_type="partial_fallback", latest_end=latest_end)


def test_partial_is_excluded_whenever_any_full_candidate_exists():
    picked = select_candidates([_partial("Partial"), _full("Full")])
    assert [c.operator_name for c in picked] == ["Full"]


def test_partial_is_excluded_even_against_a_single_full_candidate_listed_last():
    picked = select_candidates([_partial("A"), _partial("B"), _full("Z")])
    assert [c.operator_name for c in picked] == ["Z"]


def test_partial_is_used_only_when_no_full_candidate_exists():
    picked = select_candidates([_partial("A"), _partial("B")])
    assert {c.operator_name for c in picked} == {"A", "B"}


def test_empty_pool_yields_nothing():
    assert select_candidates([]) == []


# ── The remainder rule — the one that stops silent coverage gaps ──────────────

def test_partial_evening_leaves_the_tail_uncovered():
    # Operator works 1500-1900 of a 1500-2300 Evening shift.
    assert remainder_after_partial(*EVENING, time(15, 0), time(19, 0)) == (time(19, 0), time(23, 0))


def test_full_coverage_leaves_no_remainder():
    assert remainder_after_partial(*EVENING, time(15, 0), time(23, 0)) is None


def test_overnight_partial_remainder_wraps_correctly():
    # Works 2300-0300 of a 2300-0700 shift; the gap is 0300-0700.
    assert remainder_after_partial(*OVERNIGHT, time(23, 0), time(3, 0)) == (time(3, 0), time(7, 0))


def test_covered_window_respects_both_bounds():
    lo, hi = covered_window(*EVENING, time(16, 0), time(19, 0))
    assert (lo, hi) == (960, 1140)  # 1600 -> 1900


def test_covered_window_defaults_to_the_whole_shift():
    assert covered_window(*EVENING, None, None) == shift_window_minutes(*EVENING)
