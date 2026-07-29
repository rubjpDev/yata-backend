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

Intent = Literal["accumulation", "intensification", "peak", "deload", "general"]

# `(lift, set_type, reps, base_rpe)` written out literally, backoffs included.
_SetSpec = tuple[str, str, int, float]
# `(session_type, sets)`.
_SessionTemplate = tuple[str, tuple[_SetSpec, ...]]

# Self-authored: round-number set/rep/RPE targets per intent, written for this
# project; no published program (5/3/1, Sheiko, RTS, Juggernaut, ...) reproduced.
WEEK_TEMPLATES: Final[dict[Intent, tuple[_SessionTemplate, ...]]] = {
    "accumulation": (
        ("squat", (("squat", "working", 5, 7.0), ("squat", "backoff", 8, 6.5))),
        ("bench", (("bench", "working", 5, 7.0), ("bench", "backoff", 8, 6.5))),
        (
            "deadlift",
            (("deadlift", "working", 5, 7.0), ("deadlift", "backoff", 8, 6.5)),
        ),
        (
            "full",
            (
                ("squat", "working", 5, 6.0),
                ("bench", "working", 5, 6.0),
                ("deadlift", "working", 5, 6.0),
            ),
        ),
    ),
    "intensification": (
        ("squat", (("squat", "working", 3, 8.5), ("squat", "backoff", 5, 7.0))),
        ("bench", (("bench", "working", 3, 8.5), ("bench", "backoff", 5, 7.0))),
        (
            "deadlift",
            (("deadlift", "working", 3, 8.5), ("deadlift", "backoff", 5, 7.0)),
        ),
        (
            "full",
            (
                ("squat", "working", 3, 7.5),
                ("bench", "working", 3, 7.5),
                ("deadlift", "working", 3, 7.5),
            ),
        ),
    ),
    "peak": (
        ("squat", (("squat", "working", 2, 9.0), ("squat", "backoff", 4, 7.0))),
        ("bench", (("bench", "working", 2, 9.0), ("bench", "backoff", 4, 7.0))),
        (
            "deadlift",
            (("deadlift", "working", 2, 9.0), ("deadlift", "backoff", 4, 7.0)),
        ),
        (
            "full",
            (
                ("squat", "working", 2, 8.0),
                ("bench", "working", 2, 8.0),
                ("deadlift", "working", 2, 8.0),
            ),
        ),
    ),
    "deload": (
        ("squat", (("squat", "working", 5, 6.0),)),
        ("bench", (("bench", "working", 5, 6.0),)),
        ("deadlift", (("deadlift", "working", 5, 6.0),)),
        (
            "full",
            (
                ("squat", "working", 5, 6.0),
                ("bench", "working", 5, 6.0),
                ("deadlift", "working", 5, 6.0),
            ),
        ),
    ),
    "general": (
        ("squat", (("squat", "working", 5, 7.5), ("squat", "backoff", 8, 6.5))),
        ("bench", (("bench", "working", 5, 7.5), ("bench", "backoff", 8, 6.5))),
        (
            "deadlift",
            (("deadlift", "working", 5, 7.5), ("deadlift", "backoff", 8, 6.5)),
        ),
        (
            "full",
            (
                ("squat", "working", 5, 6.5),
                ("bench", "working", 5, 6.5),
                ("deadlift", "working", 5, 6.5),
            ),
        ),
    ),
}

# Self-authored spacing: one rest day between sessions, fits inside a 7-day
# week for the maximum 4 sessions (day offsets 0, 2, 4, 6).
_DAY_OFFSET_STEP: Final = 2

# Self-authored: +0.5 RPE/week keeps the weekly target on the RPE_TO_PCT 0.5
# grid; capped at 10.0 so no week ever asks for more than a true max effort.
RPE_WEEKLY_STEP: Final = 0.5
_MAX_RPE: Final = 10.0


@dataclass(frozen=True)
class PrescribedSet:
    """One set the engine has fully decided, ready for the route to persist."""

    lift: str
    set_order: int
    set_type: str
    reps: int
    intensity_type: str
    intensity: float
    weight_kg: float
    weight_mode: str


@dataclass(frozen=True)
class PrescribedSession:
    """One prescribed training day within a week."""

    day_offset: int
    session_type: str
    sets: tuple[PrescribedSet, ...]


def _week_rpe(base_rpe: float, week_index: int) -> float:
    """Apply the uniform weekly RPE progression, capped at 10.0."""
    return min(base_rpe + RPE_WEEKLY_STEP * (week_index - 1), _MAX_RPE)


def lifts_needed(intent: str, days: int) -> frozenset[str]:
    """Every lift the template requires for `days` sessions of `intent`.

    Lets the route resolve `e1rm_by_lift` before calling `prescribe_week`
    without reading `WEEK_TEMPLATES` itself (D-5): the template stays owned
    by the engine.
    """
    if intent not in WEEK_TEMPLATES:
        raise ValueError(f"unknown intent: {intent!r}")
    templates = WEEK_TEMPLATES[intent]
    if days < 1 or days > len(templates):
        raise ValueError(
            f"days must be between 1 and {len(templates)} for intent {intent!r}, "
            f"got {days}"
        )
    return frozenset(
        set_spec[0] for _, set_specs in templates[:days] for set_spec in set_specs
    )


def prescribe_week(
    intent: str,
    days: int,
    e1rm_by_lift: Mapping[str, float],
    week_index: int,
) -> tuple[PrescribedSession, ...]:
    """Prescribe one training week: every load/rep/RPE decision happens here.

    Returns every field the persistence layer needs (R18), so the caller does
    assignment only. Never extrapolates: raises `ValueError` naming the
    offending input instead (mirrors `load_for`).
    """
    if intent not in WEEK_TEMPLATES:
        raise ValueError(f"unknown intent: {intent!r}")
    templates = WEEK_TEMPLATES[intent]
    if days < 1 or days > len(templates):
        raise ValueError(
            f"days must be between 1 and {len(templates)} for intent {intent!r}, "
            f"got {days}"
        )
    if week_index < 1:
        raise ValueError(f"week_index must be at least 1, got {week_index}")

    sessions = []
    for index, (session_type, set_specs) in enumerate(templates[:days]):
        sets = []
        for order, (lift, set_type, reps, base_rpe) in enumerate(set_specs, start=1):
            one_rm = e1rm_by_lift.get(lift)
            if one_rm is None:
                raise ValueError(f"e1rm_by_lift is missing required lift: {lift!r}")
            rpe = _week_rpe(base_rpe, week_index)
            weight_kg = load_for(one_rm, reps, rpe)
            sets.append(
                PrescribedSet(
                    lift=lift,
                    set_order=order,
                    set_type=set_type,
                    reps=reps,
                    intensity_type="RPE",
                    intensity=rpe,
                    weight_kg=weight_kg,
                    weight_mode="fixed",
                )
            )
        sessions.append(
            PrescribedSession(
                day_offset=index * _DAY_OFFSET_STEP,
                session_type=session_type,
                sets=tuple(sets),
            )
        )
    return tuple(sessions)


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
