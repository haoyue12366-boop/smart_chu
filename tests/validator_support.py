"""显式合成设备矩阵；不写入真实知识发布或声称真实工艺已审核。"""

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.resources import DeviceInstance, DeviceProfile
from app.domain.schedule import CandidateSchedule, ScheduledAssignment
from app.domain.time import Interval
from tests.compiler_support import published_knowledge, runtime
from tests.unit.test_problem_compilation import compile_menu


def resource_example(policy="UNARY", *, independent=False, human=False, different=False):
    base = published_knowledge()
    evidence = base.provenance_index[0].evidence_id
    rule = base.release.rule_version
    profile = DeviceProfile(
        profile_id="synthetic-profile",
        device_type="synthetic",
        mode="test",
        constraints=({"parameter": "temperature_c", "minimum": 0, "maximum": 100},),
        rule_version=rule,
        provenance_refs=(evidence,),
    )
    devices = tuple(
        DeviceInstance(
            device_instance_id=f"synthetic-device-{i}",
            physical_resource_id="synthetic-physical",
            component_id=f"zone-{i if independent else 0}",
            capability_refs=(profile.profile_id,),
            conflict_policy=policy,
            rule_version=rule,
            evidence_refs=(evidence,),
        )
        for i in range(2)
    )
    operations = []
    for i, device in enumerate(devices):
        uses = [
            {
                "resource_type": "DEVICE",
                "resource_id": device.device_instance_id,
                "physical_resource_id": device.physical_resource_id,
                "component_id": device.component_id,
                "conflict_policy": policy,
                "rule_version": rule,
                "evidence_refs": [evidence],
                "configuration": [
                    {"parameter": "mode", "value": "test"},
                    {"parameter": "temperature_c", "value": 20 if different and i else 10},
                ],
            }
        ]
        if human:
            uses.append(
                {"resource_type": "HUMAN", "resource_id": "human_1", "conflict_policy": "UNARY"}
            )
        operations.append(
            {
                "operation_id": f"synthetic-op-{i}",
                "action": "HEAT",
                "duration": {"execution_sec": 60 * (i + 1)},
                "resource_requirements": uses,
            }
        )
    recipe = CanonicalRecipeModel(
        schema_version="1.0",
        recipe_id="synthetic-device-matrix",
        recipe_version="1",
        name="合成设备矩阵",
        provenance_refs=(evidence,),
        operations=operations,
        ingredient_requirements=(),
        material_specs=(),
        dependencies=(),
    )
    knowledge = base.model_copy(
        update={
            "recipes": (recipe,),
            "devices": devices,
            "profiles": (profile,),
            "device_choices": (),
            "recipe_contexts": (),
        }
    )
    state = runtime(knowledge)
    problem = compile_menu(recipe, state=state, knowledge=knowledge)
    assignments = tuple(
        ScheduledAssignment(
            carrier_id=c.carrier_id,
            task_ids=c.covers,
            interval=Interval(start_sec=0, end_sec=c.duration_sec),
            resource_uses=c.resource_uses,
        )
        for c in problem.standalone_candidates
    )
    candidate = CandidateSchedule(problem_hash=problem.problem_hash, assignments=assignments)
    return knowledge, state, problem, candidate
