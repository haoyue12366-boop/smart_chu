"""响应开始后的真实 SQLite 竞争不能截断提醒流或推进执行事实。"""

import json
import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest

from tests.fault_injection.stream_support import WindowsApiProcess
from tests.integration.test_notification_stream import STEAK


@pytest.mark.parametrize("heartbeat_count", [3, 7])
def test_started_stream_survives_write_lock_and_delivers_committed_message(
    tmp_path, heartbeat_count
):
    process = WindowsApiProcess(tmp_path)
    try:
        with closing(process.start()) as client:
            created = client.post(
                "/api/v1/sessions",
                json={
                    "event_id": str(uuid4()),
                    "mode": "SIMULATED",
                    "recipes": [STEAK],
                },
            )
            assert created.json()["status"] == "PUBLISHED", created.text
            sid = created.json()["session_id"]
            url = f"/api/v1/sessions/{sid}/notifications"
            before = client.get(f"/api/v1/sessions/{sid}").json()["runtime"]["executions"]
            cursor = client.get(url).json()["cursor"]
            with closing(
                sqlite3.connect(tmp_path / "runtime.sqlite3", isolation_level=None)
            ) as holder:
                holder.execute("BEGIN IMMEDIATE")
                unavailable = client.get(url + "/stream?once=true")
                assert unavailable.status_code == 503
                assert unavailable.json()["error"]["code"] == "SERVICE_NOT_READY"
                holder.execute("ROLLBACK")
            identity = str(uuid4())
            payload = {
                "notification_id": identity,
                "event_id": identity,
                "plan_version": 1,
                "kind": "TEST_LOCK_RELEASE",
                "text": "写锁释放后补发",
                "data": {},
            }
            with client.stream(
                "GET", url + "/stream", params={"after": cursor}, timeout=5
            ) as stream:
                assert stream.status_code == 200
                lines = stream.iter_lines()
                assert next(lines) == ": keepalive"
                with closing(
                    sqlite3.connect(tmp_path / "runtime.sqlite3", isolation_level=None)
                ) as holder:
                    holder.execute("BEGIN IMMEDIATE")
                    # 锁覆盖多个实际轮询周期；心跳证明同一连接仍存活。
                    for _ in range(heartbeat_count):
                        while next(lines) != ": keepalive":
                            pass
                    holder.execute(
                        "INSERT INTO notification_stream(session_id, deduplication_key, body) "
                        "VALUES (?, ?, ?)",
                        (sid, identity, json.dumps(payload, ensure_ascii=False)),
                    )
                    holder.execute("COMMIT")
                while True:
                    line = next(lines)
                    if line.startswith("data: "):
                        delivered = json.loads(line[6:])
                        if delivered["event_id"] == identity:
                            break
                assert delivered["cursor"] > cursor
                assert {k: v for k, v in delivered.items() if k != "cursor"} == payload
            batch = client.get(url, params={"after": cursor}).json()["messages"]
            assert sum(m["event_id"] == identity for m in batch) == 1
            replay = client.get(
                url + "/stream?once=true", headers={"Last-Event-ID": str(batch[-1]["cursor"])}
            )
            assert replay.status_code == 200 and "data:" not in replay.text
            assert client.get(f"/api/v1/sessions/{sid}").json()["runtime"]["executions"] == before
    finally:
        process.stop()
    log = (tmp_path / "api-1.log").read_text(encoding="utf-8")
    assert "Traceback" not in log and "response already started" not in log
