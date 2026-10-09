"""离线实验复用纯执行/物料逻辑；不声称模拟发布具有 SQL 持久性。"""

from app.domain.carrier_timing import task_intervals
from app.domain.events import RuntimeEvent
from app.domain.runtime_facts import RuntimeDetails, TaskSpan
from app.domain.runtime_session import ExecutionBinding, RuntimeSession
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.schedule import PublishedPlan
from app.domain.time import TimeOrigin
from app.runtime.event_validation import validate_event
from app.runtime.inventory import observe_inventory, prepare_inventory
from app.runtime.material_reports import source_specs, validate_reports
from app.runtime.material_reservations import materialize, plan_reservations
from app.runtime.menu_events import apply_menu
from app.runtime.state_machine import apply_execution


def event(session, identity, kind, payload, at):
    state = session.runtime
    return RuntimeEvent(
        event_id=identity,
        session_id=state.session_id,
        event_type=kind,
        occurred_at=state.time_origin.at(at),
        received_at=state.time_origin.at(at),
        source="SIMULATED",
        expected_state_revision=state.state_revision,
        base_plan_version=state.current_plan_version,
        payload=payload,
    )


def start_session(knowledge, policy, case, origin):
    session = RuntimeSession(
        runtime=RuntimeSnapshot(
            session_id="robustness-" + case["case_id"],
            state_revision=0,
            current_plan_version=0,
            knowledge_version=knowledge.release.knowledge_version,
            rule_version=knowledge.release.rule_version,
            snapshot_id=knowledge.release.snapshot_id,
            time_origin=TimeOrigin(start_at=origin),
            now_offset_sec=0,
            execution_mode="SIMULATED",
            details=RuntimeDetails(),
        ),
        policy=policy,
        knowledge_release_id=knowledge.release.release_id,
    )
    recipes = {r.recipe_id.root: r for r in knowledge.recipes}
    request = event(
        session,
        "start-" + case["case_id"],
        "START_SESSION",
        {"recipes": [{"id": rid, "name": recipes[rid].name} for rid in case["recipe_ids"]]},
        0,
    )
    validate_event(request, session)
    changed = apply_menu(session, request, knowledge)
    return changed.model_copy(
        update={
            "runtime": changed.runtime.model_copy(
                update={"state_revision": 1, "event_refs": (request.event_id,)}
            )
        }
    )


def bind_plan(session, problem, validated):
    """纯事实投影，SQL 的 CAS/原子提交由系统故障实验单独验收。"""
    state = session.runtime
    version = state.current_plan_version + 1
    identity = f"offline-plan-{state.session_id.root}-{version}"
    published = PublishedPlan(
        session_id=state.session_id,
        plan_version=version,
        parent_plan_version=state.current_plan_version,
        state_revision=state.state_revision,
        knowledge_version=state.knowledge_version,
        snapshot_id=state.snapshot_id,
        time_origin=state.time_origin,
        validated=validated,
        publication_id=identity,
        committed_at=state.time_origin.at(state.now_offset_sec),
    )
    candidates = {
        c.carrier_id: c
        for c in (
            *problem.standalone_candidates,
            *problem.shared_prep_candidates,
            *problem.thermal_batch_candidates,
            *problem.inventory_supply_candidates,
        )
    }
    ports = task_intervals(problem, validated.candidate.assignments)
    active = {e.carrier_id for e in state.executions if e.status == "RUNNING"}
    bindings = [b for b in session.bindings if b.carrier.carrier_id.root in active]
    for assignment in validated.candidate.assignments:
        carrier = candidates[assignment.carrier_id]
        bindings.append(
            ExecutionBinding(
                assignment=assignment,
                carrier=carrier,
                plan_version=version,
                task_spans=tuple(
                    TaskSpan(task_id=t, interval=ports[t]) for t in assignment.task_ids
                ),
                continuities=tuple(
                    r
                    for r in problem.mandatory_programs.reservations
                    if set(r.members) & set(assignment.task_ids)
                    and r.reservation_id not in carrier.replaced_reservation_ids
                ),
            )
        )
    new_tasks = {t for b in bindings for t in b.assignment.task_ids}
    bindings.extend(b for b in session.bindings if not set(b.assignment.task_ids) & new_tasks)
    changed = session.model_copy(
        update={
            "bindings": tuple(bindings),
            "dispatch_blocked": False,
            "runtime": state.model_copy(
                update={"current_plan_version": version, "current_plan_ref": identity}
            ),
        }
    )
    changed = prepare_inventory(changed, problem, published)
    return plan_reservations(
        changed,
        problem,
        {t for a in validated.candidate.assignments for t in a.task_ids},
        version,
        identity,
    )


def apply_observation(session, request, knowledge, dependencies, minima):
    validate_event(request, session)
    validate_reports(session, request, knowledge)
    changed = apply_execution(
        session, request, dependencies, minima, source_specs(session, knowledge)
    )
    state = changed.runtime
    assert state.details is not None
    changed = changed.model_copy(
        update={
            "runtime": state.model_copy(
                update={
                    "state_revision": state.state_revision + 1,
                    "now_offset_sec": state.time_origin.offset(request.occurred_at),
                    "event_refs": (*state.event_refs, request.event_id),
                    "details": state.details.model_copy(update={"planning_kind": "REPLAN"}),
                }
            )
        }
    )
    changed = observe_inventory(changed, request)
    return materialize(changed, request.event_id.root, request.event_id.root)
