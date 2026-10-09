"""检查固定 P5 准备输入；结果不代表 HTTP、浏览器或官方动态协议通过。"""

import argparse
import csv
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.competition_contract import validate_request
from app.domain.ids import RecipeId
from app.domain.knowledge import ReleaseRef
from app.domain.policy import SchedulingPolicy
from app.knowledge.repository import SnapshotKnowledgeRepository
from scripts.p5_preparation_design import DEVELOPMENT_PROFILE, TASK_IDS, competition_profiles
from scripts.prepare_p5 import (
    CSV_PATHS,
    EVIDENCE_PATHS,
    OFFICIAL_PATHS,
    OUTPUT,
    P4_BENCHMARK,
    P4_COMMAND,
    P4_GATE,
    P4_INPUT,
    P4_PLANS,
    POLICY,
    ROOT,
    TECHNICAL_PATHS,
    contained,
    environment,
    read,
    require,
    sha256,
    write_new,
)


def check_sources(package: Path) -> dict[str, int]:
    manifest = read(package / "manifest.json")
    require(manifest["kind"] == "PREPARATION_ONLY", "准备包不能伪装阶段验收")
    artifacts = manifest["artifacts"]
    require(len({row["path"] for row in artifacts}) == len(artifacts), "准备产物路径重复")
    required_artifacts = {
        "source_manifest.json",
        "p5_inputs.json",
        "recipe_catalog.json",
        "environment.json",
        "competition_profiles.json",
        "protocol_questions.json",
        "api_scenarios.json",
        "gate_design.json",
        "README.md",
        "source_extracts/competition_scheduling.txt",
    }
    require(required_artifacts <= {row["path"] for row in artifacts}, "必需准备产物未绑定哈希")
    for row in artifacts:
        require(sha256(contained(package, row["path"])) == row["sha256"], "准备产物哈希不符")
    sources = read(package / "source_manifest.json")["sources"]
    expected = {*OFFICIAL_PATHS, *TECHNICAL_PATHS, *EVIDENCE_PATHS, "docs/task.md"}
    require({row["path"] for row in sources} == expected, "必需原始材料/技术输入/证据缺失")
    require(len(sources) == len(expected), "来源重复")
    for row in sources:
        require(row["archive_path"] == "source_inputs/" + row["path"], "来源归档路径不一致")
        archive = contained(package, row["archive_path"])
        require(sha256(archive) == row["sha256"], "原始材料归档哈希不符")
        ledger = row["path"] == "docs/task.md"
        require(row["require_current_hash"] is (not ledger), "仅进度快照可在归档后追加记录")
        expected_role = (
            "OFFICIAL_MATERIAL"
            if row["path"] in OFFICIAL_PATHS
            else "TECHNICAL_INPUT"
            if row["path"] in TECHNICAL_PATHS
            else "P4_EVIDENCE"
            if row["path"] in EVIDENCE_PATHS
            else "PROGRESS_LEDGER_SNAPSHOT"
        )
        require(row["role"] == expected_role, "原始材料、项目选择与历史证据角色混淆")
        if not ledger:
            require(sha256(contained(ROOT, row["path"])) == row["sha256"], "当前技术输入已改变")
    inputs = read(package / "p5_inputs.json")
    for name, digest in inputs["input_bindings"].items():
        require(sha256(contained(ROOT, name)) == digest, "固定前置输入绑定失败")
    return {"artifact_count": len(artifacts), "source_count": len(sources)}


def check_knowledge(package: Path) -> dict[str, Any]:
    inputs = read(package / "p5_inputs.json")
    p4 = read(ROOT / P4_INPUT)
    require(inputs["release"] == p4["release"], "P5 必须沿用固定代理审核发布")
    require(inputs["release_root"] == p4["release_root"], "固定发布目录不符")
    ref = ReleaseRef.model_validate(inputs["release"])
    require(ref.release_kind == "development", "开发准备不能伪装正式发布")
    repository = SnapshotKnowledgeRepository(contained(ROOT, inputs["release_root"]))
    handle = repository.load(ref)
    with repository.acquire(ref) as lease:
        source = lease.loaded.snapshot.knowledge
        knowledge = lease.select(
            tuple(RecipeId(recipe.recipe_id.root) for recipe in source.recipes)
        )
    recipes = knowledge.recipes
    operations = sum(len(recipe.operations) for recipe in recipes)
    require(
        (handle.recipe_count, operations, len(knowledge.profiles)) == (100, 1562, 27),
        "数据覆盖错误",
    )
    require(
        all(r.review_status == "APPROVED" and r.approval is not None for r in recipes), "未批准菜谱"
    )
    expected = [
        {
            "id": recipe.recipe_id.root,
            "name": recipe.name,
            "operation_count": len(recipe.operations),
            "recipe_semantic_hash": recipe.semantic_hash(),
        }
        for recipe in recipes
    ]
    catalog_document = read(package / "recipe_catalog.json")
    require(catalog_document["release"] == inputs["release"], "目录发布身份不符")
    require(catalog_document["recipes"] == expected, "菜谱目录与真实发布身份/工艺不符")
    catalog = {row["id"]: row["name"] for row in expected}
    for name in CSV_PATHS:
        with (ROOT / name).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        require(len(rows) == 100, "原始 CSV 不含完整 100 菜")
        require({row["菜谱id"]: row["名称"] for row in rows} == catalog, "CSV身份或名称与发布不同")
    policy = SchedulingPolicy.model_validate(read(ROOT / POLICY))
    require(inputs["runtime_policy"] == POLICY, "运行策略错误")
    require(
        policy.initial_budget.total_ms == 4200 and policy.replan_budget.total_ms == 2400, "预算错误"
    )
    require(policy.human_count == 1 and policy.time_grid_sec == 1, "单人人工/秒级基线改变")
    require(policy.shared_prep and policy.strict_together_batch, "已批准 S02/H02 策略丢失")
    auth = read(contained(ROOT, inputs["authorization"]))
    require(auth["actor_kind"] == "DELEGATED_AGENT" and auth["approve_for_p4"] is True, "授权缺失")
    return {"recipe_count": 100, "operation_count": operations, "profile_count": 27, "csv_count": 3}


def check_p4_evidence(package: Path) -> dict[str, Any]:
    inputs = read(package / "p5_inputs.json")
    gate = read(ROOT / P4_GATE)
    command = read(ROOT / P4_COMMAND)
    require(gate["phase"] == "P4" and gate["gate"] == "development", "P4出口类型错误")
    require(gate["status"] == "DEVELOPMENT_VERIFIED" and not gate["errors"], "P4未通过")
    require(command["exit_code"] == 0 and command["gate_report"] == P4_GATE, "缺实际执行命令")
    require(command["gate_report_sha256"] == sha256(ROOT / P4_GATE), "实际命令未绑定当前P4报告")
    require(command["sources_unchanged"] is True, "前置执行期间源码发生变更")
    before, after = command["source_hashes_before"], command["source_hashes_after"]
    require(before == after and len(after) == 339, "P4前后源码身份不完整")
    for name, digest in after.items():
        require(sha256(contained(ROOT, name)) == digest, "P4已验收源码改变：" + name)
    commands = gate["commands"]
    require(all(row["exit_code"] == 0 and row["passed"] for row in commands), "P4必需命令失败")
    passed = sum(row["passed_count"] for row in commands)
    require(passed == 733, "P4通过执行数不符")
    require(
        all(row["failed_count"] == row["skipped_count"] == 0 for row in commands), "P4失败或跳过"
    )
    require({f"P4-{index:02d}" for index in range(1, 11)} <= gate["tasks"].keys(), "P4任务缺失")
    plans = read(ROOT / P4_PLANS)
    require(
        plans["status"] == "PASSED" and plans["release"] == inputs["release"], "历史计划发布不同"
    )
    require(
        plans["persisted_plan_revalidation_count"] == len(plans["plan_artifacts"]) == 100,
        "缺100计划",
    )
    for row in plans["plan_artifacts"]:
        require(sha256(contained(ROOT, row["path"])) == row["sha256"], "已校验历史计划被修改")
    for name, digest in plans["validation_source_hashes"].items():
        require(sha256(contained(ROOT, name)) == digest, "历史校验器源码改变")
    benchmark = read(ROOT / P4_BENCHMARK)
    require(benchmark["release"] == inputs["release"], "性能测量发布不同")
    for field in ("code_hashes", "source_artifacts"):
        for name, digest in benchmark[field].items():
            require(sha256(contained(ROOT, name)) == digest, "性能证据代码/输入改变")
    require(len(benchmark["code_hashes"]) == 200, "性能测量源码绑定不完整")
    return {
        "gate_status": gate["status"],
        "passed_test_executions": passed,
        "failed_test_executions": 0,
        "skipped_test_executions": 0,
        "unchanged_p4_sources": len(after),
        "unchanged_saved_plans": 100,
        "historical_results_reused_without_rerunning_gate": True,
        "benchmark_results": benchmark["results"],
        "benchmark_is_p5_http_performance": False,
    }


def check_design(package: Path) -> dict[str, Any]:
    inputs = read(package / "p5_inputs.json")
    require(inputs["purpose"] == "P5_IMPLEMENTATION_PREPARATION_ONLY", "交付类型错误")
    require(inputs["implementation_tasks"] == dict.fromkeys(TASK_IDS, "NOT_STARTED"), "P5误报完成")
    require(inputs["official_dynamic_integration_status"] == "NOT_EXECUTED", "误报官方联调")
    require(inputs["p5_phase_gate_status"] == "NOT_EXECUTED", "误报P5阶段验收")
    profiles = read(package / "competition_profiles.json")
    require(profiles == competition_profiles(), "协议事实或草案边界被改写")
    scenarios = read(package / "api_scenarios.json")
    require(scenarios["status"] == "DESIGN_ONLY_NOT_EXECUTED", "设计场景伪装测试执行")
    require(scenarios["official_dynamic_test_passed"] is False, "不能宣称官方动态测试通过")
    require(scenarios["future_simulator_disturbances_visible_to_solver"] is False, "泄露未来扰动")
    catalog = {row["id"]: row["name"] for row in read(package / "recipe_catalog.json")["recipes"]}
    fixtures = scenarios["fixtures"]
    for name, fixture in fixtures.items():
        expected_valid = fixture["valid_request"]
        require(
            isinstance(expected_valid, bool) and bool(fixture["body"]), "请求夹具标记或内容错误"
        )
        try:
            validate_request(fixture["body"], catalog)
        except ValueError:
            require(not expected_valid, "真实菜谱夹具请求不合法：" + name)
        else:
            require(expected_valid, "负向请求夹具没有暴露预期错误：" + name)
    same_name = fixtures["same_name_two_ids"]["body"]
    require(len(same_name) == 2 and len({row["id"] for row in same_name}) == 2, "同名菜被错误合并")
    require(all(row["name"] == "麻辣对虾" for row in same_name), "同名菜来源不符")
    cases = scenarios["cases"]
    require(len(cases) == inputs["counts"]["designed_scenarios"] == 38, "设计场景数量不完整")
    require(len({case["id"] for case in cases}) == len(cases), "场景身份重复")
    require({case["task"] for case in cases} == set(TASK_IDS), "八项任务场景覆盖不足")
    for case in cases:
        require(case["status"] == "DESIGN_ONLY_NOT_EXECUTED", "场景误报执行")
        require(
            case["execution_evidence"] is None and case["requires_real_backend"], "场景误报证据"
        )
        require(
            case["fixture"] in fixtures and bool(case["expected_invariant"]), "场景输入/断言缺失"
        )
        require(case["profile"] in profiles["profiles"], "场景未绑定显式协议")
    questions = read(package / "protocol_questions.json")
    question_ids = {item["id"] for item in questions["items"]}
    require(
        question_ids == set(profiles["profiles"][DEVELOPMENT_PROFILE]["unresolved_protocol_refs"]),
        "官方未决项登记缺失",
    )
    gate = read(package / "gate_design.json")
    current = read(ROOT / "scripts/verification_manifest.json")
    require(gate["status"] == "PLANNED_NOT_REGISTERED", "准备不能提前注册验收通过")
    require(gate["required_tasks"] == list(TASK_IDS), "开发验收缺必需任务")
    require(gate["preserved_formal_p5_entry"] == current["phases"]["P5"], "原正式入口被替换")
    require("P5:development" not in current["phases"], "准备阶段不提前登记空验收")
    require(not set(TASK_IDS) & current["tasks"].keys(), "当前P5任务已登记实现，需重新核对范围")
    return {
        "designed_cases": len(cases),
        "executed_p5_cases": 0,
        "open_protocol_items": len(question_ids),
    }


def check_environment(package: Path) -> dict[str, Any]:
    saved = read(package / "environment.json")
    observed = environment()
    for key in ("python", "dependencies", "node", "npm", "node_selection_authority"):
        require(saved[key] == observed[key], "当前环境与准备基线不一致：" + key)
    require(saved["model"]["key_content_archived"] is False, "模型密钥不能归档")
    require(saved["model"]["live_call_status"] == "NOT_EXECUTED", "准备不能误报 live 模型")
    require(not any(observed["p5_paths_present"].values()), "P5代码已经存在，需重新核对准备范围")
    require(saved["frontend_build_status"] == "NOT_EXECUTED_P5-06", "准备不能误报前端构建")
    return {
        "python": observed["python"],
        "node": observed["node"],
        "npm": observed["npm"],
        "backend_dependency_count": len(observed["dependencies"]),
        "frontend_build_executed": False,
        "live_llm_call_executed": False,
    }


def verify(package: Path) -> dict[str, Any]:
    package = contained(ROOT, package)
    checks = (
        ("source_archives_and_freshness", check_sources),
        ("fixed_knowledge_and_csv_identity", check_knowledge),
        ("actual_p4_prerequisites", check_p4_evidence),
        ("profiles_scenarios_and_pending_gate", check_design),
        ("local_environment_and_user_node_selection", check_environment),
    )
    results = []
    for name, check in checks:
        details = check(package)
        results.append({"name": name, "status": "PASSED", "details": details})
    return {
        "status": "READY_FOR_P5_IMPLEMENTATION_WITH_PROTOCOL_OPEN_ITEMS",
        "kind": "PREPARATION_CHECK_ONLY",
        "package": package.relative_to(ROOT).as_posix(),
        "package_manifest_sha256": sha256(package / "manifest.json"),
        "checks": results,
        "p5_implementation_status": dict.fromkeys(TASK_IDS, "NOT_STARTED"),
        "p5_executed_test_count": 0,
        "official_dynamic_protocol_verified": False,
        "historical_p4_evidence_is_current_for_existing_sources": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=OUTPUT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = contained(ROOT, args.output)
    require(not output.exists(), "检查报告已存在，禁止覆盖历史；请指定新路径")
    started = time.perf_counter()
    try:
        report = verify(args.root)
        exit_code = 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        report = {"status": "FAILED", "kind": "PREPARATION_CHECK_ONLY", "error": str(exc)}
        exit_code = 1
    report.update(
        recorded_at=datetime.now(UTC).isoformat(),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        exit_code=exit_code,
        checker_sha256=sha256(Path(__file__)),
    )
    write_new(output, report)
    print(json.dumps({"status": report["status"], "report": str(args.output)}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
