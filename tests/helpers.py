"""明确标注为合成的契约样本，不作为真实菜谱审核依据。"""

from datetime import datetime

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.time import TimeOrigin


def recipe_payload():
    return {
        "schema_version": "1.0",
        "recipe_id": "synthetic-recipe",
        "name": "合成固定两批与介入",
        "recipe_version": "1",
        "review_status": "DRAFT",
        "provenance_refs": ["synthetic-source"],
        "ingredient_requirements": [],
        "material_specs": [],
        "operations": [
            {
                "operation_id": op_id,
                "action": action,
                "duration": {
                    "execution_sec": seconds,
                    "nominal_sec": seconds,
                    "source_ref": "synthetic-source",
                },
                "provenance_refs": ["synthetic-source"],
                "resource_requirements": (
                    [
                        {
                            "resource_type": "HUMAN",
                            "resource_id": "human_1",
                            "conflict_policy": "UNARY",
                        }
                    ]
                    if human
                    else []
                ),
                "execution_policy": policy,
            }
            for op_id, action, seconds, human, policy in [
                ("prep_a", "CUT", 60, True, {}),
                ("prep_b", "WAIT", 120, False, {}),
                (
                    "steam_1",
                    "HEAT",
                    600,
                    False,
                    {
                        "batch_policy": "FIXED_RECIPE",
                        "fixed_batch_id": "batch_1",
                        "thermal_group_id": "program_1",
                        "interventions": [
                            {"operation_id": "add", "offset_min_sec": 300, "offset_max_sec": 300}
                        ],
                    },
                ),
                ("add", "ADD", 60, True, {}),
                (
                    "steam_2",
                    "HEAT",
                    600,
                    False,
                    {"batch_policy": "FIXED_RECIPE", "fixed_batch_id": "batch_2"},
                ),
            ]
        ],
        "dependencies": [
            {
                "predecessor_id": "prep_a",
                "successor_id": "steam_1",
                "reason": "合成物料依赖",
                "evidence_refs": ["synthetic-source"],
            },
            {
                "predecessor_id": "prep_b",
                "successor_id": "steam_1",
                "reason": "合成物料依赖",
                "evidence_refs": ["synthetic-source"],
            },
            {
                "predecessor_id": "steam_1",
                "successor_id": "steam_2",
                "reason": "合成固定两批",
                "evidence_refs": ["synthetic-source"],
            },
        ],
    }


def recipe():
    return CanonicalRecipeModel.model_validate(recipe_payload())


def runtime():
    from app.domain.runtime_snapshot import RuntimeSnapshot

    return RuntimeSnapshot(
        session_id="session-test",
        state_revision=0,
        current_plan_version=0,
        knowledge_version="sample-v1",
        rule_version="rules-v1",
        snapshot_id="snapshot-test",
        time_origin=TimeOrigin(start_at=datetime.fromisoformat("2026-09-22T23:59:00+08:00")),
        now_offset_sec=0,
        execution_mode="SIMULATED",
    )


def problem():
    from app.domain.policy import SchedulingPolicy
    from app.domain.scheduling_problem import RecipeInstance, SchedulingProblem

    state = runtime()
    return SchedulingProblem(
        problem_id="problem-test",
        knowledge_version="sample-v1",
        rule_version="rules-v1",
        snapshot_id="snapshot-test",
        snapshot_schema_version="1.0",
        policy=SchedulingPolicy(policy_version="p0-baseline-v1"),
        runtime=state,
        horizon_sec=172800,
        recipe_instances=(
            RecipeInstance(
                recipe_instance_id="instance-1",
                recipe_id="synthetic-recipe",
                name="合成固定两批与介入",
            ),
        ),
    )
