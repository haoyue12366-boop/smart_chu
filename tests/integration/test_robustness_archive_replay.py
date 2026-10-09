"""原始失败轨迹复现：真实菜谱前缀中仍有合法窗口，不能提前终止。"""

import gzip
import json

from app.domain.reports import PlanningResult
from app.domain.runtime_session import RuntimeSession
from app.domain.scheduling_problem import SchedulingProblem
from benchmarks.dataset import ROOT, load_inputs
from benchmarks.robustness.simulator import FutureDurations, TrajectorySimulator


def test_original_failed_prefix_can_start_successor_at_actual_zero_gap_boundary():
    directory = ROOT / "benchmarks/reports/P6-robustness-run2"
    initial = json.loads(
        gzip.decompress((directory / "initial-combination-000-NOMINAL.json.gz").read_bytes())
    )
    trajectory = json.loads(
        gzip.decompress(
            (directory / "trajectory-combination-000-NOMINAL_SHIFT-000.json.gz").read_bytes()
        )
    )
    config = json.loads((ROOT / "benchmarks/scenarios/duration_profiles.json").read_bytes())
    session = RuntimeSession.model_validate(initial["session"])
    problem = SchedulingProblem.model_validate(initial["problem"])
    result = PlanningResult.model_validate(initial["result"])
    base, _ = load_inputs()
    ids = {i.recipe_id for i in session.menu}
    knowledge = base.model_copy(
        update={
            "recipes": tuple(r for r in base.recipes if r.recipe_id in ids),
            "recipe_contexts": tuple(c for c in base.recipe_contexts if c.recipe_id in ids),
        }
    )
    sim = TrajectorySimulator(
        knowledge,
        session,
        problem,
        result.candidate,
        FutureDurations(config["seed"], "combination-000", 0, config),
        config,
    )
    assert trajectory["status"] == "FAILED"
    sim.session = RuntimeSession.model_validate(trajectory["final_session"])
    assert sim.session.runtime.now_offset_sec == 1706
    stage = next(
        s
        for s in sim.stages()
        if any(sim.operations[t][1].operation_id.root == "op_005_03" for t in s.group)
    )
    assert stage.at == 1706
    sim.observe("OPERATION_STARTED", stage, stage.at)
    record = next(
        e for e in sim.session.runtime.executions if e.execution_id.root == stage.execution_id
    )
    assert set(stage.group) <= set(record.started_task_ids)
