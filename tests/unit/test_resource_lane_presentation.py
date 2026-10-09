"""合成展示边界：真实部件分泳道，别名与已分配层位保留物理身份。"""

import pytest

from app.domain.schedule import CandidateSchedule, PublishedPlan, ScheduledAssignment
from app.domain.scheduling_problem import SchedulingProblem
from app.services.plan_presentation import presentation


def display_for(specs, *, advance_preparations=()):
    """直接构造已发布 DTO 的显示输入，不替代 Compiler/Validator 集成验收。"""
    devices = []
    tasks = []
    carriers = []
    assignments = []
    prepared_ids = {task for item in advance_preparations for task in item["task_ids"]}
    for index, (device_id, physical_id, component, layer, start, end) in enumerate(specs):
        use = {
            "resource_type": "DEVICE",
            "resource_id": device_id,
            "physical_resource_id": physical_id,
            "component_id": component,
            "conflict_policy": "STATE_COMPATIBLE" if layer else "UNARY",
            "layer_index": None if isinstance(layer, tuple) else layer,
            **(
                {"occupied_layer_indices": layer, "units": len(layer)}
                if isinstance(layer, tuple)
                else {}
            ),
            "configuration": [
                {"parameter": "temperature_c", "value": 100},
                {"parameter": "mode", "value": "synthetic"},
            ],
        }
        devices.append(
            {
                "device_instance_id": device_id,
                "physical_resource_id": physical_id,
                "component_id": component,
                "capacity": 3 if layer else 1,
                "conflict_policy": use["conflict_policy"],
            }
        )
        tasks.append(
            {
                "task_id": f"task-{index}",
                "recipe_instance_id": f"recipe-{index}",
                "operation_id": f"op-{index}",
                "operation": {
                    "operation_id": f"op-{index}",
                    "action": "HEAT",
                    "description": f"合成工序{index}",
                    "duration": {"execution_sec": end - start},
                    "resource_requirements": [use],
                },
            }
        )
        carriers.append(
            {
                "carrier_id": f"carrier-{index}",
                "kind": "STANDALONE",
                "covers": [f"task-{index}"],
                "duration_sec": end - start,
                "resource_uses": [use],
            }
        )
        assignments.append(
            ScheduledAssignment.model_validate(
                {
                    "carrier_id": f"carrier-{index}",
                    "task_ids": [f"task-{index}"],
                    "interval": {"start_sec": start, "end_sec": end},
                    "resource_uses": [use],
                }
            )
        )
    origin = {"start_at": "2026-10-08T08:00:00+08:00"}
    # 准备身份保留原任务/原时长，但不伪造候选加工、库存满足或已完成事件。
    for task in tasks:
        if task["task_id"] in prepared_ids:
            task["operation"]["action"] = "WAIT"
    carriers = [c for c in carriers if not prepared_ids.intersection(c["covers"])]
    assignments = [
        item
        for item in assignments
        if not prepared_ids.intersection(task.root for task in item.task_ids)
    ]
    problem = SchedulingProblem.model_validate(
        {
            "problem_id": "synthetic-display",
            "knowledge_version": "synthetic",
            "rule_version": "synthetic",
            "snapshot_id": "synthetic",
            "snapshot_schema_version": "1.0",
            "policy": {"policy_version": "synthetic"},
            "runtime": {
                "session_id": "synthetic-display",
                "state_revision": 0,
                "current_plan_version": 0,
                "knowledge_version": "synthetic",
                "rule_version": "synthetic",
                "snapshot_id": "synthetic",
                "time_origin": origin,
                "now_offset_sec": 0,
                "execution_mode": "MANUAL_CONFIRM",
            },
            "horizon_sec": 1200,
            "recipe_instances": [
                {
                    "recipe_instance_id": f"recipe-{i}",
                    "recipe_id": f"recipe-{i}",
                    "name": f"合成菜{i}",
                }
                for i in range(len(specs))
            ],
            "logical_tasks": tasks,
            "resources": list({d["device_instance_id"]: d for d in devices}.values()),
            "standalone_candidates": carriers,
            **({"advance_preparations": advance_preparations} if advance_preparations else {}),
        }
    )
    candidate = CandidateSchedule(problem_hash=problem.problem_hash, assignments=assignments)
    plan = PublishedPlan.model_validate(
        {
            "session_id": "synthetic-display",
            "plan_version": 1,
            "parent_plan_version": 0,
            "state_revision": 0,
            "knowledge_version": "synthetic",
            "snapshot_id": "synthetic",
            "time_origin": origin,
            "publication_id": "synthetic-display",
            "committed_at": origin["start_at"],
            "validated": {
                "candidate": candidate,
                "validation": {
                    "report_id": "synthetic-display-fixture",
                    "problem_hash": problem.problem_hash,
                    "candidate_hash": candidate.candidate_hash,
                    "validator_version": "synthetic-display-fixture",
                    "valid": True,
                },
            },
        }
    )
    return presentation(plan, problem)


@pytest.mark.parametrize(
    ("specs", "labels"),
    [
        (
            [
                ("burner_1", "stove_1", "burner_1", None, 0, 300),
                ("burner_2", "stove_1", "burner_2", None, 60, 180),
            ],
            ("灶眼一", "灶眼二"),
        ),
        (
            [
                ("fridge_cold_1", "fridge_1", "cold_zone", None, 0, 300),
                ("fridge_freezer_1", "fridge_1", "freezer_zone", None, 60, 180),
            ],
            ("冰箱 · 冷藏区", "冰箱 · 冷冻区"),
        ),
        (
            [
                ("channel-a", "appliance", "outlet-a", None, 0, 300),
                ("channel-b", "appliance", "outlet-b", None, 60, 180),
            ],
            ("appliance · outlet-a", "appliance · outlet-b"),
        ),
    ],
)
def test_independent_components_of_one_device_have_distinct_lanes(specs, labels):
    display = display_for(specs)
    assert display.resource_lanes == labels
    assert tuple(r.resource_label for r in display.resources) == labels
    assert len({r.resource_id for r in display.resources}) == 1
    assert len({r.component_id for r in display.resources}) == 2
    assert [(r.start_sec, r.end_sec) for r in display.resources] == [(0, 300), (60, 180)]
    assert not any(r.reuse_intervals for r in display.resources)


def test_device_aliases_with_one_physical_component_keep_one_lane():
    display = display_for(
        [
            ("oven-mode-a", "oven_1", "chamber", None, 0, 120),
            ("oven-mode-b", "oven_1", "chamber", None, 120, 240),
        ]
    )
    assert display.resource_lanes == ("烤箱",)
    assert {r.resource_label for r in display.resources} == {"烤箱"}


def test_layer_lanes_keep_empty_layers_and_exact_reuse_intersections():
    display = display_for(
        [
            ("steam_oven_1", "steam_oven_1", "chamber", 1, 0, 300),
            ("steam_oven_1", "steam_oven_1", "chamber", 2, 60, 180),
            ("oven_1", "oven_1", "chamber", 3, 0, 300),
        ]
    )
    assert display.resource_lanes == (
        "蒸箱 · 第1层",
        "蒸箱 · 第2层",
        "蒸箱 · 第3层",
        "烤箱 · 第1层",
        "烤箱 · 第2层",
        "烤箱 · 第3层",
    )
    assert [r.layer_index for r in display.resources] == [1, 2, 3]
    assert [
        [(interval.start_sec, interval.end_sec) for interval in r.reuse_intervals]
        for r in display.resources
    ] == [[(60, 180)], [(60, 180)], []]


def test_advance_preparation_moves_to_checklist_without_finished_operations_or_resource_bars():
    display = display_for(
        [
            ("fridge_cold_1", "fridge_1", "cold_zone", None, 0, 14400),
            ("burner_1", "stove_1", "burner_1", None, 0, 300),
        ],
        advance_preparations=[
            {
                "preparation_id": "synthetic-advance",
                "rule_id": "synthetic-user-preparation-policy",
                "recipe_instance_id": "recipe-0",
                "recipe_id": "recipe-0",
                "task_ids": ["task-0"],
                "operation_ids": ["op-0"],
                "available_at_sec": 0,
                "original_duration_sec": 14400,
                "source_kind": "USER_POLICY_ASSUMPTION",
                "evidence_refs": ["synthetic-user-instruction"],
                "description": "提前冷藏腌制4小时",
            }
        ],
    )
    assert [(op.task_id, op.start_sec, op.end_sec) for op in display.operations] == [
        ("task-1", 0, 300)
    ]
    assert not any(op.frozen or op.inventory_supplied for op in display.operations)
    assert display.resource_lanes == ("灶眼一",)
    assert [resource.task_ids for resource in display.resources] == [("task-1",)]
    assert display.range_end_sec == 300
    assert [item.model_dump(mode="json") for item in display.advance_preparations] == [
        {
            "preparation_id": "synthetic-advance",
            "recipe_instance_id": "recipe-0",
            "recipe_id": "recipe-0",
            "recipe_name": "合成菜0",
            "task_ids": ["task-0"],
            "description": "提前冷藏腌制4小时",
            "original_duration_sec": 14400,
            "source_kind": "USER_POLICY_ASSUMPTION",
        }
    ]


def test_old_plan_without_advance_preparation_keeps_full_timed_operations():
    display = display_for([("fridge_cold_1", "fridge_1", "cold_zone", None, 600, 900)])
    assert display.advance_preparations == ()
    assert [(op.start_sec, op.end_sec) for op in display.operations] == [(600, 900)]
    assert display.range_end_sec == 900
    assert [(row.start_sec, row.end_sec) for row in display.resources] == [(600, 900)]


@pytest.mark.parametrize("with_peer", [False, True])
def test_fixed_two_layers_project_once_per_layer_without_self_reuse(with_peer):
    specs = [("steam_oven_1", "steam_oven_1", "chamber", (1, 3), 0, 300)]
    if with_peer:
        specs.append(("steam_oven_1", "steam_oven_1", "chamber", 2, 60, 180))
    display = display_for(specs)
    own = [row for row in display.resources if row.task_ids == ("task-0",)]
    assert [row.layer_index for row in own] == [1, 3]
    assert len(display.operations) == (2 if with_peer else 1)
    assert len({row.entry_id for row in display.resources}) == len(display.resources)
    assert all((row.start_sec, row.end_sec) == (0, 300) for row in own)
    expected_reuse = [(60, 180)] if with_peer else []
    assert all(
        [(span.start_sec, span.end_sec) for span in row.reuse_intervals] == expected_reuse
        for row in own
    )
