"""按内容寻址的原始调用归档；写入原子发布且不覆盖既有调用身份。"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from app.domain.extraction import ExtractionRun
from app.domain.provenance import ArtifactRef, resolve_artifact_path


class ExtractionArchive:
    def __init__(self, root: Path) -> None:
        self.root = resolve_artifact_path(root)

    def _path(self, relative: str) -> Path:
        path = resolve_artifact_path(self.root / relative)
        if not path.is_relative_to(self.root):
            raise ValueError(f"归档路径越界：{path}，根目录：{self.root}")
        return path

    def _write(self, relative: str, payload: bytes, media_type: str) -> ArtifactRef:
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pending-", delete=False) as f:
            temporary = Path(f.name)
            try:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            except BaseException:
                f.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            try:
                # 原子创建新名字；同 ID 重试不得覆盖其他内容。
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != payload:
                    raise ValueError("归档身份已存在不同内容") from None
        finally:
            temporary.unlink(missing_ok=True)
        ref = ArtifactRef(
            path=relative, sha256=hashlib.sha256(payload).hexdigest(), media_type=media_type
        )
        ref.verify(self.root)
        return ref

    def put(self, payload: bytes, media_type: str) -> ArtifactRef:
        digest = hashlib.sha256(payload).hexdigest()
        return self._write(f"objects/{digest}", payload, media_type)

    def read(self, ref: ArtifactRef) -> bytes:
        return ref.verify(self.root).read_bytes()

    @staticmethod
    def _run_path(run_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
            raise ValueError("调用身份包含非法路径字符")
        return f"runs/{run_id}.json"

    def save_run(self, run: ExtractionRun) -> ArtifactRef:
        relative = self._run_path(run.run_id)
        run.verify_artifacts(self.root)
        return self._write(relative, run.model_dump_json(indent=2).encode(), "application/json")

    def load_run(self, run_id: str) -> ExtractionRun:
        path = self._path(self._run_path(run_id))
        if not path.is_file():
            raise ValueError(f"调用归档缺失：{run_id}")
        run = ExtractionRun.model_validate_json(path.read_bytes())
        if run.run_id != run_id:
            raise ValueError("调用归档身份不一致")
        run.verify_artifacts(self.root)
        return run


def archive_extraction(run: ExtractionRun, artifacts: ExtractionArchive) -> ArtifactRef:
    return artifacts.save_run(run)
