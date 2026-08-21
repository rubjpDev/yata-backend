"""Load the real system-exercise seed straight from its Alembic migration.

The migration's module filename starts with a digit (Alembic's own naming
convention) so it cannot be imported with a normal `import` statement; it is
loaded by file path instead. Tests use this so the seeded catalogue has a
single source of truth and cannot silently drift from hand-copied fixtures.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260618_0003_exercises_and_bodyweight.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "seed_migration_20260618_0003", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def system_exercises() -> list[tuple[str, str, list[str]]]:
    """(name, category, muscle_groups) for every exercise the migration seeds."""
    module = _load_migration()
    return [
        (name, category, groups.strip("{}").split(","))
        for name, category, groups in module._SYSTEM_EXERCISES
    ]


def seeded_muscle_groups() -> frozenset[str]:
    """Every distinct muscle group the catalogue migration seeds."""
    return frozenset(muscle for _, _, groups in system_exercises() for muscle in groups)
