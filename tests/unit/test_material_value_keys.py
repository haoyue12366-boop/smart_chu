"""独立核验不能把内部篡改的布尔/浮点数量当作严格整数。"""

import pytest

from tests.unit.test_schedule_validator import example, validate


@pytest.mark.parametrize("field", ["scale", "value"])
@pytest.mark.parametrize("location", ["demand", "port"])
def test_noninteger_material_values_are_rejected(field, location):
    knowledge, state, problem, candidate = example()

    def mutate(requirement):
        quantity = requirement.quantity
        assert quantity is not None
        original = getattr(quantity, field)
        replacement = True if field == "scale" else float(original)
        assert replacement == original
        return requirement.model_copy(
            update={"quantity": quantity.model_copy(update={field: replacement})}
        )

    if location == "demand":
        demand, *others = problem.material_flow.demands
        problem = problem.model_copy(
            update={
                "material_flow": problem.material_flow.model_copy(
                    update={
                        "demands": (
                            demand.model_copy(update={"requirement": mutate(demand.requirement)}),
                            *others,
                        )
                    }
                )
            }
        )
    else:
        carrier, *others = problem.standalone_candidates
        requirement, *ports = carrier.material_inputs
        problem = problem.model_copy(
            update={
                "standalone_candidates": (
                    carrier.model_copy(update={"material_inputs": (mutate(requirement), *ports)}),
                    *others,
                )
            }
        )
    candidate = candidate.model_copy(update={"problem_hash": problem.problem_hash})
    report = validate(knowledge, state, problem, candidate)
    assert not report.valid
    assert "MATERIAL_BINDING" in {issue.code for issue in report.violations}
