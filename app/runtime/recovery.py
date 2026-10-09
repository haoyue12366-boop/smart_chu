"""恢复固定知识和持久事实；禁止用最新发布替代缺失的绑定版本。"""

from collections.abc import Callable

from app.domain.knowledge import MenuKnowledgeView, ReleaseRef
from app.domain.ports import Clock
from app.runtime.service import RuntimeService
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork


def restore_session(
    store: UnitOfWork,
    session_id: str,
    clock: Clock,
    load_knowledge: Callable[[ReleaseRef], MenuKnowledgeView],
) -> RuntimeService:
    with store.engine.connect() as tx:
        repo = RuntimeRepository(tx)
        session = repo.get(session_id)
        if session.knowledge_release_id is None:
            raise ValueError("会话缺少固定知识发布引用，不能自动替换")
        release = repo.release(session.knowledge_release_id)
        if release is None:
            raise ValueError("会话固定知识发布引用缺失")
    try:
        knowledge = load_knowledge(release)
    except (OSError, ValueError) as exc:
        raise ValueError("无法恢复会话固定知识版本：" + str(exc)) from exc
    if knowledge.release != release:
        raise ValueError("恢复加载器返回的固定知识身份不符")
    runtime = RuntimeService(store, knowledge, clock)
    runtime.get(session_id)
    return runtime
