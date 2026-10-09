import importlib
import socket
import subprocess
import sys

from ortools.sat.python import cp_model
from pydantic import BaseModel
from sqlalchemy import create_engine, text


def test_python_and_dependencies():
    assert sys.version_info[:2] == (3, 12)

    class Example(BaseModel):
        count: int

    assert Example.model_validate({"count": 1}).count == 1
    for module in ("fastapi", "uvicorn", "neo4j", "alembic", "httpx", "hypothesis"):
        importlib.import_module(module)
    with create_engine("sqlite:///:memory:").connect() as conn:
        assert conn.execute(text("select 1")).scalar_one() == 1


def test_actual_cp_sat():
    model = cp_model.CpModel()
    x = model.new_int_var(0, 10, "x")
    model.add(x >= 4)
    model.minimize(x)
    solver = cp_model.CpSolver()
    assert solver.solve(model) == cp_model.OPTIMAL
    assert solver.value(x) == 4


def test_import_has_no_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("导入不允许网络或子进程副作用")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    importlib.import_module("app")


def test_all_domain_modules_import_in_fresh_process_without_connections():
    from pathlib import Path

    source = """
import importlib, pkgutil, socket, sqlite3, subprocess
def forbidden(*args, **kwargs):
    raise RuntimeError("Import attempted external I/O")
socket.create_connection = forbidden
socket.socket.connect = forbidden
sqlite3.connect = forbidden
class ForbiddenPopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        forbidden(*args, **kwargs)
subprocess.Popen = ForbiddenPopen
import app
for module in pkgutil.walk_packages(app.__path__, app.__name__ + "."):
    importlib.import_module(module.name)
"""
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
