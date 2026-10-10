"""Render 宽预算走真实知识、工作进程、独立校验及 SQLite 发布。"""

import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.domain.policy import SchedulingPolicy
from app.domain.reports import SolverBuildReport
from app.main import create_app
from app.runtime.clock import SimulationClock
from app.storage.repositories import RuntimeRepository
from app.validation.schedule import ScheduleValidator
from tests.runtime_support import ORIGIN


@pytest.fixture
def cloud_client(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("SMART_COOKING_PLANNING_PROFILE", raising=False)
    monkeypatch.setenv("SMART_COOKING_DATABASE_PATH", str(tmp_path / "render.sqlite3"))
    app = create_app(AppSettings.from_environment())
    app.state.container.clock = SimulationClock(ORIGIN)
    with TestClient(app) as client:
        yield client


def menu(client, names):
    by_name = {r["name"]: r for r in client.get("/api/v1/recipes").json()["recipes"]}
    return [{"id": by_name[name]["recipe_id"], "name": name} for name in names]


def test_resource_diagnostics_are_read_only_and_exclude_other_environment(
    cloud_client, monkeypatch
):
    monkeypatch.setenv("RESOURCE_DIAGNOSTIC_SECRET", "do-not-expose")
    response = cloud_client.get("/health/resources")
    assert response.status_code == 200
    data = response.json()
    assert len(data["boot_id"]) == 32
    assert data["solver_ready"] is True
    assert data["retained_build_reports"] == 0
    assert data["cache_release_requests"] == 0
    assert data["solver_search_workers_max"] == 4
    assert data["solver_worker_strategy"] == "FT_ADAPTIVE"
    assert "do-not-expose" not in response.text
    assert cloud_client.get("/health/ready").status_code == 200


def check_publication(client, result, count):
    assert result["status"] == "PUBLISHED", result.get("planning")
    sid = result.get("session_id") or result["plan"]["session_id"]
    version = result["plan"]["plan_version"]
    services = client.app.state.container
    with services.store.engine.connect() as tx:
        repo = RuntimeRepository(tx)
        problem = repo.problem(sid, version)
        published = repo.plan(sid, version)
    assert len(problem.recipe_instances) == count
    assert published.validated.validation.valid
    proof = ScheduleValidator().validate(
        services.knowledge_for(sid), problem.runtime, problem, published.validated.candidate
    )
    assert proof.valid, proof.violations
    assert result["planning"]["serial_reference_validation"]["valid"]
    assert len(services.worker.build_reports) == 1
    assert services.worker.cache_release_requests > 0
    assert services.worker._wire_problem_hash is None
    archive = services.settings.database_path.parent / "solver-build-reports"
    for stage in result["planning"]["stage_results"]:
        reference = stage.get("build_report_ref")
        if reference is None:
            continue
        prefix = hashlib.sha256(reference.encode("utf-8")).hexdigest()[:16]
        files = tuple(archive.glob(prefix + "-*.json"))
        assert files, reference
        for path in files:
            report = SolverBuildReport.model_validate_json(path.read_bytes())
            assert report.problem_hash == problem.problem_hash
            assert report.solver_build_id == reference
            assert report.constraint_mappings
            expected_workers = (
                4
                if problem.runtime.details.planning_kind == "REPLAN"
                and count >= 4
                and stage["objective_stage"] in {"A_MAKESPAN", "B_SPREAD"}
                else 1
            )
            assert report.search_workers == stage["search_workers"] == expected_workers
    assert client.get("/health/ready").status_code == 200
    return sid


def test_workbench_views_keep_published_display_and_omit_solver_and_policy_payloads(cloud_client):
    client = cloud_client
    result = client.post(
        "/api/v1/sessions",
        json={
            "event_id": "workbench-view",
            "mode": "SIMULATED",
            "recipes": menu(client, ["美式薯条"]),
        },
    ).json()
    sid = check_publication(client, result, 1)
    full = client.get(f"/api/v1/sessions/{sid}/plans/1").json()
    light = client.get(f"/api/v1/sessions/{sid}/plans/1?view=workbench").json()
    assert "problem" not in light
    assert light["presentation"] == full["presentation"]
    assert light["plan"]["validated"]["candidate"] == {
        "metrics": full["plan"]["validated"]["candidate"]["metrics"]
    }
    assert light["plan"]["plan_version"] == full["plan"]["plan_version"]
    state = client.get(f"/api/v1/sessions/{sid}").json()
    visible = client.get(f"/api/v1/sessions/{sid}?view=workbench").json()
    assert "policy" not in visible and "bindings" not in visible and "ledger" not in visible
    assert visible["runtime"]["state_revision"] == state["runtime"]["state_revision"]
    assert visible["runtime"]["executions"] == state["runtime"]["executions"]
    assert visible["menu"] == state["menu"]
    assert visible["clock_progress"] == state["clock_progress"]


@pytest.mark.parametrize(
    ("names", "addition"),
    [
        (("轻松一锅蒸", "美式薯条", "蒜香烤茄子", "糯米烧麦"), "酿苦瓜"),
        (("苹果芒果派", "鸡仔饼", "酿苦瓜"), "栗子冰皮月饼"),
    ],
)
def test_real_cloud_initial_addition_and_replan_keep_valid_history(cloud_client, names, addition):
    client = cloud_client
    created = client.post(
        "/api/v1/sessions", json={"event_id": uuid4().hex, "recipes": menu(client, names)}
    )
    assert created.status_code == 200, created.text
    sid = check_publication(client, created.json(), len(names))
    first_plan = client.get(f"/api/v1/sessions/{sid}/plans/1").json()
    services = client.app.state.container
    services.clock.advance(60)
    services.advance_clock(sid)
    before = client.get(f"/api/v1/sessions/{sid}").json()
    added = client.post(
        f"/api/v1/sessions/{sid}/events",
        json={
            "event_id": uuid4().hex,
            "event_type": "ADD_RECIPE",
            "expected_state_revision": before["runtime"]["state_revision"],
            "base_plan_version": 1,
            "payload": {"recipes": menu(client, (addition,))},
        },
    )
    assert added.status_code == 200, added.text
    check_publication(client, added.json(), len(names) + 1)
    replanned = client.post(f"/api/v1/sessions/{sid}/replan", json={"reason": "云端回归重排"})
    assert replanned.status_code == 200, replanned.text
    check_publication(client, replanned.json(), len(names) + 1)
    after = client.get(f"/api/v1/sessions/{sid}").json()
    assert after["runtime"]["current_plan_version"] == 3
    assert after["schedule_clock"]["started_at"] == before["schedule_clock"]["started_at"]
    assert after["policy"]["policy_version"].endswith(":render-v2:workers-4:ft-kitchen-v2")
    assert after["policy"]["solver_worker_strategy"] == "FT_ADAPTIVE"
    assert after["policy"]["search_strategy"] == "FT_KITCHEN"
    assert after["policy"]["initial_budget"]["total_ms"] == 90_000
    assert not after["requires_replan"]
    by_id = {e["execution_id"]: e for e in after["runtime"]["executions"]}
    for old in before["runtime"]["executions"]:
        assert by_id[old["execution_id"]]["task_spans"] == old["task_spans"]
        assert by_id[old["execution_id"]]["started_at"] == old["started_at"]
    assert client.get(f"/api/v1/sessions/{sid}/plans/1").json() == first_plan


def test_cloud_restart_keeps_existing_session_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_COOKING_DATABASE_PATH", str(tmp_path / "restart.sqlite3"))
    monkeypatch.setenv("SMART_COOKING_PLANNING_PROFILE", "STANDARD")
    old = SchedulingPolicy(policy_version="synthetic-old-session")
    old_ft = old.model_copy(
        update={
            "policy_version": "synthetic:render-v2:ft-kitchen-v1",
            "search_strategy": "FT_KITCHEN",
            "max_solver_search_workers": 1,
        }
    )
    old_ft_body = old_ft.model_dump_json()
    with TestClient(create_app(AppSettings.from_environment())) as client:
        services = client.app.state.container
        services.runtime.create_session("old-session", "MANUAL_CONFIRM", ORIGIN, old)
        services.runtime.create_session("old-ft-session", "MANUAL_CONFIRM", ORIGIN, old_ft)
    monkeypatch.setenv("SMART_COOKING_PLANNING_PROFILE", "RENDER")
    with TestClient(create_app(AppSettings.from_environment())) as client:
        services = client.app.state.container
        assert services.policy.policy_version.endswith(":render-v2:workers-4:ft-kitchen-v2")
        assert services.for_session("old-session")[0].get("old-session").policy == old
        restored_ft = services.for_session("old-ft-session")[0].get("old-ft-session").policy
        assert restored_ft.model_dump_json() == old_ft_body
        assert restored_ft.max_solver_search_workers == 1
        assert restored_ft.solver_worker_strategy == "FIXED"


def test_idle_recovery_rewarms_real_solver_after_exit(cloud_client):
    services = cloud_client.app.state.container
    services.worker.close()
    assert cloud_client.get("/health/ready").status_code == 503
    for _ in range(20):
        services.recover_pending()
        if services.worker.is_ready:
            break
    assert cloud_client.get("/health/ready").status_code == 200
    assert services.worker.is_ready


@pytest.mark.parametrize("count", [8, 10])
def test_large_cloud_menu_publishes_complete_validated_plan(cloud_client, count):
    names = (
        "轻松一锅蒸",
        "美式薯条",
        "蒜香烤茄子",
        "糯米烧麦",
        "苹果芒果派",
        "鸡仔饼",
        "酿苦瓜",
        "栗子冰皮月饼",
        "低温牛排",
        "手工鸡蛋豆腐",
    )[:count]
    response = cloud_client.post(
        "/api/v1/sessions", json={"event_id": uuid4().hex, "recipes": menu(cloud_client, names)}
    )
    assert response.status_code == 200, response.text
    check_publication(cloud_client, response.json(), count)
