"""真实快照及常驻进程主链，覆盖强制批次、暂停程序和独立人工资源。"""

import pytest

from app.domain.policy import SchedulingPolicy
from app.domain.ports import PlanningRequest
from app.scheduling.worker import SolverWorker
from app.services.planning_core import PlanningCore
from tests.compiler_support import menu_for, published_knowledge, runtime
from tests.unit.test_cp_sat_model import deadline


@pytest.fixture
def core():
    # 各场景测量已预热的独立请求；连续复用和故障恢复由 worker/budget 测试覆盖。
    with SolverWorker() as worker:
        yield PlanningCore(solver=worker)


@pytest.mark.parametrize(
    "names",
    [
        ("亲朋欢聚套餐",),
        ("烹香酷炒汇",),
        ("糯米烧麦",),
        ("韩式泡菜鸦片鱼头", "亲朋欢聚套餐"),
    ],
)
def test_real_baseline_paths(core, names):
    knowledge = published_knowledge()
    recipes = tuple(next(r for r in knowledge.recipes if r.name == name) for name in names)
    request = PlanningRequest(
        request_id="baseline:" + "+".join(names),
        menu=menu_for(*recipes),
        policy=SchedulingPolicy(policy_version="p2-base-decimal-v1"),
    )
    result = core.compute(request, knowledge, runtime(), deadline(4.2))
    assert result.status == "VALIDATED", result.model_dump_json(indent=2)
    assert result.validation.valid
    assert len({t for a in result.candidate.assignments for t in a.task_ids}) == len(
        core.last_problem.logical_tasks
    )
    assert not core.last_problem.policy.shared_prep
    assert not core.last_problem.policy.strict_together_batch
    assert not result.rejected_candidates
