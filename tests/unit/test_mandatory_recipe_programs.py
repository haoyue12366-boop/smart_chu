"""强制程序基于真实开发知识，不能随跨菜共享开关删除。"""

from app.compiler.instantiate import instantiate
from app.compiler.phase_expansion import expand_required_programs
from app.domain.reports import CompilationFailure
from tests.compiler_support import menu_for, published_knowledge, runtime


def test_two_shaomai_batches_are_distinct_and_preserve_each_heat_duration():
    knowledge = published_knowledge()
    recipe = next(r for r in knowledge.recipes if r.name == "糯米烧麦")
    instantiated = instantiate(menu_for(recipe), knowledge, runtime())
    expanded = expand_required_programs(instantiated, knowledge)
    assert not isinstance(expanded, CompilationFailure)
    assert len(expanded.fixed_batches) == 2
    assert len({b.batch_id for b in expanded.fixed_batches}) == 2
    assert all(len(b.members) == 1 for b in expanded.fixed_batches)
    tasks = {t.task_id: t for t in instantiated.tasks}
    assert [
        tasks[b.members[0]].operation.duration.execution_sec for b in expanded.fixed_batches
    ] == [600, 600]


def test_family_program_preserves_preheat_paused_timer_and_human_insertion():
    knowledge = published_knowledge()
    recipe = next(r for r in knowledge.recipes if r.name == "亲朋欢聚套餐")
    instantiated = instantiate(menu_for(recipe), knowledge, runtime())
    expanded = expand_required_programs(instantiated, knowledge)
    assert not isinstance(expanded, CompilationFailure)
    program = expanded.programs[0]
    assert program.active_process_sec == 2700
    assert program.before_intervention_sec == 1800
    assert program.intervention_sec == 120
    assert program.remaining_sec == 900
    tasks = {t.task_id: t for t in instantiated.tasks}
    assert len(program.preparation_members) == 1
    assert tasks[program.preparation_members[0]].operation.action == "PREHEAT"
    assert len(program.intervention_members) == 1
    assert any(
        u.resource_id == "human_1"
        for u in tasks[program.intervention_members[0]].operation.resource_requirements
    )
    assert program.timer_paused
    reservation = next(r for r in expanded.reservations if program.before_members[0] in r.members)
    assert set(
        (*program.before_members, *program.intervention_members, *program.after_members)
    ) <= set(reservation.members)
    assert len(expanded.time_relations) >= 2


def test_fixed_program_with_missing_human_or_wrong_active_duration_is_rejected():
    knowledge = published_knowledge()
    recipe = next(r for r in knowledge.recipes if r.name == "烹香酷炒汇")
    instantiated = instantiate(menu_for(recipe), knowledge, runtime())
    program = next(
        c for c in knowledge.recipe_contexts if c.recipe_id == recipe.recipe_id
    ).program_constraints[0]
    target = program.intervention_group[0]
    tasks = tuple(
        t.model_copy(
            update={"operation": t.operation.model_copy(update={"resource_requirements": ()})}
        )
        if t.operation_id == target
        else t
        for t in instantiated.tasks
    )
    assert isinstance(
        expand_required_programs(instantiated.model_copy(update={"tasks": tasks}), knowledge),
        CompilationFailure,
    )
    contexts = tuple(
        c.model_copy(
            update={"program_constraints": (program.model_copy(update={"active_process_sec": 99}),)}
        )
        if c.recipe_id == recipe.recipe_id
        else c
        for c in knowledge.recipe_contexts
    )
    assert isinstance(
        expand_required_programs(
            instantiated, knowledge.model_copy(update={"recipe_contexts": contexts})
        ),
        CompilationFailure,
    )
