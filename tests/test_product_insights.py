from arena_intelligence.product_insights import analyze_session


def snapshot(sequence, time, entities=None):
    return {
        "id": f"evt-{sequence}",
        "sequence": sequence,
        "source_time": time,
        "type": "snapshot",
        "payload": {
            "world": {"width": 32, "height": 32},
            "entities": entities or [{"id": "unit-a", "x": 1, "y": 1, "team": "alpha"}],
        },
    }


def test_stationary_requires_sustained_evidence_and_retains_references():
    events = [snapshot(i, i * 3) for i in range(5)]
    result = analyze_session(events)
    assert len(result) == 1
    assert result[0]["kind"] == "stationary_entity"
    assert result[0]["evidence_ids"] == [e["id"] for e in events]
    assert result[0]["details"]["duration_seconds"] == 12
    assert result[0]["origin"] == "local_check"
    assert "Check the simulator" in result[0]["alternative"]
    assert analyze_session(events[:3]) == []


def test_future_events_and_missing_entities_cannot_support_current_claim():
    events = [snapshot(i, i * 3) for i in range(6)]
    assert analyze_session(events, latest_source_time=8) == []
    events[3]["payload"]["entities"] = []
    assert analyze_session(events) == []


def test_stationary_does_not_bridge_sparse_updates_or_position_changes():
    assert analyze_session([snapshot(i, i * 10) for i in range(4)]) == []
    events = [snapshot(i, i * 3) for i in range(5)]
    events[3]["payload"]["entities"][0]["x"] = 2
    assert analyze_session(events) == []


def test_revision_at_same_time_does_not_count_as_new_sample():
    events = [snapshot(i, 0) for i in range(6)] + [snapshot(9, 12)]
    assert analyze_session(events) == []


def test_gap_uses_previous_intervals_and_does_not_claim_packet_loss():
    events = [snapshot(i, i) for i in range(6)] + [snapshot(6, 14)]
    result = analyze_session(events)
    assert len(result) == 1
    assert result[0]["kind"] == "telemetry_gap"
    assert result[0]["details"]["gap_seconds"] == 9
    assert result[0]["details"]["baseline_seconds"] == 1
    assert result[0]["evidence_ids"] == [e["id"] for e in events]
    assert "does not establish packet loss" in result[0]["alternative"]


def test_grouping_uses_matching_entities_and_only_reported_state():
    before = [
        {"id": str(i), "x": x, "y": y, "team": "alpha"}
        for i, (x, y) in enumerate([(1, 1), (25, 1), (1, 25), (25, 25)])
    ]
    after = [{**e, "x": 3, "y": 3} for e in before]
    result = analyze_session([snapshot(0, 0, before), snapshot(1, 12, after)])
    assert len(result) == 1
    assert result[0]["kind"] == "observed_grouping"
    assert result[0]["details"]["count"] == 4
    assert result[0]["details"]["previous_count"] == 1
    after[0]["id"] = "unseen-before"
    assert analyze_session([snapshot(0, 0, before), snapshot(1, 12, after)]) == []


def test_checks_are_stable_bounded_and_safe_on_irrelevant_events():
    entities = [{"id": str(i), "x": i, "y": i} for i in range(20)]
    events = [snapshot(i, i * 3, entities) for i in range(5)]
    first = analyze_session(events)
    assert first == analyze_session(list(reversed(events)))
    assert len(first) == 3
    assert analyze_session([{"type": "metric"}, {"type": "snapshot", "id": "bad"}]) == []


def test_evaluator_positions_never_feed_local_observer_checks():
    events = []
    for i in range(5):
        public = snapshot(i * 2, i * 3)
        public["payload"]["entities"][0]["x"] = i
        public["payload"]["visibility"] = "observer"
        truth = snapshot(i * 2 + 1, i * 3)
        truth["payload"]["visibility"] = "evaluator"
        events.extend([public, truth])
    assert analyze_session(events) == []


def test_repeated_old_sighting_is_not_a_new_position_sample():
    events = [snapshot(i, i * 3) for i in range(5)]
    for event in events:
        event["payload"]["entities"][0]["source_time"] = 0
    assert analyze_session(events) == []


def test_holding_the_assigned_objective_does_not_create_an_alert():
    events = [snapshot(i, i * 3) for i in range(5)]
    for event in events:
        event["payload"]["entities"][0]["objective_id"] = 2
        event["payload"]["world"]["nodes"] = [{"id": 2, "x": 1, "y": 1}]
    assert analyze_session(events) == []
    events[-1]["payload"]["entities"][0]["objective_id"] = 1
    assert analyze_session(events)[0]["kind"] == "stationary_entity"
