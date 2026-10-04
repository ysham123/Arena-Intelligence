import pytest

from arena_intelligence.protocol import ObservationEnvelope


@pytest.fixture
def observation():
    return ObservationEnvelope.model_validate(
        {
            "type": "observation",
            "schema_version": 1,
            "run_id": "test-run",
            "request_id": "r0",
            "cutoff_tick": 100,
            "observation": {
                "tick": 100,
                "team": 0,
                "size": 32,
                "scores": [0, 0],
                "nodes": [
                    {"id": 0, "x": 5, "y": 5},
                    {"id": 1, "x": 15, "y": 15},
                    {"id": 2, "x": 25, "y": 25},
                ],
                "blocked": [],
                "friendly": [{"id": 0, "team": 0, "x": 1, "y": 1, "objective_id": 0}],
                "visible_opponents": [],
                "last_seen": [],
                "evidence": [
                    {
                        "id": "e0",
                        "tick": 70,
                        "kind": "sighting",
                        "data": {
                            "bot_id": 1,
                            "x": 5,
                            "y": 5,
                            "observed_tick": 70,
                            "delivered_tick": 90,
                        },
                    }
                ],
            },
        }
    )
