"""按内容绑定的整组规则生成备选；保留独立物料端口和全部单独方案。"""

import time
from collections.abc import Iterator
from itertools import product

from app.domain.base import content_hash
from app.domain.candidates import SharedCandidateContext, SharedPrepCandidate, stable_id
from app.domain.compatibility import GroupRuleSpec
from app.domain.ids import CarrierId
from app.domain.scheduling_problem import LogicalTask
from app.knowledge.rules import RuleEngine


def iter_shared_prep(context: SharedCandidateContext) -> Iterator[SharedPrepCandidate]:
    def check_budget() -> None:
        if time.monotonic_ns() >= context.deadline.expires_at_ns:
            raise TimeoutError("共享候选生成截止时间已到")

    check_budget()
    instance_recipes = {i.recipe_instance_id: i.recipe_id for i in context.instantiated.menu}
    buckets: dict[tuple[str, str], list[LogicalTask]] = {}
    for task in context.instantiated.tasks:
        buckets.setdefault(
            (instance_recipes[task.recipe_instance_id].root, task.operation_id.root), []
        ).append(task)
    for rule in context.group.knowledge.rules:
        check_budget()
        if rule.kind != "SHARED_PREP":
            continue
        try:
            spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        except ValueError:
            continue
        choices = [buckets.get((b.recipe_id.root, b.operation_id.root), []) for b in spec.bindings]
        for members in product(*choices):
            check_budget()
            # Evaluate this rule only: another rule may bind the same group with other parameters.
            group = context.group.model_copy(
                update={"knowledge": context.group.knowledge.model_copy(update={"rules": (rule,)})}
            )
            decision = RuleEngine().evaluate_group(members, group)
            if not decision.compatible:
                continue
            options = [
                tuple(c for c in context.standalone if c.covers == (m.task_id,)) for m in members
            ]
            for singles in product(*options):
                check_budget()
                uses = singles[0].resource_uses
                # Current reviewed cutting/washing is one physical action with the same resources.
                # Differing equipment choices are separate alternatives, never silently discarded.
                signatures = tuple(u.model_dump(exclude={"evidence_refs"}) for u in uses)
                if any(
                    tuple(u.model_dump(exclude={"evidence_refs"}) for u in c.resource_uses)
                    != signatures
                    for c in singles[1:]
                ):
                    continue
                uses = tuple(
                    use.model_copy(
                        update={
                            "evidence_refs": tuple(
                                dict.fromkeys(
                                    ref for c in singles for ref in c.resource_uses[i].evidence_refs
                                )
                            )
                        }
                    )
                    for i, use in enumerate(uses)
                )
                candidate = SharedPrepCandidate(
                    carrier_id="pending",
                    kind="SHARED_PREP",
                    covers=tuple(m.task_id for m in members),
                    duration_sec=spec.duration_sec,
                    resource_uses=uses,
                    rule_refs=decision.rule_refs,
                    material_inputs=tuple(m for c in singles for m in c.material_inputs),
                    material_outputs=tuple(m for c in singles for m in c.material_outputs),
                    provenance_refs=decision.evidence_refs,
                )
                yield (
                    candidate.model_copy(
                        update={
                            "carrier_id": CarrierId(
                                stable_id(
                                    "shared-prep",
                                    context.group.knowledge.release.rule_version,
                                    content_hash(candidate),
                                )
                            )
                        }
                    )
                )


def generate_shared_prep(context: SharedCandidateContext) -> tuple[SharedPrepCandidate, ...]:
    return tuple({c.carrier_id: c for c in iter_shared_prep(context)}.values())
