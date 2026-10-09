"""提前准备保持原食材说明，不能出现在比赛加工时间线上。"""

import pytest

from app.api.competition_adapter import CompetitionAdapter
from app.domain.schedule import PublishedPlan
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.metrics import compute_metrics
from app.validation.competition_contract import validate_competition_projection
from app.validation.schedule import ScheduleValidator
from tests.unit.test_advance_preparation import compile_case, menu_session
from tests.unit.test_cp_sat_model import deadline


def prepared_plan():
    knowledge, session = menu_session()
    problem = compile_case(knowledge, session)
    result = GreedyScheduler().solve(problem, deadline())
    assert result.candidate is not None
    candidate = result.candidate.model_copy(
        update={"metrics": compute_metrics(result.candidate, problem)}
    )
    validation = ScheduleValidator().validate(knowledge, session.runtime, problem, candidate)
    assert validation.valid, validation.violations
    validated = {"candidate": candidate, "validation": validation}
    plan = PublishedPlan.model_validate(
        {
            "session_id": session.runtime.session_id,
            "plan_version": 1,
            "parent_plan_version": 0,
            "state_revision": session.runtime.state_revision,
            "knowledge_version": problem.knowledge_version,
            "snapshot_id": problem.snapshot_id,
            "time_origin": session.runtime.time_origin,
            "validated": validated,
            "serial_reference": validated,
            "publication_id": "prepared-projection",
            "committed_at": session.runtime.time_origin.start_at,
        }
    )
    return knowledge, problem, plan


def test_preparation_is_documented_without_fake_timed_processing():
    knowledge, problem, plan = prepared_plan()
    response = CompetitionAdapter().to_response(plan, problem, knowledge)
    validate_competition_projection(response, plan, problem, knowledge, timezone="Asia/Shanghai")
    assert len(response.model_dump()) == 5
    prepared = response.recipeDetail[0].cookingSteps[0]
    assert "开工前准备" in prepared.describe and "提前洗净并浸泡" in prepared.describe
    assert "240.5分钟" in prepared.describe
    assert prepared.cookingParameters is None
    assert len(response.detailTimeline) == 3
    assert any("cool" in line for row in response.detailTimeline for line in row.list)
    assert all("soak" not in line for row in response.detailTimeline for line in row.list)
    assert response.ingredientsSummary
    changed = response.model_copy(
        update={
            "recipeDetail": (
                response.recipeDetail[0].model_copy(
                    update={"cookingSteps": response.recipeDetail[0].cookingSteps[1:]}
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="准备|步骤"):
        validate_competition_projection(changed, plan, problem, knowledge, timezone="Asia/Shanghai")
