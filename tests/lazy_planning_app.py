"""独立进程中的真实知识/API/JSON Solver/SQLite首次及重复排程证据。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from app.config import AppSettings
    from app.main import create_app

    assert "app.compiler.compiler" not in sys.modules
    assert "app.scheduling.engine" not in sys.modules
    assert "ortools.sat.python.cp_model" not in sys.modules
    from fastapi.testclient import TestClient

    settings = AppSettings(
        planning_profile="RENDER",
        database_path=Path(sys.argv[1]),
        release_root=Path(sys.argv[2]),
    )
    app = create_app(settings)
    services = app.state.container
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        assert "app.compiler.compiler" not in sys.modules
        assert "app.scheduling.engine" not in sys.modules
        pid = services.worker.process_id
        recipes = client.get("/api/v1/recipes").json()["recipes"]
        recipe = next(item for item in recipes if item["name"] == "美式薯条")
        first = client.post(
            "/api/v1/sessions",
            json={
                "event_id": "lazy-module-real-first",
                "mode": "SIMULATED",
                "recipes": [{"id": recipe["recipe_id"], "name": recipe["name"]}],
            },
        ).json()
        assert first["status"] == "PUBLISHED", first.get("planning")
        sid = first["session_id"]
        modules = {
            name: sys.modules[name]
            for name in (
                "app.compiler.compiler",
                "app.scheduling.engine",
                "app.services.replanning",
            )
        }
        history = client.get(f"/api/v1/sessions/{sid}/plans/1").json()
        second = client.post(
            f"/api/v1/sessions/{sid}/replan", json={"reason": "真实重复模块加载验证"}
        ).json()
        assert second["status"] == "PUBLISHED", second.get("planning")
        assert all(sys.modules[name] is module for name, module in modules.items())
        assert "ortools.sat.python.cp_model" not in sys.modules
        assert "matplotlib" not in sys.modules
        assert services.worker.process_id == pid and services.worker.restart_count == 0
        assert client.get(f"/api/v1/sessions/{sid}/plans/1").json() == history
        from app.storage.repositories import RuntimeRepository
        from app.validation.schedule import ScheduleValidator

        for version in (1, 2):
            with services.store.engine.connect() as tx:
                repo = RuntimeRepository(tx)
                problem, plan = repo.problem(sid, version), repo.plan(sid, version)
            for candidate in (plan.validated.candidate, plan.serial_reference.candidate):
                proof = ScheduleValidator().validate(
                    services.knowledge_for(sid), problem.runtime, problem, candidate
                )
                assert proof.valid, proof.violations
        print(
            json.dumps(
                {
                    "first_and_repeat_published": True,
                    "warm_pid_reused": True,
                    "modules_reused": True,
                    "fresh_independent_valid": True,
                    "native_solver_absent_in_api": True,
                    "old_history_preserved": True,
                }
            )
        )
    assert not services.worker.is_alive


if __name__ == "__main__":
    main()
