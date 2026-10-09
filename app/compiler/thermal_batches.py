"""显式冷启动STRICT_TOGETHER；人工源动作串行，逻辑产物按组阶段释放。"""

import time
from collections.abc import Iterator
from itertools import product

from app.compiler.thermal_variants import transition_variants
from app.domain.base import content_hash
from app.domain.candidates import SharedCandidateContext, ThermalBatchCandidate, stable_id
from app.domain.compatibility import GroupRuleSpec
from app.domain.ids import CarrierId
from app.domain.recovery import superseded_failures
from app.domain.scheduling_problem import CarrierResourcePhase, LogicalTask, MemberTimeOffset
from app.knowledge.rules import RuleEngine


def iter_thermal_batches(context: SharedCandidateContext) -> Iterator[ThermalBatchCandidate]:
    def budget() -> None:
        if time.monotonic_ns() >= context.deadline.expires_at_ns:
            raise TimeoutError("热批次生成截止时间已到")

    budget()
    knowledge = context.group.knowledge
    instances = {i.recipe_instance_id: i.recipe_id for i in context.instantiated.menu}
    tasks = context.instantiated.tasks
    contexts = {c.recipe_id: c for c in knowledge.recipe_contexts}
    singles = {c.covers[0]: c for c in context.standalone if len(c.covers) == 1}
    retried = superseded_failures(context.group.runtime)
    fixed = {
        t
        for e in context.group.runtime.executions
        if e.execution_id not in retried
        and (e.status not in {"PENDING", "READY"} or e.started_at is not None)
        for t in e.task_ids
    }
    actions = ("LOAD", "PREPARE", "PREHEAT", "HEAT", "UNLOAD")
    for rule in knowledge.rules:
        budget()
        if rule.kind != "STRICT_TOGETHER":
            continue
        try:
            spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        except ValueError:
            continue
        if spec.thermal_model is None:
            continue
        choices = [
            tuple(
                t
                for t in tasks
                if instances[t.recipe_instance_id] == b.recipe_id
                and t.operation_id == b.operation_id
            )
            for b in spec.bindings
        ]
        for heats in product(*choices):
            budget()
            group = context.group.model_copy(
                update={"knowledge": knowledge.model_copy(update={"rules": (rule,)})}
            )
            decision = RuleEngine().evaluate_group(heats, group)
            if not decision.compatible:
                continue
            chains: list[dict[str, LogicalTask]] = []
            reservation_ids = []
            for heat in heats:
                source = contexts.get(instances[heat.recipe_instance_id])
                reservations = (
                    [r for r in source.resource_reservations if heat.operation_id in r.members]
                    if source
                    else []
                )
                if len(reservations) != 1:
                    break
                reservation = reservations[0]
                members = tuple(
                    t
                    for t in tasks
                    if t.recipe_instance_id == heat.recipe_instance_id
                    and t.operation_id in reservation.members
                )
                if len(members) != len(actions) or sorted(
                    t.operation.action for t in members
                ) != sorted(actions):
                    break
                if any(
                    t.task_id in fixed
                    or t.task_id not in singles
                    or t.operation.execution_policy.fixed_batch_id
                    or t.operation.execution_policy.interventions
                    or t.operation.execution_policy.batch_policy == "FIXED_RECIPE"
                    for t in members
                ):
                    break
                chains.append({t.operation.action: t for t in members})
                reservation_ids.append(
                    stable_id(
                        "reservation", heat.recipe_instance_id.root, reservation.reservation_id
                    )
                )
            if len(chains) != len(heats):
                continue
            all_tasks = tuple(chain[a] for chain in chains for a in actions)
            if len({t.task_id for t in all_tasks}) != len(all_tasks):
                continue
            device_uses = [
                u
                for t in all_tasks
                for u in singles[t.task_id].resource_uses
                if u.resource_type == "DEVICE"
            ]
            if len(device_uses) != len(all_tasks):
                continue
            signature = device_uses[0].model_dump(exclude={"evidence_refs"})
            if any(u.model_dump(exclude={"evidence_refs"}) != signature for u in device_uses):
                continue
            # The explicit thermal model has no active manual heat/preheat intervention.
            if any(
                any(u.resource_type == "HUMAN" for u in singles[c[a].task_id].resource_uses)
                for c in chains
                for a in ("PREHEAT", "HEAT")
            ):
                continue
            if any(
                sum(u.resource_type == "HUMAN" for u in singles[c[a].task_id].resource_uses) != 1
                for c in chains
                for a in ("LOAD", "PREPARE", "UNLOAD")
            ):
                continue
            durations = {a: [singles[c[a].task_id].duration_sec for c in chains] for a in actions}
            if any(v <= 0 for values in durations.values() for v in values):
                continue
            if len(set(durations["PREHEAT"])) != 1 or set(durations["HEAT"]) != {spec.duration_sec}:
                continue
            offsets = []
            phases = []
            cursor = 0
            for action in actions:
                stage_members = [chain[action] for chain in chains]
                stage_duration = (
                    durations[action][0]
                    if action in {"HEAT", "PREHEAT"}
                    else sum(durations[action])
                )
                finish = cursor + stage_duration
                manual = cursor
                for task in stage_members:
                    offsets.append(
                        MemberTimeOffset(
                            task_id=task.task_id, start_offset_sec=cursor, end_offset_sec=finish
                        )
                    )
                    for use in singles[task.task_id].resource_uses:
                        if use.resource_type == "HUMAN":
                            end = manual + singles[task.task_id].duration_sec
                            phases.append(
                                CarrierResourcePhase(
                                    task_id=task.task_id,
                                    start_offset_sec=manual,
                                    end_offset_sec=end,
                                    resource_use=use,
                                )
                            )
                            manual = end
                cursor = finish
            # All original internal dependencies, including zero maximum lags, remain true.
            ports = {p.task_id: p for p in offsets}
            if any(
                (
                    ports[d.successor_id].start_offset_sec - ports[d.predecessor_id].end_offset_sec
                    < d.min_lag_sec
                    or (
                        d.max_lag_sec is not None
                        and ports[d.successor_id].start_offset_sec
                        - ports[d.predecessor_id].end_offset_sec
                        > d.max_lag_sec
                    )
                )
                for d in context.instantiated.dependencies
                if d.predecessor_id in ports and d.successor_id in ports
            ):
                continue
            outer = device_uses[0].model_copy(
                update={
                    "evidence_refs": tuple(
                        dict.fromkeys(e for u in device_uses for e in u.evidence_refs)
                    )
                }
            )
            batch = ThermalBatchCandidate(
                carrier_id="pending",
                kind="THERMAL_BATCH",
                covers=tuple(t.task_id for t in all_tasks),
                duration_sec=cursor,
                resource_uses=(outer,),
                rule_refs=decision.rule_refs,
                provenance_refs=decision.evidence_refs,
                material_inputs=tuple(
                    m for t in all_tasks for m in singles[t.task_id].material_inputs
                ),
                material_outputs=tuple(
                    m for t in all_tasks for m in singles[t.task_id].material_outputs
                ),
                member_offsets=tuple(offsets),
                resource_phases=tuple(phases),
                replaced_reservation_ids=tuple(reservation_ids),
            )
            candidate = batch.model_copy(
                update={
                    "carrier_id": CarrierId(
                        stable_id(
                            "thermal-batch", knowledge.release.rule_version, content_hash(batch)
                        )
                    )
                }
            )
            yield candidate
            yield from transition_variants(candidate, context)


def generate_thermal_batches(context: SharedCandidateContext) -> tuple[ThermalBatchCandidate, ...]:
    return tuple({c.carrier_id: c for c in iter_thermal_batches(context)}.values())
