"""独立新进程核对导入边界，不受 pytest 已加载模块影响。"""

import subprocess
import sys


def test_api_import_defers_compiler_engine_and_native_solver():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import app.main; "
            "assert 'app.compiler.compiler' not in sys.modules; "
            "assert 'app.scheduling.engine' not in sys.modules; "
            "assert 'ortools.sat.python.cp_model' not in sys.modules; "
            "assert 'matplotlib' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_injected_engine_does_not_load_an_unused_native_adapter():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from app.scheduling.engine import PlanningEngine; "
            "engine = PlanningEngine(validator=object(), solver=object()); "
            "assert 'app.scheduling.cp_sat' not in sys.modules; "
            "assert 'ortools.sat.python.cp_model' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
