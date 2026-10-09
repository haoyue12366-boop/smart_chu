"""切换问题时旧CP模型必须先释放；失败的新建模不能留下错身份缓存。"""

import weakref

from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.model_builder import ModelBuilder
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_schedule_validator import example


def test_old_model_is_released_before_building_different_problem(monkeypatch):
    _, _, problem, _ = example()
    solver = CpSatScheduler()
    solver._builder(problem, deadline())
    previous = weakref.ref(solver._base_builder)
    assert previous() is not None
    new = problem.model_copy(update={"horizon_sec": problem.horizon_sec + 1})
    assert new.problem_hash != problem.problem_hash
    original = ModelBuilder.build

    def observe(builder):
        assert previous() is None, "构建新问题时仍持有旧基础模型"
        original(builder)

    monkeypatch.setattr(ModelBuilder, "build", observe)
    solver._builder(new, deadline())
    assert solver._base_builder.problem.problem_hash == new.problem_hash


def test_failed_new_problem_leaves_no_previous_model_cache(monkeypatch):
    _, _, problem, _ = example()
    solver = CpSatScheduler()
    solver._builder(problem, deadline())
    new = problem.model_copy(update={"horizon_sec": problem.horizon_sec + 1})
    original = ModelBuilder.build

    def expire(builder):
        raise TimeoutError("synthetic builder deadline")

    monkeypatch.setattr(ModelBuilder, "build", expire)
    result = solver.solve(new, None, deadline())
    assert result.status == "UNKNOWN" and result.candidate is None
    assert solver._base_builder is None
    monkeypatch.setattr(ModelBuilder, "build", original)
    restored = solver.solve(problem, None, deadline())
    assert restored.status == "OPTIMAL"
    assert restored.problem_hash == problem.problem_hash
