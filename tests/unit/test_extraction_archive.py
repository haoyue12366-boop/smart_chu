"""合成归档的完整性和失败证据；不使用正式审核样本。"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from app.domain.extraction import ExtractionRun
from app.pipeline.extraction_archive import ExtractionArchive, archive_extraction


def failed_run(archive, **overrides):
    ref = archive.put(b'{"synthetic":true}', "application/json")
    fields = dict(
        source_recipe_id="synthetic",
        source=ref,
        rendered_request=ref,
        output_schema=ref,
        provider="synthetic",
        requested_model="fixture",
        called_at=datetime.now(UTC),
        status="FAILED",
        error_message="SYNTHETIC_TIMEOUT",
        prompt_version="p1",
        schema_version="1",
        parser_version="canonical-v1",
        normalizer_version="n1",
        rule_version="r1",
        code_revision="synthetic-code",
        dependency_lock_hash="0" * 64,
    )
    fields.update(overrides)
    return ExtractionRun(**fields)


def test_content_addressing_concurrent_writers_and_run_roundtrip(tmp_path):
    archive = ExtractionArchive(tmp_path)
    payload = "真实保存的合成请求\n含中文".encode()
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(lambda _: archive.put(payload, "text/plain"), range(8)))
    assert len(set(refs)) == 1
    assert archive.read(refs[0]) == payload
    run = failed_run(archive)
    ref = archive_extraction(run, archive)
    assert ref.verify(tmp_path).is_file()
    assert archive.load_run(run.run_id) == run
    assert archive_extraction(run, archive) == ref
    assert len(list((tmp_path / "runs").glob("*.json"))) == 1


def test_same_run_id_cannot_replace_prior_attempt(tmp_path):
    archive = ExtractionArchive(tmp_path)
    run = failed_run(archive)
    archive.save_run(run)
    replacement = ExtractionRun.model_validate({**run.model_dump(), "error_message": "changed"})
    with pytest.raises(ValueError, match="身份|内容"):
        archive.save_run(replacement)
    assert archive.load_run(run.run_id) == run


@pytest.mark.parametrize("corruption", ["delete", "modify"])
def test_missing_or_changed_content_prevents_read_and_run_creation(tmp_path, corruption):
    archive = ExtractionArchive(tmp_path)
    run = failed_run(archive)
    archive.save_run(run)
    path = tmp_path / run.source.path
    if corruption == "delete":
        path.unlink()
    else:
        path.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        archive.load_run(run.run_id)
    with pytest.raises(ValueError):
        archive.save_run(failed_run(archive) if corruption == "modify" else run)


@pytest.mark.parametrize("run_id", ["../escape", "..", "a/b", "a\\b", "C:escape"])
def test_run_identity_cannot_escape_archive(tmp_path, run_id):
    archive = ExtractionArchive(tmp_path)
    with pytest.raises(ValueError):
        archive.save_run(failed_run(archive, run_id=run_id))


def test_raw_json_is_preserved_as_bytes(tmp_path):
    archive = ExtractionArchive(tmp_path)
    payload = b'{ "output": "\\u4e2d", "unused": [1, 2] }\n'
    ref = archive.put(payload, "application/json")
    assert archive.read(ref) == payload
    assert json.loads(archive.read(ref))["output"] == "中"
