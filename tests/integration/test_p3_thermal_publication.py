"""真实Neo4j发布的文件快照：四种开关不改来源，Greedy/CP-SAT结果独立校验。"""

import json

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.compatibility import GroupRuleSpec
from app.domain.policy import SchedulingPolicy
from app.domain.scheduling_problem import SchedulingProblem
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.metrics import compute_metrics
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import ROOT, menu_for, runtime
from tests.unit.test_shared_prep_coverage import deadline


@pytest.fixture(scope="module")
def published_thermal():
    output = ROOT / "data/preparations/p3-thermal-v1"
    report = json.loads((output / "publication_report.json").read_text(encoding="utf-8"))
    assert report["offline_reload_valid"] and report["p2_active_release_unchanged"]
    assert not report["formal_human_review_complete"]
    root = output / "releases"
    ref = read_release_ref(root, "development-v3-p3-thermal-v1-all")
    repository = SnapshotKnowledgeRepository(root)
    repository.load(ref)
    with repository.acquire(ref) as lease:
        ids = tuple(r.recipe_id for r in lease.loaded.snapshot.knowledge.recipes)
        k = lease.select(ids)
    ids = {"5c8200d96dc6e123a037a202", "5fe197175f8f38795ea6fe77"}
    menu = menu_for(*(r for r in k.recipes if r.recipe_id.root in ids))
    assert len(menu) == 2
    assert k.release.rule_version == "development-shared-v2"
    rule = next(r for r in k.rules if r.kind == "STRICT_TOGETHER")
    assert (
        GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate).thermal_model
        is not None
    )
    return k, menu, runtime(k)


@pytest.mark.parametrize(
    "shared,thermal", [(False, False), (True, False), (False, True), (True, True)]
)
def test_real_published_rules_compile_and_solve_with_independent_validation(
    published_thermal, shared, thermal
):
    k, menu, state = published_thermal
    problem = ProblemCompiler().compile(
        k,
        menu,
        state,
        SchedulingPolicy(
            policy_version="p3-published-four-switches",
            shared_prep=shared,
            strict_together_batch=thermal,
            allow_delegated_shared_estimates=True,
        ),
        deadline(),
    )
    assert isinstance(problem, SchedulingProblem), problem
    assert len(problem.shared_prep_candidates) == int(shared)
    assert len(problem.thermal_batch_candidates) == int(thermal)
    greedy = GreedyScheduler().solve(problem, deadline())
    assert greedy.candidate is not None, greedy
    solver = CpSatScheduler().solve(problem, greedy.candidate, deadline())
    assert solver.candidate is not None, solver
    for candidate in (greedy.candidate, solver.candidate):
        metrics = compute_metrics(candidate, problem)
        candidate = candidate.model_copy(update={"metrics": metrics})
        report = ScheduleValidator().validate(k, state, problem, candidate)
        assert report.valid, report.violations
    if thermal:
        selected = {a.carrier_id for a in solver.candidate.assignments}
        assert any(c.carrier_id in selected for c in problem.thermal_batch_candidates)
