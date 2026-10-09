"""P6 清单覆盖、身份绑定及真实服务的可重放评估。"""

from copy import deepcopy

import pytest

from benchmarks.dataset import build_suite, load_inputs, suite_hash, validate_suite
from benchmarks.runner import BenchmarkRunner, source_hashes


def test_full_suite_is_deterministic_and_covers_every_id():
    knowledge, policy = load_inputs()
    first = build_suite(knowledge, policy, seed=20261004)
    assert first == build_suite(knowledge, policy, seed=20261004)
    validate_suite(first, knowledge, policy)
    assert len(first["single_recipes"]) == 100
    assert len(first["combinations"]) == 200
    assert len(first["boundaries"]) >= 20
    assert len(first["replans"]) >= 20
    assert {i for c in first["combinations"] for i in c["recipe_ids"]} == {
        r.recipe_id.root for r in knowledge.recipes
    }
    prawns = [r.recipe_id.root for r in knowledge.recipes if r.name == "麻辣对虾"]
    assert len(prawns) == 2 and prawns[0] != prawns[1]
    assert any(set(prawns) <= set(c["recipe_ids"]) for c in first["boundaries"])


def test_evidence_binds_scenarios_without_treating_archived_code_as_current(tmp_path, monkeypatch):
    monkeypatch.setattr("benchmarks.runner.ROOT", tmp_path)
    files = {
        "pyproject.toml": "schema_version = 1\n",
        "uv.lock": "locked",
        "app/program.py": "production",
        "benchmarks/program.py": "experiment",
        "benchmarks/scenarios/input.json": '{"version":1}',
        "benchmarks/reports/copied-release/app/old.py": "archived-production",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    before = source_hashes()
    archived = tmp_path / "benchmarks/reports/copied-release/app/old.py"
    archived.write_text("another archived copy", encoding="utf-8")
    assert source_hashes() == before
    scenario = tmp_path / "benchmarks/scenarios/input.json"
    scenario.write_text('{"version":2}', encoding="utf-8")
    assert source_hashes() != before


@pytest.mark.parametrize("fault", ["release", "policy", "coverage", "duplicate", "count"])
def test_changed_or_incomplete_suite_cannot_reuse_full_evidence(fault):
    knowledge, policy = load_inputs()
    suite = deepcopy(build_suite(knowledge, policy, seed=20261004))
    if fault == "release":
        suite["release"]["manifest_hash"] = "0" * 64
    elif fault == "policy":
        suite["policy_hash"] = "0" * 64
    elif fault == "coverage":
        suite["combinations"] = [suite["combinations"][0]] * 200
    elif fault == "duplicate":
        suite["combinations"][0]["recipe_ids"][1] = suite["combinations"][0]["recipe_ids"][0]
    else:
        suite["replans"] = suite["replans"][:19]
    suite["suite_hash"] = suite_hash(suite)
    with pytest.raises(ValueError):
        validate_suite(suite, knowledge, policy)


def test_real_runner_saves_published_plan_and_independent_interface_proof(tmp_path):
    knowledge, policy = load_inputs()
    suite = build_suite(knowledge, policy, seed=20261004)
    case = next(
        c for c in suite["single_recipes"] if c["recipe_ids"] == ["61e6c51fec6e1d65587067e1"]
    )
    report = BenchmarkRunner().run(
        suite, knowledge, policy, suite["seed"], tmp_path / "run", cases=[case]
    )
    assert report["scope"] == "PARTIAL_DEVELOPMENT"
    assert report["attempted_cases"] == 1
    assert report["failure_count"] == 0, report
    row = report["samples"][0]
    assert row["status"] == "PUBLISHED"
    assert row["constraint_validation"] and row["interface_validation"]
    assert row["artifact_sha256"] and row["problem_hash"]
    assert row["recipe_count"] == 1
    assert not report["formal_acceptance"]
    with pytest.raises(FileExistsError):
        BenchmarkRunner().run(
            suite, knowledge, policy, suite["seed"], tmp_path / "run", cases=[case]
        )
