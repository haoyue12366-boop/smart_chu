"""显式真实模型检查：默认只列可用模型；指定菜谱才进行一次收费抽取。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.domain.extraction import ExtractionVersions
from app.llm.deepseek import (
    PROMPT_VERSION,
    DeepSeekSettings,
    create_deepseek_adapter,
    render_recipe_request,
)
from app.llm.extraction_contract import PARSER_VERSION
from app.llm.provider import ArchivedLLMProvider
from app.pipeline.extract import build_extraction_request
from app.pipeline.extraction_archive import ExtractionArchive
from app.pipeline.import_raw import import_recipes
from app.pipeline.normalize import NORMALIZER_VERSION
from app.pipeline.replay import replay_extraction

ROOT = Path(__file__).resolve().parents[1]


def code_revision(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "app").rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return "source-sha256:" + digest.hexdigest()


async def check(
    recipe_id: str | None, source_path: Path, timeout_sec: float, model: str | None = None
) -> dict:
    settings = DeepSeekSettings.from_environment(ROOT / ".env")
    if model is not None:
        settings = settings.model_copy(update={"model": model})
    report = {
        "schema_version": "1.0",
        "kind": "REAL_PROVIDER_CHECK",
        "checked_at": datetime.now(UTC).isoformat(),
        "provider": "deepseek",
        "requested_model": settings.model,
        "approved_count": 0,
    }
    async with httpx.AsyncClient() as client:
        if recipe_id is None:
            try:
                response = await client.get(
                    settings.base_url + "/models",
                    headers={"Authorization": "Bearer " + settings.api_key.get_secret_value()},
                    timeout=30,
                    follow_redirects=False,
                )
                report["http_status"] = response.status_code
                if response.is_success:
                    models = [item["id"] for item in response.json()["data"]]
                    report.update(
                        status="SUCCEEDED",
                        available_models=models,
                        requested_model_available=settings.model in models,
                    )
                else:
                    report.update(status="FAILED", error=f"HTTP_STATUS_{response.status_code}")
            except httpx.HTTPError:
                report.update(status="FAILED", error="TRANSPORT_ERROR")
            return report
        source = next(
            (item for item in import_recipes(source_path) if item.recipe_id.root == recipe_id), None
        )
        if source is None:
            raise ValueError("源文件不包含指定菜谱 ID")
        archive = ExtractionArchive(ROOT / "data/extraction_archive")
        body, parameters = render_recipe_request(source, settings)
        request = build_extraction_request(
            source,
            archive,
            requested_model=settings.model,
            rendered_body=body,
            parameters=parameters,
        )
        versions = ExtractionVersions(
            prompt_version=PROMPT_VERSION,
            schema_version="1.0",
            parser_version=PARSER_VERSION,
            normalizer_version=NORMALIZER_VERSION,
            rule_version="draft-rules-v1",
            code_revision=code_revision(ROOT),
            dependency_lock_hash=hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        )
        provider = ArchivedLLMProvider(
            create_deepseek_adapter(settings, client), archive, versions, timeout_sec=timeout_sec
        )
        result = await provider.extract(request)
        run = archive.load_run(result.run_id)
        report.update(
            status=result.status,
            error=result.error_message,
            run_id=result.run_id,
            source_recipe_id=recipe_id,
            source_sha256=source.source_sha256,
            returned_model=run.returned_model,
            finish_reason=run.finish_reason,
            resolved_model_revision=run.resolved_model_revision,
            versions=versions.model_dump(mode="json"),
        )
        if result.status == "SUCCEEDED":
            replay = replay_extraction(result.run_id, versions, archive)
            report.update(
                local_replay_hash=replay.replayed_content_hash,
                local_replay_external_calls=replay.external_calls,
                review_status=replay.recipe.review_status,
                operation_count=len(replay.recipe.operations),
            )
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe-id")
    parser.add_argument("--model", help="显式覆盖环境模型别名")
    parser.add_argument("--source", type=Path, default=ROOT / "docs/recipes_100.csv")
    parser.add_argument("--timeout-sec", type=float, default=90)
    args = parser.parse_args()
    report = asyncio.run(check(args.recipe_id, args.source, args.timeout_sec, args.model))
    target = ROOT / "benchmarks/reports/verification"
    target.mkdir(parents=True, exist_ok=True)
    name = "deepseek-extraction" if args.recipe_id else "deepseek-availability"
    (target / f"{name}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
