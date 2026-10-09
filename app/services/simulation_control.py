"""持久目标下推进模拟事实，最终控制事件只设置目标偏移，不再加一次增量。"""

from app.domain.events import SimulationPayload
from app.domain.ports import Deadline
from app.runtime.event_validation import validate_event
from app.runtime.service import RuntimeService
from app.runtime.simulator import Simulator
from app.storage.competition_tasks import HttpRequestRecord, HttpRequestRepository
from app.storage.repositories import RuntimeRepository


def prepare_simulation(
    runtime: RuntimeService, record: HttpRequestRecord, deadline: Deadline
) -> HttpRequestRecord:
    with runtime.store.transaction() as tx:
        requests = HttpRequestRepository(tx)
        current = requests.get(record.request_id, record.payload_hash)
        assert current is not None
        record = current
        repo = RuntimeRepository(tx)
        if record.control_phase == "EXECUTED":
            if repo.event(record.event.event_id.root) is not None:
                return record
            # 派生模拟事实已提交，控制回执尚未应用。重启期间可有其他
            # 合法事实到达，只刷新控制事件的提交版本，不再次推进模拟目标。
            latest = repo.get(record.session_id).runtime
            event = record.event.model_copy(
                update={
                    "expected_state_revision": latest.state_revision,
                    "base_plan_version": latest.current_plan_version,
                }
            )
            return requests.control(record, "EXECUTED", event)
        session = repo.get(record.session_id)
        if record.control_phase is None:
            try:
                validate_event(record.event, session)
            except ValueError:
                # 由普通 RuntimeService 写入拒绝回执；此前不派发任何模拟动作。
                return record
            record = requests.control(record, "VALIDATED")
        target = session.runtime.time_origin.offset(record.event.occurred_at)
    Simulator(runtime, record.session_id).advance(target, deadline=deadline)
    with runtime.store.transaction() as tx:
        session = RuntimeRepository(tx).get(record.session_id)
        final_event = record.event.model_copy(
            update={
                "expected_state_revision": session.runtime.state_revision,
                "base_plan_version": session.runtime.current_plan_version,
                "payload": SimulationPayload(advance_sec=0),
            }
        )
        return HttpRequestRepository(tx).control(record, "EXECUTED", final_event)
