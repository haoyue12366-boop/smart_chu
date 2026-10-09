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
    # 当前发布已采用三层蒸箱，允许同温兼容的菜独立进出。
    # 旧单层互斥版本的1620秒下界在这里不成立；仍检查真实出锅时刻，
    # 不把延后装盘当作集中出菜，并重新独立核验全部资源和物料。
    steam = next(d for d in knowledge.devices if d.device_instance_id == "steam_oven_1")
    assert steam.capacity == 3
    assert (
        0
        <= result.candidate.metrics.cooking_finish_spread_sec
        <= policy.objective.spread_target_sec
        < result.serial_reference_candidate.metrics.cooking_finish_spread_sec
    )
    proof = ScheduleValidator().validate(knowledge, state, problem, result.candidate)
    assert proof.valid, proof.violations
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
