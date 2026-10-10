"""真实发布菜单的候选上下文：菜谱按菜单取用，规则及审核门保留。"""

import time

import pytest

import app.compiler.compiler as compiler_module
from app.compiler.candidate_generation import standalone_candidates
from app.compiler.instantiate import instantiate
from app.compiler.material_flow import bind_candidate_materials
from app.compiler.shared_prep import generate_shared_prep
from app.domain.base import content_hash
from app.domain.candidates import InstantiationResult, SharedCandidateContext
from app.domain.canonical_recipe import ReviewStamp
from app.domain.compatibility import GroupContext
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.reports import CompilationFailure
from app.domain.scheduling_problem import SchedulingProblem
from tests.compiler_support import menu_for, runtime
from tests.shared_support import shared_menu


def deadline():
    return Deadline(expires_at_ns=time.monotonic_ns() + 20_000_000_000)


def policy():
    return SchedulingPolicy(
        policy_version="menu-context-test",
        shared_prep=True,
        allow_delegated_shared_estimates=True,
    )


@pytest.mark.parametrize("duplicate_instance", [False, True])
def test_candidate_context_contains_menu_recipes_and_all_shared_facts(
    monkeypatch, duplicate_instance
):
    knowledge, menu, state = shared_menu()
    selected = tuple(r for r in knowledge.recipes if r.recipe_id in {i.recipe_id for i in menu})
    if duplicate_instance:
        menu = menu_for(*selected, selected[0])
    original_hash = content_hash(knowledge)
    captured = []
    original_generate = compiler_module.iter_shared_prep

    def observe(context):
        captured.append(context)
        yield from original_generate(context)

    monkeypatch.setattr(compiler_module, "iter_shared_prep", observe)
    compiled = compiler_module.ProblemCompiler().compile(
        knowledge, menu, state, policy(), deadline()
    )
    assert isinstance(compiled, SchedulingProblem)
    assert len(captured) == 1
    view = captured[0].group.knowledge
    assert tuple(r.recipe_id for r in view.recipes) == tuple(r.recipe_id for r in selected)
    assert view.release == knowledge.release
    assert view.snapshot_hash == knowledge.snapshot_hash
    assert view.snapshot_schema_version == knowledge.snapshot_schema_version
    for field in ("devices", "profiles", "rules", "device_choices", "provenance_index"):
        assert getattr(view, field) == getattr(knowledge, field)
    assert all(c.recipe_id in {i.recipe_id for i in menu} for c in view.recipe_contexts)
    assert len(knowledge.recipes) == 100
    assert content_hash(knowledge) == original_hash
    assert compiled.shared_prep_candidates


def test_complete_shared_candidate_set_matches_full_source_context():
    knowledge, menu, state = shared_menu()
    instantiated = instantiate(menu, knowledge, state)
    assert isinstance(instantiated, InstantiationResult)
    standalone = bind_candidate_materials(
        standalone_candidates(instantiated, knowledge), instantiated
    )
    expected = generate_shared_prep(
        SharedCandidateContext(
            instantiated=instantiated,
            group=GroupContext(
                knowledge=knowledge, runtime=state, menu=menu, allow_delegated_estimates=True
            ),
            standalone=standalone,
            deadline=deadline(),
        )
    )
    compiled = compiler_module.ProblemCompiler().compile(
        knowledge, menu, state, policy(), deadline()
    )
    assert isinstance(compiled, SchedulingProblem)
    assert expected
    assert set(compiled.shared_prep_candidates) == set(expected)


@pytest.mark.parametrize("invalid_kind", ["duplicate-operation", "unapproved-review"])
def test_selected_recipe_still_requires_valid_structure_and_review(invalid_kind):
    knowledge, menu, state = shared_menu()
    selected = {i.recipe_id for i in menu}
    recipe = next(r for r in knowledge.recipes if r.recipe_id in selected)
    # 显式破坏只读输入来检验防御；不写回真实发布包。
    if invalid_kind == "duplicate-operation":
        broken = recipe.model_copy(
            update={"operations": (*recipe.operations, recipe.operations[0])}
        )
        expected = "ID 重复"
    else:
        assert recipe.approval is None
        stamp = ReviewStamp(
            review_id="synthetic-invalid-review",
            reviewer="synthetic-tester",
            reviewed_at=state.time_origin.start_at,
            evidence_refs=("synthetic-negative-case",),
            approved_content_hash="0" * 64,
        )
        broken = recipe.model_copy(update={"approval": stamp})
        expected = "未批准版本"
    changed = knowledge.model_copy(
        update={
            "recipes": tuple(
                broken if r.recipe_id == recipe.recipe_id else r for r in knowledge.recipes
            )
        }
    )
    result = compiler_module.ProblemCompiler().compile(changed, menu, state, policy(), deadline())
    assert isinstance(result, CompilationFailure)
    assert expected in result.message


def test_menu_projection_does_not_accept_unknown_recipes():
    knowledge, menu, state = shared_menu()
    changed = (menu[0].model_copy(update={"recipe_id": "unknown-recipe"}),)
    result = compiler_module.ProblemCompiler().compile(
        knowledge, changed, state, policy(), deadline()
    )
    assert isinstance(result, CompilationFailure)
    assert result.code == "UNKNOWN_RECIPE"


def test_menu_projection_does_not_mix_knowledge_versions():
    knowledge, menu, state = shared_menu()
    changed = runtime(knowledge, snapshot_id="other-snapshot")
    result = compiler_module.ProblemCompiler().compile(
        knowledge, menu, changed, policy(), deadline()
    )
    assert isinstance(result, CompilationFailure)
    assert result.code == "STATE_CONFLICT"
