"""Unit tests for app.engine: pure math with fixed inputs and a fixed now."""

import ast
import inspect
from datetime import date

import pytest

import app.engine
from app.engine import (
    SessionSummary,
    VolumeZone,
    e1rm_best_of_recent,
    estimate_1rm,
    estimated_rpe,
    lifts_needed,
    load_for,
    prescribe_week,
    should_deload,
    weekly_volume_status,
)

FORBIDDEN_IMPORTS = {"fastapi", "httpx", "sqlalchemy", "redis", "app.db", "app.deps"}


NOW = date(2026, 7, 15)


def _session(
    day: date, e1rm: float, rpe: float = 8.0, zone: VolumeZone = "below_MEV"
) -> SessionSummary:
    """Session with fixed load/reps; each test varies only what it probes."""
    return SessionSummary(
        session_date=day,
        e1rm_kg=e1rm,
        top_set_weight_kg=150.0,
        top_set_reps=5,
        top_set_rpe=rpe,
        week_volume_zone=zone,
    )


def test_engine_imports_no_io_layer() -> None:
    """The engine must stay pure: no web, DB, or client imports."""
    tree = ast.parse(inspect.getsource(app.engine))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    offenders = {
        mod
        for mod in imported
        if any(mod == f or mod.startswith(f + ".") for f in FORBIDDEN_IMPORTS)
    }
    assert not offenders, f"engine imports I/O layers: {offenders}"


def test_epley_estimates_1rm() -> None:
    """Epley: 100 kg x 5 -> 100 * (1 + 5/30)."""
    assert estimate_1rm(100, 5, "epley") == pytest.approx(116.667, abs=0.001)


def test_brzycki_estimates_1rm() -> None:
    """Brzycki: 100 kg x 5 -> 100 * 36 / 32."""
    assert estimate_1rm(100, 5, "brzycki") == pytest.approx(112.5)


def test_brzycki_single_rep_is_exact_weight() -> None:
    """At reps == 1 Brzycki returns the weight itself, no estimation error."""
    assert estimate_1rm(100, 1, "brzycki") == 100.0


def test_epley_single_rep_overshoots() -> None:
    """At reps == 1 Epley overshoots by w/30: documented formula property."""
    assert estimate_1rm(100, 1, "epley") == pytest.approx(103.333, abs=0.001)


def test_estimate_1rm_rejects_bad_inputs() -> None:
    """reps < 1, weight <= 0, and brzycki reps >= 37 all raise ValueError."""
    with pytest.raises(ValueError):
        estimate_1rm(100, 0, "epley")
    with pytest.raises(ValueError):
        estimate_1rm(0, 5, "epley")
    with pytest.raises(ValueError):
        estimate_1rm(-80, 5, "brzycki")
    with pytest.raises(ValueError):
        estimate_1rm(100, 37, "brzycki")


def test_best_of_recent_picks_the_max_estimate() -> None:
    """The best e1RM wins, whichever set produced it."""
    sets = [(140.0, 3), (150.0, 1), (130.0, 5)]
    # Epley: 154.0, 155.0 and 151.67 -> the single at 150 wins.
    assert e1rm_best_of_recent(sets) == pytest.approx(155.0)


def test_best_of_recent_rejects_empty_history() -> None:
    """No sets -> ValueError, never a silent 0.0."""
    with pytest.raises(ValueError):
        e1rm_best_of_recent([])


def test_load_for_rounds_to_plate_step() -> None:
    """150 kg 1RM, 5 reps @ RPE 8 -> 81.08% = 121.62 -> nearest 2.5 = 122.5."""
    assert load_for(150, 5, 8.0) == pytest.approx(122.5)


def test_load_for_single_at_rpe_10() -> None:
    """1 @ 10 maps to ~96.8% (inherited Epley overshoot), 96.77 -> 97.5."""
    assert load_for(100, 1, 10.0) == pytest.approx(97.5)


def test_load_for_respects_custom_step() -> None:
    """A finer step lands closer to the raw percentage."""
    assert load_for(150, 5, 8.0, step_kg=1.25) == pytest.approx(121.25)


def test_load_for_rejects_out_of_table() -> None:
    """RPE below 6.0, above 10.0, off-grid, or reps > 12: clear error."""
    for reps, rpe in [(5, 5.0), (5, 10.5), (5, 8.25), (13, 8.0)]:
        with pytest.raises(ValueError):
            load_for(150, reps, rpe)


def test_estimated_rpe_is_the_approximate_inverse() -> None:
    """85 kg x 5 at 100 kg 1RM = 85% -> closest table row is RPE 9.5."""
    assert estimated_rpe(85, 5, 100) == pytest.approx(9.5)


def test_estimated_rpe_rejects_out_of_table_intensity() -> None:
    """Heavier than 1@10 or lighter than the RPE 6 row: no extrapolation."""
    with pytest.raises(ValueError):
        estimated_rpe(100, 5, 100)  # 100% x5 is beyond the table
    with pytest.raises(ValueError):
        estimated_rpe(50, 5, 100)  # 50% x5 is below RPE 6.0


def test_weekly_volume_status_classifies_each_zone() -> None:
    """One muscle group lands in each of the four zones."""
    status = weekly_volume_status({"chest": 5, "back": 12, "quads": 16, "biceps": 20})
    assert status == {
        "chest": "below_MEV",
        "back": "MEV_MAV",
        "quads": "MAV_MRV",
        "biceps": "above_MRV",
    }


def test_weekly_volume_status_rejects_bad_input() -> None:
    """Unknown muscle groups and negative counts raise ValueError."""
    with pytest.raises(ValueError):
        weekly_volume_status({"forearms": 10})
    with pytest.raises(ValueError):
        weekly_volume_status({"chest": -1})


def test_deload_fires_on_flat_e1rm() -> None:
    """Three sessions without >0.5% e1RM progress inside the window fire."""
    history = [
        _session(date(2026, 6, 25), 180.0),
        _session(date(2026, 7, 2), 179.0),
        _session(date(2026, 7, 9), 180.5),  # +0.28%: inside flat tolerance
    ]
    fired, reasons = should_deload(history, NOW)
    assert fired is True
    assert any("e1RM flat" in r for r in reasons)


def test_deload_does_not_fire_on_steady_progress() -> None:
    """Clear e1RM progress, steady RPE, quiet volume -> no deload."""
    history = [
        _session(date(2026, 6, 25), 180.0),
        _session(date(2026, 7, 2), 183.0),
        _session(date(2026, 7, 9), 186.0),
    ]
    assert should_deload(history, NOW) == (False, [])


def test_deload_ignores_sessions_outside_window() -> None:
    """A stalled block older than the lookback window is invisible to the rule."""
    history = [
        _session(date(2026, 5, 1), 180.0),
        _session(date(2026, 5, 8), 179.5),
        _session(date(2026, 5, 15), 180.0),
        _session(date(2026, 6, 25), 181.0),
        _session(date(2026, 7, 9), 185.0),
    ]
    assert should_deload(history, NOW) == (False, [])


def test_deload_fires_on_rpe_creep() -> None:
    """RPE rising >= 1.0 at the same load/reps fires even while e1RM climbs."""
    history = [
        _session(date(2026, 6, 25), 180.0, rpe=7.5),
        _session(date(2026, 7, 2), 184.0, rpe=8.0),
        _session(date(2026, 7, 9), 188.0, rpe=9.0),  # +1.5 vs first sighting
    ]
    fired, reasons = should_deload(history, NOW)
    assert fired is True
    assert any("RPE creep" in r for r in reasons)


def test_deload_fires_on_weeks_near_mrv() -> None:
    """Two distinct ISO weeks at/above MAV reach the MRV-weeks threshold."""
    history = [
        _session(date(2026, 7, 2), 180.0, zone="above_MRV"),
        _session(date(2026, 7, 9), 184.0, zone="above_MRV"),
    ]
    fired, reasons = should_deload(history, NOW)
    assert fired is True
    assert any("MRV" in r for r in reasons)


def test_deload_accumulates_multiple_reasons() -> None:
    """Independent signals stack: flat e1RM + hot volume -> two reasons."""
    history = [
        _session(date(2026, 6, 25), 180.0, zone="above_MRV"),
        _session(date(2026, 7, 2), 179.0, zone="above_MRV"),
        _session(date(2026, 7, 9), 180.5, zone="above_MRV"),
    ]
    fired, reasons = should_deload(history, NOW)
    assert fired is True
    assert len(reasons) == 2


_E1RM_BY_LIFT = {"squat": 150.0, "bench": 100.0, "deadlift": 180.0}


def test_prescribe_week_accumulation_happy_path() -> None:
    """4 days of accumulation: squat/bench/deadlift/full, day_offset 0/2/4/6."""
    sessions = prescribe_week("accumulation", 4, _E1RM_BY_LIFT, week_index=1)
    assert [s.session_type for s in sessions] == ["squat", "bench", "deadlift", "full"]
    assert [s.day_offset for s in sessions] == [0, 2, 4, 6]

    squat_session = sessions[0]
    assert [s.set_type for s in squat_session.sets] == ["working", "backoff"]
    working, backoff = squat_session.sets
    assert working.reps == 5
    assert working.intensity == 7.0
    assert working.weight_kg == load_for(150.0, 5, 7.0)
    assert backoff.reps == 8
    assert backoff.intensity == 6.5
    assert backoff.weight_kg == load_for(150.0, 8, 6.5)

    full_session = sessions[3]
    assert [s.lift for s in full_session.sets] == ["squat", "bench", "deadlift"]
    assert all(s.set_type == "working" for s in full_session.sets)


def test_prescribe_week_intensification_happy_path() -> None:
    """Intensification: 4x3 @ 8.5 working, 2x5 @ 7.0 backoff."""
    sessions = prescribe_week("intensification", 1, _E1RM_BY_LIFT, week_index=1)
    working, backoff = sessions[0].sets
    assert (working.reps, working.intensity) == (3, 8.5)
    assert (backoff.reps, backoff.intensity) == (5, 7.0)


def test_prescribe_week_peak_happy_path() -> None:
    """Peak: 3x2 @ 9.0 working, 1x4 @ 7.0 backoff."""
    sessions = prescribe_week("peak", 1, _E1RM_BY_LIFT, week_index=1)
    working, backoff = sessions[0].sets
    assert (working.reps, working.intensity) == (2, 9.0)
    assert (backoff.reps, backoff.intensity) == (4, 7.0)


def test_prescribe_week_deload_happy_path() -> None:
    """Deload: 2x5 @ 6.0 working only, no backoff."""
    sessions = prescribe_week("deload", 1, _E1RM_BY_LIFT, week_index=1)
    assert len(sessions[0].sets) == 1
    working = sessions[0].sets[0]
    assert (working.reps, working.intensity) == (5, 6.0)


def test_prescribe_week_general_happy_path() -> None:
    """General: 3x5 @ 7.5 working, 2x8 @ 6.5 backoff."""
    sessions = prescribe_week("general", 1, _E1RM_BY_LIFT, week_index=1)
    working, backoff = sessions[0].sets
    assert (working.reps, working.intensity) == (5, 7.5)
    assert (backoff.reps, backoff.intensity) == (8, 6.5)


def test_prescribe_week_progresses_rpe_by_week() -> None:
    """Week 2 adds +0.5 RPE to every base RPE; the weight follows load_for."""
    week1 = prescribe_week("accumulation", 1, _E1RM_BY_LIFT, week_index=1)
    week2 = prescribe_week("accumulation", 1, _E1RM_BY_LIFT, week_index=2)
    assert week2[0].sets[0].intensity == week1[0].sets[0].intensity + 0.5
    assert week2[0].sets[0].weight_kg == load_for(150.0, 5, 7.5)


def test_prescribe_week_caps_rpe_at_ten() -> None:
    """A late week_index never asks for more than RPE 10.0."""
    far_week = prescribe_week("peak", 1, _E1RM_BY_LIFT, week_index=20)
    assert far_week[0].sets[0].intensity == 10.0


def test_prescribe_week_rejects_unknown_intent() -> None:
    """An unsupported intent names itself in the error."""
    with pytest.raises(ValueError, match="unknown intent"):
        prescribe_week("strength", 1, _E1RM_BY_LIFT, week_index=1)


def test_prescribe_week_rejects_out_of_range_days() -> None:
    """days below 1 or above the template's session count both raise."""
    with pytest.raises(ValueError, match="days"):
        prescribe_week("accumulation", 0, _E1RM_BY_LIFT, week_index=1)
    with pytest.raises(ValueError, match="days"):
        prescribe_week("accumulation", 5, _E1RM_BY_LIFT, week_index=1)


def test_prescribe_week_rejects_bad_week_index() -> None:
    """week_index below 1 raises."""
    with pytest.raises(ValueError, match="week_index"):
        prescribe_week("accumulation", 1, _E1RM_BY_LIFT, week_index=0)


def test_prescribe_week_rejects_missing_lift() -> None:
    """A lift the template needs but the caller did not supply names itself."""
    with pytest.raises(ValueError, match="bench"):
        prescribe_week("accumulation", 4, {"squat": 150.0, "deadlift": 180.0}, 1)


def test_lifts_needed_grows_with_days() -> None:
    """1 day needs only squat; 4 days need all three main lifts."""
    assert lifts_needed("accumulation", 1) == {"squat"}
    assert lifts_needed("accumulation", 4) == {"squat", "bench", "deadlift"}


def test_lifts_needed_rejects_bad_input() -> None:
    """Unknown intent and out-of-range days raise, same as prescribe_week."""
    with pytest.raises(ValueError, match="unknown intent"):
        lifts_needed("strength", 1)
    with pytest.raises(ValueError, match="days"):
        lifts_needed("accumulation", 5)
