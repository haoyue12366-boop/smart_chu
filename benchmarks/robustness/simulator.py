"""配对私有扰动、实际事件驱动与独立终态扫描；没有未来数据进入 Planner。"""

import hashlib
import time
from collections import Counter
from dataclasses import asdict, dataclass

from app.compiler.compiler import ProblemCompiler
from app.domain.base import content_hash
from app.domain.ids import TaskId
from app.domain.ports import Deadline
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.dispatch_guard import guard_start
from app.runtime.execution_resources import acquire, group_uses
from app.runtime.simulated_materials import simulated_payload
from app.scheduling.duration_policy import estimated_phase
from app.validation.schedule import ScheduleValidator
from benchmarks.robustness.dispatch import DispatchFailure, ObservedDependency, observed_window
from benchmarks.robustness.observed_session import apply_observation, event


def weighted_value(identity, values, weights):
    draw = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big") % sum(weights)
    for value, weight in zip(values, weights, strict=True):
        if draw < weight:
            return value
        draw -= weight
    raise AssertionError("分布配置不完整")


class FutureDurations:
    """只由模拟执行器持有；共同速度和模板局部项用稳定身份取样。"""

    def __init__(self, seed, case_id, trajectory, config):
        self.identity = f"{seed}:{case_id}:{trajectory}"
        self.config = config
        self.common = weighted_value(
            self.identity + ":crew",
            config["common_percent"],
            config["common_weights"],
        )

    def local(self, operation_key):
        return weighted_value(
            self.identity + ":local:" + operation_key,
            self.config["local_percent"],
            self.config["local_weights"],
        )

    def duration(self, nominal, operations):
        if nominal == 0:
            return 0, False  # 已验证的吸收预热端口；仍由终态热过程检查核验。
        # 同一次共同加工只发生一次，组内最大局部项表示实际共同操作速度。
        if any(estimated_phase(operation) == "FIXED_PROCESS" for _, operation in operations):
            return nominal, False
        factors = [self.common * self.local(key) for key, _ in operations]
        actual = max(1, (nominal * max(factors) + 9999) // 10000)
        lower = max((op.duration.lower_sec or 1 for _, op in operations), default=1)
        upper = min(
            (op.duration.upper_sec for _, op in operations if op.duration.upper_sec is not None),
            default=None,
        )
        if upper is not None and upper < lower:
            raise ValueError("共同阶段审核区间不相交")
        actual = max(actual, lower)
        if upper is not None:
            actual = min(actual, upper)
        return actual, True


@dataclass(frozen=True)
class ReadyStage:
    binding: object
    group: tuple[TaskId, ...]
    execution_id: str
    at: int


class ObservationCache:
    """相同不可变观察前缀复用纯转换；每条成功轨迹仍独立扫描最终约束。"""

    def __init__(self):
        self.transitions, self.final_problems = {}, {}
        self.hits, self.misses = 0, 0


class TrajectorySimulator:
    def __init__(
        self, knowledge, initial, problem, candidate, future, config, planner=None, cache=None
    ):
        self.knowledge, self.session, self.problem = knowledge, initial, problem
        self.initial_candidate = candidate
        self._future = future
        self.config, self.planner = config, planner
        self.cache = cache
        self.prefix = hashlib.sha256(
            (content_hash(initial) + ":" + problem.problem_hash).encode()
        ).hexdigest()
        self.operations = {}
        recipes = {r.recipe_id: r for r in knowledge.recipes}
        for task in problem.logical_tasks:
            instance = next(
                i for i in initial.menu if i.recipe_instance_id == task.recipe_instance_id
            )
            operation = next(
                op
                for op in recipes[instance.recipe_id].operations
                if op.operation_id == task.operation_id
            )
            self.operations[task.task_id] = (
                instance.recipe_instance_id.root + ":" + operation.operation_id.root,
                operation,
            )
        self.dependencies = tuple(
            (d.predecessor_id.root, d.successor_id.root, d.min_lag_sec, d.max_lag_sec)
            for d in problem.dependencies
        )
        contexts = {c.recipe_id: c for c in knowledge.recipe_contexts}
        self.fixed_program_tasks = {
            task.task_id
            for task in problem.logical_tasks
            for instance in initial.menu
            if instance.recipe_instance_id == task.recipe_instance_id
            for context in (contexts.get(instance.recipe_id),)
            if context is not None
            for program in context.program_constraints
            if task.operation_id in program.intervention_group
        }
        self.minima = tuple(
            (
                task.root,
                op.duration.lower_sec
                if op.duration.lower_sec is not None
                else op.duration.execution_sec,
            )
            for task, (_, op) in self.operations.items()
            if op.duration.lower_sec is not None or estimated_phase(op) == "FIXED_PROCESS"
        )
        self._finishes = {}
        self.events, self.replans = [], []
        self.perturbed, self.fixed = 0, 0
        # 在可见名义计划中固定观察哨兵，不根据随机未来挑选触发点。
        eligible = [
            (p.interval.start_sec, t.root)
            for b in initial.bindings
            for p in b.task_spans
            for t in (p.task_id,)
            if estimated_phase(self.operations[t][1]) != "FIXED_PROCESS"
            and t not in self.fixed_program_tasks
        ]
        self.sentinel = min(eligible)[1] if eligible else None
        self.trigger_done = False
        self.wait_sec = 0
        self.dispatch_diagnostics = []
        self._diagnostic_keys = set()
        self._blocked_stages = []
        self.failure_detail = None

    def diagnostic(self, detail: dict[str, object]) -> None:
        key = hashlib.sha256(str(detail).encode()).hexdigest()
        if key not in self._diagnostic_keys:
            self._diagnostic_keys.add(key)
            self.dispatch_diagnostics.append(detail)

    def completed(self):
        return {
            p.task_id: p.interval.end_sec
            for record in self.session.runtime.executions
            for p in record.task_spans
            if p.task_id in record.completed_task_ids
        }

    def stages(self):
        now = self.session.runtime.now_offset_sec
        completed = self.completed()
        records = {e.carrier_id: e for e in self.session.runtime.executions}
        planned = {p.task_id: p.interval for b in self.session.bindings for p in b.task_spans}
        owners = {
            t: b.carrier.carrier_id.root
            for b in self.session.bindings
            for t in b.assignment.task_ids
        }
        # 重排已经用这些真实完成端口建模，不能再加一遍历史偏差。
        planned.update(
            (p.task_id, p.interval)
            for e in self.problem.runtime.executions
            for p in e.task_spans
            if e.status == "COMPLETED" or p.task_id in e.completed_task_ids
        )
        result = []
        self._blocked_stages = []
        tasks = {task.task_id: task for task in self.problem.logical_tasks}
        details = self.session.runtime.details
        assert details is not None
        starts = dict(details.earliest_starts)
        buffers = {
            t: b.not_before_sec for b in self.problem.duration_buffers for t in b.root_task_ids
        }
        for binding in self.session.bindings:
            record = records.get(binding.carrier.carrier_id.root)
            spans = record.task_spans if record else binding.task_spans
            groups = {
                span.interval: tuple(p.task_id for p in spans if p.interval == span.interval)
                for span in spans
            }
            for span, group in groups.items():
                if record and set(group) & set(record.started_task_ids):
                    continue
                current = {t.root for t in group}
                shifts = []
                applicable = []
                for before, after, low, high in self.dependencies:
                    if after not in current or before in current:
                        continue
                    predecessor = TaskId(before)
                    applicable.append(
                        ObservedDependency(
                            before,
                            after,
                            completed.get(predecessor),
                            planned[predecessor].end_sec,
                            low,
                            high,
                        )
                    )
                    if (
                        predecessor in completed
                        and owners[predecessor] != binding.carrier.carrier_id.root
                    ):
                        shifts.append(completed[predecessor] - planned[predecessor].end_sec)
                # logical_tasks 的界已按名义时长传播，不能当作实际执行的绝对门槛。
                # 正式门槛来自本次编译时间原点、用户延期及显式根节点缓冲。
                not_before = max(
                    self.problem.runtime.now_offset_sec,
                    *(starts.get(tasks[t].recipe_instance_id.root, 0) for t in group),
                    *(buffers.get(t, 0) for t in group),
                )
                window = observed_window(
                    now,
                    not_before,
                    span.start_sec + (max(shifts) if shifts else 0),
                    tuple(applicable),
                )
                identity = (
                    record.execution_id.root
                    if record
                    else "offline-execution:" + binding.carrier.carrier_id.root
                )
                payload = {"task_id": group[0], "execution_id": identity}
                source_span = next(p.interval for p in binding.task_spans if p.task_id == group[0])
                phases = list(binding.carrier.resource_uses)
                phases.extend(
                    p.resource_use
                    for p in binding.carrier.resource_phases
                    if p.start_offset_sec
                    < source_span.end_sec - binding.assignment.interval.start_sec
                    and p.end_offset_sec
                    > source_span.start_sec - binding.assignment.interval.start_sec
                )
                uses = group_uses(tuple(phases))
                detail = {
                    "now_sec": now,
                    "state_revision": self.session.runtime.state_revision,
                    "task_ids": sorted(current),
                    "execution_id": identity,
                    "planned_start_sec": span.start_sec,
                    "formal_not_before_sec": not_before,
                    **asdict(window),
                    "dependencies": [asdict(d) for d in applicable],
                    "resources": [u.model_dump(mode="json") for u in uses],
                    "active_occupancies": [
                        o.model_dump(mode="json")
                        for o in details.occupancies
                        if o.released_at is None
                    ],
                }
                if window.code in {"ACTUAL_WINDOW_EXPIRED", "START_WINDOW_EMPTY"}:
                    raise DispatchFailure("实际启动窗口已关闭或没有合法交集", detail)
                blocked = window.code
                if self.session.dispatch_blocked and (record is None or record.status != "RUNNING"):
                    blocked = "PLAN_REQUIRED"
                if blocked:
                    self._blocked_stages.append({**detail, "code": blocked})
                    continue
                earliest = window.dispatch_at_sec
                assert earliest is not None
                guard = guard_start(
                    self.session, self.knowledge, self.dependencies, group, earliest
                )
                if guard.blocked:
                    guard_detail = {
                        **detail,
                        "code": "TIGHT_HUMAN_GUARD",
                        "reason": guard.reason,
                        "protected_task_ids": list(guard.protected_task_ids),
                        "protected_resource_ids": list(guard.resource_ids),
                    }
                    self._blocked_stages.append(guard_detail)
                    self.diagnostic(guard_detail)
                    continue
                proposed = event(
                    self.session, "ready-probe", "OPERATION_STARTED", payload, earliest
                )
                try:
                    acquire(
                        self.session,
                        proposed,
                        proposed.payload.execution_id,
                        uses,
                        binding,
                    )
                except ValueError as exc:
                    # 当前事实仍占用；等实际反馈到达，绝不按私有未来提前释放。
                    self._blocked_stages.append(
                        {**detail, "code": "RESOURCE_BLOCKED", "reason": str(exc)}
                    )
                    continue
                if window.preferred_start_sec > earliest:
                    self.diagnostic({**detail, "code": "PLAN_ADJUSTED_WITHIN_WINDOW"})
                result.append(ReadyStage(binding, group, identity, earliest))
        return result

    def observe(self, kind, stage, at):
        payload = simulated_payload(
            self.session,
            self.problem,
            stage.group,
            stage.execution_id,
            completed=kind == "OPERATION_COMPLETED",
        )
        identity = f"observed-{len(self.events)}-{kind}-{stage.group[0].root}-{at}"
        request = event(self.session, identity, kind, payload, at)
        key = hashlib.sha256((self.prefix + request.model_dump_json()).encode()).hexdigest()
        if self.cache is not None and key in self.cache.transitions:
            changed = self.cache.transitions[key]
            self.cache.hits += 1
        else:
            changed = apply_observation(
                self.session, request, self.knowledge, self.dependencies, self.minima
            )
            if self.cache is not None:
                self.cache.transitions[key] = changed
                self.cache.misses += 1
        self.session = changed
        self.prefix = key
        self.events.append(request.model_dump(mode="json"))

    def maybe_replan(self, stage, at):
        if (
            self.planner is None
            or self.trigger_done
            or self.sentinel not in {t.root for t in stage.group}
        ):
            return
        self.trigger_done = True
        expected = next(
            p.interval.end_sec
            for b in self.session.bindings
            for p in b.task_spans
            if p.task_id.root == self.sentinel
        )
        if at - expected < self.config["trigger_delay_sec"]:
            return
        # 唯一调用参数是当前已观察事实；未来对象、轨迹号、共同因素均不传入。
        updated, problem, result = self.planner(self.knowledge, self.session)
        self.replans.append(result)
        if updated is None:
            raise ValueError("观察触发的重排失败：" + result["failure"])
        self.session, self.problem = updated, problem
        self.prefix = hashlib.sha256(
            (content_hash(updated) + ":" + problem.problem_hash).encode()
        ).hexdigest()

    def run(self):
        started = time.perf_counter_ns()
        failure, proof = None, None
        try:
            for _ in range(self.config["max_events"]):
                if len(self.completed()) == len(self.operations):
                    break
                stages = self.stages()
                next_start = min(stages, key=lambda s: (s.at, s.group[0].root), default=None)
                next_end = min(
                    self._finishes.values(), key=lambda p: (p[0], p[1].group[0].root), default=None
                )
                if next_end and (next_start is None or next_end[0] <= next_start.at):
                    at, stage = next_end
                    del self._finishes[stage.group]
                    self.observe("OPERATION_COMPLETED", stage, at)
                    self.maybe_replan(stage, at)
                elif next_start:
                    stage = next_start
                    planned_start = next(
                        p.interval.start_sec
                        for p in stage.binding.task_spans
                        if p.task_id == stage.group[0]
                    )
                    self.wait_sec += max(0, stage.at - planned_start)
                    self.observe("OPERATION_STARTED", stage, stage.at)
                    record = next(
                        e
                        for e in self.session.runtime.executions
                        if e.execution_id.root == stage.execution_id
                    )
                    span = next(
                        p.interval for p in record.task_spans if p.task_id == stage.group[0]
                    )
                    if set(stage.group) & self.fixed_program_tasks:
                        duration, variable = span.end_sec - span.start_sec, False
                    else:
                        duration, variable = self._future.duration(
                            span.end_sec - span.start_sec,
                            tuple(self.operations[t] for t in stage.group),
                        )
                    self.perturbed += variable
                    self.fixed += not variable
                    self._finishes[stage.group] = stage.at + duration, stage
                else:
                    code = (
                        "PLAN_REQUIRED" if self.session.dispatch_blocked else "OBSERVATION_REQUIRED"
                    )
                    raise DispatchFailure(
                        "无可派发阶段或未完成反馈；实际依赖/占用阻塞",
                        {
                            "code": code,
                            "now_sec": self.session.runtime.now_offset_sec,
                            "blocked_stages": self._blocked_stages,
                        },
                    )
            else:
                raise ValueError("轨迹事件上限耗尽")
            if len(self.completed()) != len(self.operations):
                raise ValueError("轨迹未完整完成")
            # 当前固定事实重新编译，再由不依赖编译器/求解器的 Validator 扫描。
            final = self.cache.final_problems.get(self.prefix) if self.cache else None
            if final is None:
                final = ProblemCompiler().compile(
                    self.knowledge,
                    self.session.menu,
                    self.session.runtime,
                    self.session.policy,
                    Deadline(expires_at_ns=time.monotonic_ns() + 4_200_000_000),
                )
                if self.cache and isinstance(final, SchedulingProblem):
                    self.cache.final_problems[self.prefix] = final
            if not isinstance(final, SchedulingProblem):
                raise ValueError("终态编译失败：" + final.message)
            proof = ScheduleValidator().validate(
                self.knowledge,
                final.runtime,
                final,
                CandidateSchedule(problem_hash=final.problem_hash, assignments=()),
            )
            if not proof.valid:
                raise ValueError("终态独立校验失败：" + ",".join(v.code for v in proof.violations))
            self.check_duration_facts()
        except (ValueError, TimeoutError) as exc:
            failure = str(exc)
            self.failure_detail = (
                exc.detail
                if isinstance(exc, DispatchFailure)
                else {
                    "code": "REPLAN_FAILED"
                    if failure.startswith("观察触发的重排失败")
                    else "TIMEOUT"
                    if isinstance(exc, TimeoutError)
                    else "EXECUTION_OR_VALIDATION_FAILED",
                    "now_sec": self.session.runtime.now_offset_sec,
                    "reason": failure,
                }
            )
            self.diagnostic(self.failure_detail)
        return self.report(started, failure, proof)

    def check_duration_facts(self):
        for record in self.session.runtime.executions:
            for span in record.task_spans:
                if span.task_id not in record.completed_task_ids:
                    continue
                operation = self.operations[span.task_id][1]
                duration = span.interval.end_sec - span.interval.start_sec
                limits = operation.duration
                if (
                    estimated_phase(operation) == "FIXED_PROCESS"
                    or span.task_id in self.fixed_program_tasks
                ) and duration != limits.execution_sec:
                    raise ValueError("固定工艺有效时间被扰动")
                if limits.lower_sec is not None and duration < limits.lower_sec:
                    raise ValueError("实际时长低于审核允许下限")
                if limits.upper_sec is not None and duration > limits.upper_sec:
                    raise ValueError("实际时长高于审核允许上限")

    def report(self, began, failure, proof):
        completed = self.completed()
        ends = []
        for instance in self.session.menu:
            tasks = [
                t.task_id
                for t in self.problem.logical_tasks
                if t.recipe_instance_id == instance.recipe_instance_id
            ]
            if tasks and set(tasks) <= completed.keys():
                ends.append(max(completed[t] for t in tasks))
        human = sorted(
            {
                (p.interval.start_sec, p.interval.end_sec)
                for e in self.session.runtime.executions
                for p in e.resource_spans
                if p.resource.resource_type == "HUMAN"
            }
        )
        blocks, block_start, block_end = [], None, None
        for start, end in human:
            if block_end is None or start >= block_end + self.session.policy.objective.rest_gap_sec:
                if block_start is not None:
                    blocks.append(block_end - block_start)
                block_start, block_end = start, end
            else:
                block_end = max(block_end, end)
        if block_start is not None:
            blocks.append(block_end - block_start)
        planned = self.initial_candidate.metrics.makespan_sec
        finish = max(ends) if len(ends) == len(self.session.menu) else None
        spread = max(ends) - min(ends) if len(ends) == len(self.session.menu) else None
        return {
            "status": "COMPLETED" if failure is None else "FAILED",
            "failure": failure,
            "failure_detail": self.failure_detail,
            "dispatch_diagnostics": self.dispatch_diagnostics,
            "completion_sec": finish,
            "spread_sec": spread,
            "spread_over_240": spread > 240 if spread is not None else None,
            "spread_over_300": spread > 300 if spread is not None else None,
            "delay_sec": max(0, finish - planned) if finish is not None else None,
            "human_work_sec": sum(end - start for start, end in human),
            "max_human_block_sec": max(blocks, default=0),
            "dispatch_wait_sec": self.wait_sec,
            "replan_count": len(self.replans),
            "replans": self.replans,
            "notification_change_count": sum(
                r.get("notification_change_count", 0) for r in self.replans
            ),
            "completed_task_count": len(completed),
            "required_task_count": len(self.operations),
            "variable_stage_count": self.perturbed,
            "protected_stage_count": self.fixed,
            "event_counts": dict(Counter(e["event_type"] for e in self.events)),
            "elapsed_ms": (time.perf_counter_ns() - began) / 1_000_000,
            "final_state_hash": content_hash(self.session),
            "validation": proof.model_dump(mode="json") if proof else None,
            "events": self.events,
            "final_session": self.session.model_dump(mode="json"),
        }
