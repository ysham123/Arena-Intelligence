"""Native product live-tail behavior remains separate from strict final imports."""

import json

import pytest

from arena_intelligence.product_adapter import read_ndjson, recording_events


def test_live_reader_preserves_committed_records_and_retries_incomplete_utf8(tmp_path):
    recording = tmp_path / "events.ndjson"
    complete = b'{"tick":0}\n'
    recording.write_bytes(complete + b'{"label":"\xe2\x82')
    assert read_ndjson(recording, live=True) == [{"tick": 0}]
    with pytest.raises((UnicodeError, ValueError)):
        read_ndjson(recording)
    with recording.open("ab") as stream:
        stream.write(b'\xac"}\n')
    assert read_ndjson(recording, live=True) == [{"tick": 0}, {"label": "\u20ac"}]
    assert read_ndjson(recording) == read_ndjson(recording, live=True)


def test_live_reader_does_not_hide_corrupt_committed_records_or_oversized_tails(tmp_path):
    recording = tmp_path / "events.ndjson"
    recording.write_bytes(b'{"tick":0}\n{broken}\n{"tick":')
    with pytest.raises(ValueError):
        read_ndjson(recording, live=True)
    recording.write_bytes(b"x" * 1048577)
    with pytest.raises(ValueError, match="bounded"):
        read_ndjson(recording, live=True)


def test_live_converter_publishes_complete_frames_when_other_stream_is_mid_write(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"config": {"reasoner_team": 0}}))
    state = {
        "tick": 0,
        "size": 32,
        "blocked": [],
        "nodes": [{"id": 0, "x": 8, "y": 8}],
        "scores": [0, 0],
        "bots": [{"id": 0, "team": 0, "x": 2, "y": 2, "objective_id": 0}],
        "last_seen": [],
    }
    (tmp_path / "states.ndjson").write_text(json.dumps({"state": state}) + "\n" + '{"state":')
    (tmp_path / "reasoning.ndjson").write_text('{"request_id":')
    frames = recording_events(tmp_path, live=True)
    assert len(frames) == 2
    assert frames[0]["payload"]["visibility"] == "observer"
    assert frames[0]["payload"]["entities"][0]["objective_id"] == 0
    with pytest.raises(ValueError):
        recording_events(tmp_path)
