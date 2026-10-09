"""真实发布的截图五菜，按默认预算编译、求解、独立校验并保留运行记录。"""

import time

from app.compiler.compiler import ProblemCompiler
from app.config import AppSettings
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.scheduling_problem import SchedulingProblem
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.scheduling.engine import PlanningEngine
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import menu_for, runtime


def test_default_five_dish_cooking_target_with_continuous_goal(tmp_path):
    settings = AppSettings()
    ref = read_release_ref(settings.release_root, settings.release_id)
    repository = SnapshotKnowledgeRepository(settings.release_root)
    repository.load(ref)
    names = {"五味乌鸡煲", "广式月饼（台式）", "Q弹果冻鸡蛋羹", "椰香苹果香蕉烤燕麦", "酿苦瓜"}
    with repository.acquire(ref) as lease:
        knowledge = lease.select(
            tuple(r.recipe_id for r in lease.loaded.snapshot.knowledge.recipes if r.name in names)
        )
    policy = SchedulingPolicy.model_validate_json(settings.policy_path.read_bytes())
    state = runtime(knowledge)
    started = time.monotonic_ns()
    deadline = Deadline(expires_at_ns=started + policy.initial_budget.total_ms * 1_000_000)
    problem = ProblemCompiler().compile(
        knowledge, menu_for(*knowledge.recipes), state, policy, deadline
    )
    assert isinstance(problem, SchedulingProblem), problem
    result = PlanningEngine(validator=ScheduleValidator()).plan(problem, knowledge, state, deadline)
    (tmp_path / "five-dish-result.json").write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    assert result.status == "VALIDATED", result.failure
    # 共用单蒸腔的90/15/12分钟三菜禁止不等长合批，出锅差至少15+12分钟。
    # 不能把延后装盘当作五分钟达标；预算内应优于串行参考。
    assert (
        1620
        <= result.candidate.metrics.cooking_finish_spread_sec
        < result.serial_reference_candidate.metrics.cooking_finish_spread_sec
    )
    assert len(result.candidate.metrics.recipe_cooking_finishes) == 5
    assert not result.total_human_objective_optimized
    assert any(stage.objective_stage == "E_QUALITY" for stage in result.stage_results)
    print(
        "elapsed_ms",
        (time.monotonic_ns() - started) // 1_000_000,
        "continuous_sec",
        result.candidate.metrics.max_continuous_human_sec,
        "human_optimized",
        result.human_objective_optimized,
    )
