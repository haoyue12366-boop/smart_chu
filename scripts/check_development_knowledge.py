"""报告真实 V3 在秒级和分钟网格的知识校验；不声称已经调度或人工审核。"""

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.pipeline.development import load_development_knowledge
from app.validation.knowledge import validate_knowledge

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    knowledge = load_development_knowledge(ROOT)
    reports = {}
    for grid in (1, 60):
        scope = knowledge.scope.model_copy(update={"time_grid_sec": grid})
        reports[str(grid)] = validate_knowledge(knowledge.recipes, knowledge.profiles, (), scope)
    artifact = {
        "kind": "REAL_DEVELOPMENT_KNOWLEDGE_CHECK",
        "checked_at": datetime.now(UTC).isoformat(),
        "knowledge_version": knowledge.knowledge_version,
        "rule_version": knowledge.scope.rule_version,
        "source_artifacts": [a.model_dump(mode="json") for a in knowledge.source_artifacts],
        "grids": {key: report.model_dump(mode="json") for key, report in reports.items()},
    }
    output = ROOT / "benchmarks/reports/verification/P1-04-development-knowledge.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: {
                    "valid": report.valid,
                    "usable_recipe_count": len(report.usable_recipe_ids),
                    "violation_counts": dict(Counter(v.code for v in report.violations)),
                    "formal_release_eligible": report.formal_release_eligible,
                    "single_recipe_solve_status": report.single_recipe_solve_status,
                }
                for key, report in reports.items()
            },
            ensure_ascii=False,
        )
    )
    return 0 if reports["1"].valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
