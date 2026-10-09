"""只模拟已发布动作；未来扰动为模拟器私有状态。"""

from app.domain.events import RuntimeEvent
from app.domain.ports import Deadline
from app.domain.runtime_session import RuntimeSession
from app.runtime.clock import SimulationClock
from app.runtime.feedback import FeedbackAdapter
from app.runtime.scheduled_actions import PlannedAction, PublishedActions
from app.runtime.service import RuntimeService


class Simulator:
    def __init__(self, runtime: RuntimeService, session_id: str, *, seed: int = 0) -> None:
        state = runtime.get(session_id).runtime
        if state.execution_mode != "SIMULATED" or not isinstance(runtime.clock, SimulationClock):
            raise ValueError("模拟器只接受显式模拟模式和模拟时钟")
        self.runtime, self.session_id, self.clock = runtime, session_id, runtime.clock
        self.seed = seed
        self._future: list[PlannedAction] = []
        self._sequence = 0
        self._actions = PublishedActions(runtime, session_id, seed=seed)

    def inject(self, *, at: int, kind: str, payload: dict[str, object]) -> None:
        if type(at) is not int or at < self.clock.offset_sec:
            raise ValueError("扰动只能注入当前或未来整数时刻")
        self._sequence += 1
        self._future.append(
            PlannedAction(
                at, 1, f"disturbance:{self.session_id}:{self.seed}:{self._sequence}", kind, payload
            )
        )

    def advance(
        self, to_offset_sec: int, *, deadline: Deadline | None = None
    ) -> tuple[RuntimeEvent, ...]:
        if type(to_offset_sec) is not int or to_offset_sec < self.clock.offset_sec:
            raise ValueError("模拟时间只能向前推进整数秒")
        emitted = []
        while True:
            if deadline is not None and self.clock.monotonic_ns() >= deadline.expires_at_ns:
                raise TimeoutError("模拟推进预算已耗尽；已提交事实保留，可按原请求身份继续")
            session = self.runtime.get(self.session_id)
            if session.status != "ACTIVE":
                break
            # 每次从最新绑定重建未来动作，旧计划没有可继续派发的副本。
            actions = [*self._future, *self._planned(session)]
            if not actions:
                break
            action = min(actions, key=lambda a: (a.at, a.priority, a.identity))
            if action.at > to_offset_sec:
                break
            self.clock.advance(action.at)
            payload = action.payload
            if action.binding is not None:
                payload = self._actions.payload(
                    session,
                    action.binding,
                    action.group,
                    str(payload["execution_id"]),
                    completed=action.kind == "OPERATION_COMPLETED",
                )
            event = FeedbackAdapter().event(
                session, action.identity, action.kind, payload, self.clock.now()
            )
            result = (
                self.runtime.apply_event(event, deadline)
                if deadline is not None
                else self.runtime.apply_event(event)
            )
            if result.status != "APPLIED":
                raise ValueError("模拟反馈未生效：" + str(result.rejection_reason))
            emitted.append(event)
            if action in self._future:
                self._future.remove(action)
        self.clock.advance(to_offset_sec)
        return tuple(emitted)

    def _planned(self, session: RuntimeSession) -> tuple[PlannedAction, ...]:
        return self._actions.planned(session, self.clock.offset_sec)
