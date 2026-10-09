"""首轮传输完整 JSON 问题，后续用同进程哈希引用；保留完整作业校验。"""

from __future__ import annotations

import json
from multiprocessing.queues import Queue
from multiprocessing.reduction import ForkingPickler
from typing import TYPE_CHECKING

from app.scheduling.worker import SolverWorker, WorkerJob, WorkerResponse, _worker_loop

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.domain.scheduling_problem import SchedulingProblem

_cached_problem: SchedulingProblem | None = None


def _decode_job(body: str) -> WorkerJob:
    global _cached_problem
    # 完整问题由 Pydantic 的 JSON 路径直接解析，避免先构造大型 Python
    # 字典再逐层验证的重复开销；引用消息单独解码。
    result = WorkerJob.model_validate_json(body)
    _cached_problem = result.problem
    return result


def _decode_cached_job(body: str) -> WorkerJob:
    payload = json.loads(body)
    if isinstance(payload, dict) and set(payload) == {"cached_problem_hash", "job"}:
        if (
            _cached_problem is None
            or payload["cached_problem_hash"] != _cached_problem.problem_hash
        ):
            raise ValueError("工作进程缺少匹配的问题缓存，拒绝引用")
        # 完整问题已校验且不可变；其余每轮参数仍按完整 WorkerJob 契约校验。
        return WorkerJob.model_validate({**payload["job"], "problem": _cached_problem})
    raise ValueError("非法问题缓存引用")


def _decode_response(body: str) -> WorkerResponse:
    return WorkerResponse.model_validate_json(body)


def _encode_job(message: WorkerJob) -> tuple[Callable[[str], WorkerJob], tuple[str]]:
    if message.use_cached_problem:
        payload = {
            "cached_problem_hash": message.problem.problem_hash,
            "job": message.model_dump(mode="json", exclude={"problem"}),
        }
        return _decode_cached_job, (json.dumps(payload, ensure_ascii=False, separators=(",", ":")),)
    return _decode_job, (message.model_dump_json(),)


def _encode_response(message: WorkerResponse) -> tuple[Callable[[str], WorkerResponse], tuple[str]]:
    return _decode_response, (message.model_dump_json(),)


def _json_worker_loop(inbox: Queue[WorkerJob | str], outbox: Queue[WorkerResponse]) -> None:
    ForkingPickler.register(WorkerResponse, _encode_response)
    _worker_loop(inbox, outbox)


class JsonSolverWorker(SolverWorker):
    def __init__(self) -> None:
        # 仅显式构造在线工作进程时注册。纯对照/扰动脚本使用原 SolverWorker，
        # 不构造此适配器，也不改变它们的消息传输或预算。
        ForkingPickler.register(WorkerJob, _encode_job)
        super().__init__(target=_json_worker_loop)
        self.cache_problem_messages = True
