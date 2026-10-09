"""仅解释版本化规则数据；不执行文本谓词，不连接图数据库。"""

from app.domain.base import content_hash
from app.domain.compatibility import GroupContext, GroupRuleSpec
from app.domain.ports import CompatibilityDecision
from app.domain.processing_rules import ProcessingRule
from app.domain.recovery import superseded_failures
from app.domain.scheduling_problem import LogicalTask


class RuleEngine:
    def evaluate_group(
        self, members: tuple[LogicalTask, ...], context: GroupContext
    ) -> CompatibilityDecision:
        issue = self._precheck(members, context)
        source_evidence = tuple(
            dict.fromkeys(ref for member in members for ref in member.operation.provenance_refs)
        )
        if issue:
            return CompatibilityDecision(
                compatible=False, rule_refs=(), evidence_refs=source_evidence, reasons=(issue,)
            )
        reasons = []
        checked_rules = []
        checked_evidence = list(source_evidence)
        for rule in context.knowledge.rules:
            if rule.kind not in {"SHARED_PREP", "STRICT_TOGETHER"}:
                continue
            checked_rules.append(rule.rule_id)
            checked_evidence.extend(rule.evidence_refs)
            try:
                spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
            except ValueError:
                reasons.append(f"{rule.rule_id}:规则不是受支持的结构化全组规格")
                continue
            reason = self._check_rule(rule, spec, members, context)
            if reason:
                reasons.append(f"{rule.rule_id}:{reason}")
                continue
            common = set(spec.bindings[0].configuration_keys)
            for binding in spec.bindings[1:]:
                common.intersection_update(binding.configuration_keys)
            if not common:
                reasons.append(f"{rule.rule_id}:全组没有共同配置")
                continue
            return CompatibilityDecision(
                compatible=True,
                rule_refs=(rule.rule_id,),
                evidence_refs=rule.evidence_refs,
                common_configuration_keys=tuple(sorted(common)),
            )
        return CompatibilityDecision(
            compatible=False,
            rule_refs=tuple(checked_rules),
            evidence_refs=tuple(dict.fromkeys(checked_evidence)),
            reasons=tuple(reasons) or ("没有覆盖整组成员的已授权规则",),
        )

    @staticmethod
    def _precheck(members: tuple[LogicalTask, ...], context: GroupContext) -> str | None:
        k, runtime = context.knowledge, context.runtime
        if (runtime.knowledge_version, runtime.rule_version, runtime.snapshot_id) != (
            k.release.knowledge_version,
            k.release.rule_version,
            k.release.snapshot_id,
        ):
            return "知识与运行状态版本不一致"
        if len(members) < 2 or len({m.task_id for m in members}) != len(members):
            return "共享成员为空、单成员或重复"
        if set(context.unknown_material_task_ids).intersection(m.task_id for m in members):
            return "成员物料状态未知"
        ids = {m.task_id for m in members}
        retried = superseded_failures(runtime)
        for execution in runtime.executions:
            if execution.execution_id in retried:
                continue
            if ids.intersection(execution.task_ids) and (
                execution.status not in {"PENDING", "READY"} or execution.started_at is not None
            ):
                return "成员已开始、已结束、失败或取消"
        instances = {i.recipe_instance_id: i for i in context.menu}
        if len(instances) != len(context.menu):
            return "菜单实例身份重复"
        recipes = {r.recipe_id: r for r in k.recipes}
        selected: dict[str, set[str]] = {}
        for member in members:
            instance = instances.get(member.recipe_instance_id)
            if instance is None or instance.recipe_id not in recipes:
                return "成员不属于当前菜单知识"
            recipe = recipes[instance.recipe_id]
            source = next(
                (o for o in recipe.operations if o.operation_id == member.operation_id), None
            )
            if source is None or source != member.operation:
                return "成员工序与来源不一致"
            if (
                source.execution_policy.fixed_batch_id
                or source.execution_policy.interventions
                or source.execution_policy.batch_policy == "FIXED_RECIPE"
            ):
                return "固定程序或介入工序不能被通用共享替代"
            selected.setdefault(member.recipe_instance_id.root, set()).add(member.operation_id.root)
        # 从发布菜谱走可达关系，不依赖编译器可能被遗漏的直接边。
        for instance_id, chosen in selected.items():
            recipe = recipes[
                next(i.recipe_id for i in context.menu if i.recipe_instance_id.root == instance_id)
            ]
            edges: dict[str, list[str]] = {}
            for dep in recipe.dependencies:
                edges.setdefault(dep.predecessor_id.root, []).append(dep.successor_id.root)
            for start in chosen:
                pending = list(edges.get(start, ()))
                seen = set()
                while pending:
                    current = pending.pop()
                    if current in chosen:
                        return "共享成员存在祖先后继关系"
                    if current not in seen:
                        seen.add(current)
                        pending.extend(edges.get(current, ()))
        return None

    @staticmethod
    def _check_rule(
        rule: ProcessingRule,
        spec: GroupRuleSpec,
        members: tuple[LogicalTask, ...],
        context: GroupContext,
    ) -> str | None:
        if rule.rule_version != context.knowledge.release.rule_version or not rule.evidence_refs:
            return "规则版本或证据不完整"
        if spec.authority == "HUMAN_REVIEWED":
            if rule.review_status != "APPROVED":
                return "规则尚未人工批准"
        elif not (
            context.allow_delegated_estimates
            and context.knowledge.release.release_kind == "development"
            and rule.review_status == "NEEDS_REVIEW"
        ):
            return "委托估计仅允许显式开发使用"
        instances = {i.recipe_instance_id: i.recipe_id for i in context.menu}
        bindings = {(b.recipe_id, b.operation_id): b for b in spec.bindings}
        identities = [(instances[m.recipe_instance_id], m.operation_id) for m in members]
        if len(identities) != len(bindings) or set(identities) != set(bindings):
            return "规则没有绑定当前整组或成员数量超出审核范围"
        recipes = {r.recipe_id: r for r in context.knowledge.recipes}
        for member, identity in zip(members, identities, strict=True):
            binding = bindings[identity]
            if binding.recipe_hash != recipes[
                identity[0]
            ].semantic_hash() or binding.operation_hash != content_hash(member.operation):
                return "审核内容哈希已失效"
        if len({b.processing_spec for b in spec.bindings}) != 1:
            return "加工规格或物料状态不一致"
        start = max(context.runtime.now_offset_sec, *(m.earliest_start_sec for m in members))
        if any(
            m.latest_end_sec is not None and start + spec.duration_sec > m.latest_end_sec
            for m in members
        ):
            return "全组不可能满足硬时间窗口"
        if rule.kind == "SHARED_PREP":
            if len({m.operation.action for m in members}) != 1 or any(
                m.operation.action not in {"CUT", "WASH"} for m in members
            ):
                return "首版只允许同动作清洗或切配"
        else:
            if any(
                m.operation.action != "HEAT"
                or m.operation.duration.execution_sec != spec.duration_sec
                for m in members
            ):
                return "有效加热时长不同或成员不是加热工序"
            signatures = []
            devices = {d.device_instance_id: d for d in context.knowledge.devices}
            for member in members:
                uses = [
                    u for u in member.operation.resource_requirements if u.resource_type == "DEVICE"
                ]
                if len(uses) != 1 or uses[0].resource_id not in devices:
                    return "热成员没有单一明确物理设备"
                use = uses[0]
                device = devices[use.resource_id]
                if not device.physical_resource_id or not device.component_id:
                    return "设备映射未知"
                for state in context.runtime.device_states:
                    if (state.physical_resource_id, state.component_id) == (
                        device.physical_resource_id,
                        device.component_id,
                    ) and (
                        state.availability_status != "AVAILABLE" or state.occupancy_status != "FREE"
                    ):
                        return "设备状态未知、不可用或尚未确认释放"
                signatures.append(
                    (
                        device.physical_resource_id,
                        device.component_id,
                        tuple(sorted((c.parameter, str(c.value)) for c in use.configuration)),
                    )
                )
            if len(set(signatures)) != 1:
                return "全组设备或模式温度湿度不同"
        return None
