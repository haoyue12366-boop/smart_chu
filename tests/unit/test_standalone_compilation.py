"""真实发布菜单实例化；合成状态只用于验证事实身份处理。"""

import pytest

from app.compiler.candidate_generation import standalone_candidates
from app.compiler.instantiate import instantiate
from app.domain.reports import CompilationFailure
from app.domain.runtime_snapshot import ExecutionRecord
from tests.compiler_support import menu_for, published_knowledge, runtime


def test_duplicate_name_and_repeat_recipe_instances_keep_distinct_tasks():
    knowledge = published_knowledge()
    prawns = [r for r in knowledge.recipes if r.name == "麻辣对虾"]
    menu = menu_for(prawns[0], prawns[1], prawns[0])
    result = instantiate(menu, knowledge, runtime())
    assert not isinstance(result, CompilationFailure)
    assert len(result.tasks) == sum(len(r.operations) for r in (prawns[0], prawns[1], prawns[0]))
    assert len({t.task_id for t in result.tasks}) == len(result.tasks)
    candidates = standalone_candidates(result, knowledge)
    assert not isinstance(candidates, CompilationFailure)
    assert all(len(c.covers) == 1 and c.kind == "STANDALONE" for c in candidates)
    assert {c.covers[0] for c in candidates} == {t.task_id for t in result.tasks}
    for task in result.tasks:
        assert all(
            c.provenance_refs == task.operation.provenance_refs
            for c in candidates
            if task.task_id in c.covers
        )
    assert instantiate(menu, knowledge, runtime()) == result
    assert standalone_candidates(result, knowledge) == candidates


def test_completed_and_running_facts_are_separate_from_remaining_candidates():
    knowledge = published_knowledge()
    menu = menu_for(knowledge.recipes[0])
    state = runtime()
    initial = instantiate(menu, knowledge, state)
    completed = initial.tasks[0].task_id
    running = initial.tasks[1].task_id
    state = state.model_copy(
        update={
            "now_offset_sec": 300,
            "executions": (
                ExecutionRecord(
                    execution_id="done",
                    task_ids=(completed,),
                    status="COMPLETED",
                    source="SIMULATED",
                    event_refs=("synthetic-done",),
                    started_at=state.time_origin.at(0),
                    finished_at=state.time_origin.at(100),
                ),
                ExecutionRecord(
                    execution_id="running",
                    task_ids=(running,),
                    status="RUNNING",
                    source="SIMULATED",
                    event_refs=("synthetic-start",),
                    started_at=state.time_origin.at(200),
                ),
            ),
        }
    )
    result = instantiate(menu, knowledge, state)
    assert result.completed_task_ids == (completed,)
    assert result.running_task_ids == (running,)
    assert [t.task_id for t in result.tasks] == [t.task_id for t in initial.tasks]
    candidates = standalone_candidates(result, knowledge)
    assert all(completed not in c.covers and running not in c.covers for c in candidates)
    assert result.tasks[0].operation == initial.tasks[0].operation


@pytest.mark.parametrize("case", ["duplicate_instance", "name", "version", "unknown_recipe"])
def test_bad_menu_identity_or_knowledge_version_is_structured_failure(case):
    knowledge = published_knowledge()
    menu = menu_for(knowledge.recipes[0])
    state = runtime()
    if case == "duplicate_instance":
        menu = (*menu, *menu)
    elif case == "name":
        menu = (menu[0].model_copy(update={"name": "wrong"}),)
    elif case == "version":
        state = state.model_copy(update={"knowledge_version": "wrong"})
    else:
        menu = (type(menu[0])(recipe_instance_id="x", recipe_id="unknown", name="unknown"),)
    result = instantiate(menu, knowledge, state)
    assert isinstance(result, CompilationFailure)
