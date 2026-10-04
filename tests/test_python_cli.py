from pathlib import Path

import pytest

from arena_intelligence import cli


@pytest.mark.parametrize(
    "arguments",
    (["run"], ["replay", "recording"], ["benchmark"], ["evaluate"]),
)
def test_native_default_uses_invocation_directory_in_installed_layout(
    arguments, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "venv/lib/site-packages/arena/cli.py"))
    options = cli.parser().parse_args(arguments)
    assert options.engine.resolve() == tmp_path / "build/release/arena-sim"
    if hasattr(options, "ledger"):
        assert options.ledger.resolve() == tmp_path / ".arena/costs.sqlite3"


def test_budget_default_is_writable_invocation_directory_and_overrides_are_preserved(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    assert cli.parser().parse_args(["budget"]).ledger.resolve() == tmp_path / ".arena/costs.sqlite3"
    args = cli.parser().parse_args(["run", "--engine", "/custom/arena", "--ledger", "/custom/db"])
    assert args.engine == Path("/custom/arena")
    assert args.ledger == Path("/custom/db")
