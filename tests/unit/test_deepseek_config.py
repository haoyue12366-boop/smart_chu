"""DeepSeek 配置和线协议的离线合成测试。"""

import asyncio
import json

import httpx
import pytest

from app.llm.deepseek import DeepSeekSettings, create_deepseek_adapter, render_recipe_request
from app.pipeline.import_raw import import_recipes
from tests.unit.test_raw_import import ROOT


def test_settings_read_only_when_requested_and_environment_overrides_local_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "DEEPSEEK_API_KEY=synthetic-secret\nDEEPSEEK_BASE_URL=https://api.deepseek.com\n"
        "DEEPSEEK_MODEL=deepseek-chat\n",
        encoding="utf-8",
    )
    settings = DeepSeekSettings.from_environment(path, {"DEEPSEEK_MODEL": "explicit-model"})
    assert settings.model == "explicit-model"
    assert "synthetic-secret" not in repr(settings)
    assert "synthetic-secret" not in settings.model_dump_json()
    assert settings.endpoint == "https://api.deepseek.com/chat/completions"
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        DeepSeekSettings.from_environment(None, {})


def test_json_prompt_carries_actual_source_and_schema_without_credentials():
    from app.llm.extraction_contract import validate_rendered_body

    source = import_recipes(ROOT / "docs/recipes_100.csv")[0]
    settings = DeepSeekSettings(api_key="synthetic-secret", model="deepseek-chat")
    body, parameters = render_recipe_request(source, settings, max_tokens=4096)
    payload = json.loads(body)
    assert payload["model"] == "deepseek-chat"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["stream"] is False
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 4096
    assert source.steps_text in payload["messages"][1]["content"]
    assert "json" in payload["messages"][0]["content"].lower()
    assert "synthetic-secret" not in body.decode()
    validate_rendered_body(settings.model, parameters, body)


def test_real_protocol_envelope_preserves_model_id_and_backend_fingerprint_is_not_revision():
    async def run():
        def handler(request):
            assert str(request.url) == "https://api.deepseek.com/chat/completions"
            assert request.headers["Authorization"] == "Bearer synthetic-secret"
            return httpx.Response(
                200,
                json={
                    "id": "test-id",
                    "model": "deepseek-chat",
                    "system_fingerprint": "backend-only",
                    "choices": [
                        {"message": {"content": '{"synthetic":true}'}, "finish_reason": "stop"}
                    ],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            settings = DeepSeekSettings(api_key="synthetic-secret", model="deepseek-chat")
            adapter = create_deepseek_adapter(settings, client)
            return await adapter.generate(b'{"model":"deepseek-chat"}', 1)

    result = asyncio.run(run())
    assert result.status == "SUCCEEDED"
    assert result.resolved_model_revision == "unknown"
    assert result.provider_request_id == "test-id"
    assert result.returned_model == "deepseek-chat"


@pytest.mark.parametrize(
    "reason,status",
    [
        ("length", "TRUNCATED"),
        ("content_filter", "REFUSED"),
        ("aborted", "FAILED"),
        ("tool_calls", "FAILED"),
    ],
)
def test_deepseek_nonfinal_responses_are_not_success(reason, status):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": reason}]}
                )
            )
        ) as client:
            adapter = create_deepseek_adapter(
                DeepSeekSettings(api_key="test", model="fixture"), client
            )
            return await adapter.generate(b"{}", 1)

    assert asyncio.run(run()).status == status
