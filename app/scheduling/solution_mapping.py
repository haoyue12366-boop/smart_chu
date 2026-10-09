"""将实际选中载体映射为领域候选，不自行补全未求得的工序。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.schedule import CandidateSchedule, RecipeCompletion, ScheduledAssignment
from app.domain.time import Interval

if TYPE_CHECKING:
    from ortools.sat.python import cp_model

    from app.scheduling.model_builder import ModelBuilder


def map_solution(builder: ModelBuilder, solver: cp_model.CpSolver) -> CandidateSchedule:
    layers = {
        (task, occurrence.use.resource_id): solver.value(layer)
        for occurrence, layer in builder.layer_choices
        if solver.boolean_value(occurrence.presence)
        for task in occurrence.tasks
    }
    assignments = []
    for carrier in builder.candidates:
        if not solver.boolean_value(builder.selected[carrier.carrier_id]):
            continue
        assignments.append(
            ScheduledAssignment(
                carrier_id=carrier.carrier_id,
                task_ids=carrier.covers,
                interval=Interval(
                    start_sec=solver.value(builder.carrier_starts[carrier.carrier_id]),
                    end_sec=solver.value(builder.carrier_ends[carrier.carrier_id]),
                ),
                resource_uses=tuple(
                    use.model_copy(
                        update={"layer_index": layers[carrier.covers[0], use.resource_id]}
                    )
                    if (carrier.covers[0], use.resource_id) in layers
                    else use
                    for use in carrier.resource_uses
                ),
            )
        )
    completions = tuple(
        RecipeCompletion(
            recipe_instance_id=instance.recipe_instance_id,
            completion_sec=max(
                solver.value(builder.ends[t.task_id])
                for t in builder.problem.logical_tasks
                if t.recipe_instance_id == instance.recipe_instance_id
            ),
        )
        for instance in builder.problem.recipe_instances
    )
    return CandidateSchedule(
        problem_hash=builder.problem.problem_hash,
        assignments=tuple(assignments),
        recipe_completions=completions,
    )
