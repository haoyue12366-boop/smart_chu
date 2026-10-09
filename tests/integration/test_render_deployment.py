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
    assert client.get("/health/ready").status_code == 200
    return sid


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
    assert after["policy"]["policy_version"].endswith(":render-v2")
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
    with TestClient(create_app(AppSettings.from_environment())) as client:
        services = client.app.state.container
        services.runtime.create_session("old-session", "MANUAL_CONFIRM", ORIGIN, old)
    monkeypatch.setenv("SMART_COOKING_PLANNING_PROFILE", "RENDER")
    with TestClient(create_app(AppSettings.from_environment())) as client:
        services = client.app.state.container
        assert services.policy.policy_version.endswith(":render-v2")
        assert services.for_session("old-session")[0].get("old-session").policy == old


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
