"""独立 Windows API 进程与真实 TCP 客户端，用于 SSE 断连/重启故障。"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import httpx

from app.config import ROOT


class WindowsApiProcess:
    def __init__(
        self,
        directory: Path,
        *,
        factory: str = "app.main:create_app",
        environment: Mapping[str, str] | None = None,
        source_root: Path | None = None,
    ) -> None:
        self.directory = directory
        self.factory = factory
        self.environment = dict(environment or {})
        self.source_root = source_root or ROOT
        directory.mkdir(parents=True, exist_ok=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen[bytes] | None = None
        self.starts = 0
        self.last_startup_ms = 0.0

    def start(self) -> httpx.Client:
        self.starts += 1
        log_path = self.directory / f"api-{self.starts}.log"
        environment = {
            **os.environ,
            "PYTHONUTF8": "1",
            "SMART_COOKING_LANGUAGE_ENABLED": "0",
            "SMART_COOKING_DATABASE_PATH": str(self.directory / "runtime.sqlite3"),
            **self.environment,
        }
        began = time.perf_counter_ns()
        with log_path.open("wb") as log:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-m",
                    "uvicorn",
                    self.factory,
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                ],
                cwd=self.source_root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        client = httpx.Client(base_url=self.base_url, timeout=10, trust_env=False)
        try:
            while (time.perf_counter_ns() - began) / 1_000_000 < 20000:
                if self.process.poll() is not None:
                    raise RuntimeError("独立 API 退出：" + log_path.read_text(encoding="utf-8"))
                try:
                    if client.get("/health/ready").status_code == 200:
                        self.last_startup_ms = (time.perf_counter_ns() - began) / 1_000_000
                        return client
                except (httpx.ConnectError, httpx.RemoteProtocolError):
                    # Windows 启动前的临时 TCP 异常只在有界就绪探测重试。
                    # 返回客户端后，排程/提醒请求的协议错误仍直接使测试失败。
                    pass
                time.sleep(0.1)
            raise TimeoutError("独立 Windows API 未能在 20 秒内就绪")
        except BaseException:
            client.close()
            self.stop()
            raise

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            # 只终止本对象刚创建的、仍存活的精确 PID 进程树。
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.process.wait(timeout=10)
        self.process = None
