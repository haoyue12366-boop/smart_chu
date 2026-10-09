# 智能烹饪调度工程

本目录是项目根目录。项目包含离线知识流水线、Compiler → Greedy/CP-SAT → 独立校验、运行事实与重排，以及 P5 的 FastAPI 接口和 Vue 工作台。P5 使用已获用户授权的固定 development 发布：100 道菜、1562 道工序；正式发布与全部官方动态联调另行验收。

进度以 [docs/task.md](docs/task.md) 为准，工程约束见 [docs/AGENTS.md](docs/AGENTS.md)。

## GitHub 源码与 Render 部署

仓库根目录即本目录，保留全部应用源码、前端源码、依赖锁、测试、脚本和工程说明。运行数据与本机凭据不提交；知识数据包含完整默认发布 `data/preparations/p4-v1/releases/delegated-v3-multilayer-onepot-v1-all/`，其内部来源归档和校验文件必须一起保留。其他历史发布、验证报告和交付包仅留在原工作目录，本文中指向这些历史产物的链接在 GitHub 中可能不可用；依赖旧发布的历史验收也需另外取得对应数据。知识文件通过 `.gitattributes` 保持原始字节，避免 Git 换行转换破坏 SHA256 校验。

Render 新建 Python Web Service 并连接本仓库，Root Directory 留空。设置 `PYTHON_VERSION=3.12.14`、`NODE_VERSION=25.8.1`；构建命令：

```sh
uv sync --frozen --no-dev && npm --prefix web ci && npm --prefix web run build
```

启动命令：

```sh
uv run --no-sync uvicorn app.main:create_app --factory --host 0.0.0.0 --port $PORT --workers 1
```

前端由同一 FastAPI 服务提供，健康检查路径为 `/health/ready`。比赛运行建议使用常驻付费实例，并挂载持久磁盘 `/var/data`，设置 `SMART_COOKING_DATABASE_PATH=/var/data/runtime.sqlite3`、`SMART_COOKING_LANGUAGE_ARCHIVE_PATH=/var/data/intent-runs`、`SMART_COOKING_TIMEZONE=Asia/Shanghai`。保持单实例和单个 API worker。只设置路径不会创建持久磁盘，必须在 Render 中配置实际挂载。

免费实例试运行时，省略上述两个 `/var/data` 路径变量，使用项目默认可写目录；免费实例的休眠、重启、重新部署会丢失临时运行数据。在线服务默认最多等待求解进程预热 120 秒，可通过 `SMART_COOKING_SOLVER_STARTUP_TIMEOUT_SEC` 调整（有限正秒数，最大 600）。该上限仅用于进程准备，不扩大初排或重排的请求求解预算；进程未就绪仍会启动失败，日志区分预热超时和子进程提前退出。健康检查继续使用 `/health/ready`。

Render 会通过平台的 `RENDER=true` 自动启用宽预算演示配置，也可设置 `SMART_COOKING_PLANNING_PROFILE=RENDER` 显式启用。新桌的初排与重排各有 90 秒求解/发布总预算，其中 Greedy 上限 10 秒、CP-SAT 累计上限 45 秒、发布预留 25 秒；编译独立上限 20 秒，计算仅使用一个求解线程。串行参考的阶段时间随预算增加，候选仍需完整覆盖并通过独立核验。联合质量搜索及其放宽重试合计最多 10 秒，已有合法方案时减少额外等待，未取得改进不宣称完成质量优化。云端策略追加 `:render-v2` 版本，既有桌保留原绑定策略；更新后请点击“新的一桌”。此配置不代表达到比赛原实时预算。设置 `SMART_COOKING_PLANNING_PROFILE=STANDARD` 可使用原策略预算，其他平台默认保持原配置。平台识别依据见 [Render 默认环境变量](https://render.com/docs/environment-variables)。

云端工作进程在共享截止内预留 3 秒传输求解结果，异常退出后由空闲恢复循环重新预热；健康检查在收到真实就绪消息后才通过。运行状态按表批量保存，选择会话运行时只读取当前绑定和时钟字段，减少大菜单的数据库调用与重复反序列化。Render 免费 Web 服务提供 0.1 CPU、512 MB 内存，算力与本机不同，宽预算是上限，实际耗时取决于菜单及负载，见 [Render 计算规格](https://render.com/docs/compute-plans)。

大模型相关代码位于 `app/llm/`。结构化初排、加菜和重排默认使用本地知识与调度器；需要自然语言入口时设置 `SMART_COOKING_LANGUAGE_ENABLED=1`、`DEEPSEEK_API_KEY`，并按账号可用模型设置 `DEEPSEEK_MODEL`。密钥只通过平台环境变量提供，不写入仓库。

Render 部署流程依据 [FastAPI 部署](https://render.com/docs/deploy-fastapi)、[原生运行环境](https://render.com/docs/native-runtimes)及[持久磁盘](https://render.com/docs/disks)。当前已有验收在 Windows 完成；GitHub 上传不等于 Render 或公网验收，首次云端部署后仍需检查首页、`/docs`、健康检查、初排、同任务加菜重排及重启后的任务恢复。

## P5 工作台与比赛接口

启动、接口请求、重排触发和验收入口见 [P5运行与接口说明](docs/P5运行与接口说明.md)。比赛初排为 `POST /api/competition/plan?task_id=meal-001`，请求体直接提交 `[{"name":"低温牛排","id":"61e6c51fec6e1d65587067e1"}]`；同任务后续传一道菜触发累计菜单重排。分钟统一显示一位小数，重排沿用首次会话原点。项目选择与赛方确认状态分开记录。

工作台提供菜单选择、设备/菜品甘特图、执行反馈、设备故障与清空确认、历史差异、工艺子图和比赛接口测试。新会话默认在首次发布后自动计时，推算进度明确标记“计划时钟推算”，人工或设备反馈可修正当前进度。加菜和重排冻结过去及在途记录、保留必要衔接约束，立即优化剩余工序；新增任务不早于插入及发布生效时刻。完成提示在工作台和比赛任务的 JSON/SSE 接口同步提供。手动与模拟模式仍可显式选择，已有会话保持原模式。接口细节见运行文档，验证记录仅在 task.md 维护。

工作台加菜使用 `POST /api/v1/sessions/{session_id}/recipes`，请求包含 `event_id`、`base_plan_version` 及单项 `recipes`。服务端同步时钟后绑定当前状态版本，计划版本过期及重复菜品仍拒绝；同一请求重试保持首次结果。计时刷新保留已勾选的加菜选项。人工执行反馈继续通过 `/events` 严格校验其原状态与计划版本。

当前源码默认优化**出锅差（目标≤300秒）→最长连续人工→总流程**，累计人工仅统计；发布前压紧未来设备预约和出锅后收尾空档。默认分层版本 `delegated-v3-multilayer-onepot-v1-all`、策略 `p6-cook-prepared-multilayer-v1` 使用独立蒸箱与烤箱各三层，只有同温且配置兼容才能跨层复用，各菜时长和进出独立；轻松一锅蒸固定同时占第1、3层，第2层仍可同温复用。重排和追加立即计算未来计划，已完成和在途操作冻结，不等待其结束。用户确认的开工前浸泡、腌制、冷藏等准备按已备好排程，清单和原时长单独展示；炒熟后的冷却等中途等待保留。甘特图按真实灶眼、设备区域和层位展示，并标出复用区间。重启服务、强制刷新后点击“新的一桌”使用新默认；旧会话保留原知识和策略。启动步骤和原段取出时间精度说明见上述运行文档。

## 环境与验证

Python 固定为 3.12.14，后端和开发依赖精确固定在 pyproject.toml 与 uv.lock。本次实际验证在 Windows 完成；Linux 尚未运行，不将跨平台设计写成已通过 Linux 验收。

已安装 uv 时，在本目录运行：

~~~text
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app
uv run --locked pytest tests/unit tests/contract
uv run --locked python scripts/verify.py --phase P0
~~~

本机 uv 安装于项目内 .tools/bin/uv.exe，可将上面的 uv 替换成 ./.tools/bin/uv.exe。本机解释器在 .python/ 内、虚拟环境在 .venv/ 内；这些安装文件不属于源码交付。uv 缓存使用项目内 .uv-cache，避免本机跨盘临时文件重命名问题。安装新环境时 uv 可根据 .python-version 选择对应解释器。

验收报告保存至 benchmarks/reports/verification/P0.json，包含命令、退出码、测试数、耗时、平台、依赖版本及内容指纹。可使用 --task P0-05 验证某个任务及它的明确前置任务。P1 分为 --gate core 和 --gate full；未实现的后续阶段返回失败。

**当前 P0 全量检查应返回非零：12 道真实样本尚未取得人工审核依据。** 该检查没有被跳过或改成预期失败；其失败不代表代码测试全部失败。报告逐项给出代码检查与审核门槛的实际结果。

## 样本与审核

[data/issues/sample_review.md](data/issues/sample_review.md) 列出 12 个真实菜谱 ID 的具体缺口。每份样本保留原始记录、CSV 行号、原文片段、文件哈希和待审草稿，两个同名“麻辣对虾”分别保留。tests/fixtures/reviewed_sample 是将来审核样本的存放目录，当前内容均明确标为 NEEDS_REVIEW。

早期V3开发数据为1564个操作，旧离线工具曾通过100单菜与45组菜单检查；这是历史证据，不代表当前P2主链。12道代表样本承接V3的214个操作、228条依赖，保留AI建议和待审状态。[数据说明与下载](data/revisions/recipes_v3/README.md)、[逐工序审核表](data/issues/p0_review_packet.md)。合成用例独立存放，不能满足真实审核门槛。

ReviewPatch 记录基础版本、前后值、理由、审核者和证据引用。修改工艺后会清除当前批准标记；批准必须匹配当前内容哈希。真实审核输入补齐后，应保存审核记录、更新样本及 manifest 哈希，再运行完整 P0 验收，不应仅修改状态字符串。

以下命令用于确定性重建开发产物：

~~~text
uv run --locked python -m scripts.export_schemas
uv run --locked python -m scripts.build_development_version
uv run --locked python -m scripts.update_p0_samples_from_revision
uv run --locked python -m scripts.check_development_knowledge
~~~

历史 V2 / 时间补全 JSON 缺失，旧 build_scheduling_dataset 不能在当前材料下重建。现存 V3 已建立不可变开发基线 V1；2026-09-28 经用户同意修正鱼头热水烫洗后，生成 `data/development/development-v3-rebased-v2/`，保留100个ID、1562个操作和历史来源缺口。按用户最新要求，内部保持整数秒，默认输出小数分钟：15秒→0.25分钟，20秒→0.333333分钟；最多六位小数并校验能恢复原整数秒。显式整数分钟模式仍拒绝不兼容计划。原始资料不变，真实数据仍为 NEEDS_REVIEW。

## P2 命令行规划

~~~text
uv run --locked python -m scripts.run_sample_plan
uv run --locked python -m scripts.run_sample_plan --recipe-id 58e70ae1a3fd4a750f4b75b0 --output benchmarks/reports/fish-plan.json
uv run --locked python -m scripts.verify_all_recipes --output benchmarks/reports/all-recipes-new-run
~~~

`--recipe-id` 可重复指定组成多菜菜单；省略时运行修正后的鱼头菜。命令读取固定 development 发布，不调用 Neo4j 或在线大模型。输出计划、独立校验证据、小数分钟投影，以及同名 `.problem.json`、`.builds.json`。结果是模拟初排，不写入实际执行事实或 SQLite 已发布计划。冷启动及请求间工作进程恢复单独记录，4200ms 指已预热请求内预算。

本次真实PlanningCore全量检查为100/100通过，数据为修正后的1562个操作。证据见 [全量报告](benchmarks/reports/P1-P2-all-recipes-v3/report.json) 和 [鱼头计划](benchmarks/reports/P2-sample-plan.json)。本机本轮P50/P95/最大请求耗时为1406/3141/3484ms；98道含CP-SAT候选，2道使用Greedy合法回退，另有3次请求间冷恢复。前两轮失败报告保留用于复核；此结果不替代P6三轮正式性能验收。

不可行诊断按需离线运行，`--build-id` 来自 `.builds.json`，与问题哈希及失败阶段绑定：

~~~text
uv run --locked python -m scripts.diagnose_problem --problem benchmarks/reports/fish-plan.problem.json --build-report benchmarks/reports/fish-plan.builds.json --build-id <实际构建ID> --output benchmarks/reports/diagnostic.json
~~~

诊断最多2000ms、单线程、6次冲突缩减。UNKNOWN 不报告确定根因；冲突子集不宣称全局最小，未受假设控制的全局约束和变量域仍作为背景条件。

~~~text
uv run --locked python -m scripts.correct_fish_development
uv run --locked python -m scripts.publish_development
uv run --locked python -m scripts.check_snapshot --root data/releases --release-id development-v3-rebased-v2-all --deny-network
~~~

第二条命令需要运行本地 Neo4j，完成真实100道图谱投影、快照校验及开发发布。第三条在新进程禁止网络和图数据库导入，读取已发布知识。实际已停止 Neo4j 验证100道离线加载成功，见 `benchmarks/reports/verification/P1-08-offline-snapshot.json`；这不等于P2调度或正式人工审核通过。

## 离线模型与本地 Neo4j

将凭据放在本地 `.env` 或环境变量，格式见 `.env.example`；环境变量优先。真实调用与默认测试分开，模型原始日志放在已忽略的 `data/extraction_archive/`，不包含 Authorization 请求头。

~~~text
uv run --locked python -m scripts.check_deepseek --model deepseek-flash
uv run --locked python -m scripts.check_deepseek --model deepseek-flash --recipe-id 662e07388b2aa2265e115477
docker compose --env-file .env -f deploy/compose.knowledge.yaml up -d
~~~

第二条命令会进行一次真实收费抽取。用户已选择 deepseek-flash；适配器使用 JSON 输出并显式关闭思考模式，参数与返回一起归档，见 [DeepSeek 官方模式说明](https://api-docs.deepseek.com/guides/thinking_mode/)。真实成功草稿和零外部调用重放的证据见 `benchmarks/reports/verification/deepseek-extraction.json`，仍为 NEEDS_REVIEW。

Neo4j Community 5.26.28 已部署，容器端口仅绑定127.0.0.1。已通过5项真实图谱测试和6项导出/属性测试；报告见 `P1-05-real-neo4j-tests.xml`、`P1-06-real-neo4j-tests.xml`。数据位于 `deploy/neo4j-data/`，停止服务使用 compose stop，保留数据。

旧版prepare_p0_samples已防止覆盖V2/V3草稿；V3导入同样拒绝覆盖人工审核记录。原始 CSV、设备资料、Word 和项目 ZIP 均不由脚本改写。项目 ZIP 未读取或解压。

## 契约边界

- SchedulingProblem 及其嵌套输入不可变，哈希绑定策略、菜单、知识、运行状态及时间原点；实际求解模型统计保存在独立 SolverBuildReport。
- 唯一人工为 human_1。物理部件和设备能力别名分开，未知时长保留为空，数量转换不跨质量/体积量纲。
- CandidateSchedule、ValidatedSchedule、PublishedPlan 分开。领域模块只定义数据与端口，导入不建立数据库连接或调用外部模型。
- CompetitionResponse 仅有官方规定五个顶层字段。完整日期保留在内部，HH:mm 只是展示投影。
- 纯 JSON Schema 无法从自然语言判断所有设备步骤；P5 Adapter 必须向 validate_response_for_request 提供权威计划中的设备步骤位置及完整时间区间。结构校验不等于独立调度可行性校验。
- 官方响应例子原样归档，仅用于协议结构测试；其烘烤与装盘先后存在疑点，不作为调度正确性依据。U01–U14 保留在 tests/fixtures/competition/source_manifest.json。

技术依据：[uv 锁定与同步](https://docs.astral.sh/uv/concepts/projects/sync/)、[Pydantic 模型](https://docs.pydantic.dev/latest/concepts/models/)、[OR-Tools CP-SAT](https://developers.google.com/optimization/cp/cp_solver)。
