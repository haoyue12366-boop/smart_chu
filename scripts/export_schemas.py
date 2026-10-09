"""确定性导出领域与协议 Schema，不读取数据库或调用模型。"""

import json
from pathlib import Path

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.competition_contract import REQUEST_ADAPTER, CompetitionResponse
from app.domain.events import RuntimeEvent
from app.domain.extraction import ExtractionRun
from app.domain.policy import SchedulingPolicy
from app.domain.recovery import RecoveryRuleSpec
from app.domain.review import ReviewPatch
from app.domain.runtime_planning import RuntimePlanningResult
from app.domain.runtime_session import RuntimeSession
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import SchedulingProblem

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    directory = ROOT / "data/schemas"
    directory.mkdir(parents=True, exist_ok=True)
    schemas = {
        "competition_request": REQUEST_ADAPTER.json_schema(),
        "competition_response": CompetitionResponse.model_json_schema(),
        "canonical_recipe": CanonicalRecipeModel.model_json_schema(),
        "scheduling_problem": SchedulingProblem.model_json_schema(),
        "runtime_event": RuntimeEvent.model_json_schema(),
        "recovery_rule": RecoveryRuleSpec.model_json_schema(),
        "review_patch": ReviewPatch.model_json_schema(),
        "extraction_run": ExtractionRun.model_json_schema(),
        "scheduling_policy": SchedulingPolicy.model_json_schema(),
        "runtime_snapshot": RuntimeSnapshot.model_json_schema(),
        "runtime_session": RuntimeSession.model_json_schema(),
        "runtime_planning_result": RuntimePlanningResult.model_json_schema(),
    }
    for name, schema in schemas.items():
        (directory / f"{name}.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
