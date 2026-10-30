"""An incomplete quality comparison must not silently succeed."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def comparison(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("comparison_test", scripts / "compare_runs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_comparison_refuses_missing_sets(comparison, tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["compare_runs", str(tmp_path), str(tmp_path)])
    with pytest.raises(SystemExit) as failure:
        comparison.main()
    assert failure.value.code == 2


def test_comparison_refuses_missing_or_extra_rows(comparison):
    row = {"ktru_code": "A", "status": "auto"}
    with pytest.raises(ValueError, match="exactly the same rows"):
        comparison.compare({1: row}, {}, {1: {"ktru_code": "A"}})


def test_comparison_saves_regressions_and_wrong_automatic_codes(comparison):
    before = {1: {"ktru_code": "A", "status": "auto"}}
    after = {1: {"ktru_code": "B", "status": "auto"}}
    result = comparison.compare(before, after, {1: {"ktru_code": "A"}})
    assert result["broken"] == [1]
    assert result["before"] == (1, 1, 0)
    assert result["after"] == (0, 1, 1)
    assert result["changed"][0]["before"] == "A"


def test_comparison_refuses_duplicate_rows(comparison, tmp_path):
    path = tmp_path / "run.json"
    path.write_text('{"items": [{"row_number": 1}, {"row_number": 1}]}')
    with pytest.raises(ValueError, match="Duplicate"):
        comparison.load(path)
