"""合成建模报告的完整追溯、不可变归档和原子写入故障。"""

import hashlib

import pytest

from app.domain.objectives import ObjectiveStage
from app.domain.policy import ModelSize
from app.domain.reports import SolverBuildReport, SolverIndexMapping, SolveStatus
from app.storage.solver_reports import SolverReportArchive


def report():
    return SolverBuildReport(
        problem_hash="1" * 64,
        solver_build_id="synthetic-build/../../outside",
        objective_stage="B_SPREAD",
        solver_version="synthetic",
        actual_model_size=ModelSize(total_variables=3, total_constraints=2),
        constraint_mappings=(
            SolverIndexMapping(
                constraint_id="synthetic-capacity",
                variable_ids=("amount",),
                proto_constraint_indices=(0, 1),
            ),
        ),
        stage_parameters=ObjectiveStage(name="B_SPREAD", makespan_cap_sec=600),
        solve_status="OPTIMAL",
    )


def test_archive_keeps_full_bindings_and_does_not_overwrite_different_status(tmp_path):
    directory = tmp_path / "archive"
    archive = SolverReportArchive(directory)
    original = report()
    archive(original)
    archive(original)
    files = tuple(directory.glob("*.json"))
    assert len(files) == 1
    body = files[0].read_bytes()
    assert files[0].resolve().parent == directory.resolve()
    assert files[0].stem.endswith(hashlib.sha256(body).hexdigest())
    assert SolverBuildReport.model_validate_json(body) == original
    changed = original.model_copy(update={"solve_status": SolveStatus.FEASIBLE})
    archive(changed)
    assert len(tuple(directory.glob("*.json"))) == 2
    assert files[0].read_bytes() == body
    restored = [
        SolverBuildReport.model_validate_json(f.read_bytes()) for f in directory.glob("*.json")
    ]
    assert {r.solve_status for r in restored} == {"OPTIMAL", "FEASIBLE"}
    assert all(r.constraint_mappings == original.constraint_mappings for r in restored)


def test_failed_atomic_replace_leaves_existing_reports_and_no_partial_file(tmp_path, monkeypatch):
    archive = SolverReportArchive(tmp_path)
    archive(report())
    existing = {f.name: f.read_bytes() for f in tmp_path.iterdir()}

    def fail(source, destination):
        raise OSError("synthetic rename failure")

    monkeypatch.setattr("app.storage.solver_reports.os.replace", fail)
    with pytest.raises(OSError, match="rename failure"):
        archive(report().model_copy(update={"build_time_ms": 10}))
    assert {f.name: f.read_bytes() for f in tmp_path.iterdir()} == existing
