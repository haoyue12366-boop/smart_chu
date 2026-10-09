"""P2 基础编译入口；无数据库、LLM 或求解器调用。"""

import time
from itertools import chain

from app.compiler.bounds import derive_bounds
from app.compiler.candidate_generation import standalone_candidates
from app.compiler.candidate_limits import retain_optional
from app.compiler.constraint_catalog import build_catalog
from app.compiler.cooking_completion import bind_cooking_completions
from app.compiler.dependency_graph import build_dependency_graph
from app.compiler.duration_inputs import apply_duration_buffers
from app.compiler.instantiate import instantiate
from app.compiler.inventory_supply import inventory_candidates
from app.compiler.material_flow import (
    bind_candidate_materials,
    build_material_allocations,
    compile_material_flow,
)
from app.compiler.model_stats import estimate_model_size
from app.compiler.phase_expansion import expand_required_programs
from app.compiler.pruning import PruningResult, deduplicate
from app.compiler.resumption import resume_bounds, resume_candidates
from app.compiler.runtime_constraints import compile_resource_blocks
from app.compiler.shared_prep import iter_shared_prep
from app.compiler.thermal_batches import iter_thermal_batches
from app.domain.advance_preparation import active_preparations
from app.domain.base import content_hash
from app.domain.candidates import SharedCandidateContext, stable_id
from app.domain.compatibility import GroupContext
from app.domain.critical_windows import recipe_sequence_dependencies
from app.domain.duration_estimate import DurationBuffer
from app.domain.inventory import committed_fulfillments
from app.domain.knowledge import MenuKnowledgeView
from app.domain.material_flow import MaterialAllocationModel
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.pruning import PruningContext
from app.domain.recovery import compatible_retry_scope, pending_retry_groups
from app.domain.reports import CompilationFailure
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import (
    CandidateGenerationReport,
    RecipeInstance,
    SchedulingProblem,
)


class ProblemCompiler:
    def compile(
        self,
        knowledge: MenuKnowledgeView,
        menu: tuple[RecipeInstance, ...],
        runtime: RuntimeSnapshot,
        policy: SchedulingPolicy,
        deadline: Deadline,
    ) -> SchedulingProblem | CompilationFailure:
        def check_budget() -> None:
            if time.monotonic_ns() >= deadline.expires_at_ns:
                raise TimeoutError("编译共享截止时间已到")

        try:
            check_budget()
            if policy.minute_output_mode == "INTEGER" and policy.time_grid_sec % 60:
                raise ValueError("整数分钟输出须显式使用60秒网格")
            instantiated = instantiate(menu, knowledge, runtime)
            if isinstance(instantiated, CompilationFailure):
                return instantiated
            if runtime.details:
                starts = dict(runtime.details.earliest_starts)
                instantiated = instantiated.model_copy(
                    update={
                        "tasks": tuple(
                            t.model_copy(
                                update={
                                    "earliest_start_sec": max(
                                        t.earliest_start_sec,
                                        starts.get(t.recipe_instance_id.root, 0),
                                    )
                                }
                            )
                            for t in instantiated.tasks
                        )
                    }
                )
            duration_buffers: tuple[DurationBuffer, ...] = ()
            if policy.duration_policy_id == "BUFFERED" and instantiated.tasks:
                instantiated, duration_buffers = apply_duration_buffers(
                    instantiated, knowledge, runtime, policy
                )
            programs = expand_required_programs(instantiated, knowledge)
            if isinstance(programs, CompilationFailure):
                return programs
            policy_edges = recipe_sequence_dependencies(menu, knowledge, policy)
            if policy_edges:
                instantiated = instantiated.model_copy(
                    update={"dependencies": (*instantiated.dependencies, *policy_edges)}
                )
            check_budget()
            candidates = standalone_candidates(instantiated, knowledge)
            if isinstance(candidates, CompilationFailure):
                return candidates
            inventory, inventory_rejections = inventory_candidates(
                instantiated, knowledge, runtime, programs
            )
            flow = compile_material_flow(
                instantiated,
                knowledge,
                runtime,
                frozenset(task for candidate in inventory for task in candidate.covers),
            )
            candidates = bind_candidate_materials(candidates, instantiated)
            context = SharedCandidateContext(
                instantiated=instantiated,
                group=GroupContext(
                    knowledge=knowledge,
                    runtime=runtime,
                    menu=menu,
                    allow_delegated_estimates=policy.allow_delegated_shared_estimates,
                ),
                standalone=candidates,
                deadline=deadline,
            )
            pruning_context = PruningContext(
                knowledge_version=knowledge.release.knowledge_version,
                rule_version=knowledge.release.rule_version,
                policy_version=policy.policy_version,
                state_dependency_hash=content_hash(runtime),
            )
            optional = retain_optional(
                chain(
                    iter_shared_prep(context) if policy.shared_prep else (),
                    iter_thermal_batches(context) if policy.strict_together_batch else (),
                ),
                policy,
                deadline,
                pruning_context,
            )
            generated_count = len(candidates) + optional.generated_count
            pruning = (
                deduplicate(candidates, pruning_context, deadline=deadline)
                if policy.equivalence_deduplication
                else PruningResult(candidates=candidates, records=())
            )
            candidates = pruning.candidates
            shared = tuple(c for c in optional.candidates if c.kind == "SHARED_PREP")
            thermal = tuple(c for c in optional.candidates if c.kind == "THERMAL_BATCH")
            candidates = tuple(c for c in candidates if compatible_retry_scope(c.covers, runtime))
            shared = tuple(c for c in shared if compatible_retry_scope(c.covers, runtime))
            thermal = tuple(c for c in thermal if compatible_retry_scope(c.covers, runtime))
            retry_tasks = {task for group in pending_retry_groups(runtime) for task in group}
            inventory = tuple(c for c in inventory if not retry_tasks.intersection(c.covers))
            records = (*pruning.records, *optional.records)
            inventory_limited = (
                bool(inventory)
                and runtime.details is not None
                and any(
                    sum(lot.spec_id == rule.source_spec_id for lot in runtime.details.lots) > 1
                    for rule in runtime.details.inventory_rules
                    if any(
                        candidate.inventory_supply
                        and candidate.inventory_supply.rule_id == rule.rule_id
                        for candidate in inventory
                    )
                )
            )
            truncation_reasons = (
                *optional.truncation_reasons,
                *(("inventory_lot_allocations_first_fit",) if inventory_limited else ()),
            )
            candidates = resume_candidates(candidates, runtime)
            shared = resume_candidates(shared, runtime)
            thermal = resume_candidates(thermal, runtime)
            instantiated = instantiated.model_copy(
                update={"tasks": resume_bounds(instantiated.tasks, runtime)}
            )
            graph = build_dependency_graph(instantiated.tasks, instantiated.dependencies)
            bounds = derive_bounds(
                graph, (*candidates, *shared, *thermal, *inventory), runtime, policy
            )
            check_budget()
            device_ids = {
                u.resource_id
                for c in (*candidates, *shared, *thermal)
                for u in c.resource_uses
                if u.resource_type == "DEVICE"
            }
            blocks = compile_resource_blocks(
                knowledge, runtime, device_ids, bounds.horizon_sec, programs
            )
            by_task = {b.task_id: b for b in bounds.tasks}
            tasks = tuple(
                t.model_copy(
                    update={
                        "earliest_start_sec": by_task[t.task_id].earliest_start_sec,
                        "latest_end_sec": by_task[t.task_id].latest_end_sec,
                    }
                )
                for t in instantiated.tasks
            )
            problem = SchedulingProblem(
                problem_id=stable_id("problem", bounds.input_hash, knowledge.snapshot_hash),
                knowledge_version=knowledge.release.knowledge_version,
                knowledge_release_kind=knowledge.release.release_kind,
                rule_version=knowledge.release.rule_version,
                snapshot_id=knowledge.release.snapshot_id,
                snapshot_schema_version=knowledge.snapshot_schema_version,
                policy=policy,
                runtime=runtime,
                horizon_sec=bounds.horizon_sec,
                recipe_instances=menu,
                logical_tasks=tasks,
                cooking_completions=bind_cooking_completions(knowledge, menu, tasks, policy),
                dependencies=instantiated.dependencies,
                resources=knowledge.devices,
                device_profiles=knowledge.profiles,
                transition_rules=knowledge.rules,
                standalone_candidates=candidates,
                shared_prep_candidates=shared,
                thermal_batch_candidates=thermal,
                inventory_supply_candidates=inventory,
                inventory_rejections=inventory_rejections,
                fixed_supply_fulfillments=committed_fulfillments(runtime),
                advance_preparations=active_preparations(runtime),
                fixed_executions=tuple(
                    e
                    for e in runtime.executions
                    if e.status in {"COMPLETED", "RUNNING"}
                    and set(e.task_ids).intersection(t.task_id for t in tasks)
                ),
                material_supply_and_demand=tuple(d.requirement for d in flow.demands),
                candidate_generation_report=CandidateGenerationReport(
                    generated_count=generated_count + len(inventory),
                    retained_count=len(candidates) + len(shared) + len(thermal) + len(inventory),
                    removed_equivalent_ids=tuple(
                        r.removed_candidate_id for r in records if r.kind == "EQUIVALENT"
                    ),
                    candidate_truncated=bool(truncation_reasons),
                    may_lose_optimum=bool(truncation_reasons),
                    truncation_reasons=truncation_reasons,
                    enumeration_complete=optional.enumeration_complete and not inventory_limited,
                    generated_count_is_exact=optional.enumeration_complete
                    and not inventory_limited,
                ),
                pruning_records=records,
                provenance_index=knowledge.provenance_index,
                mandatory_programs=programs,
                material_flow=flow,
                material_allocations=build_material_allocations(
                    (*candidates, *shared, *thermal, *inventory),
                    flow,
                    knowledge.rules,
                    tuple(rule.rule_id for rule in runtime.details.inventory_rules)
                    if runtime.details
                    else (),
                )
                if shared or thermal or inventory
                else MaterialAllocationModel(),
                resource_blocks=blocks,
                duration_buffers=duration_buffers,
            )
            estimate = estimate_model_size(problem)
            problem = problem.model_copy(
                update={
                    "constraint_catalog": build_catalog(problem),
                    "model_size_estimate": estimate.size,
                    "model_estimated_proto_bytes": estimate.estimated_proto_bytes,
                    "model_soft_limit_exceedances": estimate.exceeded_limits,
                }
            )
            # 编译输出绑定最终内容身份。只填充模型外的只读缓存，不增加序列化字段；
            # 计算仍受本次请求截止时间约束，不占用 Greedy 的构造额度。
            _ = problem.problem_hash
            check_budget()
            return problem
        except TimeoutError as exc:
            return CompilationFailure(
                code="NO_FEASIBLE_PLAN", failure_class="NO_SOLUTION_WITHIN_BUDGET", message=str(exc)
            )
        except ValueError as exc:
            return CompilationFailure(
                code="STATE_INCOMPLETE"
                if runtime.device_states or runtime.executions
                else "DATA_NOT_READY",
                failure_class="STATE_INCOMPLETE"
                if runtime.device_states or runtime.executions
                else "PRECHECK_CONFLICT",
                message=str(exc),
            )
