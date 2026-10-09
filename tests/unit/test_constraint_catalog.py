"""约束身份覆盖真实工艺、域、设备和物料，规模阈值不能删必需路径。"""

from app.compiler.constraint_catalog import build_catalog
from app.compiler.model_stats import estimate_model_size
from app.compiler.pruning import deduplicate
from app.domain.ids import CarrierId
from app.domain.pruning import PruningContext
from app.domain.scheduling_problem import CandidateCarrier
from tests.compiler_support import published_knowledge
from tests.unit.test_problem_compilation import compile_menu


def test_same_coverage_shorter_candidate_does_not_dominate_fixed_timing():
    long = CandidateCarrier(
        carrier_id="ten-minutes", kind="STANDALONE", covers=("task",), duration_sec=600
    )
    short = long.model_copy(update={"carrier_id": CarrierId("five-minutes"), "duration_sec": 300})
    duplicate = long.model_copy(update={"carrier_id": CarrierId("same-ten-minutes")})
    result = deduplicate((long, short, duplicate))
    assert {c.duration_sec for c in result.candidates} == {300, 600}
    assert len(result.candidates) == 2
    assert len(result.records) == 1
    assert result.records[0].kind == "EQUIVALENT"
    assert not result.may_lose_optimum


def test_protected_candidate_and_different_evidence_survive():
    candidate = CandidateCarrier(
        carrier_id="a", kind="STANDALONE", covers=("task",), duration_sec=15
    )
    protected = candidate.model_copy(update={"carrier_id": CarrierId("z")})
    different = candidate.model_copy(
        update={"carrier_id": CarrierId("b"), "provenance_refs": ("other",)}
    )
    context = PruningContext(
        knowledge_version="k",
        rule_version="r",
        policy_version="p",
        state_dependency_hash="state",
        protected_carrier_ids=("z",),
    )
    result = deduplicate((candidate, protected, different), context)
    assert len(result.candidates) == 3
    assert result.records == ()


def test_compiler_embeds_catalog_estimate_and_context_bound_pruning_report():
    problem = compile_menu(published_knowledge().recipes[0])
    assert problem.constraint_catalog == build_catalog(problem)
    assert problem.model_size_estimate == estimate_model_size(problem).size
    assert problem.model_estimated_proto_bytes > 0
    assert not problem.candidate_generation_report.candidate_truncated


def test_real_catalog_has_every_domain_and_is_stably_identified():
    recipe = next(r for r in published_knowledge().recipes if r.name == "亲朋欢聚套餐")
    problem = compile_menu(recipe)
    records = build_catalog(problem)
    categories = {r.category for r in records}
    assert {
        "COVERAGE",
        "DURATION",
        "TIME_DOMAIN",
        "PRECEDENCE",
        "MATERIAL",
        "RESOURCE",
        "RESERVATION",
        "FIXED_PROGRAM",
    } <= categories
    assert len(records) == len({r.constraint_id for r in records})
    assert all(r.expression_summary for r in records)
    for task in problem.logical_tasks:
        assert any(r.category == "COVERAGE" and task.task_id in r.task_ids for r in records)
    assert build_catalog(problem) == records
    assert (
        problem.problem_hash
        == type(problem).model_validate_json(problem.model_dump_json()).problem_hash
    )


def test_soft_scale_limits_report_without_dropping_candidates():
    knowledge = published_knowledge()
    problem = compile_menu(knowledge.recipes[0], knowledge.recipes[1])
    from app.domain.policy import ModelSize

    tiny = problem.model_copy(
        update={
            "policy": problem.policy.model_copy(
                update={
                    "model_soft_limits": ModelSize(
                        total_variables=1,
                        boolean_variables=1,
                        optional_intervals=1,
                        total_constraints=1,
                        sequence_arcs=1,
                    )
                }
            )
        }
    )
    before = tiny.problem_hash
    estimate = estimate_model_size(tiny)
    assert estimate.exceeded_limits
    assert estimate.estimated_proto_bytes > 0
    assert estimate.size.total_variables > 1
    assert not estimate.candidate_truncated
    assert before == tiny.problem_hash
