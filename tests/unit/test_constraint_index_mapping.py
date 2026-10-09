"""诊断映射须同时满足类别和每个非空筛选条件，不能扩大不可行核。"""

from app.domain.scheduling_problem import ConstraintRecord
from app.scheduling.model_builder import ModelBuilder
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_schedule_validator import example


def test_diagnostic_mapping_combines_filters_without_cross_category_leakage():
    _, _, problem, _ = example()
    first, second = (t.task_id for t in problem.logical_tasks)
    carrier = problem.standalone_candidates[0].carrier_id
    records = tuple(
        ConstraintRecord(
            constraint_id=identity,
            category=category,
            task_ids=tasks,
            carrier_ids=carriers,
            resource_ids=resources,
            hardness="MANDATORY",
            origin="COMPILER",
            expression_summary="独立诊断映射样本",
        )
        for identity, category, tasks, carriers, resources in (
            ("a", "CHECK", (first,), (carrier,), ("human_1",)),
            ("b", "CHECK", (second,), (carrier,), ("human_1",)),
            ("c", "CHECK", (first,), (), ("human_1",)),
            ("d", "CHECK", (first,), (carrier,), ("oven_1",)),
            ("e", "OTHER", (first,), (carrier,), ("human_1",)),
            ("f", "CHECK", (), (), ()),
        )
    )
    builder = ModelBuilder(problem.model_copy(update={"constraint_catalog": records}), deadline())
    builder.model.add(True)
    builder.mark("CHECK", 0, (first,), (carrier,), ("human_1",))
    assert dict(builder.constraint_indices) == {"a": {0}}
    builder.model.add(False)
    builder.mark("CHECK", 1, (first, second), resources=("human_1", "oven_1"))
    assert dict(builder.constraint_indices) == {"a": {0, 1}, "b": {1}, "c": {1}, "d": {1}}
    builder.model.add(True)
    builder.mark("CHECK", 2)
    assert dict(builder.constraint_indices) == {
        "a": {0, 1, 2},
        "b": {1, 2},
        "c": {1, 2},
        "d": {1, 2},
        "f": {2},
    }
    builder.mark("UNKNOWN", 0)
    builder.mark("CHECK", 0, resources=("absent",))
    assert "e" not in builder.constraint_indices


def test_repeated_diagnostic_id_does_not_combine_different_record_filters():
    _, _, problem, _ = example()
    first, second = (t.task_id for t in problem.logical_tasks)
    carrier = problem.standalone_candidates[0].carrier_id
    records = tuple(
        ConstraintRecord(
            constraint_id="same-id",
            category="CHECK",
            task_ids=tasks,
            carrier_ids=carriers,
            hardness="MANDATORY",
            origin="COMPILER",
            expression_summary="重复标识不跨记录拼接条件",
        )
        for tasks, carriers in (((first,), ()), ((second,), (carrier,)))
    )
    builder = ModelBuilder(problem.model_copy(update={"constraint_catalog": records}), deadline())
    builder.model.add(True)
    builder.mark("CHECK", 0, (first,), (carrier,))
    assert not builder.constraint_indices
    builder.mark("CHECK", 0, (second,), (carrier,))
    assert dict(builder.constraint_indices) == {"same-id": {0}}
