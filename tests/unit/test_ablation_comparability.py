"""对照拒绝隐藏因素变化；关闭边界收紧仍保留全部依赖和强制程序。"""

import copy
import json
import time

import pytest

from app.compiler.bounds import derive_bounds
from app.compiler.candidate_limits import retain_optional
from app.compiler.dependency_graph import build_dependency_graph
from app.domain.base import content_hash
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.pruning import PruningContext
from app.domain.scheduling_problem import CandidateCarrier
from benchmarks.ablation import CONFIG, group_policy, validate_comparisons
from tests.compiler_support import runtime
from tests.runtime_support import policy
from tests.unit.test_dependency_bounds import example


def test_declared_comparisons_change_only_named_factors():
    config = json.loads(CONFIG.read_bytes())
    assert validate_comparisons(config, policy())
    for group in config["groups"]:
        derived = group_policy(policy(), group)
        assert derived.initial_budget == policy().initial_budget
        assert derived.replan_budget == policy().replan_budget
        assert derived.human_count == 1
        assert derived.preserve_mandatory_recipe_batches
        assert not derived.enable_certified_non_equivalent_pruning


@pytest.mark.parametrize("field", ["initial_budget", "time_grid_sec", "burner_ids"])
def test_groups_cannot_hide_budget_grid_or_resource_changes(field):
    group = dict(json.loads(CONFIG.read_bytes())["groups"][0])
    group[field] = "changed"
    with pytest.raises(ValueError, match="未声明"):
        group_policy(policy(), group)


def test_comparison_rejects_multiple_undeclared_feature_changes():
    config = copy.deepcopy(json.loads(CONFIG.read_bytes()))
    group = next(g for g in config["groups"] if g["id"] == "D_HINT")
    group["shared_prep"] = True
    with pytest.raises(ValueError, match="其他因素"):
        validate_comparisons(config, policy())


def test_historical_nominal_policy_identity_is_preserved():
    original = policy()
    payload = original.model_dump(mode="json")
    assert "graph_bound_preprocessing" not in payload
    assert "equivalence_deduplication" not in payload
    assert (
        content_hash(original) == "f6b7090edef4bbfd1a56e9398ad7999fa7ed9a216fa35ed5a49279e652ca5629"
    )
    assert content_hash(original) != content_hash(
        original.model_copy(update={"graph_bound_preprocessing": False})
    )


def test_looser_graph_domains_include_independent_chain_witness():
    tasks, candidates, edges = example(60)
    graph = build_dependency_graph(tasks, edges)
    state = runtime()
    base = SchedulingPolicy(policy_version="synthetic-ablation")
    tight = derive_bounds(graph, candidates, state, base)
    loose = derive_bounds(
        graph, candidates, state, base.model_copy(update={"graph_bound_preprocessing": False})
    )
    assert graph.dependencies == edges
    assert tight.horizon_sec == loose.horizon_sec
    assert tight.tasks[1].earliest_start_sec == 60
    assert loose.tasks[1].earliest_start_sec == 0
    # 独立参考：a=[0,60), b=[60,120)；两个域都包含见证。
    for bounds in (tight, loose):
        assert bounds.tasks[0].earliest_start_sec <= 0
        assert bounds.tasks[1].earliest_start_sec <= 60
        assert all(item.latest_end_sec >= 120 for item in bounds.tasks)


def test_equivalence_toggle_keeps_full_candidate_semantics_and_marks_truncation():
    first = CandidateCarrier(
        carrier_id="same-a", kind="SHARED_PREP", covers=("a", "b"), duration_sec=60
    )
    duplicate = first.model_copy(update={"carrier_id": "same-b"})
    context = PruningContext(
        knowledge_version="test",
        rule_version="test",
        policy_version="test",
        state_dependency_hash="0" * 64,
    )
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 1_000_000_000)
    base = SchedulingPolicy(policy_version="test")
    on = retain_optional((first, duplicate), base, deadline, context)
    off = retain_optional(
        (first, duplicate),
        base.model_copy(update={"equivalence_deduplication": False}),
        deadline,
        context,
    )
    assert len(on.candidates) == 1 and len(off.candidates) == 2
    assert on.records[0].kind == "EQUIVALENT"
    assert not off.records
    capped = retain_optional(
        (first, duplicate),
        base.model_copy(
            update={"equivalence_deduplication": False, "max_nonstandalone_per_problem": 1}
        ),
        deadline,
        context,
    )
    assert capped.records[0].kind == "BUDGET_TRUNCATION"
    assert not capped.enumeration_complete
    assert "PER_PROBLEM_LIMIT" in capped.truncation_reasons
