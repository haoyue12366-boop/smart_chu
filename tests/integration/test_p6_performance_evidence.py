"""完整三轮的真实 HTTP 证据，长尾、失败或未预热均不能算达标。"""

import json
from collections import Counter

from benchmarks.dataset import ROOT
from tests.p6_evidence_support import (
    current_sources,
    read_artifact,
    relative_evidence_path,
    report_for,
    same_suite,
    sha256,
)


def test_three_full_real_http_rounds_meet_fixed_targets_with_fresh_proofs():
    path, report = report_for("performance")
    assert report["status"] == "TARGETS_MET"
    assert report["scope"] == "WINDOWS_REAL_HTTP_THREE_ROUNDS"
    assert report["expected_requests"] == len(report["samples"]) == 153
    assert Counter(r["round"] for r in report["samples"]) == {1: 51, 2: 51, 3: 51}
    assert report["api_instances"] == report["concurrent_jobs"] == report["solver_workers"] == 1
    assert report["max_search_workers"] <= 4 and len(report["cold_startups"]) == 3
    assert len(report["worker_preparations"]) == 153
    assert all(p["ready"] for p in report["worker_preparations"])
    same_suite(report)
    current_sources(
        report, "benchmarks/performance.py", "benchmarks/scenarios/performance_profiles.json"
    )
    # 独立全量评估器随后换成同一在线适配器；实际 HTTP 采样只调用未变的归档助手。
    if sha256(ROOT / "benchmarks/runner.py") != report["source_hashes"]["benchmarks/runner.py"]:
        from tests.p6_evidence_support import inputs

        audit = json.loads((ROOT / inputs()["robustness_scope_audit"]).read_bytes())
        assert audit["status"] == "PASSED"
        assert audit["producer_sha256"] == sha256(ROOT / "scripts/audit_p6_robustness_scope.py")
        boundary = audit["exclusions"]["benchmarks/runner.py"]
        assert boundary["historical_sha256"] == report["source_hashes"]["benchmarks/runner.py"]
        assert boundary["current_sha256"] == sha256(ROOT / "benchmarks/runner.py")
        assert sha256(ROOT / boundary["baseline_path"]) == boundary["historical_sha256"]
        assert boundary["unchanged_ast_outside_symbol_and_import"]
    assert report["helper_hash"] == sha256(
        relative_evidence_path("tests/fault_injection/stream_support.py")
    )
    for row in report["samples"]:
        assert row["passed_correctness"] and row["http_status"] == 200
        assert row["service_elapsed_ms"] <= row["budget_ms"]
        assert row["client_elapsed_ms"] < row["excellent_client_ms"]
        assert row["budget_ms"] == (4200 if row["stage"] == "INITIAL" else 2400)
        frozen = read_artifact(path.parent, row["artifact"], row["artifact_sha256"])
        assert frozen["proof"]["valid"]
        assert frozen["plan"] == frozen["receipt"]["result"]["plan"]
