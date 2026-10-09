"""固定响应的本地 Provider 合同测试；MockTransport 不代表 live 模型验收。"""

import asyncio
import json

import httpx
import pytest

from app.domain.extraction import ExtractionVersions, GenerationParameter
from app.llm.provider import ArchivedLLMProvider
from app.llm.provider_adapter import JsonHttpAdapter, JsonResponseProfile
from app.pipeline.extract import build_extraction_request
from app.pipeline.extraction_archive import ExtractionArchive
from app.pipeline.import_raw import import_recipes
from tests.helpers import recipe_payload


def setup_request(tmp_path):
    from pathlib import Path

    source = import_recipes(Path(__file__).resolve().parents[2] / "docs/recipes_100.csv")[0]
    archive = ExtractionArchive(tmp_path)
    body = {"model": "synthetic-model", "input": source.steps_text, "temperature": 0}
    request = build_extraction_request(
        source,
        archive,
        requested_model="synthetic-model",
        rendered_body=json.dumps(body, ensure_ascii=False).encode(),
        parameters=(GenerationParameter(name="temperature", value_json="0"),),
    )
    payload = recipe_payload()
    payload["recipe_id"] = source.recipe_id.root
    versions = ExtractionVersions(
        prompt_version="synthetic-prompt-v1",
        schema_version="1",
        parser_version="canonical-v1",
        normalizer_version="normalize-v1",
        rule_version="test-v1",
        code_revision="synthetic-code",
        dependency_lock_hash="1" * 64,
    )
    return archive, request, payload, versions


async def call_provider(archive, request, versions, response, *, timeout=1.0):
    def handler(http_request):
        assert http_request.content == archive.read(request.rendered_request)
        assert http_request.headers["authorization"] == "Bearer synthetic-secret"
        if isinstance(response, Exception):
            raise response
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = JsonHttpAdapter(
            client,
            endpoint="https://synthetic.invalid/extract",
            provider_name="synthetic-http",
            headers={"Authorization": "Bearer synthetic-secret"},
            profile=JsonResponseProfile(
                output_path=("data", "recipe"),
                finish_reason_path=("finish",),
                success_reasons=("done",),
                truncation_reasons=("limit",),
                refusal_reasons=("refused",),
                model_path=("model",),
                request_id_path=("id",),
            ),
        )
        provider = ArchivedLLMProvider(adapter, archive, versions, timeout_sec=timeout)
        return await provider.extract(request)


def test_request_and_response_are_archived_and_repeat_calls_have_unique_identity(tmp_path):
    archive, request, payload, versions = setup_request(tmp_path)
    raw = json.dumps(
        {
            "data": {"recipe": payload},
            "finish": "done",
            "model": "mutable-alias",
            "id": "provider-id",
        }
    ).encode()
    a = asyncio.run(call_provider(archive, request, versions, httpx.Response(200, content=raw)))
    b = asyncio.run(call_provider(archive, request, versions, httpx.Response(200, content=raw)))
    assert a.status == b.status == "SUCCEEDED"
    assert a.run_id != b.run_id
    assert a.structured_result == b.structured_result
    assert archive.read(a.raw_response) == raw
    run = archive.load_run(a.run_id)
    assert run.resolved_model_revision == "unknown"
    assert run.returned_model == "mutable-alias"
    assert run.provider_request_id == "provider-id"
    assert run.generation_parameters == request.generation_parameters
    parsed = json.loads(archive.read(a.structured_result))
    assert parsed["review_status"] == "NEEDS_REVIEW"
    assert parsed["approval"] is None
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"synthetic-secret" not in path.read_bytes()


@pytest.mark.parametrize(
    "response,status,error",
    [
        (httpx.ReadTimeout("synthetic-secret must not leak"), "FAILED", "TRANSPORT_TIMEOUT"),
        (httpx.Response(500, content=b'{"error":"unavailable"}'), "FAILED", "HTTP_STATUS_500"),
        (httpx.Response(200, content=b"broken JSON"), "FAILED", "INVALID_PROVIDER_JSON"),
        (httpx.Response(200, json={"finish": "limit"}), "TRUNCATED", "PROVIDER_TRUNCATED"),
        (httpx.Response(200, json={"finish": "refused"}), "REFUSED", "PROVIDER_REFUSED"),
        (httpx.Response(200, json={"finish": "unexpected"}), "FAILED", "UNKNOWN_FINISH_REASON"),
        (
            httpx.Response(200, json={"finish": "done", "data": {"recipe": "{bad"}}),
            "FAILED",
            "INVALID_EXTRACTION",
        ),
    ],
)
def test_failures_are_archived_without_approved_or_structured_success(
    tmp_path, response, status, error
):
    archive, request, _, versions = setup_request(tmp_path)
    result = asyncio.run(call_provider(archive, request, versions, response))
    assert result.status == status
    assert result.structured_result is None
    assert result.error_message == error
    run = archive.load_run(result.run_id)
    assert run.status == status
    if isinstance(response, httpx.Response):
        assert archive.read(run.raw_response) == response.content
    assert "synthetic-secret" not in run.model_dump_json()


def test_model_cannot_return_human_approval_or_change_recipe_identity(tmp_path):
    archive, request, payload, versions = setup_request(tmp_path)
    for key, value in (("review_status", "APPROVED"), ("recipe_id", "different-id")):
        altered = {**payload, key: value}
        result = asyncio.run(
            call_provider(
                archive,
                request,
                versions,
                httpx.Response(200, json={"finish": "done", "data": {"recipe": altered}}),
            )
        )
        assert result.status == "FAILED"
        assert result.structured_result is None


def test_total_deadline_bounds_adapter_and_is_recorded(tmp_path):
    from app.domain.extraction import ProviderResult

    class SlowAdapter:
        provider_name = "synthetic-slow"

        async def generate(self, rendered_request, timeout_sec):
            await asyncio.sleep(5)
            return ProviderResult(status="FAILED", error_message="UNREACHABLE")

    archive, request, _, versions = setup_request(tmp_path)
    provider = ArchivedLLMProvider(SlowAdapter(), archive, versions, timeout_sec=0.01)
    result = asyncio.run(provider.extract(request))
    assert result.status == "FAILED"
    assert result.error_message == "TRANSPORT_TIMEOUT"
    assert archive.load_run(result.run_id).status == "FAILED"


def test_corrupt_input_archive_prevents_network_call(tmp_path):
    archive, request, _, versions = setup_request(tmp_path)
    (tmp_path / request.rendered_request.path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="哈希"):
        asyncio.run(call_provider(archive, request, versions, httpx.Response(200, json={})))


def test_credentials_are_rejected_before_any_body_is_archived(tmp_path):
    from pathlib import Path

    source = import_recipes(Path(__file__).resolve().parents[2] / "docs/recipes_100.csv")[0]
    archive = ExtractionArchive(tmp_path)
    with pytest.raises(ValueError, match="凭据"):
        build_extraction_request(
            source,
            archive,
            requested_model="synthetic-model",
            rendered_body=b'{"model":"synthetic-model","api_key":"synthetic-secret"}',
        )
    assert not list(tmp_path.rglob("*"))


def test_same_raw_archive_replays_without_network_and_detects_lost_contract(tmp_path):
    from app.pipeline.replay import replay_extraction

    archive, request, payload, versions = setup_request(tmp_path)
    result = asyncio.run(
        call_provider(
            archive,
            request,
            versions,
            httpx.Response(200, json={"finish": "done", "data": {"recipe": payload}}),
        )
    )
    a = replay_extraction(result.run_id, versions, archive)
    b = replay_extraction(result.run_id, versions, archive)
    assert a == b
    assert a.external_calls == 0
    assert a.kind == "LOCAL_REPLAY"
    run = archive.load_run(result.run_id)
    (tmp_path / run.response_profile.path).unlink()
    with pytest.raises(ValueError, match="不存在"):
        replay_extraction(result.run_id, versions, archive)
