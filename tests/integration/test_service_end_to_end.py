"""要求真实本机 Neo4j 停止；固定100菜主链、并发读取和重启联调。"""

import socket
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app
from tests.contract.test_competition_endpoint import STEAK


def assert_graph_offline():
    for port in (7474, 7687):
        try:
            connection = socket.create_connection(("127.0.0.1", port), timeout=0.2)
        except OSError:
            continue
        connection.close()
        raise AssertionError(f"验收要求 Neo4j 实际停止；本机 {port} 端口仍可连接")


def test_graph_offline_cold_start_real_solver_responsive_reads_and_restart(tmp_path):
    assert_graph_offline()
    settings = AppSettings(database_path=tmp_path / "offline-service.db")
    url = "/api/competition/plan?task_id=offline-meal"
    headers = {"Idempotency-Key": "initial"}
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 200
        assert len(client.get("/api/v1/recipes").json()["recipes"]) == 100
        initial = client.post(url, json=[STEAK], headers=headers)
        assert initial.status_code == 200, initial.text
        sid = client.get("/api/v1/competition-tasks/offline-meal").json()["session_id"]
        historical = client.get(f"/api/v1/sessions/{sid}/plans/1")
        graph = client.get(f"/api/v1/recipes/{STEAK['id']}/graph")
        assert graph.status_code == 200 and graph.json()["operations"]
        services = client.app.state.container
        with ThreadPoolExecutor(max_workers=1) as pool:
            added = pool.submit(
                client.post,
                url,
                json=[{"id": "6492a7a5933a4b7277dee0cf", "name": "酿苦瓜"}],
                headers={"Idempotency-Key": "additional"},
            )
            wait_until = time.monotonic() + 2
            while not services.worker._lock.locked() and time.monotonic() < wait_until:
                assert not added.done(), "未观察到真实工作进程作业"
                time.sleep(0.005)
            assert services.worker._lock.locked(), "未进入真实工作进程求解"
            for path in ("/health/live", "/health/ready", f"/api/v1/sessions/{sid}"):
                started = time.monotonic()
                observed = client.get(path)
                assert observed.status_code == 200, observed.text
                assert time.monotonic() - started < 0.5, "求解期间只读 HTTP 被阻塞"
            addition = added.result(timeout=10)
        assert addition.status_code == 200, addition.text
        assert addition.json()["overview"]["recipeCount"] == 2
        assert client.get(f"/api/v1/sessions/{sid}/plans/1").text == historical.text
        current = client.get(f"/api/v1/sessions/{sid}").json()
        assert current["runtime"]["executions"] == []
        notices = client.get(f"/api/v1/sessions/{sid}/notifications").json()
        assert len({(n["notification_id"], n["event_id"]) for n in notices["messages"]}) == len(
            notices["messages"]
        )
        assert (
            client.get(f"/api/v1/sessions/{sid}/notifications?after={notices['cursor']}").json()[
                "messages"
            ]
            == []
        )
    assert not services.worker.is_alive
    assert_graph_offline()
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.post(url, json=[STEAK], headers=headers).text == initial.text
        restored = client.get(f"/api/v1/sessions/{sid}").json()
        assert restored["runtime"] == current["runtime"]
        assert client.get(f"/api/v1/sessions/{sid}/plans/1").text == historical.text
        assert client.get(f"/api/v1/recipes/{STEAK['id']}/graph").json() == graph.json()
