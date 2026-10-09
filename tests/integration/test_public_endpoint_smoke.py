"""有明确公网目标和发布授权后才运行真实服务探测。"""

import ipaddress
import uuid
from urllib.parse import urlparse

import httpx

from app.domain.competition_decimal import DecimalCompetitionResponse
from tests.p6_evidence_support import inputs


def test_authorized_public_service_has_real_ready_and_competition_response():
    conditions = inputs()["external_conditions"]
    assert conditions["public_endpoint"], "P6-08 未提供真实公网服务目标"
    assert conditions["public_publication_authorized"] is True, "目标服务缺少实际发布/联调授权"
    endpoint = conditions["public_endpoint"].rstrip("/")
    parsed = urlparse(endpoint)
    assert parsed.scheme in {"http", "https"} and parsed.hostname
    assert parsed.username is None and parsed.password is None
    assert parsed.hostname.lower() not in {"localhost", "localhost.localdomain"}
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    assert address is None or address.is_global, "本机或内网地址不能当作公网验证"
    task_id = "p6-public-" + uuid.uuid4().hex
    with httpx.Client(base_url=endpoint, timeout=15, trust_env=False) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200, ready.text
        initial = client.post(
            "/api/competition/plan",
            params={"task_id": task_id},
            headers={"Idempotency-Key": task_id + "-initial"},
            json=[{"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}],
        )
        assert initial.status_code == 200, initial.text
        body = DecimalCompetitionResponse.model_validate(initial.json())
        assert body.overview.recipeCount == 1
        added = client.post(
            "/api/competition/plan",
            params={"task_id": task_id},
            headers={"Idempotency-Key": task_id + "-add"},
            json=[{"id": "5c8200d96dc6e123a037a202", "name": "豆豉蒸腐竹"}],
        )
        assert added.status_code == 200, added.text
        assert DecimalCompetitionResponse.model_validate(added.json()).overview.recipeCount == 2
