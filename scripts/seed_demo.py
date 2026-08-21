"""Seed a demo athlete, idempotently, driving only the public HTTP API (R42-R47).

Run as `python -m scripts.seed_demo --base-url https://<host>` from the repo
root. Registers (or logs in, R45), logs a training history, and leaves
exactly one coach run `awaiting_gate` (R46) so a visitor sees the coach gate
working in under a minute, without registering and without six weeks of real
training.

No database access, no duplicated route logic (D-7): every write goes
through `POST /v1/auth/register`, `POST /v1/blocks`,
`PATCH /v1/sets/{id}/execution` and `POST /v1/coach/runs`, exactly as a real
athlete's client would. A successful run therefore doubles as an end-to-end
smoke test of auth, the engine, persistence, retrieval, Bedrock and the gate.

R44's structural finding, recorded here because it drove this script's
shape. `app.history.resolve_e1rm` requires at least
`MIN_TOP_SETS_FOR_E1RM` (3) executed **working** sets per lift, with no seed
fallback (`app/graph.py::_gather_context` resolves all three main lifts
before the graph can run at all). But every `app.engine.WEEK_TEMPLATES`
intent contributes at most **two** working sets per lift in one week — one in
the lift's own session, one in the "full" session — verified by reading every
template in `app/engine.py`. **One block is therefore structurally
insufficient**, for any choice of `intent`/`days`, given the engine as it
stands (out of scope for this feature, R6). This script logs **two** blocks:
the first is the one the coach run and the visitor see; the second exists
solely to add a second week's worth of history and is never surfaced. A
re-run creates neither again. Ceiling: a third `WEEK_TEMPLATES` session, or a
route that returns more than one week, the day this needs to be a single
block for real.

A second, related finding: the public API has no endpoint to list a block's
sessions or an athlete's blocks, so a script confined to the public API (R43)
cannot rediscover its own IDs after the process exits. This script persists
`block_id`/`run_id` in a small local JSON file (`--state-file`, ponytail: a
file, not a database) so a second invocation can find them without guessing;
`--block-id`/`--run-id` are the escape hatch if that file is ever missing.
Ceiling: a `GET /v1/blocks` listing endpoint the day the seed needs to run
from a different machine every time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app import engine

_DISCIPLINE = "powerlifting"
_PLANNED_WEEKS = 4
_SEED_1RM_KG: dict[str, float] = {"squat": 140.0, "bench": 100.0, "deadlift": 170.0}
_HISTORY_INTENT = "accumulation"
_EXTRA_HISTORY_INTENT = "general"
_HISTORY_DAYS = 4
# How many extra session ids to scan past our best guess before giving up
# (D-7's "refuse rather than guess wrong" applied to a small allocation gap).
_SESSION_ID_SCAN_WINDOW = 6
_DEFAULT_STATE_FILE = Path.home() / ".yata_seed_demo_state.json"


class SeedError(Exception):
    """The seed cannot proceed safely; refuse rather than guess (D-7)."""


# ---------------------------------------------------------------------------
# Pure helpers — no I/O, offline-testable
# ---------------------------------------------------------------------------


def session_plan(intent: str, days: int, start_date: date) -> list[tuple[date, str]]:
    """The (date, session_type) of every session `POST /v1/blocks` will create.

    Computed via `app.engine.prescribe_week` — the exact function the route
    itself calls — so no day-offset arithmetic is duplicated here. Placeholder
    e1RMs are enough: day offsets and session types never depend on the 1RM
    value, only the prescribed weight does.
    """
    placeholder = dict.fromkeys(engine.lifts_needed(intent, days), 100.0)
    prescribed = engine.prescribe_week(intent, days, placeholder, week_index=1)
    return [
        (start_date + timedelta(days=session.day_offset), session.session_type)
        for session in prescribed
    ]


def verify_session(session: dict[str, Any], expected: tuple[date, str]) -> None:
    """Raise `SeedError` unless `session` matches the expected (date, type)."""
    expected_date, expected_type = expected
    actual_date = date.fromisoformat(session["date"])
    if actual_date != expected_date or session["session_type"] != expected_type:
        raise SeedError(
            f"session {session.get('id')} does not match the expected shape: "
            f"expected {expected_type!r} on {expected_date}, got "
            f"{session['session_type']!r} on {actual_date}"
        )


def load_state(path: Path) -> dict[str, int]:
    """The previously-recorded `block_id`/`run_id`, or an empty mapping."""
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8")))


def save_state(path: Path, state: dict[str, int]) -> None:
    """Persist `block_id`/`run_id` so a later run can find them (module docstring)."""
    path.write_text(json.dumps(state), encoding="utf-8")


def open_run_id_from_conflict(detail: str) -> int:
    """Parse the run id out of the 409 `"...already has an open run: {id}"` detail."""
    try:
        return int(detail.rsplit(":", 1)[-1].strip())
    except ValueError as error:
        raise SeedError(f"could not parse an open-run id out of: {detail!r}") from error


# ---------------------------------------------------------------------------
# HTTP-driving helpers
# ---------------------------------------------------------------------------


async def register_or_login(
    client: httpx.AsyncClient, *, email: str, password: str, display_name: str
) -> tuple[str, bool]:
    """Register the demo athlete, or log in if it already exists (R45).

    Returns `(access_token, created_new)`.
    """
    payload = {
        "email": email,
        "password": password,
        "display_name": display_name,
        "discipline": _DISCIPLINE,
    }
    register_response = await client.post("/v1/auth/register", json=payload)
    if register_response.status_code not in (201, 409):
        raise SeedError(
            f"register failed: {register_response.status_code} {register_response.text}"
        )
    created_new = register_response.status_code == 201

    login_response = await client.post(
        "/v1/auth/login", json={"email": email, "password": password}
    )
    if login_response.status_code != 200:
        raise SeedError(
            f"login failed: {login_response.status_code} {login_response.text}"
        )
    return login_response.json()["access_token"], created_new


async def _find_session_by_id(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    start_id: int,
    expected: tuple[date, str],
    window: int = _SESSION_ID_SCAN_WINDOW,
) -> dict[str, Any]:
    """Locate one session by scanning ids from `start_id`, verifying every hit.

    `POST /v1/blocks` allocates its sessions in one consecutive insert loop
    with nothing else writing in between (true right after we ourselves just
    created the block), so `start_id` is a strong guess, not a random one —
    but it is still only a guess, so every candidate is checked against
    `expected` before being trusted, and a miss within `window` raises rather
    than silently patching the wrong session.
    """
    for candidate_id in range(start_id, start_id + window):
        response = await client.get(f"/v1/sessions/{candidate_id}", headers=headers)
        if response.status_code != 200:
            continue
        session: dict[str, Any] = response.json()
        if date.fromisoformat(session["date"]) == expected[0] and (
            session["session_type"] == expected[1]
        ):
            return session
    raise SeedError(
        f"could not find session {expected[1]!r} on {expected[0]} scanning ids "
        f"{start_id}..{start_id + window - 1}"
    )


async def _mark_working_sets_executed(
    client: httpx.AsyncClient, headers: dict[str, str], sessions: list[dict[str, Any]]
) -> None:
    """PATCH every `working` set in `sessions` as executed exactly as prescribed."""
    completed_at = datetime.now(UTC).isoformat()
    for session in sessions:
        for set_row in session["sets"]:
            if set_row["set_type"] != "working":
                continue
            response = await client.patch(
                f"/v1/sets/{set_row['id']}/execution",
                headers=headers,
                json={
                    "executed_weight_kg": set_row["prescribed_weight_kg"],
                    "executed_reps": set_row["prescribed_reps"],
                    "executed_intensity": set_row["prescribed_intensity"],
                    "completed_at": completed_at,
                },
            )
            if response.status_code != 200:
                raise SeedError(
                    f"PATCH /v1/sets/{set_row['id']}/execution failed: "
                    f"{response.status_code} {response.text}"
                )


async def log_block_history(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    intent: str,
    days: int,
    start_date: date,
    first_session_id_hint: int | None,
) -> tuple[int, int]:
    """Create one block, discover its sessions, execute every working set.

    Returns `(block_id, last_session_id)`. WHEN `first_session_id_hint` is
    `None` the block's first session is expected to land on `date.today()`
    and is fetched through `GET /v1/sessions/today`; otherwise the first
    session id is guessed starting at the hint (see `_find_session_by_id`).
    """
    create_response = await client.post(
        "/v1/blocks",
        headers=headers,
        json={
            "intent": intent,
            "planned_weeks": _PLANNED_WEEKS,
            "start_date": start_date.isoformat(),
            "days": days,
            "seed_1rm_kg": _SEED_1RM_KG,
        },
    )
    if create_response.status_code != 201:
        raise SeedError(
            f"POST /v1/blocks failed: {create_response.status_code} "
            f"{create_response.text}"
        )
    block_id: int = create_response.json()["id"]

    plan = session_plan(intent, days, start_date)
    sessions: list[dict[str, Any]] = []
    if first_session_id_hint is None:
        today_response = await client.get("/v1/sessions/today", headers=headers)
        if today_response.status_code != 200:
            raise SeedError(
                f"GET /v1/sessions/today failed: "
                f"{today_response.status_code} {today_response.text}"
            )
        first_session = today_response.json()
        verify_session(first_session, plan[0])
        sessions.append(first_session)
        next_id = first_session["id"] + 1
    else:
        next_id = first_session_id_hint

    for expected in plan[len(sessions) :]:
        session = await _find_session_by_id(
            client, headers, start_id=next_id, expected=expected
        )
        sessions.append(session)
        next_id = session["id"] + 1

    await _mark_working_sets_executed(client, headers, sessions)
    return block_id, sessions[-1]["id"]


async def open_or_reuse_run(
    client: httpx.AsyncClient, headers: dict[str, str], block_id: int
) -> int:
    """Open a coach run for `block_id`, or reuse the one already open (R46)."""
    response = await client.post(
        "/v1/coach/runs", headers=headers, json={"block_id": block_id}
    )
    if response.status_code == 201:
        return int(response.json()["id"])
    if response.status_code == 409:
        return open_run_id_from_conflict(response.json().get("detail", ""))
    raise SeedError(
        f"POST /v1/coach/runs failed: {response.status_code} {response.text}"
    )


async def ensure_open_run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    block_id: int,
    run_id: int | None,
) -> int:
    """The id of a run that is `awaiting_gate` for `block_id`, opening one if needed.

    R46: a consumed (accepted/rejected/failed) run is replaced with a fresh
    one so a re-run always restores the 30-second demo path.
    """
    if run_id is not None:
        response = await client.get(f"/v1/coach/runs/{run_id}", headers=headers)
        if response.status_code == 200 and response.json()["status"] == "awaiting_gate":
            return run_id
    return await open_or_reuse_run(client, headers, block_id)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def seed(args: argparse.Namespace) -> dict[str, Any]:
    """Run the whole idempotent seed against `args.base_url`."""
    state_path = Path(args.state_file)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as client:
        access_token, created_new = await register_or_login(
            client,
            email=args.email,
            password=args.password,
            display_name=args.display_name,
        )
        headers = {"Authorization": f"Bearer {access_token}"}

        block_id: int
        if created_new:
            today = date.today()
            block_id, last_session_id = await log_block_history(
                client,
                headers,
                intent=_HISTORY_INTENT,
                days=_HISTORY_DAYS,
                start_date=today,
                first_session_id_hint=None,
            )
            await log_block_history(
                client,
                headers,
                intent=_EXTRA_HISTORY_INTENT,
                days=_HISTORY_DAYS,
                # Far enough in the past to never collide with the first
                # block's dates (today .. today+6): avoids the
                # `GET /v1/sessions/today` MultipleResultsFound trap.
                start_date=today - timedelta(days=100),
                first_session_id_hint=last_session_id + 1,
            )
            run_id = await open_or_reuse_run(client, headers, block_id)
        else:
            state = load_state(state_path)
            resolved_block_id = (
                args.block_id if args.block_id is not None else state.get("block_id")
            )
            existing_run_id = (
                args.run_id if args.run_id is not None else state.get("run_id")
            )
            if resolved_block_id is None:
                raise SeedError(
                    f"the demo athlete already exists but no state was found at "
                    f"{state_path}; pass --block-id and --run-id explicitly, or "
                    "delete the athlete and re-run"
                )
            block_id = resolved_block_id
            run_id = await ensure_open_run(
                client, headers, block_id=block_id, run_id=existing_run_id
            )

        save_state(state_path, {"block_id": block_id, "run_id": run_id})

    return {
        "email": args.email,
        "password": args.password,
        "block_id": block_id,
        "run_id": run_id,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed the YATA demo athlete over the public HTTP API."
    )
    parser.add_argument(
        "--base-url", required=True, help="e.g. https://52.211.44.9.sslip.io"
    )
    parser.add_argument("--email", default="demo@yata.dev")
    parser.add_argument("--password", default="DemoPass123!")
    parser.add_argument("--display-name", default="Demo Athlete")
    parser.add_argument(
        "--state-file",
        default=str(_DEFAULT_STATE_FILE),
        help="where block_id/run_id are cached between runs",
    )
    parser.add_argument(
        "--block-id",
        type=int,
        default=None,
        help="recovery override if the demo athlete exists but --state-file does not",
    )
    parser.add_argument(
        "--run-id",
        type=int,
        default=None,
        help="recovery override if the demo athlete exists but --state-file does not",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: parse args, run the seed, print the demo credentials (R47)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = asyncio.run(seed(args))
    except SeedError as error:
        print(f"seed failed: {error}", file=sys.stderr)
        return 1

    print(f"demo athlete ready: email={result['email']} password={result['password']}")
    print(f"block_id={result['block_id']} run_id={result['run_id']} (awaiting_gate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
