"""后台按会话隔离异常，保存可见失败；不把失败请求写成成功回执。"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import OperationalError

from app.domain.candidates import stable_id
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


@contextmanager
def isolated_recovery(store: UnitOfWork, session_id: str) -> Iterator[None]:
    with store.engine.connect() as tx:
        base = RuntimeRepository(tx).get(session_id).runtime
    expected = (base.state_revision, base.current_plan_version)
    try:
        yield
    except (TimeoutError, OperationalError):
        # 超时或数据库暂不可用保留持久队列，下轮重试，不改写已提交事实。
        logger.exception("会话 %s 本轮恢复未完成，将继续重试", session_id)
    except Exception as exc:
        # 这是独立作业的最外层边界。异常必须记录并显示，不能饿死其余桌次。
        logger.exception("会话 %s 后台推进失败，暂停该会话派发", session_id)
        message = "后台推进已暂停：" + str(exc)
        try:
            with store.transaction() as tx:
                repo = RuntimeRepository(tx)
                current = repo.get(session_id)
                if (
                    current.runtime.state_revision,
                    current.runtime.current_plan_version,
                ) != expected:
                    # 当前作业已落账或前台已修正，不把旧快照异常写入新版本。
                    # 持续故障会在下一轮基于新快照重新观察。
                    return
                if current.dispatch_blocked and current.last_planning_failure == message:
                    return
                revision = current.runtime.state_revision
                repo.save(
                    current.model_copy(
                        update={
                            "runtime": current.runtime.model_copy(
                                update={"state_revision": revision + 1}
                            ),
                            "dispatch_blocked": True,
                            "last_planning_failure": message,
                        }
                    ),
                    expected_revision=revision,
                    expected_plan=current.runtime.current_plan_version,
                )
                repo.audit(
                    stable_id("recovery-failed", session_id, str(revision)),
                    session_id,
                    json.dumps({"kind": "RECOVERY_FAILED", "message": message}, ensure_ascii=False),
                )
        except Exception:
            logger.exception("会话 %s 的恢复失败状态未能持久化", session_id)
