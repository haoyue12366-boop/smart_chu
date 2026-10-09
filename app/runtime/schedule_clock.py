"""按已经流逝的排程时钟落账；推断来源有别于人工、设备反馈及模拟。"""

from app.domain.candidates import stable_id
from app.domain.events import RuntimeEvent
from app.domain.ports import Deadline
from app.domain.runtime_clock import clock_offset
from app.domain.runtime_session import RuntimeSession
from app.runtime.scheduled_actions import PublishedActions
from app.runtime.service import RuntimeService
from app.storage.repositories import RuntimeRepository


class ClockExecutionService:
    def __init__(self, runtime: RuntimeService) -> None:
        self.runtime = runtime

    def advance(
        self, session_id: str, until_sec: int | None = None, deadline: Deadline | None = None
    ) -> RuntimeSession:
        session = self.runtime.get(session_id)
        if session.runtime.execution_mode != "SCHEDULE_CLOCK" or session.schedule_clock is None:
            return session
        current = clock_offset(session, self.runtime.clock.now())
        target = current if until_sec is None else until_sec
        if type(target) is not int or target < 0 or target > current:
            raise ValueError("排程时钟只能推进到当前已流逝的整数秒，不能推断未来完成")
        if deadline is not None and self.runtime.clock.monotonic_ns() >= deadline.expires_at_ns:
            raise TimeoutError("时钟推进预算耗尽，已落账的进度保留")
        cached = self.runtime.clock_scan_cache
        if cached is not None and cached[0] == target and cached[1] == session:
            return session
        actions = PublishedActions(self.runtime, session_id, namespace="clock")
        while True:
            if deadline is not None and self.runtime.clock.monotonic_ns() >= deadline.expires_at_ns:
                raise TimeoutError("时钟推进预算耗尽，已落账的进度保留")
            session = self.runtime.get(session_id)
            clock = session.schedule_clock
            if session.status != "ACTIVE" or clock is None:
                return session
            cursor = max(clock.processed_until_sec, session.runtime.now_offset_sec)
            if target < cursor:
                return session
            planned = actions.planned(session, cursor)
            if not planned:
                break
            action = min(planned, key=lambda item: (item.at, item.priority, item.identity))
            if action.at > target:
                break
            assert action.binding is not None
            payload = actions.payload(
                session,
                action.binding,
                action.group,
                str(action.payload["execution_id"]),
                completed=action.kind == "OPERATION_COMPLETED",
            )
            event = RuntimeEvent.model_validate(
                {
                    "event_id": stable_id(
                        "clock-event", action.identity, str(session.runtime.state_revision)
                    ),
                    "session_id": session.runtime.session_id,
                    "event_type": action.kind,
                    "payload": payload,
                    "occurred_at": session.runtime.time_origin.at(action.at),
                    "received_at": self.runtime.clock.now(),
                    "source": "SCHEDULE_CLOCK",
                    "expected_state_revision": session.runtime.state_revision,
                    "base_plan_version": session.runtime.current_plan_version,
                }
            )
            result = self.runtime.apply_event(event, deadline)
            if result.status != "APPLIED":
                latest = self.runtime.get(session_id)
                if (latest.runtime.state_revision, latest.runtime.current_plan_version) != (
                    session.runtime.state_revision,
                    session.runtime.current_plan_version,
                ):
                    continue
                raise ValueError("时钟推断未生效：" + str(result.rejection_reason))
        # 纯时间经过不增加事实版本；同事务读取最新状态，避免覆盖并发人工修正。
        if clock is not None and target <= clock.processed_until_sec:
            self.runtime.clock_scan_cache = target, session
            return session
        scanned = session
        with self.runtime.store.transaction() as tx:
            repo = RuntimeRepository(tx)
            session = repo.get(session_id)
            clock = session.schedule_clock
            if clock is None or session.status != "ACTIVE":
                return session
            if session == scanned:
                changed = session.model_copy(
                    update={
                        "schedule_clock": clock.model_copy(update={"processed_until_sec": target}),
                        "runtime": session.runtime.model_copy(
                            update={"now_offset_sec": max(target, session.runtime.now_offset_sec)}
                        ),
                    }
                )
                repo.save(
                    changed,
                    expected_revision=session.runtime.state_revision,
                    expected_plan=session.runtime.current_plan_version,
                )
                self.runtime.clock_scan_cache = target, changed
                return changed
        # 并发反馈使刚才的扫描失效；事务结束后重新扫描，不缓存未经处理的新状态。
        return self.advance(session_id, target, deadline)
