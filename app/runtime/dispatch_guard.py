"""开工前保护有限间隔链的后续资源；只决定派发，不制造占用或完成事实。"""

from dataclasses import dataclass

from app.domain.ids import TaskId
from app.domain.knowledge import MenuKnowledgeView
from app.domain.resources import ResourceUse
from app.domain.runtime_session import ExecutionBinding, RuntimeSession
from app.runtime.completed_tasks import completed_task_times
from app.runtime.execution_resources import conflict, group_uses

Dependencies = tuple[tuple[str, str, int, int | None], ...]


@dataclass(frozen=True)
class GuardDecision:
    reason: str = ""
    protected_task_ids: tuple[str, ...] = ()
    resource_ids: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return bool(self.reason)


def stage_uses(binding: ExecutionBinding, task: TaskId) -> tuple[ResourceUse, ...]:
    span = next(p.interval for p in binding.task_spans if p.task_id == task)
    start = span.start_sec - binding.assignment.interval.start_sec
    end = span.end_sec - binding.assignment.interval.start_sec
    return group_uses(
        (
            *binding.assignment.resource_uses,
            *(
                p.resource_use
                for p in binding.carrier.resource_phases
                if p.start_offset_sec < end and p.end_offset_sec > start
            ),
        )
    )


def tight_chains(session: RuntimeSession, dependencies: Dependencies) -> tuple[frozenset[str], ...]:
    """合并有限间隔、同一承载体及连续占用；补齐链内经过外部节点的路径。"""
    vertices = {t.root for b in session.bindings for t in b.assignment.task_ids}
    vertices.update(t for before, after, _, _ in dependencies for t in (before, after))
    parent = {t: t for t in vertices}

    def root(task: str) -> str:
        while parent[task] != task:
            parent[task] = parent[parent[task]]
            task = parent[task]
        return task

    def merge(tasks: set[str]) -> None:
        if tasks:
            head = min(tasks)
            for task in tasks:
                parent[root(task)] = root(head)

    tight: set[str] = set()
    for before, after, _, high in dependencies:
        if high is not None:
            merge({before, after})
            tight.update((before, after))
    for binding in session.bindings:
        merge({t.root for t in binding.assignment.task_ids})
        for scope in binding.continuities:
            merge({t.root for t in scope.members} & vertices)
    ancestors: dict[str, set[str]] = {t: set() for t in vertices}
    changed = True
    while changed:
        changed = False
        for before, after, _, _ in dependencies:
            incoming = ancestors[before] | {before}
            if not incoming <= ancestors[after]:
                ancestors[after].update(incoming)
                changed = True
    # 避免把 A->B->C 的 B 当成开工前必须完成的外部前序，造成自己等待自己。
    changed = True
    while changed:
        changed = False
        components: dict[str, set[str]] = {}
        for task in vertices:
            components.setdefault(root(task), set()).add(task)
        for members in components.values():
            if not members & tight:
                continue
            incoming = set().union(*(ancestors[t] for t in members))
            bridge = {t for t in incoming - members if ancestors[t] & members}
            if bridge:
                merge(members | bridge)
                changed = True
    return tuple(frozenset(members) for _, members in sorted(components.items()) if members & tight)


def guard_start(
    session: RuntimeSession,
    knowledge: MenuKnowledgeView,
    dependencies: Dependencies,
    group: tuple[TaskId, ...],
    at: int,
) -> GuardDecision:
    """只用已发布链和真实开始/完成/占用；不接受未来扰动或推测释放时刻。"""
    if session.policy.dispatch_guard_policy_id == "NONE":
        return GuardDecision()
    if at < session.runtime.now_offset_sec:
        raise ValueError("派发检查不能倒退到执行事实之前")
    current = {t.root for t in group}
    done = {task for task, end in completed_task_times(session.runtime).items() if end <= at}
    started = {t.root for e in session.runtime.executions for t in e.started_task_ids}
    uses = {
        t.root: stage_uses(binding, t)
        for binding in session.bindings
        for t in binding.assignment.task_ids
    }
    chains = tight_chains(session, dependencies)
    owned = next((c for c in chains if c & current), frozenset())
    active = [c for c in chains if c & started and not c <= done]
    # 链已经开启时必须继续合法接续，不能在窗口打开后再用风险策略阻断它。
    if owned in active:
        return GuardDecision()
    proposed = owned - done if owned else current
    requested = tuple(u for t in proposed for u in uses.get(t, ()))

    def blocked(reason: str, tasks: set[str] | frozenset[str]) -> GuardDecision:
        return GuardDecision(
            reason, tuple(sorted(tasks)), tuple(sorted({u.resource_id for u in requested}))
        )

    for chain in active:
        reserved = tuple(u for t in chain - done for u in uses.get(t, ()))
        if any(conflict(a, b) for a in requested for b in reserved):
            return blocked("已开启连续工艺链尚需这些资源，等待真实完成后再开工", chain)
    if not owned:
        return GuardDecision()
    predecessors = {b for b, a, _, _ in dependencies if a in owned and b not in owned}
    if predecessors - done:
        return blocked("开启连续工艺链前先完成整条链的外部前序", predecessors - done)
    details = session.runtime.details
    assert details is not None
    records = {e.execution_id: e for e in session.runtime.executions}
    for occupation in details.occupancies:
        if occupation.released_at is not None:
            continue
        owner = records[occupation.execution_id]
        if set(t.root for t in owner.task_ids) <= owned:
            continue
        if any(conflict(occupation.resource, u) for u in requested):
            return blocked("连续工艺链后续所需资源仍被其他执行占用，等待实际释放", owned)
    return GuardDecision()
