# P5 烹饪工作台

Node 25.8.1 / npm 11.11.0，依赖精确版本与 `package-lock.json` 一起提交。

```text
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web run test:unit
npm --prefix web run build
npm --prefix web run test:e2e
```

开发时先启动真实后端 `python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`，
再运行 `npm --prefix web run dev`。Vite 将 `/api`、`/health` 转发给后端。
生产构建位于 `web/dist`，由同一个 FastAPI 应用提供静态资源。

安排页按 ID 选菜，甘特图读取服务端给出的工序和物理资源区间；支持完整日期、跨日缩放、
共同批次、冻结事实和区间明细。执行页的人工确认先读取草稿，核对实际数量、产物状态和释放情况，
再提交带版本与幂等身份的普通事件。模拟模式显式使用 SIMULATED 事件。
设备可用性和实际占用分别显示，恢复按钮不能代替对应执行的清空确认。

比赛接口测试页向 `POST /api/competition/plan?task_id=...` 提交直接 name + id 数组，
保留 `Idempotency-Key`。首次为初排，后续单菜追加；成功结构保持五字段。
查询参数、累计响应和固定会话原点是用户授权的项目设计，官方动态协议联调单列。

单元测试中的合成目录明确用于显示边界。浏览器测试不 mock HTTP，启动固定 100 菜发布、
真实 Compiler、常驻 Solver、Validator 与独立 SQLite 运行库；Windows 使用本机 Edge，
其他平台需安装 Playwright Chromium。每次报告保留在 `data/verification/P5-browser/run-*`，
运行库保留在 `.tmp/p5-browser-*.sqlite3`，不删除迁移或旧事实。
自然语言服务默认禁用；结构化初排、事件和展示继续工作。
