"""真实发布→常驻工作进程→独立校验→实验文件保存与重载；动态运行库属于P4。"""

import json

import pytest

from app.scheduling.worker import SolverWorker


@pytest.fixture(scope="module")
def runner():
    from benchmarks.shared_ablation import load_published_knowledge

    knowledge = load_published_knowledge()
    with SolverWorker() as worker:
        yield knowledge, worker


@pytest.mark.parametrize(
    "shared,thermal", [(False, False), (True, False), (False, True), (True, True)]
)
def test_real_four_switches_use_core_worker_and_roundtrip_validation(
    runner, tmp_path, shared, thermal
):
    from benchmarks.shared_ablation import load_cases, read_trial, run_trial

    knowledge, worker = runner
    case = next(c for c in load_cases() if c["case_id"] == "real-h02-pair")
    path = tmp_path / "trial.json"
    row = run_trial(knowledge, case, shared, thermal, worker, path)
    assert row["status"] == "VALIDATED", row
    assert row["first_validated_candidate_ms"] is not None
    assert row["first_validated_candidate_ms"] <= row["service_ms"]
    assert row["selected_candidate_source"] in {"GREEDY", "CP_SAT"}
    assert row["reloaded_validation_valid"]
    assert row["builds"] and row["model_size_estimate"]
    assert row["source_kind"] == "REAL_DEVELOPMENT_RELEASE"
    loaded = read_trial(path)
    assert loaded["result"]["validation"]["valid"]
    assert loaded["knowledge_input_hash"] == row["knowledge_input_hash"]
    assert row["candidate_counts"]["shared"] == int(shared)
    assert row["candidate_counts"]["thermal"] == int(thermal)
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["result"]["candidate"]["assignments"].pop()
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError):
        read_trial(path)


def test_synthetic_failure_and_real_no_sharing_are_included(runner, tmp_path):
    from benchmarks.shared_ablation import load_cases, run_trial

    knowledge, worker = runner
    rows = []
    for case_id in ("real-no-sharing", "synthetic-unavailable-steam"):
        case = next(c for c in load_cases() if c["case_id"] == case_id)
        rows.append(run_trial(knowledge, case, True, True, worker, tmp_path / f"{case_id}.json"))
    assert rows[0]["status"] == "VALIDATED"
    assert rows[0]["candidate_counts"]["shared"] == rows[0]["candidate_counts"]["thermal"] == 0
    assert rows[1]["status"] == "FAILED" and rows[1]["failure"]
    assert rows[1]["metrics"] is None and rows[1]["source_kind"] == "SYNTHETIC_RUNTIME"


def test_late_member_forced_batch_can_be_worse_without_changing_heat_duration(runner):
    from benchmarks.shared_ablation import case_inputs, late_batch_witness, load_cases

    knowledge, _ = runner
    case = next(c for c in load_cases() if c["case_id"] == "synthetic-late-member")
    modified, _, _ = case_inputs(knowledge, case)
    before = {r.recipe_id: r for r in knowledge.recipes}
    assert all(r.operations == before[r.recipe_id].operations for r in modified.recipes)
    evidence = late_batch_witness(knowledge, case)
    assert evidence["standalone_valid"] and evidence["joint_valid"]
    assert evidence["forced_joint_makespan_sec"] > evidence["standalone_makespan_sec"]
