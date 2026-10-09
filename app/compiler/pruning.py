"""仅按完整领域语义等价去重；时长较短不构成替换证明。"""

import json
import time

from app.domain.base import FrozenModel
from app.domain.ports import Deadline
from app.domain.pruning import PruningContext, PruningRecord
from app.domain.scheduling_problem import CandidateCarrier


class PruningResult(FrozenModel):
    candidates: tuple[CandidateCarrier, ...]
    records: tuple[PruningRecord, ...]
    may_lose_optimum: bool = False


def deduplicate(
    candidates: tuple[CandidateCarrier, ...],
    context: PruningContext | None = None,
    *,
    deadline: Deadline | None = None,
) -> PruningResult:
    if deadline is not None and time.monotonic_ns() >= deadline.expires_at_ns:
        raise TimeoutError("等价去重截止时间已到")
    if len({c.carrier_id for c in candidates}) != len(candidates):
        raise ValueError("候选身份重复，不能以等价去重掩盖冲突")
    protected = set(context.protected_carrier_ids if context else ())
    representatives: dict[str, CandidateCarrier] = {}
    retained: list[CandidateCarrier] = []
    records: list[PruningRecord] = []
    for candidate in sorted(candidates, key=lambda c: c.carrier_id.root):
        if deadline is not None and time.monotonic_ns() >= deadline.expires_at_ns:
            raise TimeoutError("等价去重截止时间已到")
        # 包括来源、资源配置、物料端口和强制标志；不猜测集合或工艺的等价性。
        key = json.dumps(candidate.model_dump(mode="json", exclude={"carrier_id"}), sort_keys=True)
        previous = representatives.get(key)
        if previous is None or candidate.carrier_id in protected:
            representatives.setdefault(key, candidate)
            retained.append(candidate)
        else:
            records.append(
                PruningRecord(
                    removed_candidate_id=candidate.carrier_id,
                    replacement_candidate_id=previous.carrier_id,
                    context=context,
                )
            )
    return PruningResult(candidates=tuple(retained), records=tuple(records))
