from concurrent.futures import ThreadPoolExecutor

import pytest

from arena_intelligence.budget import (
    NANO_PER_USD,
    BudgetExceeded,
    CostLedger,
    worst_case_cost,
)


@pytest.fixture(autouse=True)
def default_cap(monkeypatch):
    monkeypatch.delenv("ARENA_PROJECT_CEILING_USD", raising=False)


def test_persistent_reservations_and_settlement(tmp_path):
    ledger = CostLedger(tmp_path / "cost.sqlite3")
    request = ledger.reserve("dev", "r", "analyst", NANO_PER_USD)
    assert CostLedger(ledger.path).summary()["charged_or_reserved_usd"] == 1
    assert ledger.settle(request, 1000, 2000) == pytest.approx(0.022)
    assert ledger.summary()["unreconciled_requests"] == 0
    with pytest.raises(ValueError):
        ledger.settle(request, 1000, 2000)
    request = ledger.reserve("dev", "r", "challenger", NANO_PER_USD)
    ledger.mark_uncertain(request)
    assert CostLedger(ledger.path).summary()["charged_or_reserved_usd"] == pytest.approx(1.022)


def test_transactional_reservations_cannot_race_past_bucket(tmp_path):
    ledger = CostLedger(tmp_path / "cost.sqlite3")

    def reserve(_):
        try:
            ledger.reserve("dev", "r", "analyst", 600_000_000)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        successes = list(pool.map(reserve, range(20)))
    assert sum(successes) == 16
    assert ledger.summary()["buckets"]["dev"]["used_usd"] == 9.6


def test_all_buckets_obey_55_ceiling(tmp_path):
    ledger = CostLedger(tmp_path / "cost.sqlite3")
    for bucket, amount in [("dev", 10), ("eval", 35), ("demo", 10)]:
        ledger.reserve(bucket, "r", "stage", amount * NANO_PER_USD)
    assert ledger.summary()["charged_or_reserved_usd"] == 55
    with pytest.raises(BudgetExceeded, match="55"):
        ledger.reserve("demo", "r", "stage", 1)


def test_cost_reserves_full_output_and_rejects_unbounded_input():
    assert worst_case_cost(1, 1, 8192) >= 8192 * 10000
    with pytest.raises(ValueError, match="bounded"):
        worst_case_cost(200000, 1, 4096)


def test_lower_project_cap_persists_and_cannot_be_raised(tmp_path, monkeypatch):
    path = tmp_path / "project.sqlite3"
    monkeypatch.setenv("ARENA_PROJECT_CEILING_USD", "52.55")
    ledger = CostLedger(path)
    assert ledger.summary()["ceiling_usd"] == 52.55
    monkeypatch.delenv("ARENA_PROJECT_CEILING_USD")
    assert CostLedger(path).summary()["ceiling_usd"] == 52.55
    monkeypatch.setenv("ARENA_PROJECT_CEILING_USD", "55")
    assert CostLedger(path).summary()["ceiling_usd"] == 52.55
    for bucket, amount in [("dev", 10), ("eval", 35), ("demo", 7)]:
        ledger.reserve(bucket, "r", "stage", amount * NANO_PER_USD)
    with pytest.raises(BudgetExceeded, match="52.55"):
        ledger.reserve("demo", "r", "stage", NANO_PER_USD)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1", "55.01", "not-a-number"])
def test_invalid_project_cap_is_rejected(tmp_path, monkeypatch, value):
    monkeypatch.setenv("ARENA_PROJECT_CEILING_USD", value)
    with pytest.raises(ValueError, match="CEILING"):
        CostLedger(tmp_path / "invalid.sqlite3")
