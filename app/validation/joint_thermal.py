"""从发布菜谱、整组规则独立重建热暴露及人工阶段，不依赖生成器。"""

from collections import Counter

from app.domain.base import content_hash
from app.domain.candidates import stable_id
from app.domain.compatibility import GroupRuleSpec
from app.domain.ids import TaskId
from app.domain.resources import ResourceUse
from app.domain.scheduling_problem import CandidateCarrier, LogicalTask
from app.validation.joint_transition import checked_transition
from app.validation.recovery import replaced_failures
from app.validation.schedule_context import Scan


def check_joint_thermal(scan: Scan, carrier: CandidateCarrier) -> bool:
    def reject(message: str) -> bool:
        scan.fail("THERMAL_BATCH", message, carrier.carrier_id.root)
        return False

    if not scan.problem.policy.strict_together_batch or len(carrier.rule_refs) != 1:
        return reject("共同热批次未启用或没有唯一整组规则")
    rule = next((r for r in scan.knowledge.rules if r.rule_id == carrier.rule_refs[0]), None)
    if (
        rule is None
        or rule.kind != "STRICT_TOGETHER"
        or rule.rule_version != scan.problem.rule_version
    ):
        return reject("热批次规则类型或版本错误")
    try:
        spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
    except ValueError:
        return reject("热批次规则结构不可解释")
    allowed = (
        (rule.review_status == "APPROVED")
        if spec.authority == "HUMAN_REVIEWED"
        else (
            scan.problem.policy.allow_delegated_shared_estimates
            and scan.knowledge.release.release_kind == "development"
            and rule.review_status == "NEEDS_REVIEW"
        )
    )
    if not allowed or not rule.evidence_refs:
        return reject("热批次审核状态、委托许可或证据不完整")
    transition = checked_transition(scan, carrier, rule)
    if carrier.transition_binding is not None and transition is None:
        return False
    evidence = tuple(
        dict.fromkeys((*rule.evidence_refs, *(transition[0].evidence_refs if transition else ())))
    )
    if carrier.provenance_refs != evidence:
        return reject("热批次或转换来源证据不完整")
    if spec.thermal_model != "COLD_LOAD_SETUP_PREHEAT_HEAT_STOP_UNLOAD":
        return reject("没有明确的装卸与有效热暴露边界")
    common = set.intersection(*(set(b.configuration_keys) for b in spec.bindings))
    if not common or len({b.processing_spec for b in spec.bindings}) != 1:
        return reject("整组配置或加工规格不兼容")
    tasks = {t.task_id: t for t in scan.problem.logical_tasks}
    instances = {i.recipe_instance_id: i.recipe_id for i in scan.problem.recipe_instances}
    recipes = {r.recipe_id: r for r in scan.knowledge.recipes}
    contexts = {c.recipe_id: c for c in scan.knowledge.recipe_contexts}
    if len(set(carrier.covers)) != len(carrier.covers) or any(
        t not in tasks or t not in scan.operations for t in carrier.covers
    ):
        return reject("热批次覆盖重复或缺少来源")
    heats = [tasks[t] for t in carrier.covers if scan.operations[t].action == "HEAT"]
    identities = [(instances[t.recipe_instance_id], t.operation_id) for t in heats]
    if Counter(identities) != Counter((b.recipe_id, b.operation_id) for b in spec.bindings):
        return reject("加热成员与完整规则范围不同")
    actions = ("LOAD", "PREPARE", "PREHEAT", "HEAT", "UNLOAD")
    chains: list[dict[str, LogicalTask]] = []
    reservation_ids = []
    for binding in spec.bindings:
        heat = next(
            t
            for t in heats
            if (instances[t.recipe_instance_id], t.operation_id)
            == (binding.recipe_id, binding.operation_id)
        )
        op = scan.operations[heat.task_id]
        if binding.recipe_hash != recipes[binding.recipe_id].semantic_hash() or (
            binding.operation_hash != content_hash(op)
            or op.duration.execution_sec != spec.duration_sec
        ):
            return reject("热成员来源哈希或有效加热时长偏离规则")
        context = contexts.get(binding.recipe_id)
        reservations = (
            [r for r in context.resource_reservations if heat.operation_id in r.members]
            if context
            else []
        )
        if len(reservations) != 1:
            return reject("热成员缺少唯一完整外层预约")
        reservation = reservations[0]
        members = [
            t
            for t in tasks.values()
            if t.recipe_instance_id == heat.recipe_instance_id
            and t.operation_id in reservation.members
        ]
        if len(members) != len(actions) or sorted(
            scan.operations[t.task_id].action for t in members
        ) != sorted(actions):
            return reject("源预约不是完整的装入设置预热加热取出流程")
        if len(reservation.members) != len(members):
            return reject("源预约有未覆盖工序")
        if (
            len(carrier.resource_uses) != 1
            or carrier.resource_uses[0].resource_id not in reservation.resource_options
        ):
            return reject("共同腔体不属于源预约允许设备")
        if carrier.resource_uses[0].conflict_policy != reservation.policy:
            return reject("共同腔体的竞争策略偏离源预约")
        chains.append({scan.operations[t.task_id].action: t for t in members})
        reservation_ids.append(
            stable_id("reservation", heat.recipe_instance_id.root, reservation.reservation_id)
        )
    members = [chain[action] for chain in chains for action in actions]
    if (
        Counter(t.task_id for t in members) != Counter(carrier.covers)
        or tuple(reservation_ids) != carrier.replaced_reservation_ids
    ):
        return reject("被替代的工序或预约身份遗漏、重复或增加")
    retried = replaced_failures(scan)
    fixed = {
        t
        for e in scan.runtime.executions
        if e.execution_id not in retried
        and (e.status not in {"PENDING", "READY"} or e.started_at is not None)
        for t in e.task_ids
    }
    devices = {d.device_instance_id: d for d in scan.knowledge.devices}
    source_device_uses = []
    for task in members:
        source = scan.operations[task.task_id]
        policy = source.execution_policy
        if (
            task.task_id in fixed
            or policy.fixed_batch_id
            or policy.interventions
            or policy.batch_policy == "FIXED_RECIPE"
        ):
            return reject("共同热批次替代了已发生事实或固定工艺")
        uses = source.resource_requirements
        device_uses = [u for u in uses if u.resource_type == "DEVICE"]
        humans = [u for u in uses if u.resource_type == "HUMAN"]
        if len(device_uses) != 1 or len(humans) != (source.action in {"LOAD", "PREPARE", "UNLOAD"}):
            return reject("源热模型包含未支持的设备或人工介入")
        use = device_uses[0]
        device = devices.get(use.resource_id)
        if device is None or (use.physical_resource_id, use.component_id) != device.competition_key:
            return reject("源热设备缺少一致物理映射")
        if any(u.occupancy is not None or u.occupation_policy != "WHOLE_INTERVAL" for u in uses):
            return reject("源阶段不是完整区间占用")
        source_device_uses.append(use)
    outer = carrier.resource_uses[0]
    if any(
        u.model_dump(exclude={"evidence_refs"}) != outer.model_dump(exclude={"evidence_refs"})
        for u in source_device_uses
    ):
        return reject("共同腔体、模式温度湿度或占用策略与源成员不同")
    if set(outer.evidence_refs) != {e for u in source_device_uses for e in u.evidence_refs}:
        return reject("共同腔体来源证据不完整")
    cursor = 0
    expected_ports = {}
    expected_phases: list[tuple[TaskId, int, int, ResourceUse]] = []
    for action in actions:
        stage_tasks = [c[action] for c in chains]
        lengths = [scan.operations[t.task_id].duration.execution_sec for t in stage_tasks]
        if any(d is None or d <= 0 for d in lengths):
            return reject("源阶段缺少正整数时长")
        durations = [d for d in lengths if d is not None]
        if action in {"PREHEAT", "HEAT"} and len(set(durations)) != 1:
            return reject("成员的预热或有效加热时长不同")
        stage_duration = durations[0] if action in {"PREHEAT", "HEAT"} else sum(durations)
        if action == "PREHEAT" and transition is not None:
            thermal_binding = carrier.transition_binding
            assert thermal_binding is not None
            if (
                thermal_binding.source_preheat_sec != durations[0]
                or thermal_binding.transition_offset_sec != cursor
                or thermal_binding.preheat_task_ids != tuple(t.task_id for t in stage_tasks)
            ):
                return reject("转换替代范围或原预热边界不一致")
            assert transition[0].transition_duration_sec is not None
            stage_duration = (
                0 if thermal_binding.completed_state_ref else transition[0].transition_duration_sec
            )
            human = next(
                u
                for u in scan.operations[chains[0]["LOAD"].task_id].resource_requirements
                if u.resource_type == "HUMAN"
            )
            expected_phases.extend(
                (
                    stage_tasks[0].task_id,
                    cursor + p.start_offset_sec,
                    cursor + p.end_offset_sec,
                    human.model_copy(update={"evidence_refs": transition[0].evidence_refs}),
                )
                for p in (() if thermal_binding.completed_state_ref else transition[1].human_phases)
            )
        end = cursor + stage_duration
        manual = cursor
        for task, duration in zip(stage_tasks, durations, strict=True):
            expected_ports[task.task_id] = (cursor, end)
            for use in scan.operations[task.task_id].resource_requirements:
                if use.resource_type == "HUMAN":
                    expected_phases.append((task.task_id, manual, manual + duration, use))
                    manual += duration
        cursor = end
    actual_ports = {
        p.task_id: (p.start_offset_sec, p.end_offset_sec) for p in carrier.member_offsets
    }
    actual_phases = [
        (p.task_id, p.start_offset_sec, p.end_offset_sec, p.resource_use)
        for p in carrier.resource_phases
    ]
    if len(carrier.member_offsets) != len(expected_ports) or actual_ports != expected_ports:
        return reject("逻辑时间映射与源阶段完成边界不同")
    expected_phases.sort(key=lambda p: (p[1], p[2], p[0].root))
    if actual_phases != expected_phases or cursor != carrier.duration_sec:
        return reject("实际人工阶段或外层预约时长被改变")
    return True
