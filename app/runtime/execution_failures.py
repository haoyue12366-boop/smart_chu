"""失败尝试不可覆盖；重试须绑定当前知识中的明确恢复规则。"""

from app.domain.base import content_hash
from app.domain.candidates import stable_id
from app.domain.events import ExecutionPayload, RuntimeEvent
from app.domain.ids import ExecutionId
from app.domain.knowledge import MenuKnowledgeView
from app.domain.recovery import RecoveryRuleSpec
from app.domain.runtime_session import RuntimeSession
from app.domain.runtime_snapshot import ExecutionRecord
from app.runtime.resumption import authorize_resumption


def request_retry(
    session: RuntimeSession, event: RuntimeEvent, knowledge: MenuKnowledgeView
) -> RuntimeSession:
    payload = event.payload
    assert isinstance(payload, ExecutionPayload)
    failed = next(
        (e for e in session.runtime.executions if e.execution_id == payload.execution_id), None
    )
    rule = next((r for r in session.recovery_rules if r.rule_id == payload.recovery_rule_id), None)
    if failed is None or failed.status != "FAILED" or rule is None:
        raise ValueError("缺少失败记录或已批准恢复规则")
    if sum(item.rule_id == rule.rule_id for item in session.recovery_rules) != 1:
        raise ValueError("恢复规则身份重复，无法确定授权范围")
    if failed.finished_at is None or event.occurred_at < failed.finished_at:
        raise ValueError("重试请求不能早于原执行的实际失败时刻")
    new_id = ExecutionId("retry-" + event.event_id.root)
    if any(record.execution_id == new_id for record in session.runtime.executions):
        raise ValueError("新重试身份已经被执行历史使用")
    if payload.task_id not in failed.task_ids:
        raise ValueError("重试请求工序不属于失败执行")
    if (
        rule.knowledge_version != session.runtime.knowledge_version
        or not rule.evidence_refs
        or set(rule.task_ids) != set(failed.task_ids)
        or len(rule.task_ids) != len(set(rule.task_ids))
    ):
        raise ValueError("恢复规则与失败成员或知识版本不匹配")
    if rule.kind == "REMAKE" and (
        rule.remaining_sec is not None or rule.resume_procedure is not None
    ):
        raise ValueError("重做不能用剩余时长缩短原工艺")
    if rule.source_kind == "SYNTHETIC":
        if session.runtime.execution_mode != "SIMULATED" or not all(
            ref.startswith("synthetic:") for ref in rule.evidence_refs
        ):
            raise ValueError("合成恢复规则只接受显式模拟及标注的合成依据")
    else:
        published = next((item for item in knowledge.rules if item.rule_id == rule.rule_id), None)
        if (
            published is None
            or published.kind != "RECOVERY"
            or published.review_status != "APPROVED"
            or published.rule_version != session.runtime.rule_version
            or published.evidence_refs != rule.evidence_refs
        ):
            raise ValueError("恢复规则不在固定知识的已审核发布中")
        specification = RecoveryRuleSpec.model_validate_json(
            published.group_compatibility_predicate
        )
        if (
            specification.kind != rule.kind
            or specification.remaining_sec != rule.remaining_sec
            or specification.resume_procedure != rule.resume_procedure
        ):
            raise ValueError("恢复方式或剩余时长偏离已审核工艺")
        recipes = {item.recipe_id: item for item in knowledge.recipes}
        actual = {}
        for instance in session.menu:
            recipe = recipes[instance.recipe_id]
            for operation in recipe.operations:
                task = stable_id(
                    "task", instance.recipe_instance_id.root, operation.operation_id.root
                )
                if task in {item.root for item in failed.task_ids}:
                    actual[(recipe.recipe_id, operation.operation_id)] = (
                        recipe.semantic_hash(),
                        content_hash(operation),
                    )
        expected = {
            (item.recipe_id, item.operation_id): (item.recipe_hash, item.operation_hash)
            for item in specification.bindings
        }
        if len(actual) != len(failed.task_ids) or actual != expected:
            raise ValueError("恢复规则未绑定当前完整工序及菜谱内容")
    assert session.runtime.details is not None
    if any(
        o.execution_id == failed.execution_id and o.released_at is None
        for o in session.runtime.details.occupancies
    ):
        raise ValueError("失败尝试仍占用资源，须先确认释放")
    if any(e.previous_execution_id == failed.execution_id for e in session.runtime.executions):
        raise ValueError("该失败尝试已有后继执行")
    resumption = (
        authorize_resumption(session, event, failed, rule) if rule.kind == "RESUME" else None
    )
    new = ExecutionRecord(
        execution_id=new_id,
        task_ids=failed.task_ids,
        status="PENDING",
        source=event.source,
        event_refs=(event.event_id,),
        previous_execution_id=failed.execution_id,
        recovery_rule_id=rule.rule_id,
        recovery_kind=rule.kind,
        resumption=resumption,
        remaining_sec=rule.remaining_sec,
        remaining_source_ref=rule.rule_id if rule.remaining_sec is not None else None,
    )
    return session.model_copy(
        update={
            "runtime": session.runtime.model_copy(
                update={"executions": (*session.runtime.executions, new)}
            )
        }
    )
