"""真实 HTTP/SQLite 请求内复用，但回执和错误回执之后不保活源模型。"""

import gc
import weakref
from contextvars import copy_context

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.config import AppSettings
from app.main import create_app
from app.storage.repositories import RuntimeRepository
from tests.integration.test_runtime_decode_cache import create, read
from tests.integration.test_runtime_decode_cache import store as store


@pytest.mark.parametrize("fail", [False, True])
def test_http_decodes_share_within_request_and_release_after_response(store, tmp_path, fail):
    original = create(store)
    app = create_app(AppSettings(frontend_path=tmp_path / "no-frontend"))
    app.state.container.store = store
    pointers = []

    @app.get("/synthetic-decode")
    def read(fail: bool = False):
        with store.engine.connect() as tx:
            repo = RuntimeRepository(tx)
            first, second = repo.get("same-id"), repo.get("same-id")
        assert first is second
        pointers.append(weakref.ref(first))
        if fail:
            raise ApiError("SYNTHETIC_CACHE_FAILURE", "合成反例", 409)
        return first.model_dump(mode="json")

    # 本合成用例只运行真实中间件/路由/SQLite；无应用生命周期或求解器替身。
    client = TestClient(app, raise_server_exceptions=True)
    try:
        reply = client.get("/synthetic-decode", params={"fail": str(fail).lower()})
        assert reply.status_code == (409 if fail else 200)
        if not fail:
            assert reply.json() == original.model_dump(mode="json")
        gc.collect()
        assert pointers and all(pointer() is None for pointer in pointers), (
            "HTTP 已完成，但解析缓存仍保活整份源模型"
        )
    finally:
        client.close()


def test_nested_requests_restore_outer_decode_and_do_not_share_retention(store):
    from app.storage.decoded_models import decoded_models_scope

    original = create(store)
    with decoded_models_scope():
        outer = read(store)
        with decoded_models_scope():
            inner = read(store)
            assert read(store) is inner
            assert inner == outer == original and inner is not outer
        inner_ref = weakref.ref(inner)
        del inner
        assert inner_ref() is None
        assert read(store) is outer
    outer_ref = weakref.ref(outer)
    del outer
    assert outer_ref() is None


def test_stream_context_inherited_after_completion_cannot_retain_or_resurrect_models(store):
    from app.storage.decoded_models import decoded_models_scope

    original = create(store)
    with decoded_models_scope():
        inherited = copy_context()
        before = read(store)
    before_ref = weakref.ref(before)
    del before
    assert before_ref() is None
    first = inherited.run(read, store)
    second = inherited.run(read, store)
    assert first == second == original and first is not second
    refs = [weakref.ref(first), weakref.ref(second)]
    del first, second
    assert all(pointer() is None for pointer in refs)
