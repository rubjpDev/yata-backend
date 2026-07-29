"""Deterministic training engine: pure math, no I/O"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

Formula = Literal["epley", "brzycki"]

_REP_RANGE: Final = range(1, 13)  # 1..12 reps
_RPE_VALUES: Final = tuple(6.0 + 0.5 * step for step in range(9))  # 6.0..10.0

# Self-authored: each %1RM is Epley inverted with (10 - RPE) reps in reserve;
# no value is copied from any published (RTS/RP) chart.
RPE_TO_PCT: Final[dict[tuple[int, float], float]] = {
    (reps, rpe): round(30.0 / (30.0 + reps + (10.0 - rpe)), 4)
    for reps in _REP_RANGE
    for rpe in _RPE_VALUES
}

# Self-authored: 2.5 kg is the smallest jump with a standard pair of 1.25 kg plates.
DEFAULT_STEP_KG: Final = 2.5

# MEV (Minimum Effective Volume)
# MAV (Maximum Adaptive Volume)
# MRV (Maximum Recoverable Volume)
VolumeZone = Literal["below_MEV", "MEV_MAV", "MAV_MRV", "above_MRV"]

# Self-authored round-number landmarks (MEV, MAV, MRV) in weekly hard sets;
# conservative starting points, not copied from any published (RP) table.
VOLUME_LANDMARKS: Final[dict[str, tuple[int, int, int]]] = {
    "chest": (8, 14, 20),
    "back": (10, 16, 22),
    "quads": (8, 14, 20),
    "hamstrings": (6, 12, 16),
    "glutes": (6, 12, 18),
    "shoulders": (8, 14, 20),
    "triceps": (6, 10, 16),
    "biceps": (6, 10, 16),
}

# Self-authored deload thresholds: conservative starting points to tune
# against real training logs once they exist.
DELOAD_LOOKBACK_DAYS: Final = 28
DELOAD_STALL_SESSIONS: Final = 3
DELOAD_STALL_TOLERANCE: Final = 0.005  # e1RM gains under 0.5% count as flat
DELOAD_RPE_CREEP: Final = 1.0
DELOAD_MRV_WEEKS: Final = 2


@dataclass(frozen=True)
class SessionSummary:
    """Per-session signals the deload rule consumes."""

    session_date: date
    e1rm_kg: float
    top_set_weight_kg: float
    top_set_reps: int
    top_set_rpe: float
    week_volume_zone: VolumeZone


def estimate_1rm(weight_kg: float, reps: int, formula: Formula = "epley") -> float:
    """Estimate a one-rep max from a set of `reps` at `weight_kg`.

    At reps == 1 Brzycki returns the weight exactly, while Epley
    overshoots slightly (w * 31/30): a known property of the formula.
    """
    if weight_kg <= 0:
        raise ValueError("weight_kg must be positive")
    if reps < 1:
        raise ValueError("reps must be at least 1")
    if formula == "epley":
        return weight_kg * (1 + reps / 30)
    if formula == "brzycki":
        if reps >= 37:
            raise ValueError("reps must be less than 37 for Brzycki formula")
        return weight_kg * 36 / (37 - reps)
    raise ValueError(f"unknown formula: {formula!r}")


def e1rm_best_of_recent(
    top_sets: Sequence[tuple[float, int]], formula: Formula = "epley"
) -> float:
    """Best estimated 1RM across recent top sets given as (weight_kg, reps).

    A max() over Epley answers today's product question, an sklearn
    regression over the full history is deferred until real logs prove this
    insufficient."""

    if not top_sets:
        raise ValueError("top_sets must not be empty")
    return max(estimate_1rm(weight, reps, formula) for weight, reps in top_sets)


def load_for(
    one_rm_kg: float, reps: int, rpe: float, step_kg: float = DEFAULT_STEP_KG
) -> float:
    """Barbell load for a (reps, RPE) target, rounded to the plate step."""
    if one_rm_kg <= 0:
        raise ValueError("one_rm_kg must be positive")
    if step_kg <= 0:
        raise ValueError("step_kg must be positive")
    pct = RPE_TO_PCT.get((reps, rpe))
    if pct is None:
        raise ValueError(
            f"no RPE_TO_PCT entry for {reps} reps @ RPE {rpe}: table covers "
            "1-12 reps, RPE 6.0-10.0 in 0.5 steps; refusing to extrapolate"
        )
    return round(one_rm_kg * pct / step_kg) * step_kg


def estimated_rpe(weight_kg: float, reps: int, one_rm_kg: float) -> float:
    """Approximate RPE of a set: the inverse lookup of load_for."""
    if weight_kg <= 0 or one_rm_kg <= 0:
        raise ValueError("weight_kg and one_rm_kg must be positive")
    column = {rpe: pct for (r, rpe), pct in RPE_TO_PCT.items() if r == reps}
    if not column:
        raise ValueError(f"unsupported reps: {reps} (table covers 1-12)")
    actual = weight_kg / one_rm_kg
    if actual > max(column.values()) or actual < min(column.values()):
        raise ValueError(
            f"{weight_kg}kg x {reps} at 1RM {one_rm_kg}kg falls outside the "
            "RPE 6.0-10.0 table; refusing to extrapolate"
        )
    return min(column, key=lambda rpe: abs(column[rpe] - actual))


def _zone_for(hard_sets: int, landmarks: tuple[int, int, int]) -> VolumeZone:
    """Classify a weekly hard-set count against its (MEV, MAV, MRV) landmarks."""
    mev, mav, mrv = landmarks
    if hard_sets < mev:
        return "below_MEV"
    if hard_sets < mav:
        return "MEV_MAV"
    if hard_sets <= mrv:
        return "MAV_MRV"
    return "above_MRV"


def weekly_volume_status(
    hard_sets_by_muscle: Mapping[str, int],
) -> dict[str, VolumeZone]:
    """Classify each muscle group's weekly hard sets against its landmarks.

    A hard set is a work set taken to RPE >= 7 (about 3 reps in reserve or
    closer); warm-ups and technique sets do not count. Counting happens
    upstream: this function trusts the counts it receives.
    """
    statuses: dict[str, VolumeZone] = {}
    for muscle, hard_sets in hard_sets_by_muscle.items():
        landmarks = VOLUME_LANDMARKS.get(muscle)
        if landmarks is None:
            raise ValueError(f"unknown muscle group: {muscle!r}")
        if hard_sets < 0:
            raise ValueError(f"negative hard sets for {muscle!r}: {hard_sets}")
        statuses[muscle] = _zone_for(hard_sets, landmarks)
    return statuses


def should_deload(
    history: Sequence[SessionSummary], now: date
) -> tuple[bool, list[str]]:
    """Deterministic deload check over the recent lookback window.

    Returns (fired, reasons); fires when at least one signal is present.
    """
    cutoff = now - timedelta(days=DELOAD_LOOKBACK_DAYS)
    recent = sorted(
        (s for s in history if s.session_date >= cutoff),
        key=lambda s: s.session_date,
    )
    reasons: list[str] = []

    # Signal 1: e1RM flat or declining across the last N sessions.
    if len(recent) >= DELOAD_STALL_SESSIONS:
        window = recent[-DELOAD_STALL_SESSIONS:]
        baseline = window[0].e1rm_kg
        stalled = all(
            s.e1rm_kg <= baseline * (1 + DELOAD_STALL_TOLERANCE) for s in window[1:]
        )
        if stalled:
            reasons.append(
                f"e1RM flat or declining over last {DELOAD_STALL_SESSIONS} sessions"
            )
    # Signal 2: RPE creep at the same top-set load and reps.
    first_rpe: dict[tuple[float, int], float] = {}
    for s in recent:
        key = (s.top_set_weight_kg, s.top_set_reps)
        baseline_rpe = first_rpe.setdefault(key, s.top_set_rpe)
        if s.top_set_rpe - baseline_rpe >= DELOAD_RPE_CREEP:
            reasons.append(f"RPE creep >= {DELOAD_RPE_CREEP} at same load/reps")
            break
    # Signal 3: volume at or above MAV for several distinct training weeks.
    hot_weeks = {
        s.session_date.isocalendar()[:2]
        for s in recent
        if s.week_volume_zone in ("MAV_MRV", "above_MRV")
    }
    if len(hot_weeks) >= DELOAD_MRV_WEEKS:
        reasons.append(
            f"volume near MRV for {len(hot_weeks)} weeks "
            f"(threshold {DELOAD_MRV_WEEKS})"
        )

    return (bool(reasons), reasons)
