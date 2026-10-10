"""复用只读对象仍观察真实 SQLite 状态、回滚、删除和损坏内容。"""

import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, event, update

from app.domain.policy import SchedulingPolicy
from app.domain.runtime_session import RuntimeSession
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.storage import models
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from tests.integration.test_schedule_clock import clock_knowledge
from tests.runtime_support import ORIGIN


@pytest.fixture
def store(tmp_path):
    value = UnitOfWork(tmp_path / "decode.sqlite3")
    value.migrate()
    yield value
    value.close()


def create(store, sid="same-id"):
    runtime = RuntimeService(store, clock_knowledge(), SimulationClock(ORIGIN))
    return runtime.create_session(
        sid, "MANUAL_CONFIRM", ORIGIN, SchedulingPolicy(policy_version="synthetic-decode")
    )


def read(store, sid="same-id"):
    with store.engine.connect() as tx:
        return RuntimeRepository(tx).get(sid)


def test_unchanged_body_reuses_validated_object_but_queries_database_every_time(store):
    original = create(store)
    statements = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT"):
            statements.append(statement)

    event.listen(store.engine, "before_cursor_execute", observe)
    try:
        first, second = read(store), read(store)
        assert first == second == original
        assert len(statements) == 2
        assert first is second, "相同数据库内容不应重复解析整个不可变会话"
    finally:
        event.remove(store.engine, "before_cursor_execute", observe)


def test_changed_body_with_same_revision_is_observed_and_invalid_body_is_rejected(store):
    original = create(store)
    prior = read(store)
    changed = original.model_copy(update={"dispatch_blocked": False})
    with store.transaction() as tx:
        tx.execute(update(models.sessions).values(body=changed.model_dump_json()))
    assert read(store) == changed and read(store) is not prior
    with store.transaction() as tx:
        tx.execute(update(models.sessions).values(body='{"unexpected":true}'))
    with pytest.raises(ValidationError):
        read(store)


def test_uncommitted_decode_does_not_leak_through_rollback_or_deletion(store):
    original = create(store)
    read(store)
    changed = original.model_copy(update={"status": "ENDED"})
    with pytest.raises(RuntimeError), store.transaction() as tx:
        tx.execute(update(models.sessions).values(body=changed.model_dump_json()))
        assert RuntimeRepository(tx).get("same-id") == changed
        raise RuntimeError("synthetic rollback")
    assert read(store) == original
    with store.transaction() as tx:
        tx.execute(delete(models.sessions))
    with pytest.raises(KeyError):
        read(store)


def test_same_identity_in_different_databases_and_new_model_copy_are_isolated(store, tmp_path):
    original = create(store)
    other = UnitOfWork(tmp_path / "other.sqlite3")
    other.migrate()
    try:
        create(other)
        changed = original.model_copy(update={"dispatch_blocked": False})
        with other.transaction() as tx:
            tx.execute(update(models.sessions).values(body=changed.model_dump_json()))
        assert read(other) == changed
        assert read(store) == original
        copy = read(store).model_copy(update={"status": "ENDED"})
        assert copy.status == "ENDED" and read(store).status == original.status
    finally:
        other.close()


def test_store_close_releases_the_last_cached_model(store):
    create(store)
    ref = weakref.ref(read(store))
    assert ref() is not None, "解析复用没有保留最近使用的对象"
    store.close()
    assert ref() is None


def test_cache_enforces_memory_limit_and_failed_decode_cannot_reuse_old_value(store):
    from app.storage.decoded_models import DecodedModels

    original = create(store)
    body = original.model_dump_json()
    cache = DecodedModels()
    ref = weakref.ref(cache.decode(RuntimeSession, body))
    assert ref() is not None
    with pytest.raises(ValidationError):
        cache.decode(RuntimeSession, "{}")
    assert cache.retained_body_bytes == 0
    assert ref() is None
    small = DecodedModels(max_body_bytes=len(body.encode("utf-8")) - 1)
    assert small.decode(RuntimeSession, body) is not small.decode(RuntimeSession, body)
    assert small.retained_body_bytes == 0


def test_body_limit_evicts_old_objects_and_parallel_reads_share_one_decode(store):
    from app.storage.decoded_models import DecodedModels

    original = create(store)
    body, policy = original.model_dump_json(), original.policy.model_dump_json()
    cache = DecodedModels(
        max_body_bytes=max(len(body.encode("utf-8")), len(policy.encode("utf-8")))
    )
    old = weakref.ref(cache.decode(RuntimeSession, body))
    assert old() is not None
    cache.decode(SchedulingPolicy, policy)
    assert old() is None
    assert cache.retained_body_bytes <= cache.max_body_bytes
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: cache.decode(RuntimeSession, body), range(8)))
    assert all(item is values[0] for item in values)
