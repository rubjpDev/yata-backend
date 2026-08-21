"""Guard: every muscle group the catalogue migration seeds has a landmark.

Regression guard for yata-0015: without this, adding an exercise to the seed
whose muscle groups outrun `VOLUME_LANDMARKS` reintroduces the 500 on
GET /v1/blocks/{id}/status silently.
"""

from app.engine import VOLUME_LANDMARKS
from tests._catalogue import seeded_muscle_groups


def test_every_seeded_muscle_group_has_a_volume_landmark() -> None:
    missing = seeded_muscle_groups() - VOLUME_LANDMARKS.keys()
    assert not missing, f"seeded muscle groups missing landmarks: {missing}"
