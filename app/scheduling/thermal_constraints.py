"""对实际在场的腔体预约建有虚拟起终点的序列，转换条件绑定直接前驱。"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.domain.thermal_profiles import thermal_profile
from app.domain.thermal_sequence import initial_thermal_state, planned_exit_state
from app.domain.transition_rules import resolve_transition
from app.domain.transitions import ThermalState, TransitionRuleSpec, TransitionTarget

if TYPE_CHECKING:
    from ortools.sat.python import cp_model

    from app.scheduling.model_builder import ModelBuilder
    from app.scheduling.resource_model import ResourceInterval


def add_thermal_sequences(builder: ModelBuilder, occupancies: list[ResourceInterval]) -> None:
    problem, model = builder.problem, builder.model
    carriers = {c.carrier_id: c for c in builder.candidates}
    keys = {
        (c.resource_uses[0].physical_resource_id, c.resource_uses[0].component_id)
        for c in problem.thermal_batch_candidates
        if c.transition_binding is not None
    }
    grouped: dict[tuple[str, str], list[ResourceInterval]] = defaultdict(list)
    for occurrence in occupancies:
        if occurrence.key in keys and occurrence.use.resource_type == "DEVICE":
            grouped[occurrence.key].append(occurrence)
    for key, nodes in grouped.items():
        initial = initial_thermal_state(problem, key)
        # Completed physical history remains in NoOverlap. A later observation
        # supersedes its thermal state, so only subsequent occupancy enters this circuit.
        completed = {
            t for e in problem.fixed_executions if e.status == "COMPLETED" for t in e.task_ids
        }
        nodes = [
            n
            for n in nodes
            if not (
                n.tasks
                and all(
                    t in completed and t in builder.fixed and builder.fixed[t][1] <= initial.at_sec
                    for t in n.tasks
                )
            )
        ]
        if not nodes:
            continue
        profiles = [
            thermal_profile(n.use, problem.resources, problem.device_profiles) for n in nodes
        ]
        arcs: list[tuple[int, int, cp_model.IntVar]] = []
        empty = model.new_bool_var(f"thermal-empty:{key}")
        arcs.append((0, 0, empty))
        model.add(sum(n.presence for n in nodes) == 0).only_enforce_if(empty)
        model.add(sum(n.presence for n in nodes) >= 1).only_enforce_if(empty.negated())

        def bind_transition(
            right: ResourceInterval,
            previous: ThermalState,
            previous_end: cp_model.IntVar | None,
            arc: cp_model.IntVar,
        ) -> None:
            candidate = carriers.get(right.carrier_id) if right.carrier_id is not None else None
            if candidate is None or candidate.transition_binding is None:
                return
            binding = candidate.transition_binding
            rule = next((r for r in problem.transition_rules if r.rule_id == binding.rule_id), None)
            profile = thermal_profile(right.use, problem.resources, problem.device_profiles)
            if rule is None or profile is None:
                model.add(arc == 0)
                return
            target = TransitionTarget(
                profile=profile,
                at_sec=previous.at_sec,
                rules=(rule,),
                release_kind=problem.knowledge_release_kind,
                allow_delegated_estimates=problem.policy.allow_delegated_shared_estimates,
            )
            plan = resolve_transition(previous, target, problem.rule_version)
            ports = [p for p in candidate.member_offsets if p.task_id in binding.preheat_task_ids]
            if (
                not plan.allowed
                or any(p.end_offset_sec - p.start_offset_sec != plan.duration_sec for p in ports)
                or bool(binding.completed_state_ref) != (plan.kind == "ALREADY_COMPLETED")
                or (
                    binding.completed_state_ref is not None
                    and binding.completed_state_ref != previous.source_ref
                )
            ):
                model.add(arc == 0)
                return
            spec = TransitionRuleSpec.model_validate_json(rule.group_compatibility_predicate)
            transition_start = right.start + binding.transition_offset_sec
            origin = previous_end if previous_end is not None else previous.at_sec
            model.add(transition_start >= origin).only_enforce_if(arc)
            model.add(transition_start <= origin + spec.max_idle_sec).only_enforce_if(arc)
            if previous.valid_until_sec is not None:
                model.add(transition_start <= previous.valid_until_sec).only_enforce_if(arc)

        for i, node in enumerate(nodes):
            builder.check_budget()
            absent = model.new_bool_var(f"thermal-absent:{key}:{i}")
            model.add(absent + node.presence == 1)
            first = model.new_bool_var(f"thermal-first:{key}:{i}")
            last = model.new_bool_var(f"thermal-last:{key}:{i}")
            arcs.extend(((i + 1, i + 1, absent), (0, i + 1, first), (i + 1, 0, last)))
            bind_transition(node, initial, None, first)
            carrier = carriers.get(node.carrier_id) if node.carrier_id is not None else None
            previous = planned_exit_state(
                problem, carrier, key, profiles[i], 0, f"planned-node:{i}"
            )
            for j, following in enumerate(nodes):
                if i == j:
                    continue
                builder.check_budget()
                follows = model.new_bool_var(f"thermal-order:{key}:{i}:{j}")
                arcs.append((i + 1, j + 1, follows))
                builder.sequence_arcs += 1
                model.add(following.start >= node.end).only_enforce_if(follows)
                bind_transition(following, previous, node.end, follows)
        model.add_circuit(arcs)
