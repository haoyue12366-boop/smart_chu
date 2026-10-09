"""库存满足与加工执行分别落账；实际开始后冻结供应范围。"""

from fractions import Fraction

from app.domain.amount import RationalAmount
from app.domain.candidates import stable_id
from app.domain.events import ExecutionPayload, RuntimeEvent
from app.domain.inventory import InventoryFulfillment
from app.domain.runtime_session import RuntimeSession
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem


def prepare_inventory(
    session: RuntimeSession, problem: SchedulingProblem, published: PublishedPlan
) -> RuntimeSession:
    details = session.runtime.details
    assert details is not None
    records = [
        item.model_copy(update={"status": "SUPERSEDED"}) if item.status == "PLANNED" else item
        for item in details.inventory_fulfillments
    ]
    candidates = {item.carrier_id: item for item in problem.inventory_supply_candidates}
    tasks = {item.task_id: item for item in problem.logical_tasks}
    for assignment in published.validated.candidate.assignments:
        carrier = candidates.get(assignment.carrier_id)
        if carrier is None:
            continue
        assert carrier.inventory_supply is not None
        records.append(
            InventoryFulfillment(
                fulfillment_id=stable_id(
                    "fulfillment", published.publication_id, carrier.carrier_id.root
                ),
                publication_id=published.publication_id,
                plan_version=published.plan_version,
                carrier_id=carrier.carrier_id,
                recipe_instance_id=tasks[carrier.covers[0]].recipe_instance_id,
                task_ids=carrier.covers,
                supply=carrier.inventory_supply,
                satisfied_at_sec=assignment.interval.start_sec,
            )
        )
    return session.model_copy(
        update={
            "runtime": session.runtime.model_copy(
                update={
                    "details": details.model_copy(update={"inventory_fulfillments": tuple(records)})
                }
            )
        }
    )


def observe_inventory(session: RuntimeSession, event: RuntimeEvent) -> RuntimeSession:
    details = session.runtime.details
    assert details is not None
    payload = event.payload
    if not isinstance(payload, ExecutionPayload):
        return session
    record = next(
        (item for item in session.runtime.executions if item.execution_id == payload.execution_id),
        None,
    )
    if record is None:
        return session
    from app.runtime.material_reservations import amount_in

    lots = {item.lot_id: item for item in details.lots}
    fulfillments = []
    for fulfillment in details.inventory_fulfillments:
        if fulfillment.status == "SUPERSEDED":
            fulfillments.append(fulfillment)
            continue
        reservations = {
            item.reservation_id
            for item in session.future_allocations
            if item.inventory_fulfillment_id == fulfillment.fulfillment_id
        }
        related = [item for item in session.allocations if item.reservation_id in reservations]
        started = any(item.task_id in record.started_task_ids for item in related)
        claims = tuple(
            claim.model_copy(
                update={
                    "consumed": RationalAmount.of(
                        sum(
                            (
                                amount_in(
                                    item.consumed.fraction(),
                                    lots[item.lot_id].unit,
                                    claim.quantity.unit,
                                )
                                for item in related
                                if item.lot_id == claim.lot_id
                            ),
                            Fraction(0),
                        )
                    )
                }
            )
            for claim in fulfillment.supply.lot_claims
        )
        fulfillments.append(
            fulfillment.model_copy(
                update={
                    "status": "COMMITTED"
                    if started or fulfillment.status == "COMMITTED"
                    else "PLANNED",
                    "supply": fulfillment.supply.model_copy(update={"lot_claims": claims}),
                }
            )
        )
    return session.model_copy(
        update={
            "runtime": session.runtime.model_copy(
                update={
                    "details": details.model_copy(
                        update={"inventory_fulfillments": tuple(fulfillments)}
                    )
                }
            )
        }
    )
