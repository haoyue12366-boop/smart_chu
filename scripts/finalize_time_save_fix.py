"""按用户六项条件核验实际归档；局部修复不替代正式/全量验收。"""

import gzip
import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.config import ROOT
from app.domain.policy import SchedulingPolicy
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.serial_reference import serial_order_holds
from app.validation.schedule import ScheduleValidator
from benchmarks.dataset import load_inputs
from benchmarks.runner import source_hashes, write_json
from scripts.run_time_save_regression import test_hashes

EVIDENCE = ROOT / "benchmarks/reports/verification/P6-timesave-fix-run1"
BEFORE = ROOT / "benchmarks/reports/P6-timesave-before-run3/report.json"
AFTER = ROOT / "benchmarks/reports/P6-timesave-after-run3/report.json"
ORIGINAL = ROOT / "benchmarks/reports/P6-timesave-before-run2/report.json"
EXTERNAL_TESTS = {
    "tests.contract.test_fixture_provenance::test_reviewed_samples_ready_for_p1_core",
    "tests.contract.test_official_dynamic_profile::test_official_dynamic_confirmation_has_hash_bound_source_and_current_profile",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": digest(path)}


def junit(path):
    root = ET.parse(path).getroot()
    result = {}
    for case in root.iter("testcase"):
        identity = case.attrib["classname"] + "::" + case.attrib["name"]
        outcome = (
            "FAILED"
            if case.find("failure") is not None or case.find("error") is not None
            else "SKIPPED"
            if case.find("skipped") is not None
            else "PASSED"
        )
        result[identity] = outcome
    assert result
    return result


def audit_reports(before, after, knowledge):
    rows = []
    for name, report_path, report in (("before", BEFORE, before), ("after", AFTER, after)):
        assert report["source_unchanged"] and report["rounds"] == 3
        assert len(report["samples"]) == 60
        assert report["hardware"] == before["hardware"]
        for sample in report["samples"]:
            if name == "after":
                assert sample["passed_correctness"], "修复版所有固定 HTTP 用例必须成功"
            artifact_path = report_path.parent / sample["artifact"]
            assert digest(artifact_path) == sample["artifact_sha256"]
            artifact = json.loads(gzip.decompress(artifact_path.read_bytes()))
            row = {
                "revision": name,
                "budget_ms": sample["budget_ms"],
                "replan_budget_ms": sample["replan_budget_ms"],
                "case_id": sample["case_id"],
                "round": sample["round"],
                "stage": sample["stage"],
                "service_elapsed_ms": sample["service_elapsed_ms"],
                "client_elapsed_ms": sample["client_elapsed_ms"],
                "passed_correctness": sample["passed_correctness"],
                "artifact": reference(artifact_path),
            }
            if name == "after" and sample["passed_correctness"]:
                assert sample["http_status"] == 200
                assert sample["service_elapsed_ms"] <= sample["budget_ms"]
                plan = PublishedPlan.model_validate(artifact["plan"])
                problem = SchedulingProblem.model_validate(artifact["problem"])
                serial = plan.serial_reference
                assert serial is not None and serial_order_holds(serial.candidate, problem)
                assert plan.state_revision == problem.runtime.state_revision
                assert plan.time_origin == problem.runtime.time_origin
                assert plan.knowledge_version == problem.knowledge_version
                assert plan.snapshot_id == problem.snapshot_id
                for item in (plan.validated, serial):
                    assert item.candidate.problem_hash == problem.problem_hash
                    proof = ScheduleValidator().validate(
                        knowledge, problem.runtime, problem, item.candidate
                    )
                    assert proof.valid and proof.candidate_hash == item.candidate.candidate_hash
                seconds = (
                    serial.candidate.metrics.makespan_sec
                    - plan.validated.candidate.metrics.makespan_sec
                )
                assert seconds >= 0
                expected = format(
                    (Decimal(seconds) / 60).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), ".1f"
                )
                response = json.loads(artifact["response_body"])
                assert set(response) == {
                    "overview",
                    "cookingTimeline",
                    "ingredientsSummary",
                    "detailTimeline",
                    "recipeDetail",
                }
                assert response["overview"]["timeSave"] == expected
                detail = artifact["receipt"]["result"]["planning"]
                if sample["stage"] == "REPLAN":
                    fast = any(
                        t["stage"] == "FEEDBACK_FEASIBILITY_RETURN" for t in detail["timings"]
                    )
                    row["fast_path"] = fast
                    if fast:
                        assert all(
                            s["objective_stage"] == "SERIAL_REFERENCE"
                            for s in detail["stage_results"]
                        )
                        assert not any(
                            detail.get(k, False)
                            for k in (
                                "human_objective_optimized",
                                "total_human_objective_optimized",
                                "stability_objective_optimized",
                            )
                        )
                    else:
                        # Worker 可在建模/传输预算不足时拒绝派发，返回带原因的 UNKNOWN。
                        # 保留原回退调用不等于每个目标都完成求解；不把跳过标为已优化。
                        assert len(detail["stage_results"]) > 1
                        actual = {s["objective_stage"] for s in detail["stage_results"]}
                        for stage in detail["stage_results"]:
                            if stage["objective_stage"] == "FEASIBILITY":
                                assert stage["status"] == "UNKNOWN" and stage["diagnostic_message"]
                        row["fallback_objective_stages"] = sorted(actual)
                row.update(
                    timeSave=expected,
                    parallel_sec=plan.validated.candidate.metrics.makespan_sec,
                    serial_sec=serial.candidate.metrics.makespan_sec,
                    problem_hash=problem.problem_hash,
                    reference_valid=True,
                )
            rows.append(row)
    pairing = ("budget_ms", "replan_budget_ms", "case_id", "round", "stage")
    old = {tuple(r[k] for k in pairing): r for r in rows if r["revision"] == "before"}
    new = {tuple(r[k] for k in pairing): r for r in rows if r["revision"] == "after"}
    assert len(old) == len(new) == 60 and old.keys() == new.keys()
    for key in old:
        old_artifact = json.loads(
            gzip.decompress((ROOT / old[key]["artifact"]["path"]).read_bytes())
        )
        new_artifact = json.loads(
            gzip.decompress((ROOT / new[key]["artifact"]["path"]).read_bytes())
        )
        assert old_artifact["request_url"] == new_artifact["request_url"]
        assert old_artifact["request_body"] == new_artifact["request_body"]
        assert old_artifact["request_headers"] == new_artifact["request_headers"]
    assert any(r.get("fast_path") for r in rows)
    return rows, [
        {
            **{k: new[key][k] for k in pairing},
            "service_delta_ms": new[key]["service_elapsed_ms"] - old[key]["service_elapsed_ms"],
            "before_passed": old[key]["passed_correctness"],
            "after_passed": new[key]["passed_correctness"],
        }
        for key in old
    ]


def main():
    before, after = (json.loads(p.read_bytes()) for p in (BEFORE, AFTER))
    assert before["label"] == "before" and after["label"] == "after"
    assert all(
        s["failure_count"] == s["deadline_exceeded_count"] == 0
        for stages in after["statistics"].values()
        for s in stages.values()
    )
    assert after["source_hashes"] == source_hashes()
    original = json.loads(ORIGINAL.read_bytes())
    changed = [
        p
        for p, value in original["source_hashes"].items()
        if p.startswith("app/") and after["source_hashes"].get(p) != value
    ]
    assert changed == ["app/scheduling/engine.py"], changed
    assert (
        digest(EVIDENCE / "producers/engine-before.py.txt") == original["source_hashes"][changed[0]]
    )
    assert (
        digest(EVIDENCE / "producers/time_save-run3.py.txt")
        == after["source_hashes"]["benchmarks/time_save.py"]
    )
    assert before["policies"] == after["policies"]
    assert before["source_hashes"] == after["source_hashes"]
    for report, expected in (
        (before, original["source_hashes"][changed[0]]),
        (after, after["source_hashes"][changed[0]]),
    ):
        assert len(report["engine_attestations"]) == 6
        assert all(a["sha256"] == expected for a in report["engine_attestations"])
    knowledge, _ = load_inputs()
    rows, pairs = audit_reports(before, after, knowledge)
    red = junit(EVIDENCE / "red.xml")
    assert Counter(red.values()) == {"FAILED": 9, "PASSED": 1}
    latest = junit(EVIDENCE / "regression-run1.xml")
    latest.update(junit(EVIDENCE / "regression-retry.xml"))
    latest.update(junit(EVIDENCE / "engine-final-run3.xml"))
    latest.update(junit(EVIDENCE / "edge-green.xml"))
    assert {k for k, v in latest.items() if v != "PASSED"} == EXTERNAL_TESTS
    relevant = {k: v for k, v in latest.items() if k not in EXTERNAL_TESTS}
    assert len(relevant) == 203 and all(v == "PASSED" for v in relevant.values())
    related = junit(EVIDENCE / "feedback-related.xml")
    assert len(related) == 66 and all(v == "PASSED" for v in related.values())
    final = junit(EVIDENCE / "regression-final.xml")
    assert len(final) == 264 and all(v == "PASSED" for v in final.values())
    assert final.keys() == (relevant | related).keys()
    run = json.loads((EVIDENCE / "regression-final-run.json").read_bytes())
    assert run["exit_code"] == 0 and run["source_unchanged"] and run["tests_unchanged"]
    assert run["source_hashes"] == after["source_hashes"] == source_hashes()
    assert run["test_hashes"] == test_hashes()
    assert run["report_sha256"] == digest(EVIDENCE / "regression-final.xml")
    assert run["producer_sha256"] == digest(ROOT / "scripts/run_time_save_regression.py")
    fast_v1 = SchedulingPolicy.model_validate_json(
        (ROOT / "data/policies/p6-feedback-fast-v1.json").read_bytes()
    )
    fast_v2 = SchedulingPolicy.model_validate_json(
        (ROOT / "data/policies/p6-feedback-fast-v2.json").read_bytes()
    )
    comparable = fast_v2.model_copy(
        update={
            "policy_version": fast_v1.policy_version,
            "replan_budget": fast_v2.replan_budget.model_copy(
                update={"total_ms": 2400, "solver_ms": 1500}
            ),
        }
    )
    assert comparable == fast_v1 and fast_v2.replan_budget.total_ms == 3000
    assert fast_v2.replan_budget.solver_ms == 2100
    old_manifest = json.loads((ROOT / "deploy/feedback_fix_manifest_run2.json").read_bytes())
    for name in ("fast_policy", "critical_policy"):
        item = old_manifest["artifacts"][name]
        assert digest(ROOT / item["path"]) == item["sha256"]
    for log, expected in (
        ("ruff-final-run4.log", "All checks passed!"),
        ("format-final-run4.log", "files already formatted"),
        ("mypy-final-run3.log", "Success: no issues found in 257 source files"),
    ):
        assert expected in (EVIDENCE / log).read_text(encoding="utf-8")
    summary = {
        "status": "TIMESAVE_FIX_VERIFIED_3000MS",
        "created_at": datetime.now(UTC).isoformat(),
        "formal_acceptance": False,
        "full_p6_acceptance": False,
        "source_hashes": source_hashes(),
        "test_hashes": run["test_hashes"],
        "changed_production_files": changed,
        "requirements": {
            "numerical_correctness": "PASSED_KNOWN_21_0_MIN_AND_SUCCESSFUL_HTTP_PROJECTIONS",
            "current_state_validated_reference": "PASSED_INDEPENDENT_BOTH_CANDIDATE_SCANS",
            "fast_path_and_five_sections": "PASSED_FAST_AND_ORIGINAL_CP_FALLBACK_3000_MS",
            "dynamic_state": "PASSED_ADD_CANCEL_EMPTY_COMPLETED_RUNNING_DURATION_UPDATE",
            "persistence_and_idempotency": "PASSED_SQLITE_AND_APP_RESTART_BYTE_EQUAL_HTTP_REPLAY",
            "performance_and_regression": "PASSED_2400_3000_MS_AND_RELEVANT_REGRESSIONS",
        },
        "tests": {
            "distinct_relevant_passed": len(final),
            "early_regression_distinct_passed": len(relevant),
            "feedback_related_passed": len(related),
            "final_regression_deselected_external_gates": run["external_gates_deselected"],
            "final_regression_run": reference(EVIDENCE / "regression-final-run.json"),
            "red": dict(Counter(red.values())),
            "external_gate_failures_preserved": sorted(EXTERNAL_TESTS),
        },
        "performance": {
            "before": before["statistics"],
            "after": after["statistics"],
            "paired_deltas": pairs,
            "baseline_uses_archived_original_engine": True,
            "original_engine": reference(EVIDENCE / "producers/engine-before.py.txt"),
            "before_engine_attestations": before["engine_attestations"],
            "after_engine_attestations": after["engine_attestations"],
        },
        "audited_samples": rows,
        "policy": reference(ROOT / "data/policies/p6-feedback-fast-v2.json"),
        "documentation": [
            reference(p)
            for p in (
                ROOT / "docs/task.md",
                ROOT / "docs/question.md",
                ROOT / "docs/2026-09-22-智能烹饪调度技术方案与架构设计.md",
                EVIDENCE / "commands.md",
            )
        ],
        "evidence": [
            reference(p)
            for p in (
                BEFORE,
                AFTER,
                EVIDENCE / "red.xml",
                EVIDENCE / "green-run1.xml",
                EVIDENCE / "regression-run1.xml",
                EVIDENCE / "regression-retry.xml",
                EVIDENCE / "engine-final-run3.xml",
                EVIDENCE / "edge-green.xml",
                EVIDENCE / "feedback-related.xml",
                EVIDENCE / "regression-final.xml",
                EVIDENCE / "regression-final-run.json",
                EVIDENCE / "ruff-final-run4.log",
                EVIDENCE / "format-final-run4.log",
                EVIDENCE / "mypy-final-run3.log",
            )
        ],
        "limitations": [
            "This verifies the local timeSave repair, not full competition acceptance",
            "Critical-window robustness failures remain outside this repair",
            "Official dynamic integration and formal review gates remain unresolved",
            "Windows ZIP packages retain the prior revision; repair delivered in workspace",
            (
                "Both budgets passed the fixed samples; "
                "3000ms with 2100ms Solver is the recommended profile"
            ),
            "Five fixed menus and three rounds do not guarantee every possible dynamic input",
        ],
    }
    write_json(EVIDENCE / "verification-summary.json", summary)
    manifest = {
        "schema_version": "1.0",
        "status": summary["status"],
        "created_at": summary["created_at"],
        "formal_acceptance": False,
        "full_p6_acceptance": False,
        "source_hashes": summary["source_hashes"],
        "test_hashes": summary["test_hashes"],
        "verification": reference(EVIDENCE / "verification-summary.json"),
        "policy": summary["policy"],
        "documentation": summary["documentation"],
        "producer": reference(ROOT / "scripts/finalize_time_save_fix.py"),
        "regression_producer": reference(ROOT / "scripts/run_time_save_regression.py"),
        "limitations": summary["limitations"],
    }
    write_json(ROOT / "deploy/time_save_fix_manifest_run1.json", manifest)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "distinct_relevant_passed": len(final),
                "feedback_related_passed": len(related),
                "after_statistics": after["statistics"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
