"""明确合成的10g物料链，用于库存不足、已产出和重复消耗反例。"""

import pytest

from app.compiler.instantiate import instantiate
from app.compiler.material_flow import compile_material_flow
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.runtime_snapshot import ExecutionRecord, MaterialLot
from tests.compiler_support import menu_for, published_knowledge, runtime


def source():
    def demand(ident, spec):
        return {
            "requirement_id": ident,
            "spec_id": spec,
            "quantity_kind": "EXACT",
            "quantity": {"value": 10, "unit": "g", "scale": 1},
            "provenance_refs": ["synthetic"],
        }

    def operation(ident, input_spec, output_spec):
        return {
            "operation_id": ident,
            "action": "CUT",
            "duration": {"execution_sec": 60},
            "material_inputs": [demand(ident + "-in", input_spec)],
            "material_outputs": [demand(ident + "-out", output_spec)],
            "resource_requirements": [
                {"resource_type": "HUMAN", "resource_id": "human_1", "conflict_policy": "UNARY"}
            ],
        }

    recipe = CanonicalRecipeModel(
        schema_version="1.0",
        recipe_id="synthetic-material",
        name="合成10g物料",
        recipe_version="1",
        provenance_refs=("synthetic",),
        ingredient_requirements=(demand("source", "raw"),),
        material_specs=tuple(
            {"spec_id": s, "ingredient_id": "synthetic", "name": s, "state": s}
            for s in ("raw", "cut", "final")
        ),
        operations=(operation("a", "raw", "cut"), operation("b", "cut", "final")),
        dependencies=(
            {
                "predecessor_id": "a",
                "successor_id": "b",
                "reason": "合成生产依赖",
                "evidence_refs": ["synthetic"],
            },
        ),
    )
    knowledge = published_knowledge().model_copy(
        update={"recipes": (recipe,), "recipe_contexts": ()}
    )
    state = runtime(knowledge)
    inst = instantiate(menu_for(recipe), knowledge, state)
    flow = compile_material_flow(inst, knowledge, state)
    cut = next(s for s in flow.supplies if s.source_spec_id == "cut")
    return knowledge, state, inst, cut


def completed_case(amount=10, quality="QUALIFIED", at=60, produced=True):
    knowledge, state, inst, cut = source()
    quantity = {"value": amount, "unit": "g", "scale": 1}
    lot = MaterialLot(
        lot_id="synthetic-lot",
        spec_id=cut.supply_id,
        produced_by_execution_id="synthetic-done",
        produced_at=state.time_origin.at(at),
        quantity_produced=quantity,
        quantity_available=quantity,
        quantity_reserved={"value": 0, "unit": "g", "scale": 1},
        storage_state="fresh",
        source_event_id="synthetic-output",
        version=1,
        quality_status=quality,
    )
    execution = ExecutionRecord(
        execution_id="synthetic-done",
        task_ids=(inst.tasks[0].task_id,),
        status="COMPLETED",
        source="SIMULATED",
        event_refs=("synthetic-output",),
        started_at=state.time_origin.at(0),
        finished_at=state.time_origin.at(60),
        produced=({"lot_id": lot.lot_id, "spec_id": lot.spec_id, "quantity": quantity},)
        if produced
        else (),
    )
    return (
        knowledge,
        state.model_copy(
            update={"now_offset_sec": 60, "executions": (execution,), "material_lots": (lot,)}
        ),
        inst,
        cut,
    )


def test_ten_grams_actual_output_satisfies_ten_grams_once():
    knowledge, state, inst, cut = completed_case()
    flow = compile_material_flow(inst, knowledge, state)
    actual = next(s for s in flow.supplies if s.supply_id == cut.supply_id)
    assert actual.actual_lot_ids[0].root == "synthetic-lot"
    assert actual.available_share_numerator == actual.available_share_denominator == 1


@pytest.mark.parametrize(
    "kwargs", [{"amount": 9}, {"quality": "UNKNOWN"}, {"at": 61}, {"produced": False}]
)
def test_short_unknown_future_or_unproven_output_cannot_supply_plan(kwargs):
    knowledge, state, inst, _ = completed_case(**kwargs)
    with pytest.raises(ValueError):
        compile_material_flow(inst, knowledge, state)


def test_running_output_cannot_be_used_as_already_available_stock():
    knowledge, state, inst, _ = completed_case()
    record = state.executions[0].model_copy(
        update={
            "status": "RUNNING",
            "finished_at": None,
            "remaining_sec": 30,
            "remaining_source_ref": "synthetic",
        }
    )
    with pytest.raises(ValueError, match="运行"):
        compile_material_flow(inst, knowledge, state.model_copy(update={"executions": (record,)}))


def test_available_amount_cannot_exceed_recorded_actual_production():
    knowledge, state, inst, _ = completed_case()
    # 明确合成矛盾批次，不经过模型构造器掩盖 Compiler 的职责。
    from app.domain.quantity import ScaledQuantity

    lot = state.material_lots[0].model_copy(
        update={"quantity_produced": ScaledQuantity(value=9, unit="g", scale=1)}
    )
    with pytest.raises(ValueError, match="产出|生产"):
        compile_material_flow(inst, knowledge, state.model_copy(update={"material_lots": (lot,)}))


def test_future_producer_cannot_claim_existing_qualified_output():
    knowledge, state, inst, _ = completed_case()
    state = state.model_copy(update={"executions": ()})
    with pytest.raises(ValueError, match="产出|生产"):
        compile_material_flow(inst, knowledge, state)
