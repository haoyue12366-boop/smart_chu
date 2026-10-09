"""独立重编译并扫描固定 120 条终态；配对质量及历史 65 条逐项验收。"""

import argparse
import ast
import gzip
import hashlib
import json
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.compiler.compiler import ProblemCompiler
from app.config import ROOT
from app.domain.ports import Deadline
from app.domain.runtime_session import RuntimeSession
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.duration_policy import estimated_phase
from app.validation.schedule import ScheduleValidator
from benchmarks.dataset import load_inputs
from benchmarks.runner import source_hashes, write_json

EVIDENCE = ROOT / "benchmarks/reports/verification/P6-dynamic-window-fix-run1"
HISTORY = ROOT / "benchmarks/reports/P6-feedback-critical-small-run2/report.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key(row):
    return row["case_id"], row["group"], row["trajectory"]


def quantiles(values):
    values = sorted(values)
    return {
        "count": len(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def run(before_path: Path, after_path: Path, output: Path):
    before_path, after_path, output = (p.resolve() for p in (before_path, after_path, output))
    assert not output.exists()
    before, after, history = (
        json.loads(p.read_bytes()) for p in (before_path, after_path, HISTORY)
    )
    assert all(
        r["status"] == "EXPERIMENT_COMPLETED" and r["source_unchanged"] and r["history_unchanged"]
        for r in (before, after)
    )
    assert before["hardware"] == after["hardware"]
    assert before["config"] == after["config"]
    # 历史报告还描述新增三组持续反馈策略；本轮只重放原四组。
    # 原四组使用的全部分布、种子、触发阈值和事件上限必须完全一致。
    assert set(before["config"]["groups"]) <= set(history["config"]["groups"])
    assert all(history["config"][k] == v for k, v in before["config"].items() if k != "groups")
    assert before["release"] == after["release"] == history["release"]
    assert after["source_hashes"] == before["source_hashes"] == source_hashes()
    assert (
        before["dispatch_guard_policy_id"] == "NONE"
        and after["dispatch_guard_policy_id"] == "TIGHT_HUMAN_V1"
    )
    archived = EVIDENCE / "before-source/benchmarks/robustness/simulator.py"
    assert before["simulator_source"]["sha256"] == digest(archived)
    assert after["simulator_source"]["sha256"] == digest(
        ROOT / "benchmarks/robustness/simulator.py"
    )
    old_source = ast.parse(
        (EVIDENCE / "before-source/benchmarks/robustness/dispatch.py").read_text(encoding="utf-8")
    )
    new_source = ast.parse((ROOT / "app/runtime/dispatch_windows.py").read_text(encoding="utf-8"))
    selected = {"ObservedDependency", "StartWindow", "observed_window"}

    def nodes(tree):
        return {
            n.name: ast.dump(n, include_attributes=False)
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in selected
        }

    assert nodes(old_source) == nodes(new_source), "窗口算法迁移必须保持原实际间隔语义"
    knowledge, _ = load_inputs()
    bm = {key(r): r for r in before["rows"]}
    am = {key(r): r for r in after["rows"]}
    hm = {key(r): r for r in history["rows"] if key(r) in am}
    assert len(bm) == len(am) == len(hm) == 120 and bm.keys() == am.keys() == hm.keys()
    for b, a in zip(before["initials"], after["initials"], strict=True):
        assert b["artifact"] == a["artifact"]
        bs, ass = (
            json.loads(gzip.decompress((p.parent / name["artifact"]).read_bytes()))
            for p, name in ((before_path, b), (after_path, a))
        )
        assert bs["candidate"]["assignments"] == ass["candidate"]["assignments"]
        assert bs["source_initial_sha256"] == ass["source_initial_sha256"]
        for s in (bs, ass):
            assert s["session"]["policy"]["replan_budget"]["total_ms"] == 3000
            assert s["session"]["policy"]["replan_budget"]["solver_ms"] == 2100
    checked, paired = [], []
    for identity, row in am.items():
        artifact = after_path.parent / row["artifact"]
        assert digest(artifact) == row["sha256"]
        frozen = json.loads(gzip.decompress(artifact.read_bytes()))
        assert row["status"] == frozen["status"] == "COMPLETED"
        assert (
            frozen["validation"]["valid"]
            and row["completed_task_count"] == row["required_task_count"]
        )
        session = RuntimeSession.model_validate(frozen["final_session"])
        current = ProblemCompiler().compile(
            knowledge,
            session.menu,
            session.runtime,
            session.policy,
            Deadline(expires_at_ns=time.monotonic_ns() + 4_200_000_000),
        )
        assert isinstance(current, SchedulingProblem), current
        proof = ScheduleValidator().validate(
            knowledge,
            session.runtime,
            current,
            CandidateSchedule(problem_hash=current.problem_hash, assignments=()),
        )
        assert proof.valid, proof
        recipes = {r.recipe_id: r for r in knowledge.recipes}
        instances = {i.recipe_instance_id: i for i in session.menu}
        operations = {
            t.task_id: next(
                op
                for op in recipes[instances[t.recipe_instance_id].recipe_id].operations
                if op.operation_id == t.operation_id
            )
            for t in current.logical_tasks
        }
        actual = {
            p.task_id.root: p.interval
            for e in session.runtime.executions
            for p in e.task_spans
            if p.task_id in e.completed_task_ids
        }
        fixed_checked = 0
        for e in session.runtime.executions:
            for span in e.task_spans:
                op = operations[span.task_id]
                if estimated_phase(op) == "FIXED_PROCESS":
                    assert (
                        span.interval.end_sec - span.interval.start_sec == op.duration.execution_sec
                    )
                    fixed_checked += 1
        checked.append(
            {
                "identity": list(identity),
                "artifact": row["artifact"],
                "sha256": row["sha256"],
                "proof": proof.model_dump(mode="json"),
                "fixed_duration_checks": fixed_checked,
            }
        )
        historical = hm[identity]
        failed = historical["status"] != "COMPLETED"
        resolved = []
        if failed:
            original = json.loads(
                gzip.decompress((HISTORY.parent / historical["artifact"]).read_bytes())
            )
            for dep in original["failure_detail"]["dependencies"]:
                left, right = actual[dep["predecessor_id"]], actual[dep["successor_id"]]
                lag = right.start_sec - left.end_sec
                assert dep["min_lag_sec"] <= lag
                assert dep["max_lag_sec"] is None or lag <= dep["max_lag_sec"]
                resolved.append(
                    {
                        "predecessor_id": dep["predecessor_id"],
                        "successor_id": dep["successor_id"],
                        "actual_predecessor_end_sec": left.end_sec,
                        "actual_successor_start_sec": right.start_sec,
                        "actual_lag_sec": lag,
                    }
                )
        paired.append(
            {
                "identity": list(identity),
                "historical_status": historical["status"],
                "before_status": bm[identity]["status"],
                "after_status": row["status"],
                "resolved_original_dependencies": resolved,
            }
        )
    quality = {}
    for group in after["summaries"]:
        pairs = [(bm[k], am[k]) for k in bm if k[1] == group and bm[k]["status"] == "COMPLETED"]
        quality[group] = {
            field: {
                "before": quantiles([b[field] for b, a in pairs]),
                "after": quantiles([a[field] for b, a in pairs]),
                "paired_delta": quantiles([a[field] - b[field] for b, a in pairs]),
            }
            for field in ("completion_sec", "spread_sec", "human_work_sec", "max_human_block_sec")
        }
    diagnosis = json.loads((EVIDENCE / "historical-diagnosis.json").read_bytes())
    assert diagnosis["count"] == 65 and {key(r) for r in diagnosis["rows"]} == {
        k for k, r in hm.items() if r["status"] != "COMPLETED"
    }
    times = [r["observed_request_elapsed_ms"] for r in after["replan_computations"]]
    assert times and all(t <= 3000 for t in times)
    for r in after["replan_computations"]:
        assert r["failure"] is None and r["budget_ms"] == 3000
    result = {
        "status": "FIXED_120_EXECUTION_VERIFIED_WITH_QUALITY_TRADEOFFS",
        "created_at": datetime.now(UTC).isoformat(),
        "formal_acceptance": False,
        "full_p6_acceptance": False,
        "source_hashes": source_hashes(),
        "producer_sha256": digest(Path(__file__)),
        "reports": {
            "before": {
                "path": before_path.relative_to(ROOT).as_posix(),
                "sha256": digest(before_path),
            },
            "after": {
                "path": after_path.relative_to(ROOT).as_posix(),
                "sha256": digest(after_path),
            },
            "history": {"path": HISTORY.relative_to(ROOT).as_posix(), "sha256": digest(HISTORY)},
        },
        "historical_pairs": dict(
            Counter(r["historical_status"] + "->" + r["after_status"] for r in paired)
        ),
        "current_pairs": dict(
            Counter(r["before_status"] + "->" + r["after_status"] for r in paired)
        ),
        "quality_among_both_completed": quality,
        "after_all_completed_quality": {
            g: {
                f: quantiles([r[f] for r in am.values() if r["group"] == g])
                for f in ("completion_sec", "spread_sec", "human_work_sec", "max_human_block_sec")
            }
            for g in after["summaries"]
        },
        "spread_under_300_count": sum(r["spread_sec"] < 300 for r in am.values()),
        "observed_replan_latency_ms": quantiles(times),
        "pairs": paired,
        "terminal_scans": checked,
        "infeasibility_claims": [],
    }
    write_json(output, result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "status",
                    "historical_pairs",
                    "current_pairs",
                    "spread_under_300_count",
                    "observed_replan_latency_ms",
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.before, arguments.after, arguments.output)
