"""完整建模报告原子归档；文件名绑定内容，在线无需无界持有历史副本。"""

import hashlib
import os
import tempfile
from pathlib import Path

from app.domain.reports import SolverBuildReport


class SolverReportArchive:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def __call__(self, report: SolverBuildReport) -> None:
        body = report.model_dump_json().encode("utf-8")
        build = hashlib.sha256(report.solver_build_id.encode("utf-8")).hexdigest()[:16]
        digest = hashlib.sha256(body).hexdigest()
        root = self.directory.resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{build}-{digest}.json"
        if target.resolve().parent != root:
            raise OSError("建模报告归档路径越界")
        if target.exists():
            if target.read_bytes() != body:
                raise OSError("已有建模报告归档与内容身份不符")
            return
        descriptor, name = tempfile.mkstemp(prefix=".solver-", suffix=".tmp", dir=root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
