"""真实浏览器归档：未来人工备料应靠近后续预约，释放烤箱入炉空档。"""

import json
import time
from pathlib import Path

import pytest

from app.domain.carrier_timing import task_intervals
from app.domain.ports import Deadline
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.scheduling.heating_compaction import compact_heating_slack
from app.scheduling.ranking import candidate_rank
from app.validation.schedule import ScheduleValidator


@pytest.mark.parametrize(
    "archived_name", ("failed-browser-plan.json", "failed-browser-plan-2.json")
)
def test_compacts_archived_fries_without_changing_anchors_or_human_priority(archived_name):
    root = Path(__file__).resolve().parents[2]
    archived = root / "data/verification/2026-10-08-immediate-multilayer" / archived_name
    envelope = json.loads(archived.read_text(encoding="utf-8"))
    problem = SchedulingProblem.model_validate(envelope["problem"])
    candidate = PublishedPlan.model_validate(envelope["plan"]).validated.candidate
    releases = root / "data/preparations/p4-v1/releases"
    reference = read_release_ref(releases, "delegated-v3-multilayer-onepot-v1-all")
    repository = SnapshotKnowledgeRepository(releases)
    repository.load(reference)
    with repository.acquire(reference) as lease:
        knowledge = lease.select(tuple(item.recipe_id for item in problem.recipe_instances))
    validator = ScheduleValidator()
    assert validator.validate(knowledge, problem.runtime, problem, candidate).valid
    before = task_intervals(problem, candidate.assignments)
    original_problem = problem.model_dump_json()
    compacted = compact_heating_slack(
        candidate, problem, Deadline(expires_at_ns=time.monotonic_ns() + 200_000_000)
    )
    validation = validator.validate(knowledge, problem.runtime, problem, compacted)
    assert validation.valid, validation.violations
    after = task_intervals(problem, compacted.assignments)
    long_reservations = [
        item
        for item in problem.mandatory_programs.reservations
        if max(before[t].end_sec for t in item.members)
        - min(before[t].start_sec for t in item.members)
        > 3600
    ]
    assert len(long_reservations) == 1
    for reservation in long_reservations:
        assert (
            max(after[t].end_sec for t in reservation.members)
            - min(after[t].start_sec for t in reservation.members)
            <= 3600
        )
    for boundary in problem.cooking_completions:
        for task in boundary.task_ids:
            assert after[task] == before[task]
    assert candidate_rank(compacted, problem)[:-1] <= candidate_rank(candidate, problem)[:-1]
    assert problem.model_dump_json() == original_problem
    assert {task: span.end_sec - span.start_sec for task, span in after.items()} == {
        task: span.end_sec - span.start_sec for task, span in before.items()
    }
    assert {a.carrier_id: a.resource_uses for a in compacted.assignments} == {
        a.carrier_id: a.resource_uses for a in candidate.assignments
    }
