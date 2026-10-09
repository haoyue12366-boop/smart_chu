"""验证已审核发布、真实规划证据与 P4 设计输入；不替代 P4 运行验收。"""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from app.domain.events import EventType
from app.domain.ids import RecipeId
from app.domain.policy import SchedulingPolicy
from app.domain.reports import PlanningResult
from app.domain.scheduling_problem import SchedulingProblem
from app.knowledge.loader import load_release, read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.validation.schedule import ScheduleValidator
from scripts.prepare_p4 import AUTH_PATH, BASE_ID, BASE_ROOT, OUTPUT, ROOT, VERSION, audit_release


def read(path: Path) -> dict:
    return json.loads(path.read_bytes())


def binding(path: Path) -> dict:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def shared_smoke(knowledge, output: Path) -> dict:
    from app.scheduling.worker import SolverWorker
    from benchmarks.shared_ablation import load_cases, read_trial, run_trial

    case = next(c for c in load_cases() if c["case_id"] == "real-h02-pair")
    rows = []
    with SolverWorker() as worker:
        for shared, thermal in ((False, False), (True, False), (False, True), (True, True)):
            path = output / f"shared-{int(shared)}-thermal-{int(thermal)}.json"
            row = run_trial(knowledge, case, shared, thermal, worker, path)
            require(row["status"] == "VALIDATED", "审核后共享四开关回归失败")
            require(row["candidate_counts"]["shared"] == int(shared), "共享候选丢失")
            require(row["candidate_counts"]["thermal"] == int(thermal), "热批次候选丢失")
            restored = read_trial(path)
            require(
                restored["knowledge"]["release"]["release_id"] == VERSION + "-all",
                "共享回归不是新发布",
            )
            rows.append(
                {
                    "shared": shared,
                    "thermal": thermal,
                    "status": row["status"],
                    "artifact": binding(path),
                }
            )
    return {"passed_count": len(rows), "results": rows}


def verify(output: Path, run_shared: bool) -> dict:
    release_root = ROOT / OUTPUT / "releases"
    ref = read_release_ref(release_root, VERSION + "-all")
    loaded = load_release(release_root, ref)
    source = loaded.snapshot.knowledge
    base = load_release(ROOT / BASE_ROOT, read_release_ref(ROOT / BASE_ROOT, BASE_ID))
    audit = read(ROOT / OUTPUT / "data_review.json")
    require(audit == audit_release(base), "审核台账与原发布不一致")
    auth = read(ROOT / AUTH_PATH)
    require(
        auth["actor_kind"] == "DELEGATED_AGENT" and auth["approve_for_p4"] is True, "代理授权缺失"
    )
    require(auth["base_content_hash"] == base.snapshot.content_hash, "授权内容不匹配")
    for path in (AUTH_PATH.as_posix(), (OUTPUT / "data_review.json").as_posix()):
        next(a for a in source.source_artifacts if a.path == path).verify(ROOT)
    for before, after in zip(base.snapshot.knowledge.recipes, source.recipes, strict=True):
        require(before.semantic_hash() == after.semantic_hash(), "原工艺意外改变")
        require(after.review_status == "APPROVED" and after.approval is not None, "未审核路径")
        require(after.approval.reviewer == auth["reviewer"], "审核身份错误")
    csv_ref = next(a for a in source.source_artifacts if a.path.endswith("recipes_100_调度版.csv"))
    csv_ref.verify(ROOT)
    policy = SchedulingPolicy.model_validate(read(ROOT / OUTPUT / "planning_policy_current.json"))
    require(
        policy.shared_prep
        and policy.strict_together_batch
        and policy.allow_delegated_shared_estimates,
        "运行策略未开启已准备规则",
    )
    design = read(ROOT / OUTPUT / "runtime_design.json")
    require(
        design["status"] == "DESIGN_ONLY" and not design["new_objective_implemented"],
        "设计被误标成实现",
    )
    require(
        {r["event_type"] for r in design["event_rules"]} == {e.value for e in EventType},
        "事件类型覆盖缺失",
    )
    require(
        not design["planned_end_releases_human"] and not design["passive_wait_reserves_human"],
        "人工释放设计错误",
    )
    cases = read(ROOT / OUTPUT / "runtime_scenarios.json")
    require(
        cases["status"] == "DESIGN_ONLY_NOT_EXECUTED" and len(cases["cases"]) == 15,
        "场景缺失或误报执行",
    )
    repository = SnapshotKnowledgeRepository(release_root)
    repository.load(ref)
    knowledge = repository.select(tuple(RecipeId(r.recipe_id.root) for r in source.recipes))
    report_path = ROOT / "benchmarks/reports/P4-preparation-all-recipes/report.json"
    report = read(report_path)
    require(report["release"] == ref.model_dump(mode="json"), "全量验证发布身份不同")
    require(report["recipe_count"] == report["success_count"] == 100, "全量100菜未通过")
    require(report["canonical_hash"] == source.content_hash, "全量验证内容哈希不同")
    require(
        {r["recipe_id"] for r in report["results"]} == {r.recipe_id.root for r in source.recipes},
        "全量身份不完整",
    )
    proofs = []
    for row in report["results"]:
        path = report_path.parent / row["plan_artifact"]
        require(
            hashlib.sha256(path.read_bytes()).hexdigest() == row["artifact_sha256"],
            "单菜证据被修改",
        )
        payload = read(path)
        require(payload["release"] == ref.model_dump(mode="json"), "单菜发布身份不同")
        problem = SchedulingProblem.model_validate(payload["problem"])
        result = PlanningResult.model_validate(payload["result"])
        require(result.status == "VALIDATED", "计划未成功")
        proof = ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate)
        require(proof.valid, "持久化计划独立重载验证失败：" + row["recipe_id"])
        proofs.append(binding(path))
    shared_path = output / "shared_report.json"
    if run_shared:
        shared = shared_smoke(knowledge, output / "shared")
        shared_path.write_text(
            json.dumps(shared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    else:
        shared = read(shared_path)
    require(shared["passed_count"] == 4, "共享回归缺失")
    for row in shared["results"]:
        from benchmarks.shared_ablation import read_trial

        path = ROOT / row["artifact"]["path"]
        require(binding(path) == row["artifact"], "共享回归证据已修改")
        restored = read_trial(path)
        require(
            restored["knowledge"]["release"]["release_id"] == ref.release_id, "共享回归引用旧发布"
        )
    tests = ROOT / "benchmarks/reports/verification/P4-preparation-regression.xml"
    suites = list(ET.parse(tests).getroot().iter("testsuite"))
    require(bool(suites) and sum(int(s.get("tests", "0")) for s in suites) >= 21, "必需回归未执行")
    require(
        all(int(s.get(k, "0")) == 0 for s in suites for k in ("errors", "failures", "skipped")),
        "回归失败或跳过",
    )
    required_files = [
        ROOT / AUTH_PATH,
        tests,
        report_path,
        shared_path,
        ROOT / "docs/P4准备与规则基线.md",
        ROOT / "docs/task.md",
        ROOT / "docs/AGENTS.md",
        ROOT / "AGENTS.md",
        ROOT / "docs/2026-09-22-智能烹饪调度技术方案与架构设计.md",
        ROOT / "scripts/prepare_p4.py",
        ROOT / "scripts/check_p4_preparation.py",
        ROOT / "benchmarks/reports/verification/P3-development-final.json",
    ]
    required_files.extend(
        ROOT / OUTPUT / n
        for n in (
            "data_review.json",
            "publication_report.json",
            "planning_policy_current.json",
            "runtime_design.json",
            "runtime_scenarios.json",
        )
    )
    return {
        "status": "READY_FOR_P4_IMPLEMENTATION",
        "recorded_at": datetime.now().astimezone().isoformat(),
        "authority": "DELEGATED_AGENT",
        "release": ref.model_dump(mode="json"),
        "approved_recipe_count": len(source.recipes),
        "operation_count": audit["operation_count"],
        "single_recipe_validated_count": len(proofs),
        "persisted_plan_revalidation_count": len(proofs),
        "shared_switch_validation_count": 4,
        "review_regression_passed": 21,
        "runtime_event_design_count": len(design["event_rules"]),
        "runtime_scenario_design_count": 15,
        "p4_runtime_implemented": False,
        "total_human_objective_implemented": False,
        "competition_protocol_verified": False,
        "physical_measurement_performed": False,
        "blocking_user_review": [],
        "remaining_implementation_tasks": [f"P4-{i:02}" for i in range(1, 11)],
        "artifacts": [binding(p) for p in required_files],
        "plan_artifacts": proofs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shared-smoke", action="store_true")
    args = parser.parse_args()
    report = verify(ROOT / OUTPUT, args.shared_smoke)
    (ROOT / OUTPUT / "readiness.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(report["status"], "100菜逐项批准及计划重载通过，4组共享开关通过；P4运行功能待实现")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
