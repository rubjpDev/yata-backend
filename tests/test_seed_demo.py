"""Offline tests for scripts/seed_demo.py's pure helpers (R42-R47, no network)."""

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.seed_demo import (
    SeedError,
    load_state,
    open_run_id_from_conflict,
    save_state,
    session_plan,
    verify_session,
)


def test_session_plan_matches_the_engine_templates() -> None:
    """Every (date, session_type) mirrors what POST /v1/blocks will persist."""
    start = date(2026, 1, 5)
    plan = session_plan("accumulation", 4, start)

    assert plan == [
        (date(2026, 1, 5), "squat"),
        (date(2026, 1, 7), "bench"),
        (date(2026, 1, 9), "deadlift"),
        (date(2026, 1, 11), "full"),
    ]


def test_session_plan_shrinks_with_fewer_days() -> None:
    """`days=1` only ever produces the first (squat) session."""
    plan = session_plan("general", 1, date(2026, 1, 5))
    assert plan == [(date(2026, 1, 5), "squat")]


def test_verify_session_accepts_a_matching_session() -> None:
    """No exception WHEN date and session_type both match."""
    verify_session(
        {"id": 1, "date": "2026-01-05", "session_type": "squat"},
        (date(2026, 1, 5), "squat"),
    )


def test_verify_session_rejects_a_wrong_date() -> None:
    """SeedError WHEN the session's date does not match, not a silent pass."""
    with pytest.raises(SeedError):
        verify_session(
            {"id": 1, "date": "2026-01-06", "session_type": "squat"},
            (date(2026, 1, 5), "squat"),
        )


def test_verify_session_rejects_a_wrong_session_type() -> None:
    """SeedError WHEN the session_type does not match, not a silent pass."""
    with pytest.raises(SeedError):
        verify_session(
            {"id": 1, "date": "2026-01-05", "session_type": "bench"},
            (date(2026, 1, 5), "squat"),
        )


def test_open_run_id_from_conflict_parses_the_409_detail() -> None:
    """The 409 detail `"block {id} already has an open run: {run_id}"` yields run_id."""
    assert open_run_id_from_conflict("block 4 already has an open run: 17") == 17


def test_open_run_id_from_conflict_refuses_an_unparseable_detail() -> None:
    """A detail with no trailing integer raises rather than guessing a run id."""
    with pytest.raises(SeedError):
        open_run_id_from_conflict("something unexpected")


def test_state_round_trips_through_disk(tmp_path: Path) -> None:
    """save_state then load_state returns exactly what was saved."""
    path = tmp_path / "state.json"
    save_state(path, {"block_id": 3, "run_id": 9})

    assert load_state(path) == {"block_id": 3, "run_id": 9}


def test_load_state_returns_empty_mapping_when_file_is_missing(tmp_path: Path) -> None:
    """A first-ever run has no state file yet; that is not an error."""
    assert load_state(tmp_path / "does-not-exist.json") == {}


def test_save_state_writes_valid_json(tmp_path: Path) -> None:
    """The persisted file is plain JSON, readable without this module."""
    path = tmp_path / "state.json"
    save_state(path, {"block_id": 1, "run_id": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"block_id": 1, "run_id": 2}
