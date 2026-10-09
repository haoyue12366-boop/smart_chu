"""在同一截止时间内按稳定生成顺序去重及截断；绝不裁剪独立或强制工艺。"""

import json
import time
from collections import Counter
from collections.abc import Iterable

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt
from app.domain.ids import CarrierId, TaskId
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.pruning import PruningContext, PruningRecord
from app.domain.scheduling_problem import CandidateCarrier


class OptionalSelection(FrozenModel):
    candidates: tuple[CandidateCarrier, ...]
    records: tuple[PruningRecord, ...]
    generated_count: NonNegativeInt
    enumeration_complete: bool
    truncation_reasons: tuple[NonEmpty, ...]


def retain_optional(
    candidates: Iterable[CandidateCarrier],
    policy: SchedulingPolicy,
    deadline: Deadline,
    context: PruningContext,
) -> OptionalSelection:
    retained: list[CandidateCarrier] = []
    records: list[PruningRecord] = []
    identities: dict[CarrierId, str] = {}
    representatives: dict[str, CandidateCarrier] = {}
    counts: Counter[TaskId] = Counter()
    reasons: list[str] = []
    generated = 0
    complete = True
    for candidate in candidates:
        if time.monotonic_ns() >= deadline.expires_at_ns:
            raise TimeoutError("共享候选去重与截断截止时间已到")
        generated += 1
        if candidate.kind == "STANDALONE" or candidate.mandatory_recipe_batch:
            raise ValueError("独立或强制工艺必须走不截断的保留路径")
        signature = json.dumps(
            candidate.model_dump(mode="json", exclude={"carrier_id"}), sort_keys=True
        )
        previous_signature = identities.get(candidate.carrier_id)
        if previous_signature is not None:
            if previous_signature != signature:
                raise ValueError("同一候选身份内容冲突")
            continue
        identities[candidate.carrier_id] = signature
        previous = representatives.get(signature)
        if previous is not None and policy.equivalence_deduplication:
            records.append(
                PruningRecord(
                    removed_candidate_id=candidate.carrier_id,
                    replacement_candidate_id=previous.carrier_id,
                    context=context,
                )
            )
            continue
        reason = None
        if len(retained) >= policy.max_nonstandalone_per_problem:
            reason = "PER_PROBLEM_LIMIT"
            complete = False
        elif any(counts[t] >= policy.max_nonstandalone_per_requirement for t in candidate.covers):
            reason = "PER_REQUIREMENT_LIMIT"
        if reason is not None:
            reasons.append(reason)
            records.append(
                PruningRecord(
                    removed_candidate_id=candidate.carrier_id,
                    replacement_candidate_id=None,
                    kind="BUDGET_TRUNCATION",
                    proof_rule_id="optional-limit-v1",
                    assumptions=(reason, "可能损失最优解"),
                    preserved_time_and_material_ports=False,
                    context=context,
                )
            )
            if not complete:
                break
            continue
        representatives[signature] = candidate
        retained.append(candidate)
        counts.update(set(candidate.covers))
    if time.monotonic_ns() >= deadline.expires_at_ns:
        raise TimeoutError("共享候选去重与截断截止时间已到")
    return OptionalSelection(
        candidates=tuple(retained),
        records=tuple(records),
        generated_count=generated,
        enumeration_complete=complete,
        truncation_reasons=tuple(dict.fromkeys(reasons)),
    )
