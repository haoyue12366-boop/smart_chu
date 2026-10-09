"""合成故障用于原子发布验证；真实发布另由流水线命令完成。"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from app.domain.knowledge_release import ReviewedRelease
from app.domain.provenance import ArtifactRef, ProvenanceRecord
from app.knowledge.index import build_index
from app.knowledge.loader import load_release
from app.knowledge.snapshot import SchedulingKnowledgeSnapshot
from app.pipeline.publish import ReleaseBundle, publish_release
from app.pipeline.snapshot_export import SnapshotBuildResult, SnapshotValidationReport
from app.validation.knowledge import validate_knowledge
from tests.unit.test_knowledge_gate import fixture, scope


def bundle(root, release_id="synthetic-release"):
    root.mkdir(parents=True, exist_ok=True)
    artifact = root / "synthetic-source.txt"
    artifact.write_text("SYNTHETIC fixture; not a real recipe or approval", encoding="utf-8")
    ref = ArtifactRef(
        path=artifact.name,
        sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        media_type="text/plain",
    )
    recipe, profiles, device = fixture()
    release_scope = scope(device)
    knowledge = ReviewedRelease(
        release_id=release_id,
        knowledge_version=release_id,
        recipes=(recipe,),
        profiles=profiles,
        scope=release_scope,
        source_artifacts=(ref,),
        provenance=(
            ProvenanceRecord(
                provenance_id="synthetic-source",
                origin="MODEL_SUGGESTION",
                source_file=ref.path,
                source_hash=ref.sha256,
                record_id="synthetic",
                field_path="/",
                text_span="SYNTHETIC source",
                artifact_ref=ref,
            ),
        ),
        validation=validate_knowledge((recipe,), profiles, (), release_scope),
    )
    snapshot = SchedulingKnowledgeSnapshot(
        snapshot_id="snapshot-" + knowledge.content_hash,
        content_hash=knowledge.content_hash,
        build_time=datetime.now(UTC),
        knowledge=knowledge,
    )
    build = SnapshotBuildResult(
        snapshot=snapshot,
        index=build_index(knowledge),
        validation=SnapshotValidationReport(
            valid=True,
            release_id=release_id,
            canonical_hash=knowledge.content_hash,
            snapshot_hash=knowledge.content_hash,
            graph_hash="a" * 64,
        ),
    )
    return ReleaseBundle(build=build, source_root=root)


def test_publish_repeatable_and_missing_source_keeps_old_active(tmp_path):
    releases = tmp_path / "releases"
    first = bundle(tmp_path / "input")
    ref = publish_release(first, releases)
    assert publish_release(first, releases) == ref
    second = bundle(tmp_path / "new-input", "synthetic-second")
    (second.source_root / "synthetic-source.txt").unlink()
    with pytest.raises(ValueError):
        publish_release(second, releases)
    assert json.loads((releases / "active_release.json").read_bytes()) == ref.model_dump(
        mode="json"
    )
    assert load_release(releases, ref).snapshot.knowledge.release_id == ref.release_id


def test_interrupted_write_never_activates_incomplete_version(tmp_path, monkeypatch):
    import app.pipeline.publish as publishing

    releases = tmp_path / "releases"
    ref = publish_release(bundle(tmp_path / "input"), releases)
    second = bundle(tmp_path / "new-input", "synthetic-second")
    original = publishing.write_file

    def interrupted(path, data):
        if path.name == "index.json":
            raise OSError("synthetic disk interruption")
        original(path, data)

    monkeypatch.setattr(publishing, "write_file", interrupted)
    with pytest.raises(OSError):
        publish_release(second, releases)
    assert load_release(releases, ref).snapshot.content_hash
    assert json.loads((releases / "active_release.json").read_bytes()) == ref.model_dump(
        mode="json"
    )


def test_concurrent_publish_is_whole_version_and_previous_files_remain(tmp_path):
    releases = tmp_path / "releases"
    bundles = [bundle(tmp_path / f"input-{i}", f"synthetic-{i}") for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        refs = list(pool.map(lambda item: publish_release(item, releases), bundles))
    active = json.loads((releases / "active_release.json").read_bytes())
    assert active in [ref.model_dump(mode="json") for ref in refs]
    for ref in refs:
        assert load_release(releases, ref).snapshot.knowledge.release_id == ref.release_id


@pytest.mark.parametrize("filename", ["snapshot.json", "index.json", "synthetic-source.txt"])
def test_tampered_published_content_is_rejected(tmp_path, filename):
    releases = tmp_path / "releases"
    ref = publish_release(bundle(tmp_path / "input"), releases)
    path = releases / ref.release_id / filename
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError):
        load_release(releases, ref)


def test_rehashed_but_semantically_wrong_snapshot_proof_is_rejected(tmp_path):
    from app.knowledge.loader import read_release_ref

    releases = tmp_path / "releases"
    ref = publish_release(bundle(tmp_path / "input"), releases)
    directory = releases / ref.release_id
    proof_path = directory / "snapshot_validation_report.json"
    proof = json.loads(proof_path.read_bytes())
    proof["valid"] = False
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    for item in manifest["artifacts"]:
        if item["path"] == proof_path.name:
            item["sha256"] = hashlib.sha256(proof_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="校验|证明"):
        load_release(releases, read_release_ref(releases, ref.release_id))


def test_release_carries_raw_extraction_and_review_replay_without_original_archive(tmp_path):
    import asyncio
    from dataclasses import replace

    import httpx

    from app.domain.canonical_recipe import CanonicalRecipeModel
    from app.domain.release_replay import ReleaseReplay
    from app.pipeline.release_replay import collect_replay_artifacts
    from app.pipeline.replay import replay_extraction
    from app.pipeline.review import apply_archived_review
    from tests.integration.test_llm_provider_contract import call_provider, setup_request
    from tests.unit.test_review_provenance import approval_patch

    original = bundle(tmp_path / "input")
    archive, request, _, versions = setup_request(tmp_path / "archive")
    payload = original.build.snapshot.knowledge.recipes[0].model_dump(mode="json")
    payload["recipe_id"] = request.source_recipe_id.root
    extraction = asyncio.run(
        call_provider(
            archive,
            request,
            versions,
            httpx.Response(200, json={"finish": "done", "data": {"recipe": payload}}),
        )
    )
    draft = replay_extraction(extraction.run_id, versions, archive).recipe
    review_source = archive.put(b"SYNTHETIC reviewer edit: 601 seconds", "text/plain")
    patch = approval_patch(
        draft,
        action="MODIFY",
        next_recipe_version="edited-v2",
        changes=(
            {
                "path": "/operations/0/duration/execution_sec",
                "before_json": "600",
                "after_json": "601",
            },
        ),
    )
    edited = apply_archived_review(draft, patch, archive, {"synthetic-source": review_source})
    recipe = CanonicalRecipeModel.model_validate(edited.recipe)
    knowledge = original.build.snapshot.knowledge
    provenance = tuple(
        p.model_copy(update={"extraction_run_id": extraction.run_id}) for p in knowledge.provenance
    )
    knowledge = knowledge.model_copy(
        update={
            "recipes": (recipe,),
            "provenance": provenance,
            "validation": validate_knowledge((recipe,), knowledge.profiles, (), knowledge.scope),
        }
    )
    snapshot = original.build.snapshot.model_copy(
        update={
            "knowledge": knowledge,
            "content_hash": knowledge.content_hash,
            "snapshot_id": "snapshot-" + knowledge.content_hash,
        }
    )
    build = original.build.model_copy(
        update={
            "snapshot": snapshot,
            "index": build_index(knowledge),
            "validation": original.build.validation.model_copy(
                update={
                    "canonical_hash": knowledge.content_hash,
                    "snapshot_hash": knowledge.content_hash,
                }
            ),
        }
    )
    run_ref = ArtifactRef(
        path=f"runs/{extraction.run_id}.json",
        sha256=hashlib.sha256(
            (archive.root / f"runs/{extraction.run_id}.json").read_bytes()
        ).hexdigest(),
        media_type="application/json",
    )
    binding = ReleaseReplay(
        run_id=extraction.run_id,
        run_manifest=run_ref,
        versions=versions,
        expected_content_hash=recipe.document_hash,
        review_patch_refs=(edited.patch_ref,),
    )
    with pytest.raises(ValueError, match="重放"):
        publish_release(replace(original, build=build), tmp_path / "releases")
    complete = replace(original, build=build, replays=(binding,), archive_root=archive.root)
    ref = publish_release(complete, tmp_path / "releases")
    (archive.root / extraction.raw_response.path).unlink()
    packaged = tmp_path / "releases" / ref.release_id
    restored = load_release(tmp_path / "releases", ref)
    assert collect_replay_artifacts(
        restored.snapshot.knowledge, restored.manifest.replays, packaged / "extraction"
    )
    (packaged / "extraction" / review_source.path).unlink()
    with pytest.raises(ValueError):
        load_release(tmp_path / "releases", ref)
