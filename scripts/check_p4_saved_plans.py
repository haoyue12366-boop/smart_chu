"""独立重载 P4 准备包的 100 份历史计划，不覆盖准备证据。"""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.domain.ids import RecipeId
from app.domain.reports import PlanningResult
from app.domain.scheduling_problem import SchedulingProblem
from app.knowledge.loader import load_release, read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.validation.schedule import ScheduleValidator

ROOT = Path(__file__).resolve().parents[1]


def validate(output: Path) -> dict:
    release_root = ROOT / "data/preparations/p4-v1/releases"
    release = read_release_ref(release_root, "delegated-v3-p4-preparation-v1-all")
    repository = SnapshotKnowledgeRepository(release_root)
    repository.load(release)
    report_path = ROOT / "benchmarks/reports/P4-preparation-all-recipes/report.json"
    report = json.loads(report_path.read_bytes())
    if report["release"] != release.model_dump(mode="json") or report["success_count"] != 100:
        raise ValueError("历史报告的发布身份或完整数量不符")
    identities = tuple(RecipeId(row["recipe_id"]) for row in report["results"])
    if len(identities) != 100 or len(set(identities)) != 100:
        raise ValueError("历史报告必须恰好覆盖 100 个不同菜谱身份")
    knowledge = repository.select(identities)
    operation_count = sum(len(recipe.operations) for recipe in knowledge.recipes)
    canonical = load_release(release_root, release).snapshot.knowledge
    if operation_count != 1562 or canonical.content_hash != report["canonical_hash"]:
        raise ValueError("固定知识工序或内容身份不符")
    proofs = []
    for row in report["results"]:
        path = report_path.parent / row["plan_artifact"]
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != row["artifact_sha256"]:
            raise ValueError("历史计划原始哈希不符：" + row["recipe_id"])
        payload = json.loads(raw)
        if payload["release"] != release.model_dump(mode="json"):
            raise ValueError("历史计划绑定了其他发布")
        problem = SchedulingProblem.model_validate(payload["problem"])
        result = PlanningResult.model_validate(payload["result"])
        if result.status != "VALIDATED" or result.candidate is None:
            raise ValueError("历史计划不是完整成功候选")
        proof = ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate)
        if not proof.valid:
            raise ValueError("历史计划独立校验失败：" + proof.model_dump_json())
        proofs.append({"path": path.relative_to(ROOT).as_posix(), "sha256": digest})
    result_report = {
        "status": "PASSED",
        "scope": "Independent validation of 100 saved plans; not P4 phase acceptance",
        "recorded_at": datetime.now(UTC).isoformat(),
        "command": [sys.executable, *sys.argv],
        "release": release.model_dump(mode="json"),
        "persisted_plan_revalidation_count": len(proofs),
        "operation_count": operation_count,
        "plan_artifacts": proofs,
        "validation_source_hashes": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ("app/domain", "app/validation")
            for path in sorted((ROOT / folder).rglob("*.py"))
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.output)
    print(f"{result['persisted_plan_revalidation_count']}/100 plans passed")


if __name__ == "__main__":
    main()
