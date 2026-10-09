// 浏览器验收启动真实 API、固定知识发布和常驻 Solver；不接入模拟 HTTP。
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { mkdirSync } from "node:fs";
const root = fileURLToPath(new URL("../", import.meta.url));
mkdirSync(resolve(root, ".tmp"), { recursive: true });
const python = resolve(
  root,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
const child = spawn(
  python,
  [
    "-X",
    "utf8",
    "-m",
    "uvicorn",
    process.env.SMART_COOKING_E2E_CLOCK === "1"
      ? "tests.browser_clock_app:create_app"
      : "app.main:create_app",
    "--factory",
    "--host",
    "127.0.0.1",
    "--port",
    process.env.SMART_COOKING_E2E_PORT ?? "8000",
  ],
  {
    cwd: root,
    windowsHide: true,
    stdio: "inherit",
    env: {
      ...process.env,
      SMART_COOKING_DATABASE_PATH:
        process.env.SMART_COOKING_DATABASE_PATH ??
        resolve(root, ".tmp", `p5-browser-${Date.now()}.sqlite3`),
    },
  },
);
for (const signal of ["SIGINT", "SIGTERM"])
  process.on(signal, () => {
    child.kill(signal);
  });
child.on("exit", (code) => {
  process.exitCode = code ?? 1;
});
