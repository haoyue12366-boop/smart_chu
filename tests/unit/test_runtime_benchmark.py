"""采样报告的百分位、失败与回退独立计算，固定场景引用不能漏项。"""

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.runtime_replanning import prepare_worker, summary


def test_runtime_benchmark_reports_percentiles_failures_and_fallbacks():
    rows = [
        {
            "service_elapsed_ms": value,
            "budget_ms": 2400,
            "status": "FAILED" if value == 3000 else "PUBLISHED",
            "failure_reason": "synthetic:timeout" if value == 3000 else None,
            "solver_fallback": value == 1200,
            "fallback_reasons": ["synthetic:UNKNOWN"] if value == 1200 else [],
        }
        for value in (3000, 1200, 100, 400)
    ]
    report = summary(rows)
    assert report["p50_ms"] == 400 and report["p95_ms"] == report["max_ms"] == 3000
    assert report["sample_count"] == 4
    assert report["failure_count"] == report["deadline_exceeded_count"] == 1
    assert report["solver_fallback_count"] == 1
    assert report["failure_reasons"] == {"synthetic:timeout": 1}
    assert report["fallback_reasons"] == {"synthetic:UNKNOWN": 1}
    with pytest.raises(ValueError):
        summary([])


def test_runtime_scenarios_bind_unchanged_preparation_and_all_recovery_cases():
    root = Path(__file__).resolve().parents[2]
    definitions = json.loads((root / "benchmarks/scenarios/runtime_events.json").read_bytes())
    artifact = definitions["prepared_scenarios_artifact"]
    raw = (root / artifact["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == artifact["sha256"]
    prepared = json.loads(raw)["cases"]
    assert len(definitions["cases"]) == len(prepared) == 15
    for source, executable in zip(prepared, definitions["cases"], strict=True):
        assert {key: executable[key] for key in source} == source
        assert source["case_id"] in executable["test_ref"]
    assert len(definitions["architecture_recovery_cases"]) == 7
    assert {case["number"] for case in definitions["architecture_recovery_cases"]} == set(
        range(1, 8)
    )


@pytest.mark.parametrize("ready", [True, False])
def test_benchmark_preparation_retries_once_and_keeps_failed_attempt(ready):
    class SyntheticReadinessProbe:
        calls = 0
        closes = 0

        def warmup(self):
            self.calls += 1
            return self.calls == 2 and ready

        def close(self):
            self.closes += 1

    worker = SyntheticReadinessProbe()
    records = []
    if ready:
        prepare_worker(worker, records, case="synthetic", iteration=1, phase="before_replan")
    else:
        with pytest.raises(TimeoutError, match="预热"):
            prepare_worker(worker, records, case="synthetic", iteration=1, phase="before_replan")
    assert worker.calls == 2 and worker.closes == 1
    assert len(records) == 2 and not records[0]["ready"]
    assert records[1]["ready"] is ready
    assert [row["attempt"] for row in records] == [1, 2]
    assert all(row["phase"] == "before_replan" and row["elapsed_ms"] >= 0 for row in records)


def test_saved_runtime_benchmark_binds_archived_code_and_all_warm_trials():
    root = Path(__file__).resolve().parents[2]
    output = root / "benchmarks/reports/P4-runtime-v7"
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "COMPLETED" and report["samples_per_case_warm"] == 30
    rows = [
        json.loads(line)
        for line in (output / report["samples_artifact"]).read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 186
    assert report["results"]["WARM"]["REPLAN"]["sample_count"] == 90
    for case_id, stats in report["case_results"].items():
        warm = [
            row
            for row in rows
            if row["case_id"] == case_id and row["mode"] == "WARM" and row["stage"] == "REPLAN"
        ]
        assert len(warm) == 30 and all(row["worker_ready_before"] for row in warm)
        assert all(row["budget_ms"] == 2400 for row in warm)
        assert summary(warm) == stats
    # 历史计时绑定运行时的真实字节，不能要求后续业务修复仍等于旧源码。
    manifest = json.loads(
        (
            root / "benchmarks/reports/2026-10-07-quality-fix/p4-runtime-v7-source-manifest.json"
        ).read_bytes()
    )
    assert manifest["kind"] == "RECOVERED_EXACT_HASH_SOURCE_NOT_NEW_TIMING"
    assert (
        hashlib.sha256((output / "report.json").read_bytes()).hexdigest()
        == manifest["report_sha256"]
    )
    assert (
        hashlib.sha256((output / report["samples_artifact"]).read_bytes()).hexdigest()
        == manifest["samples_sha256"]
    )
    expected = {**report["source_artifacts"], **report["code_hashes"]}
    assert {path: row["sha256"] for path, row in manifest["files"].items()} == expected
    for path, checksum in expected.items():
        archived = root / manifest["archive_root"] / path
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == checksum, path
    assert report["release"]["release_id"] == "delegated-v3-p4-preparation-v1-all"
    assert report["prewarm_failure_count"] == sum(
        not row["ready"] for row in report["prewarm_samples"]
    )
    assert report["clock_info"]["measurement"]["monotonic"]
