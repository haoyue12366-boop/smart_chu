# task.md — 智能烹饪调度分阶段实现计划

> 给执行 AI：按单个任务逐项实现和验证；可使用 superpowers:executing-plans 工作流。用户明确选择多代理执行时再切换对应工作流。本计划本身不授权额外并行代理或公网发布。

版本：1.5  
日期：2026-10-02  
目标：将已批准的技术方案实现为可验证、可重放、可参加比赛的智能烹饪调度系统。  
架构：离线审核知识经图谱导出不可变快照；在线 Compiler → Greedy/CP-SAT → 独立校验 → 条件发布；事件更新事实后重排。  
技术栈：Python 3.12、Pydantic 2、FastAPI、OR-Tools、Neo4j、SQLite/SQLAlchemy、Vue 3/TypeScript/ECharts。工程工具与约束见 [AGENTS.md](AGENTS.md)。  
设计依据：[技术方案与架构设计 v1.5](2026-09-22-智能烹饪调度技术方案与架构设计.md)。  
使用者：逐任务交给 AI 开发；每项提供输入、输出、文件范围、接口、验收断言和阶段边界。

**当前工作（2026-10-06）：65 条失败的局部执行可靠性修复已完成验证。** 开启可选 TIGHT_HUMAN_V1 后，固定三菜单×10 轨迹×四组共 120 条全部完成；历史 65 条失败均有合法完成见证，原 55 条成功保持。timeSave 后同预算新基线为 56 成功/64 失败，净恢复 64 条、无新增失败；120 条真实终态重新编译并独立重扫。当前相关回归 288 项通过（旧历史源码身份检查的原失败保留，修正后 2 项复测通过），2 项既有正式门槛明确排除；Ruff/格式 497 文件、严格 mypy app 259 文件通过。36 次实际 HTTP 零失败/超预算，开启保护重排 P95 2641ms，19 次观察重排最大 2547ms，预算 3000ms。可选 Windows v2 包实际离线安装、初排/追加、前端及重启恢复通过。保守等待增加总用时，只有 34/120 条出菜差小于 300 秒，默认保护保持 NONE；不能据此认定排程质量或比赛优秀指标全部达标。旧资料、失败和包未覆盖，不运行用户省略的全量实验，完整 P6/正式比赛验收仍未完成。[本轮清单](../deploy/window_guard_fix_manifest_run1.json) 与[详细记录](#window-guard-fix)绑定当前实际证据。

**上一轮工作（2026-10-06）：快速重排 timeSave 局部修复已完成。** 六项验收通过：真实串行参考绑定当前问题并经独立校验，已知数值、实际快速路径、追加/取消/剩余时长/已执行事实和重复请求/服务重启一致性均验证。最终源码上统一回归 264 通过、0 失败，2 项既有正式审核/官方动态门槛明确排除且保留原失败；Ruff、格式和严格 mypy app 通过。相同五组真实菜单、两档预算、三轮 HTTP 配对，修复版 60 次请求零失败/超预算；3000ms 重排组缺参考失败 3→0，P95 2719→2906ms（增加 187ms）。新增可选快速 v2 策略为重排 3000ms、Solver 2100ms；默认 OPTIMIZE、原 v1 与历史报告保留。生产变更仅限 `app/scheduling/engine.py`。本轮不修复关键窗口 65 条失败、不重跑用户省略的全量实验；旧 Windows ZIP 保持旧版，修复交付于工作区。[本轮清单](../deploy/time_save_fix_manifest_run1.json)、[逐项证据](../benchmarks/reports/verification/P6-timesave-fix-run1/verification-summary.json) 与 [下方记录](#timesave-fix) 绑定实际源码和测试哈希，完整 P6/正式比赛验收仍未完成。

**上一轮工作（2026-10-06）：question.md 修复计划第 3–5 步已按用户调整后的范围完成。** 用户明确“不用全量”，本轮省略 28000 条扰动、340 场景和 240 次消融，完成 210 条配对验证、服务/UI、性能及纯 Windows 交付复验；完整 P6 阶段仍未验收。持续反馈、可选 SERIAL_RECIPE_V1 与 FEASIBILITY_FIRST 已实现，保留原 4200/2400ms 预算。66 项相关回归、17 项属性/状态机、53 项故障、74 项服务/UI、17 项前端组件和两项局部证据检查均通过，严格 mypy app 257 文件及 Ruff/格式/前端类型/构建通过；153 次性能请求零失败/超预算，新 Windows 包已实际离线安装、初排/追加、前端访问和两次启停恢复。持续反馈完成 17/30，快速单次和逐菜保护均为 14/30；完成率收益伴随显著出菜差，逐菜保护保持默认关闭。第 1、2 步证据与中间失败版本保留；同几何发布引用碰撞已修正，采用有界内存与 SHA256 冷重放。[本轮交付清单](../deploy/feedback_fix_manifest_run2.json) 明确部分实验仍有失败、非全量/非正式验收。详见 [P6-03 修复记录](#p6-03)。

**修复前开发验收（2026-10-05）：P6-01～P6-07 完成 Windows 开发技术验收，P6-08 为 BLOCKED。** 当时完整开发入口实际 DEVELOPMENT_VERIFIED，96 项包装检查通过、0 失败/跳过，Ruff、463 文件格式及 mypy app 256 文件检查通过；P5 八项回归和 P6 七项证据均通过。340/340 全量场景、153/153 三轮性能请求、17 项属性/状态机和 53 项故障检查已实际完成；240 次消融与 16000 条合成扰动的全部失败保留，缓冲不默认启用。纯 Windows ZIP 已独立离线安装、初排/追加、前端访问和两次启停恢复，运行不需要 Node、Neo4j 或 Docker。[原阶段归档](../data/verification/P6-development-gate-run2/phase-report.json)、[原 Windows 包](../data/delivery/P6-Windows-technical-run1.zip)、[原交付清单](../deploy/release_manifest.json) 保持历史版本身份，不作为窗口修复后的完整阶段验收。固定 development 发布不改标正式；正式 P1 full 与阶段、官方动态真实联调和公网目标仍未满足，用户已确认没有新增资料或部署目标，阶段不宣称正式全部通过。

**历史工作（2026-10-04）：P6 实施中，尚未完成最终验收。** 用户目标“完成P6做最后验收”，并明确“完全使用 Windows，调整 P6-07 验收要求”。P6-01 固定 100/200/20/20 共 340 场景清单已生成，首轮实际 333 场景通过、7 场景失败，所有结果与失败保留；不能据此标全量通过。Windows 候选包已真实离线安装、启动、初排/重排和重启验证，但候选代码尚未冻结为最终版本。正式发布/阶段、官方动态条件和公网目标保持单独核对；用户确认暂无新增资料和部署目标。详细证据见 [P6 实施记录](P6实施与验收记录.md)。以下较早“当前工作”按日期保留为历史。

**当前工作（2026-10-02）：P5 准备已完成，八项功能尚未开始。** 根据 `docs/答疑9.30.txt` 更新任务关联、单菜追加和协议未决项，已生成固定输入、38个设计场景及检查工具，见 [P5 准备基线](P5准备与接口基线.md) 和 [实际准备检查](../data/preparations/p5-v1/readiness.json)。状态为 READY_FOR_P5_IMPLEMENTATION_WITH_PROTOCOL_OPEN_ITEMS。用户明确选择本机 Node 25.8.1 / npm 11.11.0，替换前期 Node 24 选型。P4-01～P4-10 保持 DEVELOPMENT_VERIFIED：733 次测试执行通过、0 失败/跳过，1113.688 秒；质量和 100 份历史计划校验通过，历史证据不改写。

**历史工作（2026-09-29）：P4 准备已完成。** 用户明确授权 Codex 代为审核数据。新代理审核版为 `delegated-v3-p4-preparation-v1-all`，逐菜批准绑定工艺哈希与授权；参数及事件语义见 [P4 准备与规则基线](P4准备与规则基线.md)。累计人工时间新增为待实现优化目标，P4-01～10 仍为 NOT_STARTED。准备验证结果以 `data/preparations/p4-v1/readiness.json` 为准。后文此前日期的待审、阻塞与 active 描述保留为历史记录，不覆盖本条当前授权。

**P4 准备验证：** 新审核版真实 Neo4j 发布与文件重载通过；100/100 单菜调度通过、顶层待审数 0，100 份持久化计划重新独立校验通过；共享四开关 4/4 通过，审核及来源回归 21 项通过。18 类事件、15 个 P4 场景已形成设计数据。全量单菜服务内 P95=3672 ms、最大3843 ms，无超预算失败；此为模拟输入上的静态规划，不是 P4 重排性能结果。Ruff、格式与 mypy 通过，准备报告状态 READY_FOR_P4_IMPLEMENTATION。下一入口为 P4-01。

**当前状态（2026-09-28）：P1 / P2 开发通路完成验证，正式人工审核待办。** P1-01至P1-08、P2-01至P2-12为IMPLEMENTED；100道、1562工序已有真实图谱发布、离线读取及PlanningCore逐菜独立校验证据。P1-09保留IN_PROGRESS：开发全量求解100/100通过，但100道真实数据仍为NEEDS_REVIEW，未生成competition正式发布。P0-06审核门槛及P1/P2正式阶段状态不冒充VERIFIED。

**最终开发回归（2026-09-28）：** 本地Neo4j停止后，`SMART_COOKING_OFFLINE_TEST=1 uv run --locked pytest tests -m "not review_gate and not real_neo4j" --ignore=tests/integration/test_all_recipes_release.py -q`：552通过、12明确排除、0失败、1条合成枚举序列化警告，157.25秒。排除项为运行中Neo4j检查及正式人工审核门槛；真实Neo4j已另行通过5项投影及6项快照检查。100道单菜已在独立186.01秒检查中通过，本轮不重复运行。Ruff check、format --check及mypy app（115文件）通过。原 `P2.json` 保留FAILED原始记录：包含真实审核缺失、当时未执行停库检查、已修复的格式和共享测试进程预热问题；不得将该历史报告改写为正式通过。最终开发汇总为 `benchmarks/reports/verification/P1-P2-development-final.json`，本轮完整XML为 `P1-P2-final-development-tests.xml`。测试场景现在各自确认预热，连续作业及故障恢复另行覆盖；未放宽请求预算。100个合成ID的正式发布证据集成测试另行1通过（6.26秒），真实competition发布仍待人工审核。最终补充修正审核待办计数：按绑定整个路径内容哈希的菜谱批准判断，不批量改写原始模型建议；相关4项通过（8.84秒），见P1-09-review-binding-tests.xml，真实待审仍为100道。

**用户确认（2026-09-27）：** 本次目标明确为按阶段顺序的 **P1＋P2**。允许先用待审 V3 数据完成开发和测试，所有真实数据保持 NEEDS_REVIEW，正式人工审核验收列为待办；允许部署本地 Neo4j；历史 V2 和时间补全 JSON 缺失时，以现有 V3 建立新的开发版本并记录来源缺口。这个授权允许开发依赖接入，不表示 P0 人工审核门槛、sample / competition 正式发布条件已经满足。开发产物与验收使用明确 development 标识，不改写旧来源，不伪造 APPROVED。

**用户确认（2026-09-28）：** 同意鱼头菜热水工艺修正。新版本 development-v3-rebased-v2 保留既有热水准备及原文15秒人工烫洗，移除错误蒸箱预热与取出，接续物料与真实蒸制；修正前后内容及授权保存在 data/development/ 下。秒级知识检查100道通过，分钟网格仍有214项不兼容，21道可表达；没有延长原文工艺时间。

**时间输出更新（2026-09-28，用户优先）：** 用户随后明确“秒级调度可以使用小数分钟来表示”。P2默认秒级网格、小数分钟投影；最多六位小数并复核回读整数秒，15秒→0.25分钟、20秒→0.333333分钟。显式整数分钟兼容模式仍拒绝无法表达的工艺。原始接口资料不改写；P5对接时需显式选择小数分钟模式，原integer设备时间契约不能悄悄宣称已经符合官方。

**执行衔接记录（2026-09-28）：** P1→P2使用已发布development快照，版本、规则和证据一致；固定菜内预约/程序作为额外领域契约保留，P0的CandidateCarrier新增provenance_refs，SchedulingProblem新增物料流/强制程序/不可用区间。运行工序新增有来源的remaining_sec，库存新增quality_status，未知状态不默认合格。目录无Git，沿用用户工作目录和本任务记录，不创建虚构提交或分支。新启用executing-plans/TDD技能沿用既定计划；正式审核待办不撤销已获授权的开发依赖接入。

**P3委托启动（2026-09-28）：** 用户明确授权“全权替我完善数据审核和参数设计，尽快开始下一阶段任务”。沿用已批准架构与逐项计划，授权范围推进到P3；模型委托复核不伪装HUMAN或实测，development许可继续有效。13项共享复核结论及首批S02/H02参数已存data/issues/p3_delegated_review.md，授权与结构化规则存data/development/。P3-01为IMPLEMENTED，P3整体IN_PROGRESS，P3-02至07未实施；正式P1/P2人工门槛继续如实保留。开发兼容与契约检查110通过、1项人工门槛明确排除；含门槛的原运行109通过/1失败报告保留。未声称已启用共享排程、取得真实共享收益或通过整个P3。

**P3准备衔接（2026-09-28）：** 已生成data/preparations/p3-v1/中的100道规范化输入、逐菜哈希迁移核对、共享实施蓝图及后续用例；只迁移技术版本，1562工序原工艺不改。参数采用data/development/p3-delegated-review-v2.json：H02补回两次设置/补水各60秒，完整开发预约从1380修正为1500秒，旧记录保留。准备/蓝图/兼容测试27通过（P3-preparation-tests.xml），相关回归53通过（P3-preparation-regression.xml），Ruff check、format --check及mypy app通过。真实Neo4j准备发布与新版本离线100道编译/两菜基线检查尚未完成：Docker Desktop因dockerInference及engine.sock残留通信文件启动失败；已保存运行目录备份，未改动数据卷。用户已选择重启Windows，待其通知后继续服务启动、prepare_p3 --publish及check_p3_preparation。完整记录见data/preparations/p3-v1/docker-recovery.md。P3仍IN_PROGRESS，P3-02～07未实施，不能标记全部准备或共享验收完成。

**P3准备完成记录（2026-09-28）：** Docker恢复后，使用已保存且哈希/全部来源校验通过的输入完成真实Neo4j投影、快照导出与独立目录发布（data/preparations/p3-v1/releases），并经纯文件重载；原P2运行指针字节不变。100道/1562工序/2条开发规则已发布，100道离线编译、2组兼容及两菜单独Greedy基线独立校验通过（17.824秒，非4200ms服务性能验收）。真实快照回归5通过（P3-preparation-real-snapshot.xml，52.80秒），离线集成1通过（P3-preparation-offline-recovered.xml，11.41秒）；首次旧临时目录权限失败报告保留。Ruff/format对app scripts tests检查通过203文件，mypy app通过120文件。P3-01登记追加准备代码、v2参数及对应测试；未执行或声称整个P3阶段验收通过。入口data/preparations/p3-v1/README.md；准备完成，共享主链P3-02～07仍待实施。普通Codex执行环境初始化故障仍在，本轮使用备用执行方式及可读准备输入完成；详情见docker-recovery.md。

**完整P3目标推进（2026-09-28）：** 用户目标为完成整个P3，目标保持active。现P3-01～03为IMPLEMENTED，P3-04～07尚未实现，阶段仍IN_PROGRESS，不能以本轮共享前处理替代整个P3。最新联合180通过、1项人工门槛按已有开发授权单列；Ruff/format检查210文件通过，mypy app124文件通过。原P2发布目录对当前执行方式不可读，初始回归31项在读取夹具时失败（P3-02-regression.xml保留）；从真实Neo4j导出与原运行snapshot_id完全一致的测试副本到.tools/p2-regression/releases，证据P3-baseline-test-copy.json。测试通过显式SMART_COOKING_TEST_RELEASE_ROOT使用该副本且再次核验原指针身份，未改原数据、权限、断言或审核状态。下一步按既有H02的1500秒蓝图实现共同热批次，并扩展顺序转换、候选限额和完整阶段实验。

**P3开发目标完成（2026-09-29）：** P3-01～07全部完成开发实现，`scripts/verify.py --phase P3 --gate development`返回DEVELOPMENT_VERIFIED：511项通过、0失败、0跳过，581.532秒；补充验收工具9项通过。此为已授权待审数据的开发验收，未把真实人工审核、competition正式发布或P4动态事务标为完成。48次四开关实验为36次完整成功及12次预设设备不可用；真实双菜热合批减少390秒，人工用时不变，迟到场景强行合批反而慢270秒。报告与限制详见P3-07及`benchmarks/reports/P3-shared-ablation-v1/README.md`。原P2活跃快照及原CSV保持不变。

<a id="task-navigation"></a>
## 1. 总体拆分与阶段边界

共 7 个阶段、61 个任务，沿用原设计 P0—P6 编号。一个任务是可以独立验证和交接的最小功能交付；任务数不等同于文件数、提交数或固定工时。

| 阶段 | 任务数 | 核心交付 | 阶段出口与边界 |
| --- | ---: | --- | --- |
| [P0](#p0) 工程与领域契约 | 7 | 环境、类型、契约、来源、审核样本、接口测试 | 类型和样本可验证；不交付伪造排程或业务 API |
| [P1](#p1) 离线数据与知识发布 | 9 | 导入、LLM 归档、审核、图谱、快照及发布 | core 打通审核样本与断图读取；full 完成 100 道并由 P2 真实逐菜验证 |
| [P2](#p2) 基础可行调度 | 12 | Compiler、Greedy、CP-SAT、独立校验、目标、预算与诊断 | 无跨菜共享的真实规划主链可运行；原菜谱强制批次和介入已经完整 |
| [P3](#p3) 跨菜共享优化 | 7 | 共享切配、共同热批次、数量与配置转换 | 共享作为可选优化，正确性和单独回退保留；不开放任意未审核中途加入 |
| [P4](#p4) 执行状态与重调度 | 10 | SQLite、事件、库存、冻结、版本发布与异常恢复 | 内部会话真实推进和重排；失败不破坏物料及历史，不推定官方动态语义 |
| [P5](#p5) 接口、语言与展示 | 8 | 官方 Adapter、内部 API、SSE、语言入口、甘特图与反馈 | 真实后端和 UI 联调；明确官方未确认事项，不用 mock 通过端到端验收 |
| [P6](#p6) 全量评估与交付 | 8 | 全量回归、消融、扰动、属性、故障、压测和 Windows 部署 | 全量数据与固定版本的证据闭环；外部协议、环境和发布权限形成最终门槛 |

### 1.1 推荐执行顺序及依赖

~~~mermaid
flowchart LR
    P0["P0 契约与审核样本"] --> P1C["P1-01 至 P1-08：core"]
    P1C --> P2["P2 基础真实调度"]
    P2 --> P1F["P1-09：100 道 full"]
    P2 --> P3["P3 跨菜共享"]
    P2 --> P4B["P4 基础仓储与事件"]
    P3 --> P4["P4 完整重排与恢复"]
    P4B --> P4
    P4 --> P5["P5 API 与展示"]
    P1F --> P6["P6 全量评估与交付"]
    P5 --> P6
~~~

默认按阶段推进；具体任务以前置 ID 为准，不按数字盲目执行。P1-09 对 P2 的依赖是有意设计：没有真实调度器就不能宣称 100 道全部可排。P2 只依赖 P1 core，因此不存在依赖环。P4-01、P4-02、P4-04 等基础工作在 P2 后具备入口，但完整 P4 验收必须包含 P3 的共享状态。

P1-09 内的数据补齐可以在 P2 完成前进入 IN_PROGRESS；其列出的 P2-12 是全量求解验收的必需依赖，不是开始整理数据的前提。等待人工依据时保持该任务未验收，不阻止样本通路已经完备的算法与接口开发。所有阶段号表示能力边界，不要求后台自动并行执行。

### 1.2 全局约束

每个任务隐含遵守 AGENTS.md 全部规则，以下数值不得在任务内私自改写：

- 固定 100 道菜谱，以 ID 为身份；2 灶眼，人工固定为唯一 human_1、容量 1；human_count 仅允许整数 1，不开放多人员配置；其他设备不额外限制容量。
- 内部整数秒，默认网格 1 秒、小数分钟输出（用户已确认）；整数分钟兼容另行检查，不能独立舍入工艺时长；完成差项目目标 240 秒，官方优秀要求小于 300 秒。
- 初排总预算 4200 ms、Greedy 150 ms、CP-SAT 3000 ms；重排分别为 2400、100、1500 ms。
- 默认一个 API 实例、一个并发调度作业、一个 Solver 工作进程，搜索线程最多 4。
- SQLite busy_timeout 100 ms、最多写重试 2 次；深度诊断离线 2000 ms、单线程、最多 6 次冲突缩减检查。
- 非单独候选每需求最多 12 个、每问题最多 256 个；强制菜内工艺与单独规范代表不被优化截断删除。
- 初始模型软门槛：总变量 6000、布尔变量 4000、可选区间 1500、约束 20000、顺序边 8000。
- 时长默认 NOMINAL；非等价支配默认关闭，仅按已证明的白名单替换规则开放。通用跨菜蒸烤只实现 STRICT_TOGETHER，不开放自由跨菜 COMMON_START/COMMON_FINISH；原菜谱固定的中途加入、取出和分批仍必须保留。
- P2 使用显式开发基线策略关闭跨菜共享；P3 验收通过后使用新的策略版本开启已支持共享。关闭优化不关闭原菜谱强制批次或介入。
- 所有实际设备阶段遵循[架构 8.6 全设备竞争矩阵](2026-09-22-智能烹饪调度技术方案与架构设计.md#s08-6)，统一 physical_resource_id/component_id 和竞争策略；同一腔体的能力别名不能并行冒充两台设备。
- 蒸烤跨菜共批要求最终有效加热时长相同并满足温度、模式、湿度和工艺兼容；冰箱按温区和共同配置共享，允许不同时长。设备竞争约束由排程决定顺序，不在知识图谱中按菜谱 ID 人为固定先后。

### 1.3 优先检查的五类跨模块问题

| 容易遗漏的输入或故障 | 用户应得到的行为 | 首次实现及验证任务 |
| --- | --- | --- |
| 同名异 ID、重复菜单实例 | 任务与物料身份不混淆 | P0-02、P2-01、P5-02 |
| 秒级步骤、长腌制和跨日 | 真实依赖、热时长和日期保留 | P2-03、P5-02、P5-06 |
| 工序失败而设备尚未清空 | 不提前复用设备或恢复已耗原料 | P4-08、P4-09 |
| 求解期间状态改变、提交响应丢失 | 拒绝旧结果，重试不重复发布 | P4-07、P6-05 |
| 合成可行性测试、UNKNOWN 与真实不可行混淆 | 结论符合证据，失败不生成假计划 | P2-12、P6-04、P6-06 |

<a id="execution-rules"></a>
## 2. AI 逐任务执行规则

### 2.1 一项任务的完整工作循环

1. 读取任务、前置任务证据及原设计对应章节，确认目标输入真实存在。
2. 检查目标文件的现有实现；只在任务明确负责的接口上扩展，复用已经建立的契约。
3. 将本任务“验收断言”写入列出的测试文件。涉及行为时先运行，确认失败确由待实现行为引起。
4. 实现“实现要点”，保持文件范围和边界；必要的内部函数分解由执行者决定，不能改变公共接口语义。
5. 运行本任务命令、受影响的回归、Ruff 和 mypy；修改前端时运行 TypeScript、对应测试和构建。修复明确失败后再记录结果。
6. 检查是否只靠 mock、遗漏强制路径或修改了基准；把实现结果、版本、命令和证据写回本任务的验证记录。
7. 满足全部断言后将任务改为 VERIFIED；到阶段最后一个必需任务时执行阶段门槛命令。按照用户授权范围继续或交付。

任务中的验收断言是最低必测行为，并非允许忽略同一业务路径中的其他边界。一个测试文件可包含多项参数化或属性测试。所列接口是跨任务交接点，内部函数和辅助类型可以合理细分。

执行步骤不要求每完成一个测试文件就询问用户。缺少实际工艺审核、外部协议或发布授权时记录具体阻塞；其余独立任务可在已有授权内推进。

### 2.2 状态与证据

| 状态 | 含义 | 允许的结论 |
| --- | --- | --- |
| NOT_STARTED | 尚未实现 | 只有计划，没有验证结果 |
| IN_PROGRESS | 正在实现或补充数据 | 不能认定交付完成 |
| IMPLEMENTED | 代码已经存在，必需验证尚未齐全 | 不能认定验收通过 |
| VERIFIED | 所有任务条件和相关回归通过，证据已记录 | 可以成为后继任务的有效依赖 |
| BLOCKED | 某项必需输入、环境或确认缺失，已说明原因 | 保留未完成状态，不能以跳过检查结项 |
| CORE_VERIFIED | 仅用于 P1 阶段：01–08 通过，09 尚未通过 | 允许样本开发，不代表全量正式数据完成 |

每个任务的验证记录起始为“未执行；无验收证据”。首次执行后替换为实际记录，至少包含：

~~~text
task_id / status / executor / started_at / finished_at
code_revision 或代码内容指纹
knowledge_version / rule_version / policy_version / source_hashes
实际运行命令、退出码、测试收集数、通过/失败/跳过数
集成服务类型、真实服务或测试替身、硬件和运行模式
report_paths / random_seed / elapsed_ms
本次修改摘要、接口变化、遗留限制及阻塞原因
~~~

执行时间戳和代码版本不能在仅编写计划时预填成已完成。尚未建立 Git 时用内容指纹，不为填写 revision 编造提交号。阶段报告放入未来实现生成的 benchmarks/reports/verification/，任务文档只保存引用和结论。

### 2.3 通用验收命令与阶段登记

P0-01 建立 scripts/verify.py；每个任务实现后将其真实测试和产物条件登记进 scripts/verification_manifest.json。验证器按明确依赖选择检查，不简单地把所有更小阶段号视为已完成。

~~~text
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app
uv run --locked python scripts/verify.py --phase P0
uv run --locked python scripts/verify.py --phase P1 --gate core
uv run --locked python scripts/verify.py --phase P1 --gate full
uv run --locked python scripts/verify.py --phase P2
uv run --locked python scripts/verify.py --phase P3
uv run --locked python scripts/verify.py --phase P4
uv run --locked python scripts/verify.py --phase P4 --gate development
uv run --locked python scripts/verify.py --phase P5
uv run --locked python scripts/verify.py --phase P6
~~~

P2–P5 检查依赖 P1 core，不能误要求 P1-09 已经结束；P6 强制依赖 P1 full。验收器必须检查必需测试和数据确实存在、收集数非零、不能静默跳过服务检查；缺项返回非零。阶段检查可重用当前代码与数据哈希完全相同的有效前置报告，输入改变则重跑受影响检查。

前端由 P5-06 建立以下命令及浏览器测试基础配置，P5-07 添加完整执行流程，P5-08 完成最终联调：

~~~text
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web run test:unit
npm --prefix web run test:e2e
npm --prefix web run build
~~~

本地组件测试选用 [Vitest](https://vitest.dev/guide/)，浏览器端到端测试选用 [Playwright](https://playwright.dev/docs/intro)。test:unit 使用非交互单次运行，test:e2e 默认连接真实测试后端；接口 mock 测试另行标记。

<a id="interfaces"></a>
## 3. 公共接口基线与文件所有权

### 3.1 核心输入输出

P0-04 先定义纯领域契约，下面的实现分别由任务交付。签名是语义基线；构造函数负责依赖注入，不把数据库对象作为每次 Solver 调用参数。

| 接口 | 输入 → 输出 | 实现责任 |
| --- | --- | --- |
| KnowledgeRepository.load / select | ReleaseRef → SnapshotHandle；菜谱 ID 元组 → MenuKnowledgeView | P1-08 |
| LLMProvider.extract | ExtractionRequest → ExtractionResponse，调用状态与归档身份独立 | P1-02 |
| ProblemCompiler.compile | knowledge、menu、runtime、policy、deadline → SchedulingProblem 或 CompilationFailure | P2-04 |
| ScheduleValidator.validate | knowledge、runtime、problem、candidate → ValidationReport | P2-06 |
| GreedyScheduler.solve | problem、deadline → GreedyResult | P2-07 |
| CpSatScheduler.solve | problem、hint、deadline → SolveResult | P2-08 |
| PlanningEngine.plan | problem、knowledge、runtime、deadline → PlanningResult | P2-10 |
| PlanningCore.compute | request、knowledge、runtime、deadline → PlanningResult | P2-11 |
| InfeasibilityAnalyzer.diagnose | problem、build_report、failure_stage、deadline → DiagnosticReport | P2-12 |
| RuleEngine.evaluate_group | members、context → CompatibilityDecision | P3-01 |
| RuntimeService.apply_event | RuntimeEvent → EventApplyResult | P4-02 |
| ReplanningService.compute | ReplanRequest、deadline → PlanningResult | P4-06 |
| PlanPublisher.publish | 已验证候选、PublishContext → PublishedPlan 或 PublishConflict | P4-07 |
| NotificationService.prepare | 已发布计划、运行状态 → 通知元组 | P4-10 |
| CompetitionAdapter.to_response | PublishedPlan、投影上下文 → CompetitionResponse | P5-02 |
| IntentService.interpret | 文本、会话与版本上下文 → IntentResult | P5-04 |
| BenchmarkRunner.run | suite、release、policy、seed → BenchmarkReport | P6-01 |

接口边界数据不得定义在 infrastructure 实现里反向供 domain 导入。P0-04 在 domain 下建立 scheduling_problem、runtime_snapshot、schedule、reports、ports、knowledge 等模型；LLM 输入/输出与调用元信息在 P0-05/P1-02 的 domain/extraction.py 定义。后续新增报告或模型在负责任务中显式扩展领域契约并同步调用方。

| 关键契约 | 最低内容 |
| --- | --- |
| MenuKnowledgeView | 固定知识/规则/snapshot 身份、选中菜谱原知识、工序/物料/设备/规则及来源索引 |
| SchedulingPolicy | policy_version、时间网格、资源假设、候选限制、阶段目标、开关、预算与时长策略身份 |
| RuntimeSnapshot | session_id、state_revision、原点、事件时刻、实际执行、物料账、设备可用性与占用、当前计划引用 |
| SchedulingProblem | 不可变问题身份、版本、实例与需求、候选、资源、物料、时间、冻结事实、目录及模型预估 |
| PlanningResult | 状态、完整候选或失败、校验结果、指标/界、阶段耗时、模型/诊断引用；尚未持久化不称 PublishedPlan |
| PublishContext | 当前会话、base_state_revision、base_plan_version、knowledge/snapshot 身份、请求/发布去重身份 |
| EventApplyResult | 事件身份、是否首次生效、处理状态、新 state_revision、事实变化、是否需要重排及拒绝原因 |
| DiagnosticReport | 原求解状态、目标阶段、约束证据、基础可行性、截断/时间域因素、是否完成及最小性证明标志 |
| ExtractionRun | 原文/提示/契约/参数与模型信息、原始返回引用、处理版本、审核补丁与发布追溯 |
| VerificationReport | 阶段/任务、命令、版本、用例数、结果、跳过、耗时和产物哈希；不得将未运行写成通过 |

### 3.2 文件所有权与交接规则

| 文件组 | 首次建立 | 后续扩展边界 |
| --- | --- | --- |
| app/domain/ 与基础 Protocol | P0 | 后续任务可扩展自己负责的领域概念，公共字段变化带版本与回归 |
| app/pipeline/、app/knowledge/ | P1 | P3 使用内存规则接口，在线路径不反向引入图数据库连接 |
| app/compiler/、app/scheduling/、app/validation/ | P2 | P3 增共享，P4 增运行状态，P6 增声明的时长策略及调优 |
| app/storage/、app/runtime/ | P4 | P5 暴露接口与通知；不在 API 重写状态规则 |
| app/services/planning_core.py | P2 | 只计算；P4 的 planning/publishing 编排持久化，避免两套发布逻辑 |
| app/api/ | P5 | 官方和内部协议分开；已发布计划是唯一权威输出 |
| web/ | P5 | 只展示和提交事件；不持有权威库存和设备状态 |
| scripts/verify.py、verification_manifest.json | P0 | 每任务增登记与检查；不包含业务调度逻辑 |
| tests/、benchmarks/ | 从 P0 持续建立 | P6 聚合系统级评估；不得后移所属模块的基础正确性测试 |

任务列出的目录路径表示该任务负责的一组同职责文件，执行时在记录中列出实际文件。必要的 __init__.py、包导出、配置登记和同模块测试辅助文件属于配套修改；不能以此为由改动无关模块。

<a id="stage-status"></a>
## 4. 阶段状态和验收门槛

| 阶段 | 当前状态 | 已验收任务 | 验收证据 |
| --- | --- | --- | --- |
| P0 | IN_PROGRESS | 5 / 7 | [阶段报告](../benchmarks/reports/verification/P0.json)：122 项，121 通过 / 1 审核门槛失败 / 0 跳过 |
| P1 | IN_PROGRESS | 0 / 9 | 开发发布及真实Neo4j已验证，100道开发排程通过；正式人工审核待办，详见顶部最终开发记录 |
| P2 | IN_PROGRESS | 0 / 12 | 12项开发实现完成，552项最终开发回归通过；正式前置人工审核待办，详见顶部证据 |
| P3 | IMPLEMENTED | 7 / 7（开发验收） | 全部开发任务通过；正式人工审核与competition发布仍待办 |
| P4 | DEVELOPMENT_VERIFIED | 10 / 10（开发验收） | 733 项执行通过、0 失败/跳过；当前性能及限制见 P4 最终记录 |
| P5 | DEVELOPMENT_VERIFIED | 8 / 8（开发验收） | 当前 P6 开发入口完整复验八项接口/前端回归；正式前置与外部协议验收保持独立 |
| P6 | BLOCKED | 7 / 8（开发技术验收） | P6-01～P6-07 当前完整入口通过，Windows 包真实离线安装与重启；P6-08 缺正式发布/阶段、官方联调及公网条件 |

每个阶段的入口、产物、边界与出口在下一节阶段开头列出。不能因为“代码文件已创建”直接更新上表。任务全部通过而阶段集成检查未执行时，阶段仍保持 IN_PROGRESS。

P6 的性能目标验收使用固定单并发、正式数据和已声明硬件，至少 3 轮重复结果均保留。要求所有成功计划合法，明确可行的验收场景成功完成，服务内观测最大耗时达到 4200/2400 ms 目标；另核对官方优秀 <5000/<3000 ms 的已确认计时口径。P50/P95/最大值、失败和回退同时报告。计时口径未确认或有未处理超限时，不把优秀性能目标标为通过；用户若调整目标，应先记录新策略和验收基线。

## 5. 逐阶段任务

<a id="p0"></a>
## P0 工程与领域契约（7 项任务）

**阶段入口：** 已确认架构 v1.3、现有原始材料和本任务计划；不依赖已整理的完整 100 道数据。

**阶段产物：** 可重复的开发环境、统一类型与协议、来源审核链、12 道代表审核样本与合成反例。

**验收出口：** P0-01 至 P0-07 全部 VERIFIED；质量入口实际执行，契约往返、身份/单位/时间及样本来源检查通过。样本尚需工艺审核时阶段未通过，不得把模型建议标为人工批准。

**范围边界：** 不启动业务排程、不伪造比赛成功响应、不要求完成全量数据或前端。

**交接内容：** 把版本化契约、样本 manifest、问题清单和验收报告交给 P1。

**阶段验收命令：**

~~~text
uv run --locked python scripts/verify.py --phase P0
~~~

| 任务 | 交付内容 | 当前状态 |
| --- | --- | --- |
| [P0-01](#p0-01) | 建立工程环境与可执行质量入口 | VERIFIED |
| [P0-02](#p0-02) | 定义身份、版本、时间和数量基本类型 | VERIFIED |
| [P0-03](#p0-03) | 定义菜谱、工序、设备及物料知识契约 | VERIFIED |
| [P0-04](#p0-04) | 冻结算法、运行事件和计划交换契约 | VERIFIED |
| [P0-05](#p0-05) | 实现字段来源、审核记录和数据问题契约 | VERIFIED |
| [P0-06](#p0-06) | 建立代表性审核样本与合成反例 | BLOCKED |
| [P0-07](#p0-07) | 建立比赛协议契约及阶段验收登记 | IMPLEMENTED |

<a id="p0-01"></a>
### P0-01 建立工程环境与可执行质量入口

**状态：VERIFIED。** 前置任务：无。

**输入：** 架构技术栈和本机 Python 3.12 / Windows 目标（2026-10-04 用户修订）；不依赖菜谱已经整理完成。

**输出：** 可导入的 app 包、固定依赖、Ruff/mypy/pytest 配置、验收命令执行器及非零失败规则。

**生产/数据文件范围：** `pyproject.toml`、`uv.lock`、`.python-version`、`.gitignore`、`.env.example`、`scripts/verify.py`、`tests/conftest.py`。

**测试文件：** `tests/unit/test_environment.py`、`tests/unit/test_verify_runner.py`。

**接口约定：** verify.py --phase P0；命令结果记录 command、exit_code、elapsed_ms、collected_count。

**实现要点：** 创建必要包入口并验证 Pydantic、CP-SAT、SQLAlchemy 等基础兼容；建立离线测试默认配置。阶段检查登记随后续任务增加，不能为未实现阶段返回成功。

**验收断言：**

- [x] 在锁定环境运行一项实际测试和一个 CP-SAT 最小可行实例；记录 Python 与依赖版本。
- [x] 验收子命令退出非零、测试集为空或阶段未登记时，verify.py 均失败。
- [x] 无凭据导入 app 不触发连接、模型调用或子进程；Windows 路径和含中文路径测试通过。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_environment.py tests/unit/test_verify_runner.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不创建业务成功响应、不连接收费模型、不搭建前端和运行库；仅建立最小可验证工程入口。

**设计定位：** 架构文档 3.2、4.3、20.1。

**验证记录（2026-09-23，执行者 Codex）：** Python 3.12.14 工程、精确依赖锁、离线导入及验收器完成。真实 CP-SAT 最优解与 SQLite 内存查询通过。验收器拒绝失败、空集、跳过及缺产物，质量失败不标 VERIFIED，显式区分 P1 core/full 前置依赖。

上列任务命令已实际执行，并由阶段验收器复核；10 项收集，10 通过、0 失败、0 跳过，退出码 0。共同质量检查 Ruff、格式检查、mypy 均退出 0。每条实际命令、开始/结束时间、耗时、平台和文件指纹见 [P0.json](../benchmarks/reports/verification/P0.json)。Schema 1.0，样本 development-scheduling-v3（AI建议，未发布），策略测试基线 p0-baseline-v1；没有正式知识/规则发布。来源哈希见 fixture_manifest.json 及报告 artifact_hashes。

<a id="p0-02"></a>
### P0-02 定义身份、版本、时间和数量基本类型

**状态：VERIFIED。** 前置任务：[P0-01](#p0-01)。

**输入：** 固定菜谱 ID、执行实例身份及整数时间和物料量约束。

**输出：** 带语义的 ID 类型、版本引用、TimeOrigin、整数秒与网格工具、ScaledQuantity、ErrorCode。

**生产/数据文件范围：** `app/domain/ids.py`、`app/domain/time.py`、`app/domain/quantity.py`、`app/domain/errors.py`。

**测试文件：** `tests/unit/test_domain_primitives.py`。

**接口约定：** align_up(value_sec: int, grid_sec: int) -> int；align_down(value_sec: int, grid_sec: int) -> int；ScaledQuantity(value: int, unit: str, scale: int)。

**实现要点：** 拒绝非法单位和非正缩放，明确同量纲换算及有界整数；区分实际时间戳与相对排程偏移，哈希排除与语义无关的生成时间。

**验收断言：**

- [x] 两个同名不同 recipe_id 不混淆；不同类型 ID 不被业务函数按名称互换。
- [x] 跨日保持完整日期；60 秒网格上 61 秒下界为 120、上界为 60；区间 [0,60) 与 [60,120) 不冲突。
- [x] 不把小数直接强转 int 丢失物料；质量与体积无审核换算时拒绝相加。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_domain_primitives.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不定义新工艺时长，不把所有数量统一成未经依据的克数。

**设计定位：** 架构文档 6.2–6.3、8.4–8.5。

**验证记录（2026-09-23，执行者 Codex）：** 身份、版本、严格整数秒、带时区原点、半开区间和精确数量完成。验证跨日、夏令时、网格上下界、不截断小数及不跨量纲相加。Hypothesis 以确定性模式验证 100 个网格样本。

上列任务命令已实际执行，并由阶段验收器复核；12 项收集，12 通过、0 失败、0 跳过，退出码 0。共同质量检查 Ruff、格式检查、mypy 均退出 0。每条实际命令、开始/结束时间、耗时、平台和文件指纹见 [P0.json](../benchmarks/reports/verification/P0.json)。Schema 1.0，样本 development-scheduling-v3（AI建议，未发布），策略测试基线 p0-baseline-v1；没有正式知识/规则发布。来源哈希见 fixture_manifest.json 及报告 artifact_hashes。

<a id="p0-03"></a>
### P0-03 定义菜谱、工序、设备及物料知识契约

**状态：VERIFIED。** 前置任务：[P0-02](#p0-02)。

**输入：** 原始菜谱结构、设备清单和原子工序拆分规则。

**输出：** CanonicalRecipeModel、OperationTemplate、MaterialSpec、DeviceProfile、DeviceInstance、ResourceUse、ProcessingRule 及 JSON Schema。

**生产/数据文件范围：** `app/domain/canonical_recipe.py`、`app/domain/material.py`、`app/domain/resources.py`、`app/domain/processing_rules.py`。

**测试文件：** `tests/contract/test_canonical_contract.py`。

**接口约定：** CanonicalRecipeModel.model_validate(payload)；知识对象包含 provenance_refs、review_status、输入输出和最小/最大时间关系。

**实现要点：** 表达人工阶段、被动等待、固定菜内批次、热加工端口、损耗与物料规格；定义 physical_resource_id/component_id、竞争策略、配置范围和占用阶段；Schema 校验与领域可行性检查分开。

**验收断言：**

- [x] 包含并行前处理、明确两批蒸制和中途加料的样例能无损往返序列化。
- [x] APPROVED 路径的未知必需时长、非法枚举和悬空工序引用形成明确错误；DRAFT 允许保留问题字段，不静默转成默认零。
- [x] 设备模式、单位和规格身份可引用，名称相同但规格不同保持区别。

- [x] 设备实例、能力别名和物理部件分开引用；正式执行资源缺少物理映射或竞争策略时不能通过契约校验，草稿允许带问题保留。

**任务验证命令：**

~~~text
uv run --locked pytest tests/contract/test_canonical_contract.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 此任务定义表达能力；设备路径的完整领域判定在 P1-04 实现。

**设计定位：** 架构文档 5.2–5.4、6、8。

**验证记录（2026-09-23，执行者 Codex）：** 菜谱、原子工序、物料、固定批次和中途介入、物理设备及竞争策略契约完成，导出 JSON Schema。草稿保留未知量；批准路径拒绝未知必需时长和悬空引用。完整领域可行性仍属 P1-04。

上列任务命令已实际执行，并由阶段验收器复核；9 项收集，9 通过、0 失败、0 跳过，退出码 0。共同质量检查 Ruff、格式检查、mypy 均退出 0。每条实际命令、开始/结束时间、耗时、平台和文件指纹见 [P0.json](../benchmarks/reports/verification/P0.json)。Schema 1.0，样本 development-scheduling-v3（AI建议，未发布），策略测试基线 p0-baseline-v1；没有正式知识/规则发布。来源哈希见 fixture_manifest.json 及报告 artifact_hashes。

<a id="p0-04"></a>
### P0-04 冻结算法、运行事件和计划交换契约

**状态：VERIFIED。** 前置任务：[P0-03](#p0-03)。

**输入：** 领域基本类型和架构模块契约。

**输出：** RuntimeEvent、RuntimeSnapshot、SchedulingProblem、CandidateSchedule、ValidationReport、SolveResult、SolverBuildReport、PlanningFailure 及端口 Protocol。 并定义 ReleaseRef、SnapshotHandle、MenuKnowledgeView、RecipeInstance、SchedulingPolicy、PublishContext 与 EventApplyResult 的跨模块字段。

**生产/数据文件范围：** `app/domain/events.py`、`app/domain/runtime_snapshot.py`、`app/domain/scheduling_problem.py`、`app/domain/schedule.py`、`app/domain/ports.py`、`app/domain/reports.py`、`app/domain/knowledge.py`、`app/domain/policy.py`。

**测试文件：** `tests/contract/test_runtime_problem_contract.py`。

**接口约定：** 核心接口名称及参数以本文件“公共接口基线”表为准；SchedulingProblem 不含 ORM、数据库连接或求解器对象。

**实现要点：** 区分原始候选、经验证候选与已发布计划；定义预期版本、状态来源、失败分类和不可变问题哈希；扩展字段需版本化。

**验收断言：**

- [x] SchedulingProblem 序列化后哈希稳定，修改策略/状态/菜单改变哈希；实际建模报告不改写问题。
- [x] FEASIBLE、UNKNOWN、INFEASIBLE、MODEL_INVALID 可独立表示；失败没有伪造的完整成功数据。
- [x] 事件必需 event_id、source、occurred_at 和预期版本；异常可明确表示尚未确认释放和未知恢复时刻。
- [x] human_count 只能为整数 1，人工资源只能是 human_1；配置为 0、2 或包含第二个人工资源的请求明确拒绝。

**任务验证命令：**

~~~text
uv run --locked pytest tests/contract/test_runtime_problem_contract.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 只定义契约与校验，不在 domain 中启动算法或落库；后续任务实现对应端口。

**设计定位：** 架构文档 4.2、9.1、15、16、19。

**验证记录（2026-09-23，执行者 Codex）：** 不可变问题、运行事件/快照、候选/已校验候选/已发布计划、知识、策略和公共 Protocol 完成。哈希绑定菜单、知识、策略、状态与原点，实际模型报告独立；严格固定 human_1；故障不推定释放。未实现算法或落库。

上列任务命令已实际执行，并由阶段验收器复核；13 项收集，13 通过、0 失败、0 跳过，退出码 0。共同质量检查 Ruff、格式检查、mypy 均退出 0。每条实际命令、开始/结束时间、耗时、平台和文件指纹见 [P0.json](../benchmarks/reports/verification/P0.json)。Schema 1.0，样本 development-scheduling-v3（AI建议，未发布），策略测试基线 p0-baseline-v1；没有正式知识/规则发布。来源哈希见 fixture_manifest.json 及报告 artifact_hashes。

<a id="p0-05"></a>
### P0-05 实现字段来源、审核记录和数据问题契约

**状态：VERIFIED。** 前置任务：[P0-03](#p0-03)。

**输入：** 原始材料定位、模型建议和人工估计的不同来源。

**输出：** ProvenanceRecord、ReviewPatch、DataIssue、ExtractionRun、ArtifactRef；审核状态转换校验。

**生产/数据文件范围：** `app/domain/provenance.py`、`app/domain/review.py`、`app/domain/extraction.py`、`app/pipeline/issues.py`。

**测试文件：** `tests/unit/test_review_provenance.py`。

**接口约定：** apply_review_patch(recipe: CanonicalRecipeModel, patch: ReviewPatch) -> CanonicalRecipeModel；批准记录需要明确审核者和证据。

**实现要点：** 保留前后值、理由、版本与输入内容引用；模型抽取不具备自动人工批准权限，失败调用也可归档。

**验收断言：**

- [x] 模型建议不能因 confidence 高自动变成 APPROVED。
- [x] 修改工艺字段后原批准版本失效，旧补丁和来源仍可追溯。
- [x] 同一原文重复处理产生不同 run_id 而保留相同 source_hash；内容引用不存在时校验失败。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_review_provenance.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不代替人工判断缺失参数；真正调用模型和归档文件在 P1-02。

**设计定位：** 架构文档 5.3–5.4、17.6。

**验证记录（2026-09-23，执行者 Codex）：** 来源、审核补丁、内容引用和抽取归档契约完成。修改使旧批准失效；模型不能自批；内容必须存在且哈希相符；拒绝旧版本、错误前值和容器替换绕过来源保护。未调用真实模型或伪造人工记录。

上列任务命令已实际执行，并由阶段验收器复核；7 项收集，7 通过、0 失败、0 跳过，退出码 0。共同质量检查 Ruff、格式检查、mypy 均退出 0。每条实际命令、开始/结束时间、耗时、平台和文件指纹见 [P0.json](../benchmarks/reports/verification/P0.json)。Schema 1.0，样本 development-scheduling-v3（AI建议，未发布），策略测试基线 p0-baseline-v1；没有正式知识/规则发布。来源哈希见 fixture_manifest.json 及报告 artifact_hashes。

<a id="p0-06"></a>
### P0-06 建立代表性审核样本与合成反例

**状态：BLOCKED。** 前置任务：[P0-03](#p0-03)、[P0-05](#p0-05)。

**输入：** 100 道原始菜谱、设备文件；可追溯的工艺补全与审核记录。

**输出：** 至少 12 个不同菜谱 ID 的开发样本及独立合成反例；样本覆盖矩阵和未解决问题清单。

**生产/数据文件范围：** `tests/fixtures/reviewed_sample/`、`tests/fixtures/synthetic/`、`tests/fixtures/fixture_manifest.json`、`data/issues/sample_review.json`。

**测试文件：** `tests/contract/test_fixture_provenance.py`。

**接口约定：** fixture_manifest.json 标记 source_kind、recipe_ids、knowledge_version、hashes、review_status 和覆盖的工艺特征。

**实现要点：** 覆盖长准备、菜内并行、两灶争用、多设备接力、不同热时长、明确分批、中途介入、同名异 ID 和共享切配；必要人工审核作为真实输入保留。

**验收断言：**

- [x] 审核样本引用可定位原文，缺少依据的字段保持 NEEDS_REVIEW，不因开发方便自动批准。
- [x] 合成数值用例明确 synthetic；成功和失败反例均有预期原因。
- [ ] 进入 P1 core 的样本达到规定审核门槛；若不足，任务保持未验收并列明具体缺口。

**任务验证命令：**

~~~text
uv run --locked pytest tests/contract/test_fixture_provenance.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 本任务不要求全部 100 道补齐；12 道样本通过不能替代最终全量验收。

**设计定位：** 架构文档 2.3、5.6、21.2–21.3。

**验证记录（2026-09-23，执行者 Codex）：** 12 个真实菜谱 ID 的原文、CSV 行号、片段和哈希已整理，两个麻辣对虾分别保留。建立 9 类工艺线索与独立合成合法/非法样例。用户回复“暂无，先整理待审核清单”。当前 0 个 APPROVED，全部 NEEDS_REVIEW。用户随后授权AI估时和设备替代，已完成100道V3数据修订：原始ID不变，1564个数值原子操作、真实原料/半成品流、DAG、设备会话和固定介入齐备；145/145开发调度验证通过。12个代表样本已直接承接V3，共214个操作、228条依赖；仍缺真实人工审核，不能进入 P1 core。开发验证不替代正式P1/P2验收。

上列任务命令已实际执行，并由阶段验收器复核；P0-06关联测试50项收集，49通过、1个人工审核门失败、0跳过，退出码 1。共同质量检查 Ruff、格式检查、mypy 均退出 0。每条实际命令、开始/结束时间、耗时、平台和文件指纹见 [P0.json](../benchmarks/reports/verification/P0.json)。Schema 1.0，样本 development-scheduling-v3（AI建议，未发布），策略测试基线 p0-baseline-v1；没有正式知识/规则发布。来源哈希见 fixture_manifest.json 及报告 artifact_hashes。

<a id="p0-07"></a>
### P0-07 建立比赛协议契约及阶段验收登记

**状态：IMPLEMENTED。** 前置任务：[P0-04](#p0-04)、[P0-06](#p0-06)。

**输入：** 原始接口规范、官方字段、代表样本和 P0 验收入口。

**输出：** 比赛输入/输出 Schema、排序与时间字段契约用例、P0 验收登记及 P1 core/full 门槛定义。

**生产/数据文件范围：** `app/domain/competition_contract.py`、`tests/fixtures/competition/`、`scripts/verification_manifest.json`。

**测试文件：** `tests/contract/test_competition_schema.py`。

**接口约定：** CompetitionRequest = list[RecipeSelection]；CompetitionResponse 的顶层字段恰为规定五项。

**实现要点：** 保存官方例子与明确反例；记录 U01–U14 当前处理，测试不向官方请求增加字段。Schema 检验作为早期约束，不伪造业务执行成功。

**验收断言：**

- [x] 包装成 data 数组或新增成功顶层调试字段的例子被拒绝。
- [x] 同名异 ID、名称核对、跨日和非法时间格式具备明确用例。
- [x] uv run --locked python scripts/verify.py --phase P0 执行所有必需检查并生成带版本的报告。

**任务验证命令：**

~~~text
uv run --locked pytest tests/contract/test_competition_schema.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不启用公网 API；官方未明确的会话语义保留为外部条件。

**设计定位：** 架构文档 18、21.1、23。

**验证记录（2026-09-23，执行者 Codex）：** 用户已补回官方接口规范，完整读取后实现请求/响应 Schema、官方样例、21 项协议测试和 U01–U14 记录。阶段验收器实际运行全部检查并返回审核门槛失败。自身测试通过但 P0-06 未通过，本任务不能标 VERIFIED。官方样例仅用于结构，不作排程正确性证据。

上列任务命令已实际执行，并由阶段验收器复核；21 项收集，21 通过、0 失败、0 跳过，退出码 0。共同质量检查 Ruff、格式检查、mypy 均退出 0。每条实际命令、开始/结束时间、耗时、平台和文件指纹见 [P0.json](../benchmarks/reports/verification/P0.json)。Schema 1.0，样本 development-scheduling-v3（AI建议，未发布），策略测试基线 p0-baseline-v1；没有正式知识/规则发布。来源哈希见 fixture_manifest.json 及报告 artifact_hashes。

<a id="p1"></a>
## P1 离线数据与知识发布（9 项任务）

**阶段入口：** P0 已通过；具备样本审核依据和用于图谱集成测试的真实 Neo4j 环境。

**阶段产物：** 离线导入/抽取/审核链、图谱投影、快照/索引、原子发布和只读加载。

**验收出口：** core：P1-01 至 P1-08 全部 VERIFIED，至少 12 道审核样本形成有效发布，关闭 Neo4j 后新读取进程可加载。full：P1-09 在 P2 可用后通过，100 道 ID 完整、路径审核与真实单菜求解全部核对。仅 full 通过才标阶段 VERIFIED。

**范围边界：** core 不是正式 100 道交付，不把读取进程测试描述成尚未实现的完整 HTTP 服务验收。

**交接内容：** core 产物供 P2–P5 开发；full 固定发布供 P6；任何后续知识修改需要新版本与受影响回归。

**阶段验收命令：**

~~~text
uv run --locked python scripts/verify.py --phase P1 --gate core
uv run --locked python scripts/verify.py --phase P1 --gate full
~~~

| 任务 | 交付内容 | 当前状态 |
| --- | --- | --- |
| [P1-01](#p1-01) | 导入原始菜谱和设备资料 | IMPLEMENTED |
| [P1-02](#p1-02) | 实现离线 LLMProvider 与调用归档 | IMPLEMENTED |
| [P1-03](#p1-03) | 完成规范化与人工审核补丁流程 | IMPLEMENTED |
| [P1-04](#p1-04) | 实现工艺与设备路径发布校验 | IMPLEMENTED |
| [P1-05](#p1-05) | 构建 Neo4j 图谱投影与证据关系 | IMPLEMENTED |
| [P1-06](#p1-06) | 导出知识快照与索引并验证语义 | IMPLEMENTED |
| [P1-07](#p1-07) | 实现原子发布与归档重放验证 | IMPLEMENTED |
| [P1-08](#p1-08) | 实现在线只读知识加载及 P1 core 验收 | IMPLEMENTED |
| [P1-09](#p1-09) | 完成固定 100 道全量审核与可行性验证 | IN_PROGRESS |

<a id="p1-01"></a>
### P1-01 导入原始菜谱和设备资料

**状态：IMPLEMENTED。** 前置任务：[P0-07](#p0-07)。前置审核未通过，不标记 VERIFIED。

**输入：** 根目录 recipes_100.csv 与设备参数清单参考.json，原始文件保持不变。

**输出：** SourceDocument、源行定位、100 条原文导入结果、设备原始模型与源文件 SHA-256。

**生产/数据文件范围：** `app/pipeline/import_raw.py`、`app/pipeline/device_import.py`、`app/domain/source_document.py`、`data/raw/manifest.json`。

**测试文件：** `tests/unit/test_raw_import.py`。

**接口约定：** import_recipes(path: Path) -> tuple[SourceDocument, ...]；导入结果 recipe_id 与源 ID 一一对应。

**实现要点：** 处理编码、表头、换行、引号及数值单位；保留屏幕提示和缺失字段的原文证据，不推测未取得的程序参数。

**验收断言：**

- [x] 导入 100 个 ID、99 个不同菜名；两个麻辣对虾不合并。
- [x] 762 条原始步骤的来源定位保持可追溯；原始统计与规范化原子工序数分开。
- [x] 再次导入不改变原文件哈希；损坏行明确报错，不静默丢弃。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_raw_import.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不读取 ZIP，不将原始字符串直接变成正式可调度知识。

**设计定位：** 架构文档 2.3、5.2、附录 B。

**验证记录（2026-09-27，Codex）：** 已实现 SourceDocument、import_recipes、import_devices、scripts/import_sources.py 和 data/raw/manifest.json；保留原始字节哈希、CSV 物理行范围、编号步骤字符偏移、单位与设备原文。原始 762 步和 V3 的 1564 个导出操作分别登记，未推定屏幕程序参数或生成审核批准。先运行测试确认模块缺失导致收集失败，再实现；最终 `uv run --locked pytest tests/unit/test_raw_import.py tests/unit/test_environment.py tests/contract/test_canonical_contract.py -q --junitxml=benchmarks/reports/verification/P1-01-tests.xml`：34 通过、0 失败、0 跳过，其中新导入测试 21 项。`uv run --locked python -m scripts.import_sources`、Ruff check / format --check、mypy app 均退出 0（29 个 app 源文件）。Windows 本地运行，无外部服务、无 LLM 调用；数据为真实来源导入，损坏反例明确为合成。报告及内容指纹见 [P1-01 开发检查](../benchmarks/reports/verification/P1-01-development.json)。依赖 P0-07 未验收，不能作为 P1 core 已通过证据。

<a id="p1-02"></a>
### P1-02 实现离线 LLMProvider 与调用归档

**状态：IMPLEMENTED。** 前置任务：[P1-01](#p1-01)、[P0-04](#p0-04)、[P0-05](#p0-05)。

**输入：** SourceDocument、抽取 Schema、提示模板、显式配置的供应商连接信息。

**输出：** ExtractionRun、原始请求与返回归档、结构化草稿或结构化调用失败。

**生产/数据文件范围：** `app/llm/provider.py`、`app/llm/extraction_contract.py`、`app/llm/provider_adapter.py`、`app/pipeline/extract.py`、`app/pipeline/extraction_archive.py`、`app/domain/ports.py`、`app/domain/extraction.py`。

**测试文件：** `tests/unit/test_extraction_archive.py`、`tests/integration/test_llm_provider_contract.py`。

**接口约定：** LLMProvider.extract(request: ExtractionRequest) -> ExtractionResponse；archive_extraction(run, artifacts) -> ArtifactRef。

**实现要点：** 记录真实渲染消息、契约、参数、模型别名及可取得的实际版本、调用 ID、失败/截断状态；测试使用固定原始响应，真实调用单独标记。

**验收断言：**

- [x] 原始内容与哈希均存在；缺失模型修订号记 unknown，不能伪造。
- [x] 超时、截断和非法 JSON 返回失败记录，不生成 APPROVED 数据。
- [x] 从同一归档响应重复解析可复现；重新调用产生新 run_id 并比较差异。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_extraction_archive.py tests/integration/test_llm_provider_contract.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不绑定唯一模型供应商，不把密钥写入归档，不在线批量抽取全部菜谱。

**设计定位：** 架构文档 17.1、17.3、17.6。

**验证记录（2026-09-27，Codex）：** 已实现通用 JSON HTTP 适配、不可变内容寻址归档、真实请求参数核对、拒绝/截断/超时记录和本地重放。用户确认使用 deepseek-flash，凭据只存本地忽略文件；真实调用保留非法单位、截断和成功三个 run，成功输出仍待审，实际模型修订号 unknown，重放外部调用数 0。报告见 [DeepSeek 检查](../benchmarks/reports/verification/deepseek-extraction.json)。离线合成协议、并发归档、损坏/缺失内容、超时和伪造批准用例通过。联合命令 `uv run --locked pytest tests/unit tests/contract tests/integration -q -m "not review_gate" --junitxml=benchmarks/reports/verification/P1-development-tests.xml`：205 通过、0 失败、0 跳过、1 项人工审核门未选择。Ruff check、format --check 和 mypy app（46 个文件）通过；这是开发检查，不代表正式 P1 core 验收。

<a id="p1-03"></a>
### P1-03 完成规范化与人工审核补丁流程

**状态：IMPLEMENTED。** 前置任务：[P1-02](#p1-02)。

**输入：** 原文、抽取草稿、设备原始模型、ReviewPatch 和审核证据。

**输出：** 有版本 CanonicalRecipeModel、问题清单、审核链以及固定归档重放结果。

**生产/数据文件范围：** `app/pipeline/normalize.py`、`app/pipeline/review.py`、`app/pipeline/replay.py`、`data/reviewed/`、`data/issues/`。

**测试文件：** `tests/unit/test_normalization_review.py`。

**接口约定：** normalize(draft, evidence) -> NormalizationResult；replay_extraction(run_id, versions) -> ReplayReport。

**实现要点：** 拆复合步骤、标明人工与等待、规范单位及设备模式；原文冲突进入问题清单，只有明确补丁可以改变工艺字段。

**验收断言：**

- [x] 翻拌、浇汁、取出和中途加料不因拆步消失。
- [x] 固定本地解析器、规则和审核补丁后结果哈希相同；缺补丁或归档时重放失败。
- [x] 没有依据的温度/时长不静默裁剪到设备范围；人工审核不把估计变成实测。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_normalization_review.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 处理草稿可以保留问题；发布门槛由 P1-04、P1-07 检查，不靠规范化器默认值过关。

**设计定位：** 架构文档 5、6.4、17.6。

**验证记录（2026-09-27，Codex）：** 已实现有依据的复合步骤展开、精确数量单位换算、缺失信息问题清单、审核补丁与证据归档和固定版本重放。8 项专项测试覆盖介入/固定批次保留、来源边界、改后再审、缺补丁/证据失败及哈希复现；没有为真实数据生成批准，也未把估计变成实测。未知设备模式交由独立能力校验或明确补丁处理，不裁剪温度或工艺时间。联合回归与质量检查见 P1-02 记录及 [JUnit](../benchmarks/reports/verification/P1-development-tests.xml)。正式人工审核仍待办。

<a id="p1-04"></a>
### P1-04 实现工艺与设备路径发布校验

**状态：IMPLEMENTED。** 前置任务：[P1-03](#p1-03)。

**输入：** 规范化菜谱、设备清单、审核规则及分钟表达策略。

**输出：** KnowledgeValidationReport，包含可用路径、阻断问题与来源。

**生产/数据文件范围：** `app/validation/knowledge.py`、`app/validation/device_paths.py`、`app/validation/time_compatibility.py`。

**测试文件：** `tests/unit/test_knowledge_gate.py`。

**接口约定：** validate_knowledge(recipes, profiles, rules, release_scope) -> KnowledgeValidationReport。

**实现要点：** 检查 ID/引用、DAG、必需参数、温湿模式、固定批次、完整物料路径和分钟网格；区分结构合法与尚待实际单菜求解的可行性证据。

**验收断言：**

- [x] 超范围温度、缺失强制介入、悬空物料、工艺环和不合法时间表达被拒绝。
- [x] 长冷藏不会按蒸烤最长时间截断；不同设备能力范围独立校验。
- [x] sample 发布只接受已审核子集；competition 发布的 100 ID 完整性另有强制门槛。

- [x] 逐阶段检查所有必需设备的物理映射、竞争规则与参数依据，包括路径实际使用的洗碗机、咖啡饮水机或解冻功能；缺少必需信息形成阻断问题，不默认零时长或新增独立设备。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_knowledge_gate.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不以规则检查替代 P2 的调度求解；不凭借备用设备创造未经审核的新路径。

**设计定位：** 架构文档 5.6、8、12.5。

**验证记录（2026-09-27，Codex）：** 已实现独立 DAG、物料来源及份额、设备能力/物理别名/竞争策略、强制介入和分钟网格校验。设备能力直接解析原始 JSON，保留各模式范围与未知程序时长。18 项门槛单元反例、1 项真实设备清单、4 项来源重建、2 项真实 V3 检查通过。实际 V3 秒级检查为 99 条结构可用路径、1 个 DEVICE_PATH_INVALID（鱼头热水烫洗被误映射到蒸箱）；分钟网格另有 214 个时间兼容问题。报告见 [真实知识检查](../benchmarks/reports/verification/P1-04-development-knowledge.json)。工艺修正已询问用户；没有把校验通过视作单菜求解完成。联合回归与质量证据见 P1-02 记录；正式人工审核未通过。

<a id="p1-05"></a>
### P1-05 构建 Neo4j 图谱投影与证据关系

**状态：IMPLEMENTED。** 前置任务：[P1-04](#p1-04)。

**开发验证（2026-09-28）：** 真实 Neo4j 5.26.28 上5项测试通过（71.47秒），覆盖同名异ID、幂等、版本冲突、缺边重建、标签及跨版本连边拒绝、事务中断回滚。报告 P1-05-real-neo4j-tests.xml；100道实际投影计数见 P1-development-release.json。真实数据仍待审核，不标记正式 VERIFIED。

**输入：** 审核知识、固定知识/规则版本、可用 Neo4j 测试实例。

**输出：** 可重建图谱、索引、唯一性约束、来源及规则关系。

**生产/数据文件范围：** `app/knowledge/graph_projection.py`、`app/knowledge/graph_schema.py`、`app/knowledge/cypher/`、`deploy/compose.knowledge.yaml`。

**测试文件：** `tests/integration/test_graph_projection.py`。

**接口约定：** GraphProjector.project(release: ReviewedRelease) -> GraphProjectionReport；所有查询显式限定 knowledge_version。

**实现要点：** 导入 Recipe、Operation、MaterialSpec、设备配置、规则和来源；依赖投影与全图分开；同一版本重复构建幂等。

**验收断言：**

- [ ] 实体数、ID、必需边与规范化来源一致；同名菜保留两个节点。
- [ ] 可共享关系不被导入为强制 PRECEDES；不同版本的关系不会串联。
- [ ] 使用真实 Neo4j 完成导入和重建测试；无服务时明确未执行，不能 mock 为阶段通过。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_graph_projection.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 仅维护流程访问图数据库，在线代码不导入该连接实现作为启动前提。

**设计定位：** 架构文档 7、19.1、20.1。

**验收说明：** 开发验证证据见本节记录及统一报告；正式审核前置尚未满足，保持IMPLEMENTED。

<a id="p1-06"></a>
### P1-06 导出知识快照与索引并验证语义

**状态：IMPLEMENTED。** 前置任务：[P1-05](#p1-05)。

**开发验证（2026-09-28）：** 6项真实图谱导出与属性测试通过（93.80秒）；随机子集来自现存100道，校验完整规范化内容、稳定语义哈希、错版本和缺失固定成员/依赖/证据关系。报告 P1-06-real-neo4j-tests.xml。静态索引不包含菜单选择或执行事实。

**输入：** 固定版本图谱、规范化知识及 manifest 草案。

**输出：** SchedulingKnowledgeSnapshot、KnowledgeIndex、snapshot_validation_report.json。

**生产/数据文件范围：** `app/pipeline/snapshot_export.py`、`app/knowledge/snapshot.py`、`app/knowledge/index.py`。

**测试文件：** `tests/integration/test_snapshot_export.py`、`tests/property/test_snapshot_roundtrip.py`。

**接口约定：** export_snapshot(release_id: str) -> SnapshotBuildResult；content_hash 不包含自身字段，引用完整且确定性序列化。

**实现要点：** 导出工序、依赖、物料、设备、共享规格桶及证据索引；逐项核对与规范化来源的一致性，分离静态兼容与本次候选分组。

**验收断言：**

- [ ] 重复导出同一内容获得相同语义哈希；生成时间变化不破坏可复现定义。
- [ ] 故意漏边、错版本、丢强制批次或修改引用时导出校验失败。
- [ ] 属性测试覆盖规范化数据经图谱投影再导出的调度语义保持。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_snapshot_export.py tests/property/test_snapshot_roundtrip.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不在快照中写实际库存、当前预约或具体菜单选择结果。

**设计定位：** 架构文档 7.4–7.5、9.1、21.8。

**验收说明：** 开发验证证据见本节记录及统一报告；正式审核前置尚未满足，保持IMPLEMENTED。

<a id="p1-07"></a>
### P1-07 实现原子发布与归档重放验证

**状态：IMPLEMENTED。** 前置任务：[P1-06](#p1-06)。

**开发验证（2026-09-28）：** 7项发布测试通过，包含中断、缺来源、竞争激活、篡改拒绝和完整抽取/审核归档离线重放（合成故障与审核记录）。真实100道生成不可变 development-v3-rebased-v2-all 发布，来源文件随包保存；52.85秒完成实际图谱、导出及发布。尚无真实人工审核或P2全量解，因此 competition 发布被明确阻断。

**输入：** 完整发布产物、审核报告、归档索引及当前 active_release。

**输出：** 不可变 sample/competition 发布目录、校验 manifest 和原子激活结果。

**生产/数据文件范围：** `app/pipeline/publish.py`、`app/pipeline/release_manifest.py`、`data/releases/`。

**测试文件：** `tests/integration/test_release_activation.py`。

**接口约定：** publish_release(bundle: ReleaseBundle) -> ReleaseRef；失败不改变 active_release。

**实现要点：** 验证所有哈希、Schema、规则、来源和文件引用；构建完成后激活新版本，活动旧会话所需版本不能提前移除。

**验收断言：**

- [ ] 导出中断、缺归档内容、校验失败时旧发布仍可读取。
- [ ] 竞争发布不会形成 manifest 与快照跨版本组合。
- [ ] sample 发布明确标记，不具备正式 100 道资格；本地抽取审核链可重放。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_release_activation.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不提供静默覆盖已发布文件的修改入口；正式全量求解门槛由 P1-09 收口。

**设计定位：** 架构文档 5.5、7.5、19.4。

**验收说明：** 开发验证证据见本节记录及统一报告；正式审核前置尚未满足，保持IMPLEMENTED。

<a id="p1-08"></a>
### P1-08 实现在线只读知识加载及 P1 core 验收

**状态：IMPLEMENTED。** 前置任务：[P1-07](#p1-07)。

**开发验证（2026-09-28）：** 已实际停止本地Neo4j，在新Python进程加载100道/1562工序，网络调用0、未导入Neo4j驱动；3项离线及版本租约测试通过（9.58秒），随后已恢复服务。报告 P1-08-offline-tests.xml、P1-08-offline-snapshot.json。正式 core 仍受 P0-06 人工审核门槛约束；用户已授权开发接入，不能将开发证据改写为正式 CORE_VERIFIED。

**输入：** 有效 sample 发布文件、至少 12 道审核样本、故意损坏或错版的测试产物。

**输出：** SnapshotKnowledgeRepository、MenuKnowledgeView、P1 core 验收报告。

**生产/数据文件范围：** `app/knowledge/repository.py`、`app/knowledge/loader.py`、`scripts/check_snapshot.py`、`scripts/verification_manifest.json`。

**测试文件：** `tests/integration/test_knowledge_without_neo4j.py`。

**接口约定：** KnowledgeRepository.load(ref: ReleaseRef) -> SnapshotHandle；select(recipe_ids: tuple[str, ...]) -> MenuKnowledgeView。

**实现要点：** 在新的只读进程中加载知识并按菜单提取；引用计数保护旧版本；图谱子图由内存静态关系生成。

**验收断言：**

- [ ] 关闭 Neo4j 后新进程仍可加载并提取样本；监控在线提取图数据库调用次数为 0。
- [ ] 损坏或 Schema 不兼容文件拒绝加载；缺会话版本不替换最新知识。
- [ ] uv run --locked python scripts/verify.py --phase P1 --gate core 通过；此时未声称 FastAPI 完整冷启动已实现。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_knowledge_without_neo4j.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 该任务允许 P2 开始；P1-09 未通过时 P1 只能标记 CORE_VERIFIED。

**设计定位：** 架构文档 7.5、19.4、20.2。

**验收说明：** 开发验证证据见本节记录及统一报告；正式审核前置尚未满足，保持IMPLEMENTED。

<a id="p1-09"></a>
### P1-09 完成固定 100 道全量审核与可行性验证

**状态：IN_PROGRESS。** 前置任务：[P1-08](#p1-08)、[P2-12](#p2-12)。

**输入：** 100 道源数据、逐条审核与补全记录，以及已通过 P2 的真实基础 Planner。

**输出：** competition 发布包、100 ID 完整性报告、逐菜独立校验通过的单菜计划与未解决问题数。

**生产/数据文件范围：** `data/reviewed/`、`data/issues/`、`data/releases/`、`scripts/verification_manifest.json`。

**测试文件：** `tests/integration/test_all_recipes_release.py`。

**接口约定：** verify.py --phase P1 --gate full；正式发布须具备全部 100 道至少一条合法可表达路径的求解与校验证据。

**实现要点：** 逐条补齐工艺，不批量拍脑袋填值；使用真实 Compiler、Greedy/CP-SAT 和 Validator 跑全量；最终冻结候选发布版本供 P6 使用。

**验收断言：**

- [ ] 100 个原始 ID 全覆盖，同名异 ID 保留；每道有审核路径且实际单菜测试通过。
- [ ] 正式必需字段和路径没有阻断 DataIssue；剩余非阻断说明明确列出。
- [ ] 真实源哈希、规则/知识版本、计划与校验报告绑定，可重新运行同一全量检查。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_all_recipes_release.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 这是跨阶段持续的数据任务；缺人工审核或依据时如实阻塞该任务，不阻断已具备样本的 P2–P5 工程开发。

**设计定位：** 架构文档 5.6、21.2、22。

**验证记录（2026-09-28，开发口径）：** `SMART_COOKING_VERIFY_ALL_OUTPUT=benchmarks/reports/P1-P2-all-recipes-v3 uv run --locked pytest tests/integration/test_all_recipes_release.py -q` 实际1项通过，186.01秒；此时该文件仅收集开发单菜检查，随后新增独立review_gate正式门槛。真实主链100/100通过，逐菜问题、计划、独立校验、SolverBuildReport和SHA256在 `benchmarks/reports/P1-P2-all-recipes-v3/`。P50=1406ms、P95=3141ms、最大3484ms，无请求内超4200ms；98道有CP-SAT候选、2道仅Greedy合法回退。知识冷加载4094ms、初次进程启动1734ms，3次请求间冷恢复3157/2579/2297ms单列，不计入已预热请求耗时。前两轮98/100、99/100失败证据保留在同目录的无后缀及-v2版本，未覆写。所有100道保持NEEDS_REVIEW，正式审核和competition发布为用户已同意保留的待办；不是P6正式性能认证。发布入口已要求100个独立校验的从零单菜证据，加载也核对完整性和绑定，未使用开发证据冒充批准。

<a id="p2"></a>
## P2 基础可行调度（12 项任务）

**阶段入口：** P0 及 P1 core 通过；P1-09 可以继续处理，不能形成进入 P2 的循环前置条件。

**阶段产物：** 不可变问题、独立校验器、Greedy、CP-SAT、指标、分阶段目标、常驻进程、预算和诊断；真实命令行主链。

**验收出口：** 12 个任务全部 VERIFIED；12 道审核样本逐菜和代表多菜边界运行真实主链；强制工艺、两灶/人工、物料、热时间和分钟投影正确；小规模精确与故障回退检查通过，实际耗时有记录。

**范围边界：** 跨菜共享关闭，原菜内固定批次和必要介入完整；此时无持久化会话或官方 API 完成声明。

**交接内容：** 把可调用 PlanningCore、问题快照、样本计划、Validator 和性能基线交给 P3/P4；P1-09 使用同一主链跑全量单菜。

**阶段验收命令：**

~~~text
uv run --locked python scripts/verify.py --phase P2
~~~

| 任务 | 交付内容 | 当前状态 |
| --- | --- | --- |
| [P2-01](#p2-01) | 实例化工序并生成单独执行候选 | IMPLEMENTED |
| [P2-02](#p2-02) | 展开固定菜内批次、介入及多设备路径 | IMPLEMENTED |
| [P2-03](#p2-03) | 实现依赖图、时间域和分钟表达 | IMPLEMENTED |
| [P2-04](#p2-04) | 汇总物料、资源和运行事实形成 SchedulingProblem | IMPLEMENTED |
| [P2-05](#p2-05) | 建立约束目录、规模预估与安全去重 | IMPLEMENTED |
| [P2-06](#p2-06) | 实现独立计划校验器及故意错误用例 | IMPLEMENTED |
| [P2-07](#p2-07) | 实现资源日历和确定性 Greedy 插入回退 | IMPLEMENTED |
| [P2-08](#p2-08) | 实现 CP-SAT 基础时间与资源模型 | IMPLEMENTED |
| [P2-09](#p2-09) | 实现串行参考与统一指标计算 | IMPLEMENTED |
| [P2-10](#p2-10) | 实现分阶段目标、提示和已验证候选池 | IMPLEMENTED |
| [P2-11](#p2-11) | 实现常驻进程、统一截止时间及主进程回退 | IMPLEMENTED |
| [P2-12](#p2-12) | 实现不可行诊断并完成基础主链验收 | IMPLEMENTED |

<a id="p2-01"></a>
### P2-01 实例化工序并生成单独执行候选

**状态：IMPLEMENTED。** 前置任务：[P1-08](#p1-08)、[P0-04](#p0-04)。

**开发验证：** 6项真实发布数据测试通过；同名异ID、重复实例、稳定任务身份、已完成/运行中工序与剩余候选分离、名称及版本冲突。候选保留明确灶眼备选和来源。尚未完成完整P2联合验收。

**输入：** MenuKnowledgeView、本次 RecipeInstance、空或明确的 RuntimeSnapshot。

**输出：** 稳定 task_id、逻辑需求及保留全部合法路径的 standalone_candidates。

**生产/数据文件范围：** `app/compiler/instantiate.py`、`app/compiler/candidate_generation.py`、`app/domain/candidates.py`。

**测试文件：** `tests/unit/test_standalone_compilation.py`。

**接口约定：** instantiate(menu, knowledge, runtime) -> InstantiationResult；候选覆盖原始操作时保留 provenance_refs。

**实现要点：** 把模板变成本次实例；同一道菜的多次实例保持不同身份；已完成需求与尚未满足需求分开。

**验收断言：**

- [ ] 按源 ID 与实例生成任务，同名或重复实例不会覆盖。
- [ ] 强制工序数与知识来源可核对；单独候选没有跨菜自动合并。
- [ ] 相同输入版本实例化稳定，状态变化不会重写知识模板。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_standalone_compilation.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不进行跨菜共享；所有未满足必需需求必须有明确候选或结构化失败原因。

**设计定位：** 架构文档 6.2、6.5、9.1–9.2。

**验收说明：** 具体开发测试见本节记录；统一回归登记于 scripts/verification_manifest.json。正式审核依赖未完成，保持IMPLEMENTED。

<a id="p2-02"></a>
### P2-02 展开固定菜内批次、介入及多设备路径

**状态：IMPLEMENTED。** 前置任务：[P2-01](#p2-01)。

**开发验证：** 3项强制工艺测试通过：烧麦两次各600秒蒸制分开；套餐2700秒有效过程与120秒暂停介入、预热分别保留；缺人工或时长矛盾拒绝。设备预约覆盖原成员，不增加跨菜批次。

**输入：** 原菜谱明确的复合工艺、人工介入、分批与设备参数。

**输出：** 载体实际阶段、逻辑时间端口、固定批次和配置转换所需关系。

**生产/数据文件范围：** `app/compiler/phase_expansion.py`、`app/compiler/mandatory_batches.py`、`app/domain/thermal.py`。

**测试文件：** `tests/unit/test_mandatory_recipe_programs.py`。

**接口约定：** expand_required_program(candidate, processing_rules) -> ExpandedCarrier；成员热暴露与设备预约分别表达。

**实现要点：** 保留装入、取出、翻拌、中途加料和多设备接力；只有审核规则允许的阶段布局可展开。

**验收断言：**

- [ ] 两批蒸制始终为两次必要过程，不因设备不计容量变成一批。
- [ ] 固定程序中的中途加料和取出翻拌有人工区间及正确热时间端口。
- [ ] 多设备接力的真正完成边界在最后必需操作之后。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_mandatory_recipe_programs.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 本任务完成菜内强制批次；任意跨菜组合留到 P3，不能通过关闭共享功能关闭本任务工艺。

**设计定位：** 架构文档 5.2、8.2、11、12.3。

**验收说明：** 具体开发测试见本节记录；统一回归登记于 scripts/verification_manifest.json。正式审核依赖未完成，保持IMPLEMENTED。

<a id="p2-03"></a>
### P2-03 实现依赖图、时间域和分钟表达

**状态：IMPLEMENTED。** 前置任务：[P2-02](#p2-02)。

**开发验证：** P2-01至03联合21项通过，证据P2-01-03-tests.xml（5.80秒）。新增候选会重算安全下界；最大间隔反向传播、长准备跨日、循环依赖及整数分钟冲突明确检测。按用户更新支持可回读整数秒的小数分钟，保留整数分钟兼容模式。

**输入：** 逻辑工序、已展开端口、min/max lag、运行原点和时间网格。

**输出：** DAG 验证、可追溯时间域、安全上下界和关键路径启发值。

**生产/数据文件范围：** `app/compiler/dependency_graph.py`、`app/compiler/bounds.py`、`app/compiler/time_projection.py`。

**测试文件：** `tests/unit/test_dependency_bounds.py`、`tests/unit/test_minute_grid.py`。

**接口约定：** build_dependency_graph(tasks, dependencies) -> DependencyGraph；derive_bounds(graph, candidates, runtime, policy) -> TimeBounds。

**实现要点：** 区分适用于全部候选的数学下界与仅用于排序的启发值；无官方 deadline 不凭空添加硬上界；时间域不足保留诊断信息。

**验收断言：**

- [ ] 依赖环、最早晚于最晚和冻结历史时间冲突被定位。
- [ ] 长准备和跨日时间不会截断；独立取整造成的冲突被检测。
- [ ] 改变候选时长后，使用固定旧关键路径作为非法强下界的反例被拒绝。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_dependency_bounds.py tests/unit/test_minute_grid.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 资源争用不直接固化为任意先后；不把时间域裁剪的无解解释为菜谱本身无解。

**设计定位：** 架构文档 8.4–8.5、9.4、12.5。

**验收说明：** 具体开发测试见本节记录；统一回归登记于 scripts/verification_manifest.json。正式审核依赖未完成，保持IMPLEMENTED。

<a id="p2-04"></a>
### P2-04 汇总物料、资源和运行事实形成 SchedulingProblem

**状态：IMPLEMENTED。** 前置任务：[P2-03](#p2-03)。

**开发记录：** 已接入真实快照、独立实例物料份额、固定程序、设备不可用区间、冻结事实及共享截止时间；12项库存不足、错误物料、同物理设备别名及运行状态测试通过。2026-09-28 与 P2-05 合跑18项通过，见 `benchmarks/reports/verification/P2-04-05-tests.xml`。未进入P2求解器验收。

**输入：** 工序和候选、审核物料转换、设备状态、RuntimeSnapshot、SchedulingPolicy。

**输出：** 完整不可变 SchedulingProblem 或 CompilationFailure；初排提供明确空执行快照。

**生产/数据文件范围：** `app/compiler/material_flow.py`、`app/compiler/runtime_constraints.py`、`app/compiler/compiler.py`。

**测试文件：** `tests/unit/test_problem_compilation.py`。

**接口约定：** ProblemCompiler.compile(knowledge, menu, runtime, policy, deadline) -> SchedulingProblem | CompilationFailure。

**实现要点：** 以统一量纲表达供需和产出可用时刻；将已发生事实、未来资源不可用区间及原点编译成领域约束，不连接数据库。

**验收断言：**

- [ ] 库存不足、规格不符和产出未形成时不能满足需求。
- [ ] 同一物料不会被两个单独候选重复作为已消耗供应。
- [ ] 固定开始/完成、运行中占用及设备不可用在 IR 中可追溯；异常未知状态明确返回。

- [ ] 蒸与烤若映射同一物理腔体，则共享占用资源；独立机器与冰箱不同温区保持正确边界，不能按工序或菜谱名字复制实例。
- [ ] 资源冲突编译为待排程决定的互斥关系；原有工艺依赖保留，不把菜谱 ID 顺序写成强制 PRECEDES。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_problem_compilation.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** P2 验证给定运行快照的表达能力；真实事件推进和剩余模型重建由 P4 完成。

**设计定位：** 架构文档 9、12.5–12.6、16.5。

**验收说明：** 具体开发测试见本节记录；统一回归登记于 scripts/verification_manifest.json。正式审核依赖未完成，保持IMPLEMENTED。

<a id="p2-05"></a>
### P2-05 建立约束目录、规模预估与安全去重

**状态：IMPLEMENTED。** 前置任务：[P2-04](#p2-04)。

**开发验证：** 2026-09-28 单元及属性测试6项通过，与P2-04合跑18项通过。仅删除除载体身份外全部语义相同的候选，保留受保护身份；10分钟与5分钟候选不会互相删除。约束目录绑定版本，预估规模及软门槛超限写入不可变问题；保留全部必需候选和约束。报告 `benchmarks/reports/verification/P2-04-05-tests.xml`；实际求解器统计仍待P2-08。

**输入：** 候选、隐式变量域、来源规则、目标策略和 SchedulingProblem 构建过程。

**输出：** ConstraintRecord、PruningRecord、ModelSizeEstimate；等价去重后的候选及保留映射。

**生产/数据文件范围：** `app/compiler/constraint_catalog.py`、`app/compiler/model_stats.py`、`app/compiler/pruning.py`。

**测试文件：** `tests/unit/test_constraint_catalog.py`、`tests/property/test_pruning_equivalence.py`。

**接口约定：** deduplicate(candidates, context) -> PruningResult；estimate_model_size(problem) -> ModelSizeEstimate。

**实现要点：** 每项删除标记 EQUIVALENT、CERTIFIED_DOMINANCE 或 BUDGET_TRUNCATION；默认关闭非等价剪枝，登记时间域和附加界来源。

**验收断言：**

- [ ] 10 分钟候选在固定开始与无等待后继下不能被 5 分钟候选错误支配删除。
- [ ] 小规模枚举验证等价去重保持覆盖和目标；强制批次及单独规范代表保留。
- [ ] 超软门槛记录原因和截断状态，不删除强制约束；问题哈希在冻结后不变。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_constraint_catalog.py tests/property/test_pruning_equivalence.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 本任务不承诺通用支配证明；真实 OR-Tools 模型统计在 P2-08 关联输出。

**设计定位：** 架构文档 9.5–9.6、15.4。

**验收说明：** 具体开发测试见本节记录；统一回归登记于 scripts/verification_manifest.json。正式审核依赖未完成，保持IMPLEMENTED。

<a id="p2-06"></a>
### P2-06 实现独立计划校验器及故意错误用例

**状态：IMPLEMENTED。** 前置任务：[P2-04](#p2-04)、[P2-05](#p2-05)。

**开发记录：** 已从发布来源独立扫描覆盖、依赖、加工时长、物料、物理资源、外层预约及固定程序。2026-09-28 共125项通过（19.34秒）：包括100道真实发布路径逐菜编译/独立校验、设备竞争矩阵、历史与物料端口篡改、真实中途介入延迟一秒等。候选由原有离线开发工具仅在测试中提供，不是P2新求解器或完整PlanningCore验收；原工具不能进入生产主链。证据 `benchmarks/reports/verification/P2-06-tests.xml` 与同名 `.log`。

**输入：** 正式知识依据、运行快照、SchedulingProblem 和人工构造候选计划。

**输出：** ValidationReport，逐项约束失败带任务、资源、物料和来源定位。

**生产/数据文件范围：** `app/validation/schedule.py`、`app/validation/source_coverage.py`、`app/validation/dependencies.py`、`app/validation/resources.py`、`app/validation/thermal_batches.py`、`app/validation/material_balance.py`。

**测试文件：** `tests/unit/test_schedule_validator.py`、`tests/property/test_schedule_invariants.py`。

**接口约定：** ScheduleValidator.validate(knowledge, runtime, problem, candidate) -> ValidationReport。

**实现要点：** 独立扫描覆盖、时间、资源、设备配置、材料、热过程与历史；检查 Compiler 是否漏掉知识中的强制步骤。

**验收断言：**

- [ ] 故意漏工序、人工重叠、双分配、过度加热、历史改写等错误计划均被拒绝。
- [ ] 合法共用边界 [0,60)/[60,120) 不误报；固定批次内部合法重叠与外层预约区分。
- [ ] 不能通过修改 IR 删除强制需求来蒙混通过来源覆盖检查。
- [ ] 两道菜分别指定不同虚构人工 ID 以规避冲突时必须拒绝；全菜单人工重叠统一扫描，两个灶眼可用不代表能同时执行两段全主动翻炒。

- [ ] 按架构 8.6 参数化扫描所有设备策略：拒绝同灶重叠、同腔体别名重叠、同温区配置冲突以及同通道程序冲突；允许不同灶的合法被动段并行、兼容温区的不同时长存放及烟机兼容状态共享。
- [ ] 可选设备在计划中实际被使用时同样校验；不同工序配置两两可行但全体无共同配置的重叠集合被拒绝，未知映射不被忽略。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_schedule_validator.py tests/property/test_schedule_invariants.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不导入 cp_sat.py 的约束函数作为判定标准；未知参数不能按零默认而通过。

**设计定位：** 架构文档 12.8、21.1、21.8。

**验收说明：** 具体开发测试见本节记录；统一回归登记于 scripts/verification_manifest.json。正式审核依赖未完成，保持IMPLEMENTED。

<a id="p2-07"></a>
### P2-07 实现资源日历和确定性 Greedy 插入回退

**状态：IMPLEMENTED。** 前置任务：[P2-05](#p2-05)、[P2-06](#p2-06)。

**开发验证：** 9项测试通过（8.74秒），含真实复杂菜单固定程序、不同设备共同人工、兼容设备并行、超时诚实失败、冻结事实后插入及完整资源/物料/虚拟产物撤销。用RED→GREEN修复冻结锚点、首变体失败终止后续及失败评估不计入24次上限的问题。证据 `benchmarks/reports/verification/P2-07-tests.xml`。随后全开发回归507通过、13个正式审核/服务专用测试未纳入，质量检查Ruff、166文件格式、mypy100源码均通过；不代表阶段正式VERIFIED。

**执行裁定（Ruling）：** P2基础Greedy先生成每道菜的有限内部布局，再以全资源阶段共同空档插入；保留原菜内批次和介入。该保守启发式可能错失更细粒度跨菜穿插，不改变CP-SAT候选或时间域，失败不能宣称不可行。独立单载体插入接口和完整状态撤销同时实现。

**输入：** 完整 SchedulingProblem、空档日历、候选布局、物料映射和 Deadline。

**输出：** GreedyResult、PlacementResult、完整候选或构造失败原因；可重复决策轨迹。

**生产/数据文件范围：** `app/scheduling/calendars.py`、`app/scheduling/placement.py`、`app/scheduling/greedy.py`。

**测试文件：** `tests/unit/test_greedy_placement.py`、`tests/property/test_greedy_rollback.py`。

**接口约定：** GreedyScheduler.solve(problem, deadline) -> GreedyResult；find_earliest_feasible_placement(candidate, calendars, problem, deadline) -> PlacementResult。

**实现要点：** 按阶段偏移求共同空档，在设备前后批次固定的空档内跳过冲突；按物理资源维护独占、批次、兼容状态和共享辅助日历，实现三个有限变体、评分与完整撤销。

**验收断言：**

- [ ] 多资源同时可用、紧最大间隔、后继预热变化和跨日空档均符合规则。
- [ ] 回退恢复资源、物料、需求覆盖和虚拟产物的原状态哈希；冻结事实不撤销。
- [ ] 全菜单使用同一 human_1 日历；自动蒸制期间允许切菜，蒸制中的人工翻拌或取出与切菜冲突时必须错开。
- [ ] 3 个变体、24 个插入评估、32 次空档检查、深度 3/尝试 8/单独重启 1 共用预算；失败不报告 INFEASIBLE。

- [ ] 资源空档按全设备策略判断；兼容冰箱任务允许不同持续时间的重叠，同腔体别名和冲突工作状态必须错开；不能把全部设备简化为单任务串行日历。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_greedy_placement.py tests/property/test_greedy_rollback.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 有限布局与回退仅限制 Greedy 搜索，不能固定或删掉 CP-SAT 的合法备选。

**设计定位：** 架构文档 14。

**验收说明：** 具体开发测试见本节记录；统一回归登记于 scripts/verification_manifest.json。正式审核依赖未完成，保持IMPLEMENTED。

<a id="p2-08"></a>
### P2-08 实现 CP-SAT 基础时间与资源模型

**状态：IMPLEMENTED。** 前置任务：[P2-05](#p2-05)、[P2-06](#p2-06)。

**开发验证：** 2026-09-28 新CP-SAT生产模块118项通过（28.00秒）：100道真实开发单菜、5组多菜边界、物理设备/人工策略矩阵、两种相反关键路径的最优顺序、无提示及冲突提示、UNKNOWN与INFEASIBLE区分。全部成功候选经独立校验；保存实际模型大小、protobuf字节数及本轮约束索引映射。证据 `benchmarks/reports/verification/P2-08-tests.xml` 与同名 `.log`。未声明完整PlanningCore、预算达标或正式人工审核通过。

**执行裁定（Ruling）：** P2-07 与 P2-08 相互没有前置依赖，先接入P2-08并用已通过125项测试的独立校验器核验；P2-07仍须实现，不能以CP-SAT替代Greedy。此顺序便于尽早核对实际整数模型，若发现接口问题则同步修正双方契约。

**输入：** SchedulingProblem、可选 Greedy 提示与阶段求解预算。

**输出：** SolveResult、CandidateSchedule、SolverBuildReport 和当前阶段有效界。

**生产/数据文件范围：** `app/scheduling/cp_sat.py`、`app/scheduling/model_builder.py`、`app/scheduling/solution_mapping.py`。

**测试文件：** `tests/unit/test_cp_sat_model.py`、`tests/integration/test_small_exact_schedules.py`。

**接口约定：** CpSatScheduler.solve(problem, hint: CandidateSchedule | None, deadline) -> SolveResult。

**实现要点：** 建立覆盖、可选区间、前置、物料与状态约束；按物理资源为独占和外层批次建立互斥，为允许共享的温区及辅助设备建立重叠期间共同配置约束，不能统一添加单任务 NoOverlap；在实际建模报告中保存变量和 proto 约束索引，不污染领域问题对象。

**验收断言：**

- [ ] 小规模独立枚举最优值与 Solver 结果一致；无解、未知和模型错误分类正确。
- [ ] 强制批次、必要人工和完整热时间成立；非法约束用法在固定版本上被发现。
- [ ] 两灶上的全主动翻炒不得同时进行；不同菜的装入、取出和运行中冻结人工段加入同一个 human_1 NoOverlap，共同批次不能假定一个人瞬间完成多个独立动作。
- [ ] 无提示仍可求解；提示不被当成硬约束，耗时限额不会产生伪造完整解。

- [ ] 全设备矩阵中的合法并行可被求得，冲突叠放被排除；同腔体预热、装入、加工、取出及尚未释放的实际占用按规则完整约束，同名能力不能绕过物理互斥。
- [ ] 构造 A 先 B 优于 B 先 A 及相反的两个小问题，对照独立枚举证明设备顺序由约束和目标决定，没有预先按 ID 固定。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_cp_sat_model.py tests/integration/test_small_exact_schedules.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 暂不引入跨菜共享选项；基础最小 Cmax 可用于模型验收，多目标由 P2-10 实现。

**设计定位：** 架构文档 12、15.2。

**验收说明：** 具体开发测试见本节记录；统一回归登记于 scripts/verification_manifest.json。正式审核依赖未完成，保持IMPLEMENTED。

<a id="p2-09"></a>
### P2-09 实现串行参考与统一指标计算

**状态：IMPLEMENTED。** 前置任务：[P2-07](#p2-07)、[P2-08](#p2-08)。

**开发验证：** 7项指标与串行参考测试通过，与P2-07/P2-08联合26项通过（8.68秒）。59/60秒休息边界、历史忙碌起点、实际/剩余人工工作、稳定task_id扰动、长准备与固定程序串行参考、无预算不造参考，以及独立拒绝自报错误指标。证据 `benchmarks/reports/verification/P2-07-09-tests.xml`。

**接口裁定（Ruling）：** `build_serial_reference(problem, deadline)`只返回可独立验证的候选；P2-10通过注入的Validator验证后才能用于C_cap或节省时间。验证结果保存在规划结果，不回写原不可变problem，避免problem_hash与参考candidate_hash循环依赖。无有效参考时不生成节省量。

**输入：** 已验证计划、同版本菜谱和当前策略。

**输出：** 可验证 T_serial、Cmax、FinishSpread、MaxHumanBusyBlock 和扰动向量。

**生产/数据文件范围：** `app/scheduling/serial_reference.py`、`app/scheduling/metrics.py`。

**测试文件：** `tests/unit/test_schedule_metrics.py`。

**接口约定：** compute_metrics(schedule, problem) -> ScheduleMetrics；build_serial_reference(problem, deadline) -> SerialReferenceResult。

**实现要点：** 采用同一工艺事实构造逐道参考；忙碌块按 60 秒休息阈值扫描，保留实际/剩余/缓冲指标区别。

**验收断言：**

- [ ] 串行参考不忽略长准备和强制介入；无有效参考时不伪造节省比例。
- [ ] 相隔 59 与 60 秒的人工段按定义进入不同忙碌块判定。
- [ ] 重排历史忙碌起点和 task_id 身份保持，新任务不产生虚假的旧计划扰动。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_schedule_metrics.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 指标准确计算不代表已经优化；不能混用不同模型阶段的界。

**设计定位：** 架构文档 13.1–13.5、18.4。

**验收说明：** 具体开发测试见本节记录；统一回归登记于 scripts/verification_manifest.json。正式审核依赖未完成，保持IMPLEMENTED。

<a id="p2-10"></a>
### P2-10 实现分阶段目标、提示和已验证候选池

**状态：IMPLEMENTED。** 前置任务：[P2-09](#p2-09)。

**输入：** Greedy、CP-SAT、Validator、统一指标与当前 SchedulingPolicy。

**输出：** PlanningEngine、分阶段最佳已验证候选、停止阶段和优化标志。

**生产/数据文件范围：** `app/scheduling/objectives.py`、`app/scheduling/candidate_pool.py`、`app/scheduling/engine.py`。

**测试文件：** `tests/unit/test_objective_stages.py`。

**接口约定：** PlanningEngine.plan(problem, knowledge, runtime, deadline) -> PlanningResult；候选池只接收独立校验通过的方案。

**实现要点：** 实现 A/B/C/D 目标、C_cap 与统一比较器；按潜在人工阶段阈值关闭昂贵的精确忙碌优化，保留准确指标。

**验收断言：**

- [ ] 目标阶段失败或预算耗尽仍保留之前适用且合法的候选。
- [ ] C_cap、240 秒完成差目标和 120 秒/5% 容忍按整数网格计算。
- [ ] 未证明最优明确标记；跳过人工精确阶段时 human_objective_optimized=false。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_objective_stages.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不能用未审核的目标放宽硬工艺；候选阶段界不冒充全局最优证明。

**设计定位：** 架构文档 13、14.5、15。

**验证记录（2026-09-28）：** A/B/C/D、整数C_cap、串行参考、人工忙碌和可选旧计划稳定性已实现。稳定性必须显式传入previous_plan；被独立校验拒绝的阶段不得置优化成功标志。尚无合法候选时优先将剩余求解预算用于可行解。目标/真实主链/进程故障联合21项通过（29.90秒），见 `benchmarks/reports/verification/P2-worker-budget-fix.xml`；完整阶段回归见P2.json。

<a id="p2-11"></a>
### P2-11 实现常驻进程、统一截止时间及主进程回退

**状态：IMPLEMENTED。** 前置任务：[P2-10](#p2-10)。

**输入：** PlanningEngine、sample 知识、明确空运行快照和规划请求。

**输出：** 可在命令行完成的真实规划主链、阶段耗时、主进程保存的已验证候选与过期结果拒绝。

**生产/数据文件范围：** `app/scheduling/budget.py`、`app/scheduling/worker.py`、`app/services/planning_core.py`、`scripts/run_sample_plan.py`。

**测试文件：** `tests/integration/test_planning_budget.py`、`tests/fault_injection/test_solver_worker_failure.py`。

**接口约定：** PlanningCore.compute(request, knowledge, runtime, deadline) -> PlanningResult；此阶段结果尚未作为 SQLite 发布计划。

**实现要点：** 常驻进程负责重计算，API 未来可继续处理事件；编译、构造、优化与验证共用截止时间，异常进程重建不修改实际事实。

**验收断言：**

- [ ] 求解中退出或超时，有适用候选则返回验证结果，无候选则明确失败。
- [ ] 初排 4200/Greedy 150/CP-SAT 3000 ms 的共享预算及预留检查成立；记录真实运行耗时。
- [ ] 样本主链使用真实算法和 Validator；所有成功输出完整，无后台遗留无限计算任务。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_planning_budget.py tests/fault_injection/test_solver_worker_failure.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不在 P2 声称官方接口或事务发布已完成；SQLite 条件发布在 P4-07。

**设计定位：** 架构文档 15.1–15.5、20.4。

**验证记录（2026-09-28）：** SolverWorker常驻spawn进程、单作业锁、过期job/PID/problem身份拒绝、崩溃及挂起终止、PlanningCore和真实CLI完成。5项初版进程/预算测试通过（19.41秒）；其后以合作超时反例复现IPC无返回预算问题并修复，工作进程为结果返回预留最多150ms且不超过阶段剩余一半。批量入口在请求间显式恢复预热并单列时间；请求内始终服从原截止时间。CLI输出整数秒、小数分钟、问题快照和构建报告。100道全量实际验证及限制见P1-09；不包含SQLite条件发布、HTTP或官方协议联调。

<a id="p2-12"></a>
### P2-12 实现不可行诊断并完成基础主链验收

**状态：IMPLEMENTED。** 前置任务：[P2-11](#p2-11)。

**输入：** 问题、约束目录、SolverBuildReport、失败阶段及固定代表样本。

**输出：** DiagnosticReport、P2 集成报告、小规模精确验证和可供 P1-09 使用的单菜测试入口。

**生产/数据文件范围：** `app/scheduling/diagnostics.py`、`scripts/diagnose_problem.py`、`scripts/verification_manifest.json`。

**测试文件：** `tests/integration/test_infeasibility_diagnostics.py`、`tests/integration/test_baseline_end_to_end.py`。

**接口约定：** InfeasibilityAnalyzer.diagnose(problem, build_report, failure_stage, deadline) -> DiagnosticReport。

**实现要点：** 在线只做快速确定检查；深度纯可行性诊断离线执行，假设映射附 Solver build ID；区分基础矛盾与候选/时间域/目标阶段限制。

**验收断言：**

- [ ] UNKNOWN 没有确定根因；假设冲突子集不标称全局最小；2000 ms、单线程及最多 6 次缩减均受限。
- [ ] 关闭跨菜共享时样本强制批次、取放、物料及人工均正确，所有成功方案经过独立校验。
- [ ] uv run --locked python scripts/verify.py --phase P2 通过，并记录没有静默跳过的用例和未支持输入。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_infeasibility_diagnostics.py tests/integration/test_baseline_end_to_end.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不执行 P3 优化或 P4 实际会话；基础路径完整后才允许共享及真实重调度接入。

**设计定位：** 架构文档 15.4、21.1–21.3。

**验证记录（2026-09-28）：** 限时纯可行性诊断、线性约束分组假设及最多6次缩减实现。重新构建的诊断索引用独立diagnostic_build_id绑定，不沿用旧模型索引；全局约束/变量域不机械条件化，覆盖缺口明确输出。UNKNOWN不推断原因，后续目标失败单独检查基础可行性，不宣称全局最小冲突或工艺必然无解。诊断+CLI5项通过（16.93秒），真实基础边界4项通过（21.10秒）；库存与快照证明审查反例连同诊断20项通过（9.75秒）。正式 `uv run --locked python scripts/verify.py --phase P2` 报告保留真实审核和服务门槛，不把development变成正式通过。

<a id="p3"></a>
## P3 跨菜共享优化（7 项任务）

**阶段入口：** P2 已通过；有已审核共享规格、数量关系和允许热批次规则。

**阶段产物：** 共享前处理、共同热批次、物料分配、预热复用及限额控制。

**验收出口：** 7 个任务全部 VERIFIED；T01–T07 类反例和合法共享通过；不同热时长不被错误统一；单独与共享都可选择；同一基准的消融报告包含无收益与失败情况。

**范围边界：** 不开放任意未审核的中途加入或通用异步取放；共享不可行不能删除原菜必需需求。

**交接内容：** 稳定的 carrier/task/material 映射、策略开关和共享反例交给 P4；P2 单独基线仍可运行。

**阶段验收命令：**

~~~text
uv run --locked python scripts/verify.py --phase P3
~~~

| 任务 | 交付内容 | 当前状态 |
| --- | --- | --- |
| [P3-01](#p3-01) | 实现跨菜共享兼容规则与证据查询 | IMPLEMENTED |
| [P3-02](#p3-02) | 生成共享前处理候选并实现精确覆盖 | IMPLEMENTED |
| [P3-03](#p3-03) | 实现共享产出与多菜物料分配 | IMPLEMENTED |
| [P3-04](#p3-04) | 实现同温同模式共同热批次 | IMPLEMENTED |
| [P3-05](#p3-05) | 完善共同批次预热复用、转换和取放边界 | IMPLEMENTED |
| [P3-06](#p3-06) | 接入共享搜索、候选限额和模型降级 | IMPLEMENTED |
| [P3-07](#p3-07) | 完成共享正确性验收和受控收益实验 | IMPLEMENTED |

<a id="p3-01"></a>
### P3-01 实现跨菜共享兼容规则与证据查询

**状态：IMPLEMENTED。** 前置任务：[P2-12](#p2-12)。

**输入：** 本次菜单、静态规格桶、ProcessingRule、成员时间与运行状态。

**输出：** CompatibilityDecision，包含全组可用配置、共享条件及拒绝证据。

**生产/数据文件范围：** `app/knowledge/rules.py`、`app/compiler/compatibility.py`。

**测试文件：** `tests/unit/test_group_compatibility.py`。

**接口约定：** RuleEngine.evaluate_group(members, context) -> CompatibilityDecision。

**实现要点：** 检查加工规格、物料状态、数量范围、设备模式、热时间与已发生事实；用索引筛选后再做运行时判断。

**验收断言：**

- [ ] 相同原料切丝/切块不兼容；祖先后继任务不被强制同步共享。
- [ ] 两两有交集但全组无共同配置的集合被拒绝。
- [ ] 运行中、已完成或未知物料状态不能以静态索引结果直接重新合并。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_group_compatibility.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 兼容意味着允许生成候选，不意味着必须采用共享。

**设计定位：** 架构文档 7.3、9.3、10.2、11.2。

**验证记录（2026-09-28）：** 实现内容绑定的全组规则解释器、编译器接口及动态GroupContext；端口members改为带菜谱实例身份的LogicalTask，context显式包含菜单、运行状态及委托开发开关，避免同工序ID串菜。结构化谓词只解析数据，不执行文本；规则绑定菜谱/工序哈希，保留数量及物料状态；检查全组配置交集、祖先后继、运行事实、未知物料、硬窗口、等长热过程与物理设备状态。委托估计仅在显式development上下文可用，不改变ReviewPatch人工批准约束。拒绝结果保留规则及来源证据。`uv run --locked pytest tests/unit/test_group_compatibility.py tests/contract -m "not review_gate" -q`：110通过，1项正式人工审核门槛排除，报告P3-01-development-tests.xml；功能测试18项。初始缺模块以及后续未知设备、拒绝证据两条测试均先失败后修复。含门槛原运行P3-01-delegated-compatibility.xml为109通过/1个人工门槛失败，未覆盖报告。Ruff与mypy通过；P3-02～07及真实共享主链尚未实施，故阶段不标VERIFIED。

<a id="p3-02"></a>
### P3-02 生成共享前处理候选并实现精确覆盖

**状态：IMPLEMENTED。** 前置任务：[P3-01](#p3-01)。

**输入：** 兼容需求、单独候选和经过审核的共享时长函数。

**输出：** 可选 SharedPrepCandidate、被替代子图与逻辑需求映射、精确覆盖约束。

**生产/数据文件范围：** `app/compiler/shared_prep.py`、`app/scheduling/model_builder.py`、`app/domain/candidates.py`。

**测试文件：** `tests/unit/test_shared_prep_coverage.py`。

**接口约定：** generate_shared_prep(problem_context) -> tuple[SharedPrepCandidate, ...]；每个需求的被选覆盖总数等于 1。

**实现要点：** 保留单独方案，按规则覆盖切配子图而非仅合并名称；共享载体实际资源预约一次。

**验收断言：**

- [ ] 两个成员用一次实际加工满足，原单独操作不重复执行。
- [ ] 共享组与其他共享组重叠时不能双重满足需求。
- [ ] 成员晚就绪导致共享更差时，算法仍能选择单独方案。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_shared_prep_coverage.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不固定 Greedy 的共享选择；共享候选数量限制是搜索策略而非食品加工规则。

**设计定位：** 架构文档 10.1–10.4、12.2。

**验证记录（2026-09-28）：** 内容绑定规则生成可选共享切配载体，保留单独候选及分离的源物料端口；同一人工只预约一次并合并来源证据。Compiler显式shared_prep与allow_delegated_shared_estimates开关接入；CP-SAT覆盖等式支持多个逻辑需求、原子同步端口和重叠组互斥；Greedy全菜单布局比较共享与单独，沿用统一截止时间和设备/物料插入检查。独立Validator从源规则、工序哈希和成员资源重算，不调用生成器。7项共享覆盖测试通过，含晚就绪时强制共享更慢、两种算法保留单独选择，以及伪造时长和人工资源拒绝。与P3-03、P2求解/校验/边界/指标/契约及准备包离线检查联合180通过、1项正式审核门槛单列（72.27秒，P3-02-03-regression.xml）；未声称共同热批次已实现或整个P3通过。

<a id="p3-03"></a>
### P3-03 实现共享产出与多菜物料分配

**状态：IMPLEMENTED。** 前置任务：[P3-02](#p3-02)。

**输入：** 共享载体、物料规格、实际投入与需求数量、出成率和产出端口。

**输出：** 共享计划产物及成员分配关系；独立数量校验。

**生产/数据文件范围：** `app/compiler/material_flow.py`、`app/scheduling/material_constraints.py`、`app/validation/material_balance.py`。

**测试文件：** `tests/unit/test_shared_material_allocations.py`。

**接口约定：** build_material_allocations(carriers, demands, rules) -> MaterialAllocationModel。

**实现要点：** 区分计划供应与真实库存；多批次供应使用显式数量分配；来源和单位从正式知识取得。

**验收断言：**

- [ ] 产量大于两个需求之和时余量可记录但不伪造已完成库存。
- [ ] 损耗、单位不一致或分配超量使候选不可用或模型失败。
- [ ] 共享载体未选中时其产出不可供其他任务使用。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_shared_material_allocations.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 本任务建立计划数量关系；真正完成后的入库、消费及余料利用在 P4 实现。

**设计定位：** 架构文档 6.3、10.5、12.6。

**验证记录（2026-09-28）：** 已增加MaterialAllocationModel：源供应身份、按候选绑定的精确需求份额、损耗和计划余量。数量使用有理数与整数缩放；单位不一致、净产量偏移或超额分配拒绝。选中变量控制供料量，未选中共享载体供料为0；保留源定性份额，不伪造质量出成率与实际库存。独立校验重新扫描源供需及剩余量，可拒绝IR分配篡改。6项分配测试通过，含明确标注合成的300g供应/两份100g/损耗及90g计划余量、真实两端口和未选中候选供料为0。与P3-02联合13项通过；联合回归180通过，报告P3-02-03-regression.xml。跨批实际库存和事件仍属于P4，热候选接入将在P3-04继续扩展。

<a id="p3-04"></a>
### P3-04 实现同温同模式共同热批次

**状态：IMPLEMENTED。** 前置任务：[P3-01](#p3-01)、[P3-03](#p3-03)。

**输入：** 规则允许的跨菜热工序、设备配置交集、成员有效加热时间与人工操作。

**输出：** STRICT_TOGETHER 热批次候选及外层预约/内层成员时间模型。

**生产/数据文件范围：** `app/compiler/thermal_batches.py`、`app/domain/carrier_timing.py`、`app/scheduling/model_builder.py`、`app/scheduling/resource_model.py`、`app/scheduling/calendar_projection.py`、`app/scheduling/layouts.py`、`app/validation/joint_thermal.py`及其直接消费者。顺序相关转换模块`thermal_constraints.py`归P3-05接入。

**测试文件：** `tests/unit/test_joint_thermal_batches.py`。

**接口约定：** generate_thermal_batches(context) -> tuple[ThermalBatchCandidate, ...]；实际腔体按批次预约，成员热暴露逐项校验。

**实现要点：** 烤箱和蒸箱均仅生成温度、模式、湿度、工艺兼容且最终有效加热时长相同的 STRICT_TOGETHER 跨菜候选；不同有效时长保留分批候选，显式菜内固定程序不受此开关影响。

**验收断言：**

- [x] 20 分钟与 8 分钟成员不会被统一加工 20 分钟。
- [x] 同温不同模式、湿度无交集或热窗口不兼容不会误共批。
- [x] 共同批次只预约一次腔体且必要人工段互斥。

- [x] 对蒸箱与烤箱分别验证：兼容的两个 20 分钟成员可选同批；20 分钟与 10 分钟成员不能自由同腔体重叠，也不能偷偷改变工艺时长。
- [x] 同批并非强制：等待另一成员会变差时仍保留单独排程选择；A/B 同批与 C 独立批次的先后由工艺、人工、设备状态和目标共同决定。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_joint_thermal_batches.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不实现自由跨菜 COMMON_START/COMMON_FINISH；原菜谱已明确的中途加入、取出和固定批次仍按审核模板实现。

**设计定位：** 架构文档 11、12.4、21.3。

**验证记录（2026-09-29）：** 26项专项检查通过。H02生成完整10工序载体：装入120＋设置120＋预热300＋热加工720＋取出240＝1500秒，两个热端口均为[540,1260)，6个人工实际阶段合计480秒；源PREPARE→PREHEAT零最大间隔按共同阶段完成端口保留。共同批次覆盖源预约，CP-SAT和Greedy仅预约一次腔体；独立校验从源规则/菜谱重建端口与人工阶段，可拒绝热时长、人工缺失/重叠、预约缺失、配置篡改。合成蒸箱/烤箱20分钟同批通过真实Compiler→CP-SAT→Validator；20/8、20/10、模式/湿度不符、硬窗口无交集拒绝。真实三实例检查允许A/B同批与C单独的两种设备顺序；晚就绪反例中强制合批比单独更慢，Greedy保留单独选择。

**直接消费者修复：** Greedy、物料就绪、完成时间、日历和指标使用成员逻辑偏移；人工工作量按实际阶段计算。人工优化目标原先按源逻辑工序建序列，导致共享切配/热批次被误判不可行；现按载体实际可选人工阶段建序列，未选中阶段跳过，并按实际候选阶段数执行规模阈值。通过真实可行计划固定时间的目标见证测试证明新增目标不破坏合法性；该测试不声称请求预算内完成最优搜索。

**发布边界：** 新增结构化`thermal_model=COLD_LOAD_SETUP_PREHEAT_HEAT_STOP_UNLOAD`明确冷装入、共同阶段释放及加热停止后串行取出的开发假设。现有不可变准备v1没有该字段，仍不生成热批次；专项测试显式在内存增补，未冒充已发布知识或实机验证。后续必须创建新的来源归档与技术规则版本，经真实Neo4j发布和全链路验收后再记录阶段完成。联合回归449通过、1项正式人工审核门槛按已授权开发范围单列，耗时155.93秒；报告`benchmarks/reports/verification/P3-04-regression.xml`与`P3-04-progress.json`。完整P3尚未完成。

**真实发布追加（2026-09-29）：** 新开发版本`development-v3-p3-thermal-v1-all`、规则`development-shared-v2`已完成真实Neo4j投影、快照导出与离线重载；snapshot_id为`snapshot-cae611943e691787fa115b12d4f8972c5f4bce12df52e85bf53e7b18e68edeb2`。新版本明确热边界字段，100道/1562工序只迁移技术版本，工艺修改数0，原P2运行指针不变。旧材料从原发布已校验归档继承，新来源从工作区逐项绑定哈希，不覆盖原资料或旧发布。发布前7项检查通过，真实快照四开关Compiler→Greedy/CP-SAT→Validator集成4项通过；报告P3-thermal-release-tests.xml与P3-thermal-publication-tests.xml。真实发布记录为`data/preparations/p3-thermal-v1/publication_report.json`。这些检查不代替P3-07完整消融、存储链路及性能验收；正式人工审核仍待办。

<a id="p3-05"></a>
### P3-05 完善共同批次预热复用、转换和取放边界

**状态：IMPLEMENTED；整阶段验收待P3-07。** 前置任务：[P3-04](#p3-04)。

**输入：** 前后批次配置、设备观测、已审核转换表及装卸热边界。

**输出：** 共同批次的配置转换、有效预热复用、成员取放阶段与人工预约。

**生产/数据文件范围：** `app/compiler/transitions.py`、`app/scheduling/thermal_constraints.py`、`app/scheduling/placement.py`。

**测试文件：** `tests/unit/test_batch_transitions.py`。

**接口约定：** resolve_transition(previous_state, next_profile, rule_version) -> TransitionPlan。

**实现要点：** 区分冷启动、连续保持、未知温度和已发生转换；插入新批次后重新核对后继转换。

**验收断言：**

- [x] 同模式同温但已关闭设备不自动免预热。
- [x] 插入批次改变后继配置时，Greedy 和 CP-SAT 都验证必要转换与人工窗口。
- [x] 装卸及翻拌不能导致未经允许的过度热暴露；没有规则的变换被拒绝。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_batch_transitions.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 基础强制预热已在 P2 表达，此处扩展跨菜复用和顺序选择。

**设计定位：** 架构文档 11.4–11.5、14.6。

**验证记录（2026-09-29）：** 已接入完整候选时间映射、CP-SAT实际直接前驱序列、Greedy插入后继复核和独立Validator；转换及人工段计入后继外层预约一次。23项热转换测试覆盖关闭、过期、插入、人工争用、真实已完成转换零时长抵扣及较新观测覆盖旧执行热状态。转换抵扣绑定实际来源事件，计划不能充当完成事实。相关回归476 passed、1 deselected（正式人工审核门槛），194.74秒，报告`benchmarks/reports/verification/P3-05-regression.xml`；Ruff检查/格式230文件、mypy app139文件通过。真实Neo4j连接查询通过。原热组第三菜顺序用例已纠正为实际蒸箱LOAD/UNLOAD，并重新纳入回归。转换表及隔热装入假设仅存在于显式合成用例，真实H02保持1500秒冷启动开发规则；无实测转换参数进入发布。

用户已确认删除旧`docs/问题/数据问题.docx`，不再恢复或排查；历史来源测试仅使用原数据哈希一致的发布归档副本，不改工作区资料或审核性质。

<a id="p3-06"></a>
### P3-06 接入共享搜索、候选限额和模型降级

**状态：IMPLEMENTED；整阶段验收待P3-07。** 前置任务：[P3-05](#p3-05)。

**输入：** 单独/共享候选、统一策略、规模预估、Greedy 和 CP-SAT。

**输出：** 共享功能开关可切换的真实规划主链、截断记录和前后规模报告。

**生产/数据文件范围：** `app/compiler/compiler.py`、`app/compiler/pruning.py`、`app/scheduling/greedy.py`、`app/scheduling/model_builder.py`、`app/scheduling/engine.py`。

**测试文件：** `tests/integration/test_shared_search_limits.py`。

**接口约定：** 候选限制初始为每需求 12 个非单独候选、每问题 256 个；强制菜内批次不受优化截断删除。

**实现要点：** 将共享选择交给 Greedy/CP-SAT；安全去重后按预算截断非强制候选，超规模时关闭昂贵附加目标而不删硬约束。

**验收断言：**

- [x] 关掉共享后恢复可验证单独基线，强制工艺仍完整。
- [x] 过量候选明确记录截断与目标阶段，不能声称未截断问题的最优解。
- [x] 去重、构建和求解共用全链路预算；无结果可诚实失败。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_shared_search_limits.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 非等价支配规则仍默认关闭；没有证明的“更快候选”仅作排序。

**设计定位：** 架构文档 9.3、9.5–9.6、14、15。

**验证记录（2026-09-29）：** 共享及热批次改为确定顺序的流式生成，统一截止时间下先按完整语义去重，再按每需求12/每问题256限额保留；触及总限额不继续枚举全空间。记录等价替换与预算截断，后者无伪造替代、明确可能损失最优解；提前停止时生成数量标为非全空间精确计数。独立方案、强制菜内程序、硬依赖不裁剪。SolverBuildReport记录截断、生成计数、保留方案数、剪枝分类和实际模型规模。8项限额专项及23项热转换测试通过；完整相关回归484 passed、1 deselected（人工审核门槛），233.51秒，报告`benchmarks/reports/verification/P3-06-regression.xml`。Ruff检查/格式232文件、mypy app140文件通过。软门槛测试确认不启用昂贵人工/稳定性附加目标且仍独立验证硬约束。

<a id="p3-07"></a>
### P3-07 完成共享正确性验收和受控收益实验

**状态：IMPLEMENTED；开发阶段DEVELOPMENT_VERIFIED，正式人工门槛待办。** 前置任务：[P3-06](#p3-06)。

**输入：** P2 单独基线、P3 共享主链及同版本菜单、预算和设备。

**输出：** P3 集成验收报告、T01–T07/T14–T15 对应的当前能力用例、共享收益与不利案例。

**生产/数据文件范围：** `benchmarks/shared_ablation.py`、`benchmarks/scenarios/shared_cases.json`、`scripts/verification_manifest.json`。

**测试文件：** `tests/integration/test_shared_end_to_end.py`。

**接口约定：** verify.py --phase P3；对照只改变 shared_prep / strict_together_batch 开关，保留源工艺。

**实现要点：** 增加来源覆盖和共享反例，记录规模、时长、人工、首次可行解与回退；运行中加入的完整事件验收交 P4。

**验收断言：**

- [x] 所有成功计划通过独立校验；覆盖共享等待反而更慢的例子。
- [x] 共享前处理、共同热批次分别消融，不把多个变化都归因于图数据库。
- [x] 无收益和失败案例都纳入报告；只描述本阶段真实已实现的静态及给定快照行为。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_shared_end_to_end.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 验收不要求每个菜单必然从共享受益；不以编造更短工艺时长制造收益。

**设计定位：** 架构文档 21.3–21.6。

**验证记录（2026-09-29）：** 正式入口`verify.py --phase P3`保留原P1人工审核依赖；新增显式`--gate development`绑定用户授权文件哈希及待办清单，只在本门槛使用已列明的开发基线回归。完整开发入口511项通过、0失败、0跳过，581.532秒，8个登记项（开发输入门槛＋7个P3任务）全部DEVELOPMENT_VERIFIED，报告`benchmarks/reports/verification/P3-development.json`。验收工具补充9项通过，覆盖开发不能冒充正式、授权篡改拒绝、实验代码/场景指纹及报告递归排除；报告`P3-verifier-final-tests.xml`。最终Ruff检查与236文件格式通过、mypy app140文件通过。验收工具来源补强后的汇总见`P3-development-final.json`，保留原阶段报告。

**真实链路与实验：** 已发布文件仓储→真实Compiler→Greedy→常驻CP-SAT→独立Validator→实验文件保存与重载，6项端到端测试通过；后续SQLite会话、条件发布、执行事件及通知事务仍属P4，没有伪造运行库验收。4场景×4开关×3次重复共48次，只有两项共享开关变化，同场景知识/运行快照哈希和其他策略完全一致。36个成功计划均独立校验；12个合成设备不可用场景全部返回INFEASIBLE_MODEL。真实两道豆豉蒸腐竹：10830→10440秒，减少390秒；人工2760秒不变，共享前处理无收益仍保留。合成迟到成员自动选择单独方案19600秒；强制合批见证19870秒，慢270秒；源操作时长不变。报告`benchmarks/reports/P3-shared-ablation-v1/report.json`、`analysis.json`与README，逐次输入和结果均保存。

**性能及限制：** 固定小样本预热服务P50/P95/最大为2266/3485/3625毫秒，请求预算失败率0；冷启动、工作进程恢复及实验I/O另计。存在32个UNKNOWN求解阶段、25次请求含阶段UNKNOWN、21次工作进程重启；已验证完整候选得到保留，不宣称全部附加目标求至最优。主报告fallback_rate=0仅指没有完全依赖Greedy的最终返回，不能解释为所有优化阶段成功。将串行参考阶段上限从300调整为600毫秒且最多占剩余Solver预算20%，总3秒Solver及4.2秒请求预算没有放宽，防止完整热模型正常传输/建模被过短首阶段终止。

**代表案例定位：** T01/T02由整组兼容、共享覆盖和物料分配反例覆盖；T03由迟到前处理/热批次及上述实验覆盖；T04～T06由真实H02和合成蒸烤20/8、20/10、20/20以及温度/模式/湿度冲突覆盖；T07由单人人工、灶眼及转换人工冲突回归覆盖。T14/T15在本阶段验证计划余量不冒充实际库存、已开始/完成成员不得重新合组；完整运行中加菜与事件事务继续由P4验收。

<a id="p4"></a>
## P4 执行状态与重调度（10 项任务）

**阶段入口：** 基础仓储任务可在 P2 后开始；完整阶段验收要求 P3 通过，具体前置见任务 ID。

**阶段产物：** 真实状态库、幂等事件、物料账、执行模拟、剩余重排、条件发布、恢复及持久化通知。

**验收出口：** 本次授权开发出口为 10 个任务全部 DEVELOPMENT_VERIFIED；原正式入口及外部门槛继续保留。加菜、延误、取消、共享余料与七类恢复案例通过；并发过期结果被拒绝，提交前后故障幂等，模拟与人工事实区分。状态机和相关故障检查已实际运行。

**范围边界：** 完成内部显式会话能力；不因官方动态协议尚未明确而发明会话关联；不实现真实 IoT 控制。

**交接内容：** RuntimeService、ReplanningService、PlanPublisher、通知记录与版本契约交给 P5。

**阶段验收命令：** 本轮运行 development；原正式入口仍保留，未认作已经正式通过。

~~~text
uv run --locked python scripts/verify.py --phase P4 --gate development
uv run --locked python scripts/verify.py --phase P4
~~~

| 任务 | 交付内容 | 当前状态 |
| --- | --- | --- |
| [P4-01](#p4-01) | 建立运行库、仓储和版本化迁移 | DEVELOPMENT_VERIFIED |
| [P4-02](#p4-02) | 实现事件去重与执行状态机 | DEVELOPMENT_VERIFIED |
| [P4-03](#p4-03) | 实现实际物料账、预约消费和库存替代 | DEVELOPMENT_VERIFIED |
| [P4-04](#p4-04) | 实现模拟时钟与执行反馈适配 | DEVELOPMENT_VERIFIED |
| [P4-05](#p4-05) | 编译剩余任务并冻结真实执行事实 | DEVELOPMENT_VERIFIED |
| [P4-06](#p4-06) | 实现加菜、延误和合法取消重排 | DEVELOPMENT_VERIFIED |
| [P4-07](#p4-07) | 实现条件发布、并发冲突和重启恢复 | DEVELOPMENT_VERIFIED |
| [P4-08](#p4-08) | 实现设备异常、释放确认和物料不足 | DEVELOPMENT_VERIFIED |
| [P4-09](#p4-09) | 实现工序失败、恢复规则与新尝试 | DEVELOPMENT_VERIFIED |
| [P4-10](#p4-10) | 完成通知事务、状态机及异常恢复验收 | DEVELOPMENT_VERIFIED |

**P4 最终开发验收（2026-10-02）：DEVELOPMENT_VERIFIED。** 以该最终出口为准，以下各日期的实施中与待办是当时记录，首轮失败及全部历史性能数据仍保存。固定发布为 `delegated-v3-p4-preparation-v1-all`（100 菜、1562 工序、27 设备配置、S02/H02），策略 `p4-runtime-v1`，知识/规则/快照与原批准内容未改写。真实 Compiler、Greedy、CP-SAT、独立 Validator、SQLite 事件/库存/执行/发布/通知贯通。

实际完整命令 `uv run --locked python scripts/verify.py --phase P4 --gate development` 返回 DEVELOPMENT_VERIFIED，退出 0：**733 项测试执行通过、0 失败、0 跳过，1113.688 秒**（含任务间重复回归）。P2/P3 前置实际重跑，全部 P4 必需任务、15 个准备场景、七类恢复及状态机执行通过；没有排除必需测试。质量命令 `ruff check .`、`ruff format --check .`、`mypy app` 全通过，337 文件格式、199 生产源文件。[完整报告](../benchmarks/reports/verification/P4-development.json)、[命令/日志/源码前后指纹](../data/verification/P4-development-command-run2.json)、[质量证据](../data/verification/P4-final-quality-run3.json)、[100/100 历史计划兼容](../data/verification/P4-final-saved-plans-run2.json)。验收前后生产、测试、脚本及基准源码一致；数据哈希、版本、工具环境与实际状态机统计均保留。任务状态使用 DEVELOPMENT_VERIFIED，不改写原正式入口结果。

| 任务 | 实际通过数 | 验收秒数 | 状态 |
| --- | ---: | ---: | --- |
| P4-01 | 4 | 10.125 | DEVELOPMENT_VERIFIED |
| P4-02 | 3 | 94.422 | DEVELOPMENT_VERIFIED |
| P4-03 | 40 | 43.750 | DEVELOPMENT_VERIFIED |
| P4-04 | 6 | 15.437 | DEVELOPMENT_VERIFIED |
| P4-05 | 29 | 87.406 | DEVELOPMENT_VERIFIED |
| P4-06 | 19 | 30.719 | DEVELOPMENT_VERIFIED |
| P4-07 | 9 | 13.391 | DEVELOPMENT_VERIFIED |
| P4-08 | 12 | 14.687 | DEVELOPMENT_VERIFIED |
| P4-09 | 43 | 45.078 | DEVELOPMENT_VERIFIED |
| P4-10 | 47 | 284.157 | DEVELOPMENT_VERIFIED |

状态机采用独立参考模型、确定性 Hypothesis、无外部样本数据库，每类配置 200 条、每条最多 50 步；实际菜单 **220 条/8749 步**，执行账 **223 条/10038 步**，原始统计保存在完整报告的测试输出中。有限样本不等于形式化证明。

前次验收性能（历史代码）：[第五轮完整报告](../benchmarks/reports/P4-runtime-v5/report.json) 绑定该次验收生产源码及驱动哈希，3 个零秒加菜/延误/取消场景各 30 个预热样本及 1 个冷样本，共 186 请求，93 个 SQLite 试验库；成功计划重载后再次独立校验。90 次预热重排 **P50=1901.391ms、P95=2127.233ms、最大=2223.686ms、失败 0、超预算 0、Greedy 最终回退 10 次**。预热准备失败 0 次；初排、冷样本、分阶段 UNKNOWN 原因和最终回退分别保留。

| 请求组 | 样本数 | P50 ms | P95 ms | 最大 ms | 失败 | 超预算 | 最终 Greedy 回退 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 冷进程起始组 初排 | 3 | 3384.604 | 3603.095 | 3603.095 | 0 | 0 | 2 |
| 冷进程起始组 重排 | 3 | 1864.679 | 1897.842 | 1897.842 | 0 | 0 | 1 |
| 预热 初排 | 90 | 3473.800 | 3722.646 | 4515.270 | 1 | 1 | 0 |
| 预热 重排 | 90 | 1901.391 | 2127.233 | 2223.686 | 0 | 0 | 10 |

**尚存性能限制：** 90 次预热初排中 1 次耗时 **4515.270ms**，超过 4200ms 并在发布事务的截止检查处回滚，未提交该初排计划；原始样本为 `samples.jsonl` 第 123 行（`delay-at-zero` 第 30 个预热试验），分阶段观测 Greedy 1063ms、发布 657ms。后续收到延误事实后的重排在 1879.810ms 成功，试验库 `p4-bench-1-30.sqlite` 保留；只读核对唯一提交记录为状态版本 2 的重排、计划版本 1、父版本 0，见 [复核证据](../data/verification/P4-final-initial-timeout-review.json)。该长尾的根因尚未由现有样本确定，不能归因为系统负载或宣称全部初排达标；继续列入 P6 固定硬件性能调优和正式验收。失败统计没有删除，阶段通过表示完整 P4 内部行为及诚实超时处理验收通过。

首组从冷进程开始，其重排可能已就绪，逐行记录 ready 状态。采用高分辨率计量，预热在服务测量之外且至多两次原定 10 秒尝试，请求 4200/2400ms、Greedy 150/100ms、CP-SAT 3000/1500ms、发布 400/300ms 均不增加。这是当前硬件上 3 场景的内部性能证据，未代替 P6 三轮正式/客户端计时验收或任意输入保证。

第四轮的 1 次可行场景失败完整保留在 [v4](../benchmarks/reports/P4-runtime-v4/report.json)，首轮完整门槛 727 通过/2 失败保留在 [run1](../benchmarks/reports/verification/P4-development-run1.json)。针对该错误的 60ms 回退反例先失败，再与耗尽首轮后的版本重算、H02、启动及身份兼容 **15 项无跳过通过**，见 `P4-final-reserve-balance-red.xml`、`P4-final-reserve-balance-fixed.xml`；没有以单次偶然成功替代修复。全部以前的试验、原始材料、准备发布和计划哈希保留，门槛历史对应关系见 `data/verification/P4-gate-history.json`。

交付边界：库存资格与失败恢复机制已实现，真实发布仍没有开放的 INVENTORY_SUPPLY/RECOVERY 工艺依据；合法正向路径用明确合成规则验收。未知结果、未知恢复时刻及没有批准工艺的续做/重做明确拒绝或状态不足，不推定食品保鲜或复杂热过程续做合法。自然语言解析、HTTP、SSE、前端、官方协议、网络通知传输和实机工艺验证仍由 P5 及后续正式阶段处理，本轮交付内部服务与持久通知记录。

**P4 准备增补（2026-09-29）：** 数据、参数与事件触发基线见 [P4 准备基线](P4准备与规则基线.md)，新授权无需再次逐菜确认。P3 以已有开发验收证据进入本阶段，不倒写历史正式验收；准备工作不计入 P4 的 10 项运行实现验收。

**P4 实施进度（2026-09-30，阶段仍为 IN_PROGRESS）：** 已接通 SQLite 事件落账、剩余编译、Greedy/CP-SAT、独立校验和双版本条件发布。新增 `p4-runtime-v1` 策略，在原共享截止时间内加入 D1 累计人工目标与后续阶段上界；历史准备策略保持原内容。真实发布菜谱南瓜鸡肉盅已完成显式模拟执行，消费/产出落账、冷藏等待不占人工。未知原料数量保留 `RECIPE_BATCH` 原配方份额，不声明为克数或实测值。

本批实际证据：`data/verification/P4-progress.xml` 为 24 通过、0 跳过，覆盖迁移升级保留历史、WAL/回滚/有限锁等待、事件幂等、腌制剩余时间、零秒加菜预算、求解中事件合并、提交前回滚/提交后响应丢失、迟到失败冻结、人工超时不自动完成、模拟重放/旧动作撤销、通知撤销及 D1/D2 独立枚举与历史常数。`data/verification/P4-p2-targeted.xml` 为 53 通过、0 跳过，运行 `test_cp_sat_model.py`、`test_schedule_validator.py`、`test_objective_stages.py`、`test_compiler_material_stock.py`；继续使用已核对身份的 `.tools/p2-regression/releases` 副本。`mypy app --cache-dir .tmp/p4-mypy` 已通过 175 个源文件。

**P4 继续实施（2026-10-01，阶段仍为 IN_PROGRESS）：** 已增加独立的未来物料预约表与 `0003_future_allocations` 迁移；计划发布在同一事务中建立预约，实际产出后才绑定实物批次。重排与短缺撤销未消费预约，运行中投入受保护，累计消费只记增量，失败后的部分消费及旧产物保留历史。完成反馈核对实际消费与产出；模拟消费按精确有理数转换 g/kg，并尊重其他执行的预约。已验证提交前中断不留下新计划的预约。

合法 `REMAKE` 的明确合成规则已贯通新执行身份、补料、损耗、真实算法重排及模拟完成；旧失败记录和已耗人工不被覆盖，任意新 ID 不能绕过已批准重试。失败尝试的合格余料也不能在没有库存替代依据时自动进入后续供给。设备别名按同一物理资源更新可用性，恢复不清空占用；释放单个共享占用者保留其他占用并追加实际占用时段；故障打断保留结果待确认状态。通知按载体及阶段区分同时段操作，失败撤销对应待发完成提醒，冲突暂停后不再发送开始提醒。

本轮实际命令以项目已有 `.tools/bin/uv.exe` 执行 `run --locked`，未安装或升级依赖；系统 PATH 原先没有 uv，已定位 README 约定的项目工具。`ruff check .`、`ruff format --check .` 通过（297 个文件已格式化），`mypy app` 通过（180 个源文件，缓存设为 `.tmp/p4-mypy`）。运行 Schema 已用 `python -m scripts.export_schemas` 同步导出。统一回归 `data/verification/P4-runtime-oct01.xml` 为 **110 通过、0 失败、0 跳过，70.33 秒**，包含 44 项 P4 相关测试和 66 项运行契约/P2 回归；具体命令、数据身份与源码哈希登记于 `data/verification/P4-runtime-oct01.json`。独立重载历史 100 份单菜计划（1562 工序）的证据另存 `data/verification/P4-runtime-reservations-100.json`，没有覆盖原准备包和历史计划。

**库存替代继续实施（2026-10-01，P4-03/05/07/10）：** 已接通完整前处理范围的库存候选、Greedy 与 CP-SAT 数量约束、独立校验、条件发布、实际扣料及消费后的剩余任务编译。库存满足单独保存为 `InventoryFulfillment`，不生成虚构的清洗/切配执行或操作通知；未开始的满足记录重排后保留为 SUPERSEDED，实际消费开始后冻结为 COMMITTED。新 `0004_inventory_fulfillments` 迁移保留原表和历史，满足记录与发布身份关联并参与同一事务。规格比较包含形态、尺寸、处理方式和成分；没有明确规则、质量、实际形成证据或有效期依据时不替代。合成规则显式提供 `max_age_sec`，不得解释为真实菜谱的保鲜测量。

新增 21 项库存测试覆盖：80g 真实余料供给新菜 50g、完整两步前处理替代、无规则/不完整范围/错误数量/缺证据/版本不符/未形成/已过期/无有效期拒绝、两菜争用同一批库存、两种算法分别独立校验、五类篡改、重启与通知、实际迟到消费拒绝及未开始预约重排。测试先暴露 Greedy 重复承诺、错误单位异常和迟到消费缺校验，再修复。合并原 P4 及对应 P2/契约回归为 **131 通过、0 失败、0 跳过，88.97 秒**，见 [XML](../data/verification/P4-inventory-checkpoint.xml) 与 [命令/源码指纹记录](../data/verification/P4-inventory-checkpoint.json)。Ruff 检查、格式（307 文件）、mypy（188 源文件）均通过，Schema 已导出。新增 `python -m scripts.check_p4_saved_plans --output <新证据路径>` 重载验证工具，以当前契约及独立校验器重新检查全部 100 份历史计划、1562 工序通过；[本轮独立报告](../data/verification/P4-inventory-saved-plans.json) 保留原计划哈希，没有改写准备证据。

**恢复与共享重试继续实施（2026-10-01，P4-02/05/06/09）：** 新增内容绑定的 `RecoveryRuleSpec`，正式来源的恢复声明须匹配固定知识中已批准的 RECOVERY 规则、版本、证据、菜谱/工序哈希及完整成员；合成规则只允许显式模拟和 `synthetic:` 证据。拒绝规则身份重复、成员重复、执行身份碰撞、早于失败的重试，以及借 REMAKE 剩余时长缩短原工艺。新执行保存 `recovery_rule_id`/`recovery_kind`，旧序列化记录缺省不输出新增字段。已批准的共享重做不再被旧失败记录永久排除，Compiler 保留完整重试成员，Greedy 能安排必需的共享载体，独立 Validator 拒绝绕过 Compiler 的拆组候选。

新增 `test_failed_execution_retry.py` 使用明确合成的两菜精确物料场景贯通：共同开始消费、重复失败去重、原料补入、完整组重做、两种真实算法及独立校验、SQLite 发布与模拟完成。原失败记录和已消费量未恢复，重新投入使用新执行身份；缺成员恢复规则明确拒绝。`test_execution_retry.py` 增加发布内容/版本/身份/时间反例和完整绑定的正向夹具，不把合成 APPROVED 声明当作真实工艺审核。

本轮合并 P4、对应 P2/契约及受影响 P3 共享/热批次/常驻工作进程回归为 **200 通过、0 失败、0 跳过，182.27 秒**；[测试 XML](../data/verification/P4-recovery-checkpoint.xml)、[命令及源码指纹](../data/verification/P4-recovery-checkpoint.json)。Ruff 检查、格式（310 文件）、mypy（190 源文件）通过；运行 Schema 与恢复规则 Schema 已导出。新增恢复字段后，当前独立校验器重载历史单菜计划仍为 **100/100 通过，1562 工序**，见 [兼容性证据](../data/verification/P4-recovery-saved-plans.json)。历史报告、准备包和原工艺均保持原内容。

尚未达到阶段出口：库存多批次分配目前采用首次适配并显式报告枚举不完整，更多分配组合、共享生产余料及强制程序替代还需完善验证；RESUME 尚需完整剩余工艺及真实保留投入表达，不能仅凭 `remaining_sec` 宣称已完成续做；连续设备占用、完整热批次恢复与运行热批次/取消组合、15 个准备场景与七类恢复案例的完整覆盖、状态机属性测试、全量 P3 回归/性能报告及 `P4:development` 登记仍须完成。共享 REMAKE 和固定知识重启恢复已有针对性通过证据，但不等于全部异常恢复场景验收。上述测试包含明确标注的合成工艺/恢复规则和真实授权发布菜谱，不能解释为实机工艺验证。本轮没有执行尚未完成的 P4 阶段验收入口，也没有将任一任务或阶段标记为已完整验收。

**设备事件触发继续实施（2026-10-01，P4-02/06/08）：** 新增 `device_triggers.py`，按固定知识中的物理资源/组件识别设备别名，并检查未完成需求的设备备选、强制外层预约、当前绑定及实际运行占用。旧计划没有选中的合法备用设备故障或恢复也参与触发；已完成、已消费库存满足及未开始即取消的旧绑定不再单独引起重排。无变化观测按物理状态比较，排除设备别名、观测时间和来源身份变化；共享设备全部占用者的变化均纳入比较，不能只看代表性的 `active_execution_id`。

先用真实 Compiler、Greedy、CP-SAT、独立 Validator 和 SQLite 的合成单工序场景复现 **5 个行为失败、1 个通过**，见 `data/verification/P4-device-trigger-behavior-red4.xml`；更早的报告是夹具必填字段/发布资源证据不足，不计作目标行为反例。修正后专项 **12 项通过**，涵盖别名故障切换灶眼、未选备用设备恢复、重复事件与无变化观测、完成/取消后的旧绑定、故障后恢复及清空仍不推定加工结果、错误释放身份拒绝，以及共享第二占用者释放。合成测试复用已发布设备配置和证据，不改变真实菜谱或声明实机验证。

本轮加入既有 P4、相关 P2/P3 回归后 **210 项通过、0 失败、0 跳过**（pytest 217.68 秒；XML 测试集时间 217.29 秒）。[测试 XML](../data/verification/P4-device-trigger-checkpoint.xml)、[命令与源码指纹](../data/verification/P4-device-trigger-checkpoint.json)、[质量结果](../data/verification/P4-device-trigger-quality.json) 已保存。Ruff 检查、格式（312 文件）和 mypy（191 源文件）通过。本轮未变更 Schema，未重新执行历史 100 单菜计划检查；其最近证据仍为上轮 `P4-recovery-saved-plans.json`。前述连续设备占用、完整 RESUME、库存组合和完整阶段验收待办继续保留，阶段及十项任务仍为 IN_PROGRESS。

**连续设备占用继续实施（2026-10-01，P4-02/04/05/08）：** 发布绑定保存来自强制外层预约的完整工艺范围，实际占用保存对应预约身份与成员。装入等中间工序完成后释放主动人工，设备保持持有；下一合法成员开始时，在同一事件事务内交接到新的 `execution_id`。交接晚于上一工序完成时追加中间实际持有区间，保留原工序实际结束时刻；旧执行的迟到清空不能释放新执行。模拟器不再把中间阶段完成报告为腔体清空。明确提前清空仍落实际事实，并暂停未完成连续工艺；故障恢复不推定已获得合法续做路径。

Compiler 保留仍在执行的完整外层预约，Greedy 合并其已发生区间而不与自身重复冲突；CP-SAT 根据实际资源段固定原物理设备。独立 Validator 从原知识重建完整预约范围，拒绝篡改占用身份，且仍扫描其他菜的物理冲突。兼容冷藏允许其他执行同时占用，交接只改变本工艺的占用者。取消后的真实持有保留至明确清空，重启恢复绑定与占用身份。新增可选字段缺省不输出，旧准备包的序列化及哈希保持兼容，已重新导出运行 Schema。

先复现 **5 个行为失败**（`P4-continuous-red.xml`），修正后连续占用专项 **15 项通过**，含两种真实算法、备选灶眼冻结、独立反篡改、提前清空、延迟交接、外来抢占拒绝、取消、兼容共享、故障及重启。完整相关回归 **225 项通过、0 失败、0 跳过，196.54 秒**，见 [测试 XML](../data/verification/P4-continuous-checkpoint.xml) 和 [执行命令/源码指纹](../data/verification/P4-continuous-checkpoint.json)。Ruff 检查、格式（314 文件）、mypy（192 源文件）通过。当前契约与独立校验器重载历史单菜计划 **100/100 通过，1562 工序**，见 [兼容性报告](../data/verification/P4-continuous-saved-plans.json)。中间关联回归的两项旧数据权限失败已在设定既有 P2 副本环境后的完整回归中通过；未跳过测试或修改原始发布数据。

本次完成普通连续工艺的占用和交接链路，未以此代替复杂热批次恢复验收。后续仍须完成完整 RESUME、实际后续阶段时刻、热批次部分完成/重试/取消组合、库存多批次与共享余料、15 场景及七类恢复案例全覆盖、属性/状态机测试、全量 P3/性能结果和 `P4:development`。十项任务及阶段仍为 IN_PROGRESS，不声明整个 P4 已完成。

**续做与统一计算预算继续实施（2026-10-02，P4-05/06/07/09）：** 新增 `ResumeProcedure` 与持久化 `ResumptionEvidence`。单独工序及共享前处理的 `SAME_PROCESS` 续做必须确认原载体、完整成员、原物料批次和数量、原资源配置、合格保留状态及最长中断时窗；仅有 `remaining_sec` 和消费报告的旧声明明确拒绝。新执行保留父执行内容哈希及授权事件，不将已消费投入还原为原料，不重复建立消费预约。Compiler 从授权生成剩余载体，原发布工序保持内容身份；Greedy、CP-SAT 与独立 Validator 均核对保留投入和剩余工艺。开始过期及提前完成拒绝，旧失败记录保留；真实发布仍没有开放的恢复规则，正向路径使用明确合成工艺，未以此声明复杂热过程续做已完成。

新增 22 项续做验收涵盖单独/共享执行、审核内容绑定、数量/范围/资源/质量/时间反例、独立伪造拒绝、重启、运行中重排及累计扣料。跨版本冲突的第二次计算共用 Greedy 和 CP-SAT 时间余额，保持重排 CP-SAT 合计 1500 ms；先复现约 1812 ms 的合成超支，再修正并通过 4 项预算/事件合并测试。相关回归发现常驻进程短阶段反复丢弃未分配作业的预热，以及首次构建报告导入影响就绪；已保留有上限的同一次预热并预载报告依赖，真实作业超时仍终止并拒收过期结果。工作进程、崩溃/挂起、短阶段及 P3 四开关专项 14 项通过。

上述变更的相关回归为 **256 项通过、0 失败、0 跳过，285.52 秒**，见 `data/verification/P4-resume-final.xml` 和 `P4-resume-final-command.json`。保留首轮 `P4-resume-checkpoint.xml` 的 246 通过、1 个 P3 工作进程构建证据失败，以及后续针对性修复证据；未放宽断言。新契约经当前独立校验器重载历史单菜计划 **100/100 通过，1562 工序**，见 `P4-resume-saved-plans.json`，已同步导出 Schema。该 256 项结果早于随后热批次资源申请修复，后续源码仍须相应回归，不将旧结果解释为最新所有变更已经通过。

**状态机验收继续实施（2026-10-02，P4-02/03/10）：** 已新增指定的 `test_event_idempotency.py` 与 `test_runtime_invariants.py`，采用独立菜单/版本参考模型和精确 10g 参考账，运行真实 SQLite；执行参考账的初始发布运行真实 Compiler、算法和 Validator。每类配置 200 条、每条最多 50 步序列、确定性 Hypothesis 生成并关闭外部数据库。实际 **220 条菜单序列/8749 步**、**223 条执行序列/10038 步**，两项测试无跳过通过，330.11 秒，见 `data/verification/P4-stateful-all.xml`（测试集属性含实际数量）。覆盖重复/异内容事件、旧版本/非法来源、取消/延误/结束、累计消费增量与倒退/超量拒绝、完成/失败不改写、未知/报废产物不可用、人工释放和重启。样本统计保存在对应测试产物目录，不把有限样本称为形式化证明。

当前继续实施真实 H02 运行批次加菜、取消和内部阶段反馈，已复现共享主动阶段重复申请同一人工的异常并开始修复；该链路及复杂热批次恢复仍未通过。库存组合/共享余料、15 个准备场景及七类恢复案例的完整登记、全量回归/性能报告和 `P4:development` 仍须完成，十项任务及阶段保持 IN_PROGRESS。

**P4 最终边界复查（2026-10-02，阶段仍为 IN_PROGRESS）：** 已将准备包的 15 个设计场景转为真实内部调用测试，并补齐架构 16.9.4 的七类故障恢复测试。新增独立的运行中投入检查：累计只投入 3g 的 10g 工序仍欠 7g，盘点余量降至 6g 后事件和原消费保持落账，返回 STATE_INCOMPLETE 并暂停派发；补足实物可重新发布，独立 Validator 也拒绝绕过服务的不足计划。针对性结果 [14 项通过](../data/verification/P4-input-debt-fixed.xml)，保留原 [3 项失败反例](../data/verification/P4-input-debt-red.xml)。

取消最后一道未开始菜品现在发布已独立校验的空计划，撤销旧提醒，允许随后加菜；取消最后一道运行菜仍保留原执行、主动人工历史及实际占用，收到完成反馈后才释放。发布层增加对会话全部当前菜单的核对，拒绝“仅取消一菜却省掉另一菜”的空计划。发布事务回滚与响应丢失保留原编译/求解记录并新增 PUBLICATION 耗时；[本轮 10 项通过](../data/verification/P4-empty-publish-fixed.xml)，原先菜单核对和耗时缺口的失败证据分别为 `P4-cancel-empty-publish-red.xml`、`P4-publish-timing-red.xml`。热批次、投入、库存与七类恢复组合检查为 34 通过、2 失败（两项空计划随后按上述证据修复），完整原记录为 `P4-input-cancel-thermal-fixed.xml`，不改写失败报告。

新增 `P4:development` 验收登记绑定既有代理授权内容哈希、P4 准备包、重新执行的 P2/P3 前置回归和十项 P4 测试，原正式 `P4` 入口保持不变。`P4-development-entry-fixed.xml` 为 11 项通过，包含 100 份历史单菜计划重新独立校验及入口防伪回归。知识按完整会话菜单提取原菜谱，取消后的历史来源仍保留，全部设备、规则、证据与固定版本保持相同；未删工序或改变求解预算。首轮真实 SQLite 性能报告 [P4-runtime-v1](../benchmarks/reports/P4-runtime-v1/report.json) 记录 90 次预热重排 P50/P95/最大为 2234/2359/2844 ms，2 次失败、37 次最终 Greedy 回退；包含冷启动循环和初排统计。优化后的第二轮采样及最终完整验收仍在运行，尚不声明阶段完成。

**P4 阶段进度边界与采样补充（2026-10-02）：** 真实 H02 热批次的三项反例已经修复：当前加热阶段已超过预计结束时，不利用后续未开始阶段的时长掩盖缺失反馈；内部阶段未确认实际开始且计划时刻已过时，返回 STATE_INCOMPLETE；收到明确开始反馈后保留已完成阶段、更新实际人工边界并可恢复发布。绕过 Runtime 投影的同类旧进度也由独立 Validator 以 HISTORY_PROGRESS 拒绝。[组合验证 14 项通过、0 跳过](../data/verification/P4-stage-final-fixed.xml)，原失败证据为 `P4-internal-overdue-red.xml`、`P4-internal-validator-red.xml`。

已补充合成共享产出余料全链路：同一共享执行形成两批各 100g 产物，原菜分别实际消耗 20g 后各剩 80g；明确的合成库存规则从其中一批为新增菜分配并实际消耗 50g，余量为 30g，原共享执行不重做、不生成虚构切配反馈，两种算法分别通过独立校验。与共享失败重做回归共 [3 项通过](../data/verification/P4-shared-leftover-final.xml)。规则、数量、规格及有效期均明确为合成，不扩展真实发布的权限。

第二轮性能采样因初排后的进程预热未就绪而中断，已完成 66 次重排均成功，保留全部原样本、SQLite 库及 [INCOMPLETE 中断记录](../benchmarks/reports/P4-runtime-v2/interruption.json)，不把不完整样本报告为通过。基准准备现在至多尝试两次原定 10 秒预热，逐次记录失败与耗时；准备时间在服务测量之外，只有确认就绪后才列为 WARM，服务 4200/2400 ms 及求解预算不变。第三轮采样绑定全部实际生产源码和基准文件指纹；新增验收直接核对 186 条请求、每场景 30 条预热重排、百分位、来源及源码哈希，缺少报告时已实际失败，原证据为 `P4-benchmark-proof-red.xml`。当前 Ruff 检查、335 文件格式和 mypy app 199 文件通过，最终阶段验收尚未结束。

**P4 首轮阶段验收与复查（2026-10-02，仍为 IN_PROGRESS）：** 实际执行完整 `uv run --locked python scripts/verify.py --phase P4 --gate development`，首轮为 **727 通过、2 失败、0 跳过，1295.094 秒**，结果 FAILED。完整失败报告另存 [P4-development-run1.json](../benchmarks/reports/verification/P4-development-run1.json)，命令、日志和前后源码指纹见 `data/verification/P4-development-command.json`、`.log`；本轮验收期间生产与测试源码未变化。失败为迟到批次内部阶段确认后的完整候选构造，以及跨版本重算时第一轮可能耗尽 Greedy 余额；单独重跑两条路径通过，按任务组合又复现批次构造失败与一次冷启动失败，原结果均保留，不按偶然单次成功宣称已解决。

新增可复现的“第一轮真实 Greedy 找到候选后消耗至分配截止时间”故障注入及启动上下文有限重试测试，修复前 **3 项实际失败**，见 `P4-final-budget-startup-red.xml`。两次计算共用原 Greedy 余额，首轮保留后轮构造时间；CP-SAT 总余额及请求截止不重新领取。冻结布局改用工序/预约索引和内部字符串身份做窗口传播，内容身份在编译阶段物化并受原请求截止约束，模型字段与哈希语义不变。生命周期冷启动准备最多重试一次原定 10 秒尝试，请求内 warmup/solve 仍服从原截止；失败和重启计数保留。首次修复为 18 通过、1 个批次失败，进一步消除固定开销后，`P4-final-layout-fixed2.xml` **22 通过、0 失败、0 跳过，95.47 秒**，含全部 7 项 H02 阶段、两种预算反例、真实进程与旧身份兼容。Ruff 和 mypy 199 文件已通过，最终格式与当前源码性能仍须重测。

本机 Python 3.12 的实际预算时钟为 GetTickCount64（15.625 ms 分辨率），新性能采样以更高分辨率的 QueryPerformanceCounter 记录服务外部耗时，并分别保存两种时钟信息；业务和请求截止时钟保持原契约。前三轮性能数据保留为对应源码证据，第四轮将绑定当前生产源码，仍为每场景 30 次预热、3 个冷样本及 186 条总请求，不将历史 v3 成绩认作最新源码已经达标。完整阶段出口尚未通过，十项状态与断言继续保留为未验收完成。

<a id="p4-01"></a>
### P4-01 建立运行库、仓储和版本化迁移

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P2-12](#p2-12)。

**输入：** P0 运行契约、SQLite 单实例边界和数据所有权。

**输出：** 会话、事件、执行、物料、设备、计划、通知、诊断及审计表，UnitOfWork 和迁移历史。

**生产/数据文件范围：** `app/storage/models.py`、`app/storage/repositories.py`、`app/storage/unit_of_work.py`、`alembic.ini`、`app/storage/migrations/versions/0004_inventory_fulfillments.py`。

**测试文件：** `tests/integration/test_runtime_storage.py`。

**接口约定：** UnitOfWork.transaction()；仓储读出领域对象，不把 SQLAlchemy 对象交给 Solver。

**实现要点：** 使用 WAL、短事务、唯一性与外键约束；建立 event_id、计划版本及分配幂等键，事务超时受预算限制。

**验收断言：**

- [x] 迁移新库及升级已有样本库都保留历史，重复迁移幂等。
- [x] 唯一事件身份、计划版本和物料引用约束真实生效。
- [x] 提交前异常无部分写入，锁竞争不会无限阻塞。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_runtime_storage.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不把 Neo4j 和 SQLite 做运行事件双写；不接入微服务或多写者集群。

**设计定位：** 架构文档 19、15.5。

**验证记录（2026-10-02）：** P4-01 在完整 development 出口中 **4 通过、0 失败、0 跳过，10.125 秒**，依赖和质量检查通过。4 版 Alembic 迁移、WAL/外键/唯一性、升级保留历史、事务回滚与有限锁等待。实际命令与逐项输出见 [完整验收报告](../benchmarks/reports/verification/P4-development.json)、[第二轮命令及源码指纹](../data/verification/P4-development-command-run2.json)。

<a id="p4-02"></a>
### P4-02 实现事件去重与执行状态机

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P4-01](#p4-01)。

**输入：** RuntimeEvent、预期 state_revision、当前会话与 UnitOfWork。

**输出：** EventApplyResult、新状态版本、审计记录和重排触发标记。

**生产/数据文件范围：** `app/runtime/service.py`、`app/runtime/event_validation.py`、`app/runtime/state_machine.py`、`app/runtime/triggers.py`。

**测试文件：** `tests/unit/test_execution_state_machine.py`、`tests/stateful/test_event_idempotency.py`。

**接口约定：** RuntimeService.apply_event(event: RuntimeEvent) -> EventApplyResult；同 ID 同载荷返回既有结果，同 ID 异载荷冲突。

**实现要点：** 处理开始、完成、取消与状态观测；非法转换拒绝，按执行身份处理迟到事件，模型调用结果不能直接落成状态。

**验收断言：**

- [x] 重复开始/完成不重复改变业务效果；完成不能退回 PENDING。
- [x] 旧版本或非法来源拒绝后账本保持不变。
- [x] 开始确认和计划起始不同，时间到达不自动完成人工模式的工序。
- [x] 完成人工拌料后立即结束该人工占用，腌制独立继续计时；重复完成不重复释放，预计时间到达不自动释放。
- [x] 按触发表生成 requires_replan 和理由；无变化观测、重复事件及按计划完成且剩余计划仍合法时不重复全量求解。
- [x] human_1 已被主动操作占用时，另一道菜的主动开始请求不能作为合法并行接受；矛盾的实际反馈需记录冲突，不能自动增加人手。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_execution_state_machine.py tests/stateful/test_event_idempotency.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 事件处理先提交事实，仅发出重排需求，不在事务中启动 Solver。

**设计定位：** 架构文档 16.1–16.4、19.3。

**验证记录（2026-10-02）：** P4-02 在完整 development 出口中 **3 通过、0 失败、0 跳过，94.422 秒**，依赖和质量检查通过。18 类结构化事件、先去重后检查版本、来源及转换校验、冲突审计、人工占用互斥与无变化触发抑制；独立状态机参考模型。实际命令与逐项输出见 [完整验收报告](../benchmarks/reports/verification/P4-development.json)、[第二轮命令及源码指纹](../data/verification/P4-development-command-run2.json)。

<a id="p4-03"></a>
### P4-03 实现实际物料账、预约消费和库存替代

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P4-02](#p4-02)、[P3-03](#p3-03)。

**输入：** 实际执行事件、物料批次、计划分配和已审核库存替代规则。

**输出：** 追加式物料账、合法余料供应、预约撤销与可追溯消费记录。

**生产/数据文件范围：** `app/runtime/material_ledger.py`、`app/runtime/inventory.py`、`app/compiler/inventory_supply.py`、`app/runtime/material_reports.py`、`app/runtime/running_inputs.py`、`app/validation/running_inputs.py`。

**测试文件：** `tests/integration/test_material_ledger.py`、`tests/integration/test_inventory_substitution.py`、`tests/unit/test_compiler_material_stock.py`、`tests/integration/test_running_material_inputs.py`、`tests/integration/test_shared_leftover_replanning.py`。

**接口约定：** apply_material_effects(event, session, previous=None, source_specs=None) -> RuntimeSession；重复事件不重复扣料或产出。

**实现要点：** 区分事件报告的累计消费与新增账目，按执行身份核对差量避免开始/完成/失败重复记账；实际消费不随重排撤销。

**验收断言：**

- [x] 共享已完成后新增菜只能使用真实余量，不能重写原加工数量。
- [x] 预约取消可释放尚未消费部分，已消费部分保持历史。
- [x] 产物未实际形成、规格不符或有效期不满足时不能库存替代。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_material_ledger.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 库存不能成为无来源无限供应；未审核食品有效期不自行估计成硬规则。

**设计定位：** 架构文档 6.3、10.5、16.6、19。

**验证记录（2026-10-02）：** P4-03 在完整 development 出口中 **40 通过、0 失败、0 跳过，43.750 秒**，依赖和质量检查通过。追加式累计消费增量、真实产出/损耗/盘点、未来预约撤销、运行中欠料及完整库存资格检查；共享 80g 余料实际供给新增菜 50g 后余 30g，两种算法独立校验。实际命令与逐项输出见 [完整验收报告](../benchmarks/reports/verification/P4-development.json)、[第二轮命令及源码指纹](../data/verification/P4-development-command-run2.json)。

<a id="p4-04"></a>
### P4-04 实现模拟时钟与执行反馈适配

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P4-02](#p4-02)。

**输入：** 已确认执行模式、候选计划的操作安排、注入事件与可控制时钟。

**输出：** 显式 SIMULATED 事件序列、人工确认适配及确定性场景推进。

**生产/数据文件范围：** `app/runtime/clock.py`、`app/runtime/feedback.py`、`app/runtime/simulator.py`。

**测试文件：** `tests/unit/test_simulation_clock.py`、`tests/integration/test_real_runtime_flow.py`。

**接口约定：** Simulator.advance(to_offset_sec: int) -> tuple[RuntimeEvent, ...]；Clock 区分单调截止时间和业务时间。

**实现要点：** 模拟器根据当前有效计划和已发生事实调度事件；废弃计划的未来模拟动作撤销，真实反馈保留。

**验收断言：**

- [x] 相同场景与种子产生相同事件；计划改变不触发已经撤销的未来开始。
- [x] 人工模式只提醒，不自动完成；超出预计结束不自动释放设备。
- [x] 预生成未来扰动只由模拟器持有，Planner 输入仅包含已发生事件。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_simulation_clock.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不连接真实硬件，不能把模拟结果标作设备实测。

**设计定位：** 架构文档 16.3、21.7。

**验证记录（2026-10-02）：** P4-04 在完整 development 出口中 **6 通过、0 失败、0 跳过，15.437 秒**，依赖和质量检查通过。确定性模拟、业务与求解时钟分离、人工确认释放主动占用、预计结束只提醒、撤销旧计划未来动作；运行真实授权菜谱。实际命令与逐项输出见 [完整验收报告](../benchmarks/reports/verification/P4-development.json)、[第二轮命令及源码指纹](../data/verification/P4-development-command-run2.json)。

<a id="p4-05"></a>
### P4-05 编译剩余任务并冻结真实执行事实

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P4-03](#p4-03)、[P4-04](#p4-04)、[P3-07](#p3-07)。

**输入：** 新 RuntimeSnapshot、当前菜单和计划、库存及运行中载体。

**输出：** 剩余 SchedulingProblem、受影响闭包、固定资源及工序映射。

**生产/数据文件范围：** `app/runtime/replanning.py`、`app/runtime/stage_progress.py`、`app/compiler/runtime_constraints.py`、`app/compiler/compiler.py`。

**测试文件：** `tests/integration/test_replan_freezing.py`、`tests/integration/test_running_thermal_batch.py`、`tests/integration/test_cancelled_execution_history.py`、`tests/integration/test_continuous_device_occupation.py`、`tests/unit/test_problem_identity_cache.py`。

**接口约定：** prepare_replan(session_snapshot, event_result, knowledge, policy) -> ReplanRequest。

**实现要点：** 跨菜共享映射按成员 task_id 跟踪；已完成事实只作为输入，运行中非中断批次保留实际占用；受影响范围用于分析，不保证仅局部就足够求解。

**验收断言：**

- [x] 重排不会移动已完成时间、复做已消费工序或提前释放运行设备。
- [x] 已开始腌制按实际起点保留已等待时间，不从零计时；等待不锁人工，下一主动操作重新申请 human_1。
- [x] 运行中批次不接受未经规则允许的新成员。
- [x] 给定库存、时间原点和设备状态与编译报告一致，旧缓存不跨 state_revision 复用。

- [x] 冻结映射到物理资源及实际工作状态：同腔体正在蒸制时不能用烤制别名插入任务；兼容温区剩余存放不被无故全串行，故障未释放的资源不能提前复用。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_replan_freezing.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不以扰动惩罚替代冻结硬约束；未知剩余时间不直接赋 0。

**设计定位：** 架构文档 16.5–16.7。

**验证记录（2026-10-02）：** P4-05 在完整 development 出口中 **29 通过、0 失败、0 跳过，87.406 秒**，依赖和质量检查通过。完整未完成需求、腌制实际起点、H02 批次成员与内部实际阶段、连续设备持有、取消后历史和占用保留、剩余观测老化及独立 HISTORY_PROGRESS 拒绝；旧序列化身份兼容。实际命令与逐项输出见 [完整验收报告](../benchmarks/reports/verification/P4-development.json)、[第二轮命令及源码指纹](../data/verification/P4-development-command-run2.json)。

<a id="p4-06"></a>
### P4-06 实现加菜、延误和合法取消重排

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P4-05](#p4-05)。

**输入：** 已经提交的 ADD_RECIPE、DELAY_RECIPE、CANCEL_RECIPE 事件及剩余问题。

**输出：** 完整新候选计划、扰动向量、失败或需要状态补充的明确结果。

**生产/数据文件范围：** `app/services/replanning.py`、`app/services/planning.py`、`app/scheduling/budget.py`、`app/scheduling/objectives.py`、`app/scheduling/ranking.py`、`app/services/session_knowledge.py`。

**测试文件：** `tests/integration/test_menu_events_replanning.py`、`tests/unit/test_total_human_objective.py`、`tests/integration/test_replan_aggregate_budget.py`、`tests/integration/test_planning_budget.py`、`tests/integration/test_cancel_all_recipes.py`、`tests/unit/test_session_knowledge.py`、`tests/integration/test_replan_greedy_reserve.py`。

**接口约定：** ReplanningService.compute(request: ReplanRequest, deadline) -> PlanningResult。

**实现要点：** 新增菜来自固定库，取消只作用于可撤销需求；重排 2400 ms/Greedy 100/CP-SAT 1500 ms 共享预算，保留旧事实。

**验收断言：**

- [x] 累计人工用时独立 D1 阶段保持出菜达标与总耗时上界；共享只计一次、被动等待不计，后续连续忙碌和稳定性阶段不使上层指标变差。
- [x] 比较器、Greedy、求解阶段及报告采用统一目标口径；覆盖总人工更少的选择、运行历史固定常数、超时保留完整合法候选。
- [x] 加菜后的成功结果覆盖全部当前需求，旧计划缺新菜不能直接回退为成功。
- [x] 延误不创造未经确认的硬截止；取消运行菜不立即释放设备。
- [x] 失败保留已提交事件和状态，旧计划派发必须根据新状态重新判定。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_menu_events_replanning.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 先完成内部显式会话语义；不猜测官方重复数组代表全量还是增量。

**设计定位：** 架构文档 13.5、15、16、23。

**验证记录（2026-10-02）：** P4-06 在完整 development 出口中 **19 通过、0 失败、0 跳过，30.719 秒**，依赖和质量检查通过。加菜/延误/合法取消及全部取消空计划、完整新菜单覆盖、D1 累计人工与 D2 独立枚举、历史固定和稳定性上界；显式零秒重排与跨版本共享预算。Greedy 首轮截止预算为 75ms，后轮用实际余额，共享 100ms 请求额度；同时验证 60ms 当前构造与耗尽首轮后的重算。实际命令与逐项输出见 [完整验收报告](../benchmarks/reports/verification/P4-development.json)、[第二轮命令及源码指纹](../data/verification/P4-development-command-run2.json)。

<a id="p4-07"></a>
### P4-07 实现条件发布、并发冲突和重启恢复

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P4-06](#p4-06)。

**输入：** 已验证候选、基准状态/计划版本、知识引用及持久化发布身份。

**输出：** PublishedPlan 或 PublishConflict、不可变计划历史及恢复后的活动状态。

**生产/数据文件范围：** `app/services/publishing.py`、`app/runtime/recovery.py`。

**测试文件：** `tests/integration/test_plan_compare_and_swap.py`、`tests/fault_injection/test_commit_boundary.py`、`tests/fault_injection/test_planning_publish_recovery.py`、`tests/integration/test_runtime_recovery.py`。

**接口约定：** PlanPublisher.publish(candidate, context: PublishContext) -> PublishedPlan | PublishConflict；状态条件不符不能覆盖。

**实现要点：** 校验和耗时求解在事务外；发布检查版本并原子写计划与通知意图；提交结果未知通过持久身份查询，有限重算受原截止时间约束。

**验收断言：**

- [x] 求解中收到完成或故障事件，旧结果不能发布。
- [x] 提交前崩溃无半计划；提交后响应丢失，重复请求不产生第二次发布。
- [x] 重启恢复旧知识引用与真实执行，不因计划已过时就自动补完成。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_plan_compare_and_swap.py tests/fault_injection/test_commit_boundary.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 条件发布是正式成功返回的必要条件；不能只凭内存缓存判断提交成功。

**设计定位：** 架构文档 16.8、19.3、20.2。

**验证记录（2026-10-02）：** P4-07 在完整 development 出口中 **9 通过、0 失败、0 跳过，13.391 秒**，依赖和质量检查通过。事务外求解、状态/计划双版本 CAS、至多一次原预算重算、原子计划/预约/通知、提交前回滚、提交后响应丢失的持久身份恢复、重启与固定知识缺失拒绝。实际命令与逐项输出见 [完整验收报告](../benchmarks/reports/verification/P4-development.json)、[第二轮命令及源码指纹](../data/verification/P4-development-command-run2.json)。

<a id="p4-08"></a>
### P4-08 实现设备异常、释放确认和物料不足

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P4-07](#p4-07)。

**输入：** DEVICE_UNAVAILABLE、DEVICE_RECOVERED、DEVICE_RELEASE_CONFIRMED、MATERIAL_SHORTAGE/MATERIAL_ADJUSTED。

**输出：** 独立可用性与占用状态、库存调整账、阻塞标记及重排请求。

**生产/数据文件范围：** `app/runtime/device_events.py`、`app/runtime/device_triggers.py`、`app/runtime/execution_resources.py`、`app/runtime/continuous_occupation.py`。

**测试文件：** `tests/integration/test_device_and_material_failures.py`、`tests/unit/test_device_material_exceptions.py`。

**接口约定：** 异常语义按 execution_id 和 observed_at 生效；expected_recovery_at 可空，不等于实际释放。

**实现要点：** 先提交真实故障与盘点变化再重排；未知恢复排除该设备未来新任务，必要时 STATE_INCOMPLETE；余料不足撤销未来分配。

**验收断言：**

- [x] 设备恢复但未清空不能复用；旧执行释放事件不能释放新执行占用。
- [x] 同一盘点调整重复投递只生效一次；历史消费不恢复。
- [x] 有合法替代设备可求新计划，无路径时明确失败或状态不足。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_device_and_material_failures.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** DEVICE_UNAVAILABLE 不自动证明操作失败；故障结束估计不能代替恢复/清空确认。

**设计定位：** 架构文档 8.3、15.5、16.9。

**验证记录（2026-10-02）：** P4-08 在完整 development 出口中 **12 通过、0 失败、0 跳过，14.687 秒**，依赖和质量检查通过。设备可用性与实际占用分离、设备别名和兼容共享、精确执行释放、恢复不清空/不推定结果、未知恢复阻塞、盘点不恢复历史消费。实际命令与逐项输出见 [完整验收报告](../benchmarks/reports/verification/P4-development.json)、[第二轮命令及源码指纹](../data/verification/P4-development-command-run2.json)。

<a id="p4-09"></a>
### P4-09 实现工序失败、恢复规则与新尝试

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P4-08](#p4-08)。

**输入：** OPERATION_FAILED、OPERATION_RETRY_REQUESTED、MANUAL_OVERRIDE 与审核过的恢复路径。

**输出：** 不可变失败记录、合格部分产物或损耗、新 execution_id 和审计修正记录。

**生产/数据文件范围：** `app/runtime/execution_failures.py`、`app/runtime/resumption.py`、`app/domain/recovery.py`、`app/validation/resumption.py`。

**测试文件：** `tests/integration/test_execution_retry.py`、`tests/integration/test_failed_execution_retry.py`、`tests/integration/test_resume_execution.py`。

**接口约定：** request_retry(session, event, knowledge) -> RuntimeSession（由 RuntimeService.apply_event 处理重试事件）；保留旧尝试全部历史。

**实现要点：** 共享载体失败影响全部成员及分配；半成品未知不可用；续做和重做区分，白名单修正不改写已完成事实。

**验收断言：**

- [x] 同一失败重复到达不重复报废或扣料。
- [x] 重试创建新身份，旧失败不被覆盖；原料已消费需新投入或合法可续做物料。
- [x] 完成与迟到失败冲突登记并暂停相关派发，不能按最后到达强行覆盖。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_failed_execution_retry.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不现场发明续烤或补救时间，不以人工 override 绕过工艺校验。

**设计定位：** 架构文档 16.9.2–16.9.4。

**验证记录（2026-10-02）：** P4-09 在完整 development 出口中 **43 通过、0 失败、0 跳过，45.078 秒**，依赖和质量检查通过。共享失败与损耗幂等、完整规则内容/成员/版本绑定、合法 REMAKE 新执行身份、具备保留投入与工艺证据的 SAME_PROCESS RESUME、迟到失败审计暂停；无规则及复杂热过程未知路径明确拒绝，正向规则明确为合成。实际命令与逐项输出见 [完整验收报告](../benchmarks/reports/verification/P4-development.json)、[第二轮命令及源码指纹](../data/verification/P4-development-command-run2.json)。

<a id="p4-10"></a>
### P4-10 完成通知事务、状态机及异常恢复验收

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P4-09](#p4-09)。

**输入：** 发布计划、异常事件、物料账、时钟与持久化通知意图。

**输出：** 版本化通知记录与去重键、P4 全流程和故障恢复证据。

**生产/数据文件范围：** `app/runtime/notifications.py`、`app/runtime/notification_projection.py`、`scripts/verification_manifest.json`、`benchmarks/scenarios/runtime_events.json`、`benchmarks/runtime_replanning.py`。

**测试文件：** `tests/integration/test_notification_outbox.py`、`tests/integration/test_p4_prepared_scenarios.py`、`tests/stateful/test_runtime_invariants.py`、`tests/fault_injection/test_runtime_recovery.py`、`tests/fault_injection/test_solver_worker_failure.py`、`tests/unit/test_runtime_benchmark.py`、`tests/unit/test_verify_runner.py`、`tests/unit/test_solver_startup_context.py`。

**接口约定：** NotificationService.prepare(plan, state) -> tuple[Notification, ...]；记录先持久化，SSE 传输由 P5 完成。

**实现要点：** 新计划撤销失效提醒，实际已经发送的保留历史并生成明确变更；用状态机生成重复、乱序及非法事件核对独立参考账本。

**验收断言：**

- [x] 第 16.9.4 节七类恢复案例逐项通过；故障后重排失败仍保留故障和损耗。
- [x] 加菜、执行、共享余料、故障恢复及版本发布的完整内部流程运行真实存储与算法。
- [x] `uv run --locked python scripts/verify.py --phase P4 --gate development` 通过；既有 P2/P3 回归不退化。原正式 P4 入口保留，正式门槛仍单独记录。

**任务验证命令：**

~~~text
uv run --locked pytest tests/stateful/test_runtime_invariants.py tests/fault_injection/test_runtime_recovery.py tests/integration/test_notification_outbox.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不承诺网络恰好一次送达，也不把尚未接入的 SSE/UI 写为已交付。

**设计定位：** 架构文档 16.9.4、17.5、21.8–21.9。

**验证记录（2026-10-02）：** P4-10 在完整 development 出口中 **47 通过、0 失败、0 跳过，284.157 秒**，依赖和质量检查通过。通知同事务去重、撤销旧待发提醒并保留已发送历史、准备 15 场景和七类恢复逐项执行、真实进程故障、独立执行参考账状态机、当前完整性能证据与验收器回归。实际命令与逐项输出见 [完整验收报告](../benchmarks/reports/verification/P4-development.json)、[第二轮命令及源码指纹](../data/verification/P4-development-command-run2.json)。

<a id="p5"></a>
## P5 接口、自然语言与展示（8 项任务）

**阶段入口：** 当前授权开发沿用实际通过的 P4:development、固定代理审核发布和 [P5 准备基线](P5准备与接口基线.md)，可先从 P5-01 开始；正式门槛单独保留。真实服务、前端构建和浏览器环境在对应任务落实并验收；缺真实模型连接不伪装 live LLM 验证。

**阶段产物：** 比赛五字段 Adapter、内部 API、自然语言入口、通知 SSE、甘特图、执行与异常反馈。

**验收出口：** 8 个任务全部 VERIFIED；真实后端完成浏览器端到端流程；Neo4j 关闭后完整服务冷启动及内部初排/重排通过；API/UI/历史/通知一致。真实 LLM 联通与官方未知动态语义单列验证状态。

**范围边界：** 界面只展示已发布事实，不自行排程。9 月 30 日答疑明确同任务排程后单菜输入为追加、建议 task_id、不要求插入时刻；任务传递位置、执行推进、动态响应范围和时间原点仍待确认，开发 profile 与官方联调分别记录。

**当前开发验收（2026-10-03，DEVELOPMENT_VERIFIED）：** 八项任务及P3/P4前置检查实际通过。`python scripts/verify.py --phase P5 --gate development` 第三轮退出0，完整阶段814次测试执行通过、0失败、0跳过，耗时1424.063秒；外层含停启Neo4j为1428.868秒。前端包装另实际运行17项单元及3条真实浏览器流程，类型、格式、构建与后端Ruff/mypy全部通过。正式入口与官方未决项仍保留，以下历史记录按执行时状态保留。

报告：[阶段报告](../benchmarks/reports/verification/P5-development.json)、[实际命令与Neo4j恢复](../data/verification/P5-development-command-run3.json)、[完成证据索引](../data/verification/P5-completion-20261003-run1.json)。实际停止Neo4j后冷启动、内部初排/重排、子图、求解中读取及重启通过；服务已恢复原运行状态。浏览器比赛流程观察到一次明确503，使用原幂等身份重试后恢复；不宣称所有HTTP请求一次成功或日志完全无错误。

当前源码第七轮性能186请求完成，90次预热重排P50/P95/最大为1804.9848/2083.5377/2168.6678ms，失败0、超2400ms目标0；预热初排最大3688.0135ms。[性能报告](../benchmarks/reports/P4-runtime-v7/report.json)绑定当前全部生产源码和驱动；第六轮一次超预算及此前失败报告保留。这是固定开发场景采样，P6正式性能认证另行验收。

真实模型补充：[DeepSeek单调用报告](../data/verification/P5-live-language-run3.json)记录本地deepseek-chat实际1次调用、1635.291ms、CLARIFY及本地重放一致。采用明确合成空菜单/空目录场景，移除审批拒绝的100菜目录外发；没有提交业务事件或输出凭据。该报告证明供应商联通与解析，100菜业务链与其他意图由已明确标注模型替身的真实业务测试分别验收。

当前入口：`python scripts/verify.py --phase P5 --gate development`；运行和接口示例见[P5运行与接口说明](P5运行与接口说明.md)。八任务按本次授权development范围登记，原正式P0/P1/P4/P5和全部外部官方联调要求保持。

**P5 准备（2026-10-02，READY_FOR_P5_IMPLEMENTATION_WITH_PROTOCOL_OPEN_ITEMS）：** 本轮完成文档、输入、场景、检查工具与环境准备，P5-01～08 仍为 NOT_STARTED。默认开发 profile 为 `QA0930_TASK_INSERT_DEVELOPMENT`：建议查询参数 task_id 关联持久会话，后轮单菜映射 ADD_RECIPE，内部规划累计菜单；PLAN_ONLY 不凭连续调用补完成，累计响应及 SESSION_ORIGIN 是项目选择。静态数组及原五字段规范保留；原整数分钟类型与用户批准的小数分钟之间的差异在 P5-02 明确补齐。准备包入口为 [p5_inputs.json](../data/preparations/p5-v1/p5_inputs.json)，固定既有代理审核 development 发布、运行策略和授权；原正式验收入口保持不变，P5:development仅形成设计，尚未注册。

**准备实际验证（2026-10-02）：** `uv run --locked python -m scripts.prepare_p5 --output data/preparations/p5-v1` 和 `uv run --locked python -m scripts.check_p5_preparation --root data/preparations/p5-v1 --output data/preparations/p5-v1/readiness.json` 均退出0。[准备检查](../data/preparations/p5-v1/readiness.json) 五组检查通过，耗时7145.929ms：38份归档/派生产物、28份来源绑定、真实仓储加载100菜/1562工序/27配置、三个CSV身份名称一致；P4实际733次通过报告及原339个源码/配置文件、100份历史计划哈希一致。该步骤核对已有证据，没有重跑P4整阶段或单菜求解。38个场景和8组请求夹具仍为设计，9项官方协议问题保留部分明确或待确认状态；P5功能测试执行数为0。

**质量与拒绝检查：** [质量报告](../data/verification/P5-preparation-quality-run1.json) 记录 Ruff check、format --check（340文件）、mypy app（199文件）和三个准备工具类型检查通过。[完整性检查](../data/verification/P5-preparation-negative-checks-run2.json) 五项通过：拒绝损坏产物、未知真实菜谱夹具、虚假官方确认、覆盖已有准备包及覆盖既有报告；原准备包和报告保持不变。首轮汇总脚本只读stdout而漏读JSON中的拒绝原因产生失败，修正读取方式后重新执行五项，首轮报告保留。实际探测并固定本机Node25.8.1/npm11.11.0及九项后端依赖；前端安装/构建、浏览器、HTTP和live模型由P5对应任务实施。

**建议实施顺序：** P5-01 → P5-03 → P5-02 → P5-05/P5-04 → P5-06 → P5-07 → P5-08。保留八个任务编号；先普通事件/API，后比赛动态 Adapter，再接通知、语言和前端。

**P5 实施进度（2026-10-02，IN_PROGRESS）：** 已建立真实 FastAPI 生命周期、内部会话/事件/历史/快照查询、比赛五字段投影与持久任务/HTTP 幂等映射，正在完成通知、自然语言及前端接入。用户明确授权自行设计 `task_id` 位置、追加响应范围与重排原点，并要求分钟保留一位小数；当前实现采用查询参数、累计活动菜单及固定首次会话原点，属于 USER_AUTHORIZED_PROJECT_DESIGN，未改写为官方确认。比赛接口独立运行 8 项全部通过，40.95 秒，报告 `data/verification/P5-competition-progress.xml`，包括真实三菜初排后逐次追加至五菜、重试、冲突及重启恢复。此前失败报告保留；原准备包只代表历史准备核对，本阶段尚未完成八任务、浏览器或阶段验收。当前检查与后续工作见 [P5实施记录](P5实施记录.md)。

**交接内容：** 候选交付版本、接口契约、浏览器测试、原始耗时数据与外部问题登记交给 P6。

**阶段验收命令：**

~~~text
uv run --locked python scripts/verify.py --phase P5
~~~

| 任务 | 交付内容 | 当前状态 |
| --- | --- | --- |
| [P5-01](#p5-01) | 装配应用生命周期、健康检查和错误响应 | DEVELOPMENT_VERIFIED |
| [P5-02](#p5-02) | 实现比赛 Adapter 与正式计划投影 | DEVELOPMENT_VERIFIED |
| [P5-03](#p5-03) | 实现会话、事件、计划与知识查询 API | DEVELOPMENT_VERIFIED |
| [P5-04](#p5-04) | 接入自然语言意图并转换为普通事件 | DEVELOPMENT_VERIFIED |
| [P5-05](#p5-05) | 完成通知模板、SSE 和恢复读取 | DEVELOPMENT_VERIFIED |
| [P5-06](#p5-06) | 建立前端与真实计划甘特图 | DEVELOPMENT_VERIFIED |
| [P5-07](#p5-07) | 实现执行面板、异常反馈和重排差异 | DEVELOPMENT_VERIFIED |
| [P5-08](#p5-08) | 完成接口与展示端联调验收 | DEVELOPMENT_VERIFIED |

<a id="p5-01"></a>
### P5-01 装配应用生命周期、健康检查和错误响应

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P4-10](#p4-10)。

**输入：** 固定发布文件、SQLite、常驻工作进程和依赖装配。

**输出：** FastAPI 应用、live/ready 路由、资源启停、可追溯错误边界。

**生产/数据文件范围：** `app/main.py`、`app/config.py`、`app/api/health.py`、`app/api/errors.py`、`app/services/container.py`。

**测试文件：** `tests/integration/test_application_lifecycle.py`。

**接口约定：** create_app(settings: AppSettings) -> FastAPI；ready 检查知识、状态库和工作进程，不检查 Neo4j。

**实现要点：** 应用生命周期装配端口；输出失败与日志身份一致，不能异常后包成成功五字段；配置从环境和版本文件读取。

**验收断言：**

- [x] Neo4j 关闭时完整应用冷启动并 ready；知识损坏或状态库不可用不虚报 ready。
- [x] 健康与事件读取在求解期间可响应；退出回收工作进程。
- [x] 无效配置失败可定位，不暴露凭据或原始异常堆栈给普通客户端。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_application_lifecycle.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 正式错误协议缺项按明确内部约定实现并登记 U12，不修改比赛成功结构。

**设计定位：** 架构文档 18.7、20.1–20.2。

**当前验证（2026-10-02）：** 应用生命周期 3 项通过，包含真实快照与常驻进程启停、缺失发布拒绝和错误响应可追踪。联合报告 `data/verification/P5-api-language-notifications-progress.xml` 为 21 项通过、66.21 秒。实际关闭 Neo4j 的服务冷启动和求解中 HTTP 读取仍待 P5-08 补齐；保持 IN_PROGRESS。

<a id="p5-02"></a>

**增量验证（2026-10-03）：** 生命周期及物料相关回归23项通过（`P5-lifecycle-range-material-run9.xml`）；实际 Neo4j 停止时完整冷启动、求解中 HTTP 读取及重启1项通过（`P5-service-neo4j-off-run1.xml`）。退出等待正在恢复的事务/工作进程，避免关闭后的写入竞争。P5:development 完整阶段命令正在执行，暂不标记任务通过。


### P5-02 实现比赛 Adapter 与正式计划投影

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P5-01](#p5-01)、[P0-07](#p0-07)。

**输入：** 官方直接数组、知识库身份、经过事务发布的完整计划。

**输出：** POST /api/competition/plan 与五个规定顶层字段。

**生产/数据文件范围：** `app/api/competition.py`、`app/api/competition_adapter.py`、`app/api/competition_profiles.py`、`app/validation/competition_contract.py`、`app/storage/competition_tasks.py`、对应新迁移；必要的公共小数分钟与投影上下文契约同步直接消费者，旧版本保留。

**测试文件：** `tests/contract/test_competition_endpoint.py`、`tests/contract/test_competition_replanning.py`、`tests/integration/test_competition_task_recovery.py`。

**接口约定：** CompetitionAdapter.to_response(plan: PublishedPlan, context) -> profile 对应的 CompetitionResponse；显式 task_id 开发模式为 QA0930_TASK_INSERT_DEVELOPMENT，无任务上下文保留 INDEPENDENT_REQUEST。原数组请求主体与成功五字段不变；动态响应范围和原点由版本化 profile 明确。

**实现要点：** 校验 ID/名称；持久任务/会话映射和 HTTP 请求幂等身份；首轮初排、同任务后轮单菜追加、整桌覆盖、重启及提交后响应恢复。task_id 为做饭任务，区别于工序 TaskId。映射时间与设备参数并排序，完整日期保留；补齐版本化小数分钟投影，不能通过独立取整改变工艺。旧静态 recipeCount=输入长度与开发累计响应分开校验，尚未确认的动态官方范围保持登记。

**验收断言：**

- [x] 成功顶层恰好五项，无 code/message/data 和内部 debug 字段。
- [x] 同名异 ID 覆盖正确；长准备、分钟兼容及参数合法性通过。
- [x] 没有合法完整计划时返回明确失败，不能人工拼装成功时间线。
- [x] 同任务三菜初排后追加一道菜仍覆盖原菜和新菜；不同 task_id 隔离、首轮单菜合法、后轮多项不猜测。
- [x] HTTP 同键同内容幂等、异内容冲突，任务映射与请求身份重启后保留；首次并发和提交后响应丢失不创建第二会话或第二份菜。
- [x] 无实际执行反馈不补完成；运行/完成事实仍按 P4 冻结，显式开发 profile 不冒充完整官方联调。

**任务验证命令：**

~~~text
uv run --locked pytest tests/contract/test_competition_endpoint.py tests/contract/test_competition_replanning.py tests/integration/test_competition_task_recovery.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 按答疑明确的同任务单菜新增落实开发映射，不按菜名、数组长度或调用间隔猜测任务。任务传递位置、动态输出范围及时间原点采用已标注的项目选择并单列官方确认；原静态协议不静默放宽。

**设计定位：** 架构文档 18、23。

**当前验证（2026-10-02）：** 三个任务测试文件实际 8 项通过，40.95 秒，报告 `data/verification/P5-competition-progress.xml`；包含五字段、一位小数、非法请求、真实三菜到五菜、原点固定、无模拟完成、任务隔离、重试冲突和重启恢复。真实并发、提交后响应丢失注入、同名异 ID、独立投影校验和冻结历史动态输出仍需补充，状态保持 IN_PROGRESS。性能及持久化修复相关 P4 回归 55 项通过，47.03 秒，报告 `data/verification/P5-scheduling-storage-regression.xml`。

<a id="p5-03"></a>

**增量验证（2026-10-03）：** 独立五字段/原料/参数扫描、同名异ID及库存供应投影24项通过（`P5-inventory-independent-projection-run2.xml`）；三菜连续到五菜与独立哈希反例23项通过（`P5-validator-publication-run2.xml`）。并发首次请求、提交后丢响应、错误发布身份和重复在途请求已有实际故障测试；同身份互斥不重复计算。失败证据保留，完整阶段仍在执行。


### P5-03 实现会话、事件、计划与知识查询 API

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P5-01](#p5-01)。

**输入：** RuntimeService、PlanningService、PlanPublisher 和只读 KnowledgeRepository。

**输出：** 内部 /api/v1 会话、事件、历史计划、菜谱及子图 API。

**生产/数据文件范围：** `app/api/sessions.py`、`app/api/events.py`、`app/api/plans.py`、`app/api/recipes.py`。

**测试文件：** `tests/contract/test_internal_api.py`。

**接口约定：** 按架构 18.2 路由；事件必须带 event_id 与 expected_state_revision，结果明确区分事件接受与重排成功。

**实现要点：** 提供前端所需稳定 DTO；读历史计划不触发重排，子图由快照生成；复用统一错误和版本边界。

**验收断言：**

- [x] 同事件重试幂等，同 ID 异载荷冲突，读取旧计划不篡改当前版本。
- [x] 加菜失败不把已提交执行事实撤销；响应显示实际结果。
- [x] 知识查询不访问 Neo4j，非法 recipe_id 不模糊匹配同名菜。

**任务验证命令：**

~~~text
uv run --locked pytest tests/contract/test_internal_api.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** API 不直接写表或内嵌规则；内部扩展字段不流入官方成功响应。

**设计定位：** 架构文档 18.2、19、20.3。

**当前验证（2026-10-02）：** 内部 API 3 项通过，包含真实初排、历史读取、同身份重试、未知 ID/名字拒绝和无效释放不写事实；联合报告见 `data/verification/P5-api-language-notifications-progress.xml`。显式模拟推进与执行反馈准备仍待接入，不将路由存在视作全部执行面板功能完成。

<a id="p5-04"></a>

**增量验证（2026-10-03）：** 模拟推进、只读反馈草稿、计划/设备展示已接入；明确模拟时间戳重复请求先读取持久身份，原目标不会随当前时钟重新计算。模拟时间戳与控制重启6项通过（`P5-simulation-timestamp-recovery-run2.xml`）。后台和 HTTP 同身份请求互斥，事实与重排结果分开；等待完整阶段验收。


### P5-04 接入自然语言意图并转换为普通事件

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P5-03](#p5-03)、[P1-02](#p1-02)。

**输入：** 用户文本、当前菜单/版本、LLMProvider、结构化意图 Schema。

**输出：** IntentRun、需要澄清的结果或通过校验后的普通 RuntimeEvent。

**生产/数据文件范围：** `app/llm/intent_contract.py`、`app/llm/intent_service.py`、`app/api/language_events.py`。

**测试文件：** `tests/contract/test_language_events.py`。

**接口约定：** IntentService.interpret(text, context) -> IntentResult；明确事件仍经 RuntimeService 统一处理。

**实现要点：** 支持加菜、取消未开始菜、明确时间偏好；模糊指令返回澄清内容，不能转为假定硬截止。

**验收断言：**

- [x] 未知菜品、非法 JSON、超时不产生业务事件。
- [x] 重复请求或回调不重复加菜，旧状态意图需重新验证。
- [x] 禁用 LLM 时结构化比赛初排/内部重排正常；计划数值不由模型另算。

**任务验证命令：**

~~~text
uv run --locked pytest tests/contract/test_language_events.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 模型调用成功不等于业务事件提交成功；真实供应商验收与固定响应测试分开记录。

**设计定位：** 架构文档 17.2–17.3、17.6。

**当前验证（2026-10-02）：** 语言入口 7 项通过，联合报告 `data/verification/P5-api-language-notifications-progress.xml`。模型使用明确的 FixedProvider 替身，普通业务部分使用真实发布、求解、独立校验和 SQLite；覆盖未知 ID、非法 JSON、供应商超时、澄清不写事实、实际加菜幂等、旧状态重验与禁用模型。IntentRun 保存原文、上下文、提示、Schema、原始输出与错误。真实供应商连接及更多取消/时间偏好边界尚未验收。

<a id="p5-05"></a>

**增量验证（2026-10-03）：** 新增未开始取消与重试、开始后取消拒绝、带时区的明确开始偏好；业务链使用真实发布与运行库，模型是显式 FixedProvider。阶段输入/前端工具链/语言联合13项通过（`P5-registered-frontend-language-run2.xml`），其中语言10项。live供应商保持未验证；等待完整阶段验收。


### P5-05 完成通知模板、SSE 和恢复读取

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P5-03](#p5-03)、[P4-10](#p4-10)。

**输入：** 持久化通知、计划版本、触发时刻、客户端游标。

**输出：** 步骤通知、计划变更通知、SSE 事件流及按游标恢复。

**生产/数据文件范围：** `app/runtime/notification_templates.py`、`app/api/notifications.py`、`app/services/notification_dispatcher.py`、`app/storage/notification_stream.py`。投递编排放在 services，避免 runtime 反向依赖应用容器。

**测试文件：** `tests/integration/test_notification_stream.py`。

**接口约定：** GET /api/v1/sessions/{session_id}/notifications/stream；消息携带 notification_id、event_id 和 plan_version。

**实现要点：** 模板填入已经验证的动作与参数；重排撤销旧未发提醒，断连后重放与客户端去重配合。

**验收断言：**

- [x] 断连重连不会丢失持久化待收消息，也不会重复产生业务执行。
- [x] 延迟到达的旧版本提示可被客户端识别，不显示成当前操作。
- [x] 同温热批次通知明确成员取放与必要介入，不仅显示外层总时长。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_notification_stream.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不承诺网络只投递一次，通知到达不能被当作实际操作已完成。

**设计定位：** 架构文档 17.4–17.5、21.9。

**当前验证（2026-10-02）：** 真实通知断连/重启集成 1 项通过，联合报告 `data/verification/P5-api-language-notifications-progress.xml`。在首次读取前产生两个已发布版本，二者持久计划变更均可恢复；支持 SSE Last-Event-ID、重复读取去重身份、旧提醒撤销，读取不产生执行事实。初次测试暴露只投递当前版本的问题，已改为计划变更与发布同事务写入。跨菜共同热批次成员提示和浏览器旧版本过滤还待补充。

<a id="p5-06"></a>

**增量验证（2026-10-03）：** 真实H02成员结束提醒已通过；读取提醒不产生完成事实。模板改为读取选中载体及固定执行占用，修复抽象灶眼身份；断连游标、历史变更、旧提醒撤销及前端版本过滤已实现。单项回归与当前阶段检查继续执行，不将旧浏览器后台锁异常算作无异常。


### P5-06 建立前端与真实计划甘特图

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P5-02](#p5-02)、[P5-03](#p5-03)。

**输入：** 正式菜谱 API、PublishedPlan DTO、内部错误与版本信息。

**输出：** Vue 3 页面、TypeScript 客户端、菜谱选择、计划列表和按资源/菜品切换的甘特图。

**生产/数据文件范围：** `web/package.json`、`web/package-lock.json`、`web/tsconfig.json`、`web/vite.config.ts`、`web/src/api/`、`web/src/views/PlanningView.vue`、`web/src/components/ScheduleGantt.vue`、`web/playwright.config.ts`。

**测试文件：** `web/tests/unit/planning_view.test.ts`、`web/tests/unit/schedule_gantt.test.ts`。

**接口约定：** 前端读取服务器时间和指标；本地转换只用于显示，不重新决定工序开始或时长。

**实现要点：** 按用户 2026-10-02 指示，使用本机 Node 25.8.1 / npm 11.11.0，固定 Vite 及其余依赖并提交 package-lock.json；建立 typecheck、test:unit、test:e2e、build 及 Vitest/Playwright 基础配置；同名菜按 ID 选择；明确加载、失败和无计划状态。安装、构建和测试验证依赖兼容性，原 Node 24 选型不再是 P5 要求。

**验收断言：**

- [x] 跨日与长准备能查看完整时间范围，不将次日任务折回当天。
- [x] 显示设备、人工和共同批次层次，人工冲突不能被图层遮盖成合法排程。
- [x] npm --prefix web run typecheck、test:unit 和 build 通过；无后端时显示失败而非固定成功样板。

**任务验证命令：**

~~~text
npm --prefix web run test:unit -- tests/unit/planning_view.test.ts tests/unit/schedule_gantt.test.ts
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 本阶段不增加拖拽改工艺、移动端独立 App 或前端算法；ECharts 只负责可视化。

**设计定位：** 架构文档 20.3、18.4–18.6。

**增量验证（2026-10-03）：** 前端类型检查、格式、17项单元与构建实际通过（`P5-web-cancellation-run2.xml`及`P5-registered-frontend-language-run2.xml`）。甘特图读取秒级后端投影，保留完整日期、人工资源与共同批次；没有后端的失败状态不产生样板计划。当前入口实际运行工具链，不以dist目录存在替代验收。等待完整阶段报告。

<a id="p5-07"></a>
### P5-07 实现执行面板、异常反馈和重排差异

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P5-04](#p5-04)、[P5-05](#p5-05)、[P5-06](#p5-06)。

**输入：** 内部事件 API、语言意图、SSE、设备状态与历史计划。

**输出：** 模拟/人工模式执行面板、明确故障和释放确认操作、只读子图及重排差异。

**生产/数据文件范围：** `web/src/views/ExecutionView.vue`、`web/src/components/KnowledgeGraph.vue`、`web/src/components/PlanDiff.vue`、`web/src/components/DeviceStatus.vue`、`web/src/api/events.ts`。

**测试文件：** `web/tests/unit/execution_view.test.ts`、`web/tests/e2e/replanning.spec.ts`。

**接口约定：** 所有用户动作提交含幂等键和预期状态版本的事件；界面更新以服务器确认结果为准。

**实现要点：** 显示运行状态、异常原因、物料不足及新旧计划变化；自然语言需要澄清时提供明确选项；SSE 按身份和版本去重。

**验收断言：**

- [x] 重复点击不会重复扣料或加菜；状态冲突显示当前版本而非本地覆盖。
- [x] 设备恢复和清空确认是不同业务动作，未清空不能展示可复用。
- [x] 浏览器运行选菜、开始、加菜、设备故障、恢复和查看差异的完整流程。

**任务验证命令：**

~~~text
npm --prefix web run test:unit -- tests/unit/execution_view.test.ts
npm --prefix web run test:e2e -- tests/e2e/replanning.spec.ts
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不把后台实现参数或诊断内部编号当作普通用户必须理解的操作项。

**设计定位：** 架构文档 16.9、17、20.3。

**增量验证（2026-10-03）：** 人工草稿确认、实际数量、模拟推进、故障恢复与明确清空、历史差异和子图均已接入真实浏览器流程。提醒正文/游标刷新恢复，空数量不默认为零，取消按钮仅显示未开始活动菜品；17项前端单元通过。新浏览器阶段检查拒绝后台Traceback/SQLite锁异常，完整阶段正在执行。

<a id="p5-08"></a>
### P5-08 完成接口与展示端联调验收

**状态：DEVELOPMENT_VERIFIED。** 前置任务：[P5-07](#p5-07)。

**输入：** 真实后端、当前授权的固定 development 发布（100 菜）、SQLite、工作进程和构建后的前端；sample 仅用于明确的合成边界测试，不替代真实主链。

**输出：** P5 联调证据、浏览器操作记录、静态官方契约及 QA0930 显式开发动态 profile 报告、未决官方语义清单。

**生产/数据文件范围：** `web/playwright.config.ts`、`scripts/verification_manifest.json`、`benchmarks/scenarios/api_flows.json`。

**测试文件：** `tests/integration/test_service_end_to_end.py`、`web/tests/e2e/competition_flow.spec.ts`。

**接口约定：** verify.py --phase P5；浏览器使用真实后端，禁止以 mock 成功响应代替主链。

**实现要点：** 贯通菜单、初排、执行、加菜、异常、通知、重启；检查 API/UI 指标源于同一计划，记录网络与服务内耗时。

**验收断言：**

- [x] Neo4j 关闭时应用冷启动后完成初排、内部重排和子图查看。
- [x] 五字段契约、历史版本、通知去重及错误流通过；前端类型、单元、端到端和构建检查通过。
- [x] 使用 task_id 的连续初排/单菜追加调用在真实后端贯通；任务隔离、重启、请求重试、旧通知撤销和浏览器显示版本一致。
- [x] 官方已明确的输入答疑与项目选择分别验收；未确认的动态响应、执行推进和时间原点保持未验证，不把内部或开发 profile 演示算作全部官方联调。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_service_end_to_end.py -q
npm --prefix web run test:e2e -- tests/e2e/competition_flow.spec.ts
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** P5 验收覆盖已明确接口和内部能力；官方全部外部条件与公网最终验收由 P6-08 收口。

**设计定位：** 架构文档 18、20、21.6、23。

**增量验证（2026-10-03）：** P5:development已登记八任务、实际P4依赖和新p5-v2实施输入，原正式入口与历史报告保持。已实际停Neo4j完成冷启动/初排/内部追加/求解中读取/子图/重启；最新开发阶段命令正在停库环境实际运行后端、前端工具链和浏览器，不把入口存在或单项通过算作整阶段通过。

<a id="p6"></a>
## P6 全量评估与交付（8 项任务）

**阶段入口：** P0–P5 全部阶段通过，特别是 P1 full 的 100 道数据；版本和运行资源可固定。

**阶段产物：** 全量实验、性能、时长扰动、属性和故障报告，可复现部署与最终交付清单。

**验收出口：** 8 个任务全部 VERIFIED，满足第 4 节性能门槛及所有正确性要求；官方动态语义与真实公网联调有证据。缺外部条件时 P6-08 保持 BLOCKED，可交付技术部分但阶段不标全部通过。

**范围边界：** 不通过改工艺、删难例或隐去失败达标；公网发布和正式参赛提交需实际授权和明确目标。

**交接内容：** 交付固定代码/依赖/知识/策略/Windows 部署包身份、可访问服务证据、测试报告及 task.md 完整执行记录。

**阶段验收命令：**

~~~text
uv run --locked python scripts/verify.py --phase P6
~~~

**已授权技术入口（2026-10-05）：** `python scripts/verify.py --phase P6 --gate development` 登记 P6-01～P6-07 与当前 P5 八项接口/前端回归，绑定 `p6-final-acceptance-v1` 授权和固定开发发布。缺少完整报告、源码已变或 Windows 包未实际安装重启均失败；入口登记本身不代表通过。正式 P6 仍依赖 P1 full 和 P5；P6-08 外部条件不纳入开发入口冒充通过。

**实际技术出口（2026-10-05）：** 监督命令 `python -m scripts.verify_p6_development --output data/verification/P6-development-gate-run2 --neo4j-container smart-cooking-knowledge-neo4j-1` 已真实执行上述完整入口并恢复知识维护服务原状态；[原阶段归档](../data/verification/P6-development-gate-run2/phase-report.json) 为 DEVELOPMENT_VERIFIED，96 项包装检查通过、0 失败/跳过，735719ms。Ruff、463 文件格式及 mypy app 256 文件均通过；P5 八项当前回归与 P6 七项证据通过。以下勾选仅针对绑定开发发布的已授权技术断言，不能代替正式 P1 full、官方联调或公网验证。测试后仅两份结果登记文档更新，原报告/指纹不重写，交付时另存全部文件集合及字节差异审计。

| 任务 | 交付内容 | 当前完整阶段状态（本轮局部修复已完成） |
| --- | --- | --- |
| [P6-01](#p6-01) | 固定全量、组合和重排评估集 | IN_PROGRESS（本轮全量复验按用户省略） |
| [P6-02](#p6-02) | 完成共享、图预处理、提示和剪枝消融 | IN_PROGRESS（本轮全量复验按用户省略） |
| [P6-03](#p6-03) | 实现可选缓冲策略并完成时长扰动实验 | IN_PROGRESS（局部修复完成；全量省略） |
| [P6-04](#p6-04) | 执行系统级属性和状态机回归 | IN_PROGRESS（当前技术复验通过；完整阶段未验收） |
| [P6-05](#p6-05) | 完成全系统故障注入与恢复验证 | IN_PROGRESS（当前技术复验通过；完整阶段未验收） |
| [P6-06](#p6-06) | 固定硬件进行端到端性能测试与调优 | IN_PROGRESS（当前技术复验通过；完整阶段未验收） |
| [P6-07](#p6-07) | 形成可复现 Windows 离线部署包 | IN_PROGRESS（当前技术复验通过；完整阶段未验收） |
| [P6-08](#p6-08) | 完成官方条件核对与最终验收交付 | BLOCKED |

**实施记录（2026-10-04，开发口径）：** 清单 SHA-256 `60276a5d3232a34905f1f298529b093ef7cee237394ca3c943c6053d8bed6613`，固定 `development` 知识与 `p4-runtime-v1` 策略。实际运行 `python -m benchmarks.runner --output benchmarks/reports/P6-full-development-run1`，退出 1：340 场景/365 请求，333 场景通过、7 失败，1079.144 秒，评估前后生产与基准源码一致。100 单菜和 20 边界菜单通过；2 次初排和 5 次重排未完成，包含发布截止回滚和限时未获得候选；所有成功结果经过持久化重载、独立约束和一位小数接口事实扫描。初排 P50/P95/max 为 3028.055/3875.205/4485.664ms，重排为 2255.662/2516.858/2612.160ms。首次评估与 Windows 安装检查存在时间重叠，数据不能充当 P6-06 固定无并行负载的三轮性能认证。[完整报告](../benchmarks/reports/P6-full-development-run1/report.json)。后续局部修复和部分诊断不替代整批重跑，正式入口未改变。
<a id="p6-01"></a>
### P6-01 固定全量、组合和重排评估集

**状态：IN_PROGRESS（修复后全量报告未运行；用户省略本轮全量，原开发证据保留）。** 前置任务：[P1-09](#p1-09)、[P5-08](#p5-08)。用户授权的固定 development 发布用于技术评估，正式 P1 full 门槛保持独立；开发结果不标为正式 VERIFIED。

**输入：** 正式 100 道发布、完整服务、设备与策略版本。

**输出：** 100 单菜、至少 20 个边界多菜场景、200 组 3–5 菜组合、至少 20 个重排脚本及可重放清单。

**生产/数据文件范围：** `benchmarks/dataset.py`、`benchmarks/scenarios/full_suite.json`、`benchmarks/runner.py`。

**测试文件：** `tests/integration/test_full_benchmark_dataset.py`。

**接口约定：** BenchmarkRunner.run(suite, release, policy, seed) -> BenchmarkReport；失败保留在总样本数中。

**实现要点：** 保证所有菜谱 ID 在组合集出现；固定原点、菜单、事件、种子与预算，带真实数据/合成标识。

**验收断言：**

- [x] 测试集数量和覆盖自动核对，重复 ID 不被计为不同覆盖。
- [x] 每个成功计划经过独立约束与接口校验，失败带状态和原因。
- [x] 相同清单可以重放；改变数据版本不能复用旧结果充当新证据。

- [x] 边界场景覆盖架构 8.6 的灶具、蒸箱、烤箱、同腔体别名、冷藏/冷冻、烟机及已支持的可选设备通道；同时包含合法并行和竞争冲突。缺少真实样例的规则用明确标注的合成测试补足，不编造正式菜谱或设备参数。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_full_benchmark_dataset.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不修改测试集删除困难或不利案例；P6 不能用 sample 发布运行正式全量验收。

**设计定位：** 架构文档 21.2–21.6。

**验证记录（2026-10-04）：** 清单/身份/真实主链工具测试实际 7 通过，`data/verification/P6-dataset-tests-run3.xml`；两轮全量的全部失败记录保留，不替代后续最终性能验收。已聚合设备竞争、源能力范围、小规模精确对照、强制/运行中热批次、连续占用与共享余料，共 182 通过、0 跳过，129.73 秒；`P6-boundary-supplements-run1.xml` 及 `P6-boundary-supplement-evidence-run1.json` 绑定测试哈希与真实/合成范围。冷藏/冷冻、烟机和可选通道以源模式范围及显式合成竞争矩阵补足，不宣称新增可选设备生产路径。正式输入与最终代码聚合门槛未满足，P6-01 保持 IN_PROGRESS。

**2026-10-05 当前完整全量实测：** `P6-full-development-run3/report.json` 为 PASSED / FULL_DEVELOPMENT，340/340 场景、365 次请求、0 失败，约 12.7 分钟；100 单菜、200 组合、20 边界与 20 重排脚本覆盖完整。使用与在线相同的 JSON 工作进程适配器，逐份独立约束/接口证据及源码指纹留档。历史失败 run1/run2 保留；任务状态待当前完整开发阶段命令汇总后更新。

<a id="p6-02"></a>
### P6-02 完成共享、图预处理、提示和剪枝消融

**状态：IN_PROGRESS（修复后全量报告未运行；用户省略本轮全量，原开发证据保留）。** 前置任务：[P6-01](#p6-01)。

**输入：** 固定评估集、完整 Solver、明确不同策略。

**输出：** 逐道参考、Greedy、CP-SAT、提示、共享、预处理及剪枝的受控对照结果。

**生产/数据文件范围：** `benchmarks/ablation.py`、`benchmarks/scenarios/ablation_policies.json`。

**测试文件：** `tests/unit/test_ablation_comparability.py`。

**接口约定：** 对照必须固定工艺事实、资源、时间和预算；每组只有声明因素变化。

**实现要点：** 记录在线知识提取与离线图谱导出成本；区分等价模型组织、数学预处理与候选截断贡献。

**验收断言：**

- [x] 图谱与 JSON 通路产生等价问题时，不声称数据库改变数学最优值。
- [x] 无共享组仍执行原菜谱强制批次；所有比较组都通过同一正确性校验。
- [x] 同时报告解质量、首次可行耗时、总耗时和截断状态，不只选最好一次。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_ablation_comparability.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 性能改善的归因只基于受控因素；非等价剪枝默认仍需证明才能启用。

**设计定位：** 架构文档 21.4–21.5。

**验证记录（2026-10-04）：** 8 个固定真实菜谱菜单×10 组×3 轮，240 次真实常驻 CP-SAT/Greedy 对照，`benchmarks/reports/P6-ablation-run1/report.json` 实际完成且源码前后一致；串行与 CP 各组均 24/24 合法，Greedy 单独组 17/24 合法，7 次失败完整保留，不删困难菜单。图谱实际离线导出 24673.481ms，规范 JSON 构建 7222.848ms；8 组问题哈希逐组等价，数据库不被声称改变数学最优值。全部组的知识、运行状态和强制程序哈希一致，独立复核见 `data/verification/P6-ablation-comparability-audit-run1.json`。`boundary-008` 实际产生共享前处理及共同热候选，cap=1 明确标记可能损失最优解；其他没有共享变化的场景保留。名义策略历史哈希不变，默认等价去重/图域收紧开关保留，仅受控实验关闭；无已证明的非等价白名单，继续禁用。工具及原边界回归 21 通过，`P6-ablation-tools-run1.xml`。此实验先于后续缓冲实现，最终固定代码需重新聚合检查。

**2026-10-05 当前完整消融实测：** `P6-ablation-run2/report.json` 为 EXPERIMENT_COMPLETED，当前源码下 240 次对照全部留档；Greedy 单独组 15/24 合法、9 次失败，其余九组分别 24/24 合法。真实图导出与规范 JSON 的 8 组问题哈希一致，不据此声称图数据库改善数学最优值。`P6-ablation-evidence-run1.xml` 独立逐份复核 1 通过、24.49 秒；未将实验中保留的诚实失败改写为全部规划成功。

<a id="p6-03"></a>
### P6-03 实现可选缓冲策略并完成时长扰动实验

**状态：IN_PROGRESS（本轮局部修复与技术复验已完成；全量按用户省略，原 16000 条基线保留）。** 前置任务：[P6-01](#p6-01)。

**输入：** 名义估计、来源/样本信息、审核允许区间及固定模拟轨迹。

**输出：** NOMINAL/BUFFERED 策略、DurationEstimate、至少 40 菜单×100 轨迹×4 组的实验报告。

**生产/数据文件范围：** `app/scheduling/duration_policy.py`、`app/domain/duration_estimate.py`、`benchmarks/robustness/simulator.py`、`benchmarks/robustness/runner.py`。

**测试文件：** `tests/unit/test_duration_policy.py`、`tests/integration/test_robustness_no_future_leak.py`；窗口纠正追加 `tests/unit/test_robustness_windows.py`、`tests/integration/test_robustness_dispatch.py`、`tests/integration/test_robustness_archive_replay.py`、`tests/integration/test_robustness_dispatch_evidence.py`。

**接口约定：** DurationPolicy.apply(problem_inputs, estimates) -> DurationAdjustedInputs；修改策略生成新的 problem_hash，不改原知识事实。

**实现要点：** 只调整允许估计阶段或显式缓冲；对名义/缓冲与顺延/重排四组配对比较；每可比组 30 样本仅是尝试经验分位数的门槛。

**验收断言：**

- [x] 固定有效热时长和最小/最大工艺限制不变；8 分钟热工序不因缓冲变成 10 分钟。
- [x] 模拟器未来随机值未出现在调度输入；共同速度因素保持组间可比性。
- [x] 未完成轨迹计入失败率，报告实际完成差超 240/300 秒比例、延迟和重排次数；合成分布不冒充实测。

**任务验证命令：**

~~~text
uv run --locked pytest tests/unit/test_duration_policy.py tests/integration/test_robustness_no_future_leak.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** BUFFERED 不默认成为正式策略；是否启用由正确性与同口径实验取舍决定并记录版本。

**设计定位：** 架构文档 13.6、21.7。

**验证记录（2026-10-04）：** `DurationEstimate` 保留模板/工艺哈希、份量条件、设备能力引用、名义估计/边界、数据版本和真实代理审核引用；无观测样本不生成经验分位数。BUFFERED 采用菜谱未开始根节点前的显式自由等待，默认等待量为允许估计阶段总名义时间的 15%，不预约人工/设备，不修改源工序时长、固定热暴露、最大间隔或实际工时；已开始菜谱不追加此缓冲。BUFFERED 问题绑定新策略和缓冲事实，Validator 独立从原知识和运行事实重算门槛，拒绝删除、篡改或忽略缓冲。默认 NOMINAL JSON/哈希兼容，策略不自动启用。策略、消融身份与冻结历史回归 `P6-duration-policy-run1.xml` 24 通过。四组真实扰动模拟与无未来泄漏集成实验尚待执行，此记录不等于鲁棒性收益成立。

**全量实验记录（2026-10-05）：** `benchmarks/reports/P6-robustness-run2/report.json` 实际退出 0，EXPERIMENT_COMPLETED，40 菜单×100 轨迹×4 组共 16000 条、80 次初始计划尝试；运行前后源码一致，6481017.735ms。NOMINAL_SHIFT / NOMINAL_REPLAN / BUFFERED_SHIFT / BUFFERED_REPLAN 各 4000 条，完成 855/837/767/764，失败 3145/3163/3233/3236，失败率 78.625%/79.075%/80.825%/80.9%。主要原因是实际最大工艺间隔超限；另保留初排失败、运行阶段需补充反馈、重排无完整候选及 2 次绑定预算超限。完成差超过 240/300 秒的比例仅在已完成轨迹中计算，未完成分别计数；没有将完成样本的较小时间差解释为整体收益。BUFFERED 两组失败率均高于对应名义组，默认继续 NOMINAL。实际观察触发重排各 853 次，真实原生重算共 243 次，其余相同完整观察前缀复用单独记录；SQL 事务/通知实际持久性由故障矩阵验证。此结果是明确合成扰动实验完成，不是鲁棒性已达标或正式数据实测。

**启动窗口修复记录（2026-10-05，本次授权第 1、2 步）：** 第 1 步完成合成反例与真实归档重放：180 秒名义前驱实际 120 秒完成、零最大间隔且多前驱并行的用例，以及 `combination-000/NOMINAL_SHIFT/000` 在 1706 秒仍可合法启动、旧算法却建议 1778 秒的归档见证。覆盖提前/按时/延迟、最小及最大间隔、共同热载体、固定热阶段、缓冲和显式延迟、资源未释放、暂停、缺失反馈、同秒完成与历史不可变。将归档旧模拟器通过 pytest 插件显式注入最终 17 项行为用例，`red-legacy-confirmed.xml` 为 10 失败、7 通过、0 错误/跳过，6.059 秒，退出 1 为预期红灯；早期夹具修订和未正确注入的 `red-final.xml` 保留，但不作为旧版本反例证明。

第 2 步由 `benchmarks/robustness/dispatch.py` 独立计算实际启动窗口；`simulator.py` 使用已确认依赖结束、最小/最大间隔、正式等待门槛与当前资源占用推进。原计划及其顺延时间只作为窗口内的偏好，不再因偏好超界直接判实际失败；名义时长传播出的任务最早时间不冒充实际硬门槛。缺失完成观测、资源阻塞、暂停、窗口关闭和重排失败分别记录；诊断保存依赖实际结束、窗口上下界、偏好/启动时间、资源占用、执行 ID 与状态版本。执行事件继续通过真实状态转换与独立终局校验，不放宽热时长或工艺约束，也不向规划器泄漏未来随机值。

**文件范围：** 修改 `benchmarks/robustness/simulator.py`、`runner.py`、`docs/question.md`、本任务记录及 `scripts/verification_manifest.json`；新增 `dispatch.py`、`compare_dispatch.py` 与上列四份窗口测试。生产 `app/` 的 256 个源码文件、原时长分布、固定知识、单观察点/最多一次重排、4200/2400ms 预算均保持不变。`protected-source-audit.json` 逐项确认扰动采样、未来值持有、观察、重排触发与固定工艺检查 AST 未变；`production-source-audit.json` 确认生产文件无差异。默认仍 NOMINAL。

**最终局部验证：** [green-final.xml](../benchmarks/reports/verification/P6-robustness-window-fix/green-final.xml) 实际 45 通过、0 失败/跳过，17.466 秒；包含窗口单元、行为集成、真实归档重放、小实验完整证据、原无未来泄漏与时长策略回归。Ruff check、469 文件 format 检查、mypy app 256 文件与窗口模块类型检查均退出 0，见 [verification-summary.json](../benchmarks/reports/verification/P6-robustness-window-fix/verification-summary.json)。实际命令：

~~~text
uv run --locked --offline pytest tests/unit/test_robustness_windows.py tests/unit/test_duration_policy.py tests/integration/test_robustness_dispatch.py tests/integration/test_robustness_archive_replay.py tests/integration/test_robustness_dispatch_evidence.py tests/integration/test_robustness_no_future_leak.py -q --junitxml=benchmarks/reports/verification/P6-robustness-window-fix/green-final.xml
uv run --locked --offline ruff check .
uv run --locked --offline ruff format --check .
uv run --locked --offline mypy app
uv run --locked --offline mypy benchmarks/robustness/dispatch.py
uv run --locked --offline python -m benchmarks.robustness.compare_dispatch --output benchmarks/reports/P6-robustness-window-compare-run2 --baseline benchmarks/reports/verification/P6-robustness-window-fix/baseline.json --case-id combination-000 --case-id combination-018 --case-id combination-052 --trajectories 10
~~~

**受控小规模对照：** [P6-robustness-window-compare-run2/report.json](../benchmarks/reports/P6-robustness-window-compare-run2/report.json) 为 EXPERIMENT_COMPLETED，`partial=true`、`formal_acceptance=false`；三个菜单×10 轨迹×四组×两版本，共 240 条、120 对，230743.5987ms。两版本逐对使用原归档初排、同随机种子 20261004、同扰动/知识/策略与完整观察状态重排缓存；12 次真实重排计算，求解结果只在同一完整已观测状态间共享。全部 252 份新轨迹/重排归档哈希复核，完成轨迹均独立终局校验，失败保留完整分母。

| 实验组 | 旧版完成/失败（30 条） | 修复后完成/失败（30 条） | 失败→完成 | 完成→失败 |
| --- | ---: | ---: | ---: | ---: |
| NOMINAL_SHIFT | 8/22 | 14/16 | 6 | 0 |
| NOMINAL_REPLAN | 8/22 | 14/16 | 6 | 0 |
| BUFFERED_SHIFT | 8/22 | 13/17 | 5 | 0 |
| BUFFERED_REPLAN | 9/21 | 14/16 | 5 | 0 |
| 合计（每版 120 条） | 33/87 | 55/65 | 22 | 0 |

剩余 65 条新版本失败均标记 ACTUAL_WINDOW_EXPIRED；不能据此声称关键窗口策略已解决，也不能把这一小样本成功率外推为全量或真实厨房概率。初排、原全量报告及实验期间源码指纹不变；原报告 SHA-256 `2baabc221737e4009339af86879ed56c8e3720bf3118eb9da721e693ce2e00da`，基线和原源码字节副本见 [baseline.json](../benchmarks/reports/verification/P6-robustness-window-fix/baseline.json)。

**第 1、2 步完成时的阶段边界（历史记录）：** 当时第 1、2 步局部验证完成，第 3 步持续反馈、第 4 步关键窗口策略及第 5 步修复后全量/阶段复验尚未开展。原 `test_p6_robustness_evidence.py` 未删除：实际执行在源码绑定断言处拒绝旧报告，`historical-source-guard.xml` 为 1 项预期失败、退出 1，1.025 秒，表明旧 16000 条结果不能验收新模拟器。原阶段与交付归档保留历史身份，P6-03 保持 IN_PROGRESS；不以 45 项局部绿灯或小规模实验替代全量 P6 验收。完整命令、退出状态、时间与指纹见 [commands.md](../benchmarks/reports/verification/P6-robustness-window-fix/commands.md) 和 verification-summary.json。

**持续反馈与策略修复记录（2026-10-06，用户授权第 3–5 步）：** 用户明确选择“依次完成第 3–5 步”，随后选择“采用可选快速重排：合法候选先发布，构造失败再用 CP-SAT”。持续反馈复用生产触发规则，逐个处理实际开始、完成和剩余估计事件，同秒完成优先、同秒原因合并；不再限制只观察首工序或最多重排一次。到可见预计结束仍未完成时报告预先声明的固定 30 秒合成估计，私有未来结束时间不进入规划输入，不伪装实测。重复反馈不重复落账，冲突身份拒绝，未确认完成不释放占用，过期状态或计划版本拒绝发布；重排稳定性参考绑定当前真实发布身份。

`FEASIBILITY_FIRST` 仅在 REPLAN 时提前返回独立校验通过的 Greedy 完整候选；构造失败继续真实 CP-SAT，准备、构造、校验和绑定仍共享 2400ms 截止时间。初排保持 4200ms 优化流程，默认 OPTIMIZE/NONE 字段不写入原策略 JSON；默认策略哈希保持原值。快速路径不虚报人工/稳定性等目标已优化。`SERIAL_RECIPE_V1` 为显式带来源策略边，按菜单实例顺序限制同时展开为一道菜；Compiler、独立来源校验和真实执行入口共同执行，原工艺间隔、固定程序、热时长、资源容量和实际占用保留。保守顺序的总用时、完成差和人工代价需随完成率一起报告，不默认启用。

**红绿与试验版本：** 原持续反馈注入反例 `feedback-red.xml` 4 失败/1 通过；关键策略字段反例 `critical-red.xml` 3 失败/1 通过；快速模式及夹具修订、非法候选来源字面量等失败 XML 均保留，不将夹具失败冒充算法反例。`green-before-pilot.xml` 为 61 通过，`cache-publication-red.xml` 为同几何不同发布身份的一项预期失败，`cache-service-green1.xml` 为修正身份、冷缓存恢复/损坏拒绝及真实 SQLite 快速追加/重复/重启五项通过。生产 mypy 257 文件通过；曾额外尝试对旧未标注 benchmark 严格检查而失败，未降低 mypy app 门槛。

原生持续反馈原型 pilot-run2 七条完成、233376.448ms，不能作为当前源码证据；快速 pilot-run3 七条完成、64578.4517ms，其引用版本随后修正。small-run1 因已复现发布引用碰撞主动中止，保存 119 个完整轨迹归档、当时源码一致和中止原因，不将其包装为完整配对结果。最新 pilot-run4 七条完成、64 次实际重排、62 Greedy/2 CP-SAT，其全部归档、当前源码、原报告及终态证明通过 `pilot4-audit.json`；仍是部分实验。small-run2 重新运行同三菜单×10 轨迹×七组共 210 条；当时后续计划为新目录执行 340 全量场景、240 次消融、17 项属性/状态机、53 项故障、153 次性能、新 Windows 包以及 40×100×7=28000 条全量扰动；全量部分随后按下文用户要求省略。每个命令日志、退出码和耗时由 `scripts/run_feedback_revalidation.py` 保存，失败即停，原报告与交付包保留。完整阶段所需指针已改为新报告，缺失、未完成或源码不匹配继续使验收失败；原指针与旧完整测试保存在 `P6-feedback-critical-fix/before-source/metadata` 和 `before-source/tests`。该段保留当时的安排；当前局部结果见下文，完整阶段或正式外部门槛未验收。

**用户范围调整（2026-10-06）：** 磁盘容量实查 D 盘约 8GB、C 盘约 32GB；当时约 900 份归档占 440MB，完整七组按当时体量估计需 150–200GB。询问可用存储后用户明确“不用全量”，因此取消 28000 条扰动、340 场景和 240 次消融的全量运行，保留新版本 210 条配对验证和服务、性能、Windows 复验。完整 P6 证据测试仍严格要求七组各 4000 条，不改成 210 条以取得绿灯；相应全量指针保留为未执行要求，原 16000 条报告继续只作为历史基线。当前监督入口使用 `--without-robustness`；其完成代表列出的技术复验完成，不等于完整 P6 阶段通过，也不再等待用户扩容。

**第 3–5 步源码配对结果（2026-10-06）：** [small-run2](../benchmarks/reports/P6-feedback-critical-small-run2/report.json) 实际完成三菜单×10 轨迹×七组共 210 条，耗时 2995008.0995ms；源码和原报告不变。全部归档、原四组初排字节、100 条完成轨迹的独立终态证明与 110 条失败分母通过 [small2-audit](../benchmarks/reports/verification/P6-feedback-critical-fix/small2-audit.json)，没有源码排除项。实际原生重排 1796 次，候选来源计入失败尝试为 Greedy 1274、CP-SAT 501、无候选来源 21；27 次实际计算失败保留。每条轨迹重复请求和缓存命中另计，不能将缓存次数冒充新求解。

| 当前组 | 完成 | 失败 | 全部轨迹 | 失败率 |
| --- | ---: | ---: | ---: | ---: |
| NOMINAL_SHIFT | 14 | 16 | 30 | 53.3% |
| NOMINAL_REPLAN | 14 | 16 | 30 | 53.3% |
| BUFFERED_SHIFT | 13 | 17 | 30 | 56.7% |
| BUFFERED_REPLAN | 14 | 16 | 30 | 53.3% |
| NOMINAL_FAST_REPLAN | 14 | 16 | 30 | 53.3% |
| NOMINAL_FAST_CONTINUOUS | 17 | 13 | 30 | 43.3% |
| CRITICAL_CONTINUOUS | 14 | 16 | 30 | 53.3% |

持续反馈相对快速单次：6 条失败转完成、3 条完成转失败，净增加 3 条完成；两组均完成的 11 对轨迹中，完成总时长差的中位数为 −26 秒，出菜差却增加 8866 秒，人工总时长差中位数为 0。逐菜策略相对持续反馈：3 条失败转完成、6 条完成转失败，净减少 3 条完成；两组均完成的 11 对轨迹中，完成总时长增加 7158 秒、出菜差增加 8412 秒（均为配对差中位数）。两个持续组的所有完成轨迹均超过 300 秒出菜差。本轮不支持默认启用逐菜策略，也不能把持续反馈完成率提高解释成综合排程质量提高；可选模式保留明确质量标志。详细同口径时间、人工、缓存和失败分类见 [comparison-summary](../benchmarks/reports/verification/P6-feedback-critical-fix/comparison-summary.json)。原四组仍为 55 完成/65 失败，与此前 68 降到 65 的剩余数一致；新组分母单列，不据此继续扣减旧失败。

**第 3–5 步工程复验完成（2026-10-06）：** [监督报告](../benchmarks/reports/verification/P6-feedback-revalidation-run1/report.json) 的 14 个阶段全部实际退出 0，状态为 `LIMITED_REVALIDATION_COMPLETED_FULL_OMITTED_BY_USER`，源码前后不变。归档审计、Ruff、全仓库格式、严格 mypy app 257 文件、66 项相关回归、前端类型、17 项组件测试和构建通过。17 项属性/状态机通过：十类系统性质各实际 200 样本，完整会话实际 109 条序列/4445 步，非配置数冒充执行数。53 项故障检查通过并保存实际故障证据。断图后的 [74 项服务/UI 回归](../data/verification/P6-feedback-service-run1/report.json) 全部通过、零失败/跳过，涵盖内部 API、官方适配器、SSE 通知和真实浏览器；Neo4j 恢复原运行状态。另两项局部证据检查通过，验证 210 条配对归档、全部终态证明和原报告不可覆盖当前模拟器；完整 28000 条门槛不降级、不冒充已通过。

**第 3–5 步性能与交付：** [三轮 153 次真实 HTTP 性能](../benchmarks/reports/P6-performance-feedback-run1/report.json) 为 `TARGETS_MET`：93 次初排、60 次重排均零失败/超预算。服务内初排 P50/P95/最大为 2312/2718/2875ms，重排为 1766/2016/2078ms；客户侧及冷启动数据另保留。该性能报告采用原默认优化策略，与合成持续反馈中保留的预算失败分母分别报告，不能用此零失败替代扰动结果。新 [Windows ZIP](../data/delivery/P6-Windows-feedback-run1.zip) 的 257 个生产文件与当轮源码逐文件一致；[独立安装复验](../data/verification/P6-windows-feedback-run1/report.json) 已实际断图、离线安装、初排/追加、前端访问及两次启停恢复，运行无需 Node、Neo4j 或 Docker。原交付清单、旧包和旧实验保留；[本轮局部清单](../deploy/feedback_fix_manifest_run2.json) 绑定报告、原始 XML、新 ZIP、两份可选策略及当轮源码/前端哈希，`formal_acceptance=false`、`full_p6_acceptance=false`，状态明确 `LOCAL_IMPLEMENTATION_VERIFIED_PARTIAL_ROBUSTNESS_HAS_FAILURES`。首次局部清单也保留；run2 追加故障和 Windows XML 的直接绑定。修复后第 3–5 步的用户调整范围已完成，完整 P6 与 P6-08 外部门槛保持未验收。

<a id="timesave-fix"></a>
#### 快速重排 timeSave 局部修复（2026-10-06）

**状态：TIMESAVE_FIX_VERIFIED_3000MS。** 用户授权先修复 timeSave，并允许重排总预算在 2400–3000ms 范围内调整。目标属于 P6-03 快速反馈与 P6-06 性能的局部修复；完整阶段状态仍为 IN_PROGRESS，不覆盖关键窗口的 65 条剩余失败或官方动态联调。

**实现与范围：** 生产代码仅修改 `app/scheduling/engine.py`。快速 Greedy 完整候选若已满足串行顺序，复用本轮独立校验结果作为真实参考；否则在同一截止时间和 Solver 总额度内构造并独立校验串行参考。参考进入合法候选池，选择总用时不超过参考的真实计划；不截断负值、不使用旧菜单或旧执行状态的参考。取消全部需求的实际空计划以自身为参考。缺少合法参考或预算不足时在发布前诚实失败，保留事实、历史版本并阻止过期计划派发。Greedy 构造失败仍保留原 CP-SAT 优化回退；初排和默认 OPTIMIZE 保留原优化流程。

| 用户验收项 | 已运行的证据 |
| --- | --- |
| 数值正确 | 合成双菜参考 2880 秒、并行 1620 秒，先按整数秒相减，再转为一位小数分钟，实际输出 `21.0`；真实 HTTP 输出逐份复算；参考更快时选择实际参考计划并输出 `0.0` |
| 参考有效 | 重新独立扫描当前计划和串行参考，验证串行顺序及相同 problem_hash、菜单、state_revision、knowledge_version、snapshot_id、time_origin；缺少参考和合法并行冒充串行的反例均拒绝发布 |
| 快速路径可用 | 真实 Compiler/Greedy/CP-SAT/Validator/SQLite/HTTP 服务，日志出现 `FEEDBACK_FEASIBILITY_RETURN`；成功结果恰为比赛五部分，未虚报可选目标已优化；真实反例确认构造失败后的原 CP-SAT 目标路径仍执行 |
| 动态状态正确 | 追加、取消单菜、取消全部、已完成和运行中工序、剩余时间更新；运行工序真实开始时间与已完成记录保持，参考重新绑定当前问题 |
| 持久化一致 | SQLite 重开及 FastAPI 应用重启；相同幂等请求的完整 HTTP 响应字节相同，同一发布计划的 timeSave 相同 |
| 性能与回归 | 最终源码上 41 个相关文件统一运行，264 通过、0 失败、2 外部门槛明确 deselect，266.91 秒；两档预算、三轮共 60 次修复后 HTTP 请求零失败/超预算；Ruff、格式和严格 mypy app 257 文件通过 |

**红绿和失败保留：** `red.xml` 在原引擎上 9 失败、1 通过；早期宽范围回归中的 21 项旧快照读取问题用同身份/同哈希已授权测试副本复验通过，未改原资料或 ACL。正式审核和官方动态确认两项既有失败保留，不改成通过。中间实现曾在 Greedy 失败时省掉原 CP-SAT 优化回退，真实见证测试暴露后修正；两个不合法的合成夹具失败记录及旧预算分配的实际 HTTP 失败全部保留。最终核心 37 项、反馈 66 项与宽范围回归统一去重后为 264 个实际通过用例，不能相加当作独立数量。准确命令、退出码、开始/结束时间和应用/测试哈希见 [regression-final-run.json](../benchmarks/reports/verification/P6-timesave-fix-run1/regression-final-run.json)。

**同配置性能对照：** 五菜单 combination-000、018、041、043、052，各三轮，修复前和修复后各 60 次实际 HTTP 请求。旧引擎通过独立实验进程从逐字归档源码装载，六份实际进程声明核对其 SHA256；其他生产源码与请求、预算、硬件相同，当前引擎同样保存实际声明。两份报告的全部原始请求/响应、计划、问题、证明及耗时按 SHA256 核验；失败请求保留在统计分母内。

| 重排总预算 / Solver | 修复前失败 / 次数 | 修复后失败 / 次数 | 修复前 P50 / P95 / 最大 | 修复后 P50 / P95 / 最大 |
| --- | ---: | ---: | --- | --- |
| 2400 / 1500ms | 5 / 15 | 0 / 15 | 1719 / 2125 / 2125ms | 1828 / 2219 / 2219ms |
| 3000 / 2100ms | 3 / 15 | 0 / 15 | 2375 / 2719 / 2719ms | 2406 / 2906 / 2906ms |

3000ms 组修复前的三次失败均缺少串行参考，修复后为零；服务内 P50 增加 31ms、P95 增加 187ms，修复后客户端 P50/P95/最大为 2407.8855/2910.5949/2910.5949ms。初排始终采用 4200ms，总计修复后 30 初排及 30 重排均成功且未超预算。原始 [before-run3](../benchmarks/reports/P6-timesave-before-run3/report.json)、[after-run3](../benchmarks/reports/P6-timesave-after-run3/report.json) 与旧诊断报告均保留；固定五菜单、三轮不能保证所有复杂动态输入均成功。

**策略使用与交付：** 新增 [p6-feedback-fast-v2.json](../data/policies/p6-feedback-fast-v2.json)，只改版本号、重排总预算 3000ms 和 Solver 2100ms；新增 600ms 分配给求解，仍预留发布时间。原快速/逐菜 v1 哈希保持，默认 OPTIMIZE 不自动开启快速模式。开发服务新建会话可通过 `SMART_COOKING_POLICY_PATH` 选择 v2，已有会话继续绑定原策略。当前修复交付于工作区，已有 Windows ZIP 是上一轮源码版本，本轮未重建或冒充其包含修复。执行证据见 [commands.md](../benchmarks/reports/verification/P6-timesave-fix-run1/commands.md)、[六项核验汇总](../benchmarks/reports/verification/P6-timesave-fix-run1/verification-summary.json) 和 [time_save_fix_manifest_run1.json](../deploy/time_save_fix_manifest_run1.json)；`formal_acceptance=false`、`full_p6_acceptance=false`。用户省略的全量实验继续不运行。

<a id="p6-04"></a>
### P6-04 执行系统级属性和状态机回归

**状态：IN_PROGRESS（当前源码对应技术复验已通过；完整 P6 依赖和阶段未全量验收，原开发证据保留）。** 前置任务：[P6-01](#p6-01)。

**输入：** 当前完整实现、独立参考账本、可行见证/结构合法/已知矛盾生成器。

**输出：** 关键性质每类至少 200 样本、至少 100 条最多 50 步序列的结果及缩减失败回归库。

**生产/数据文件范围：** `tests/property/test_system_properties.py`、`tests/stateful/test_full_session.py`、`tests/regression/`、`benchmarks/property_profiles.py`。

**测试文件：** `tests/property/test_system_properties.py`、`tests/stateful/test_full_session.py`。

**接口约定：** 生成器类型决定预期；限时 UNKNOWN 不被视为必然不可能或求解器错误证明。

**实现要点：** 组合覆盖、数量、热过程、历史、通知、版本和回放；已有任务级性质测试保留，本任务补充跨模块序列。

**验收断言：**

- [x] 每个成功结果保持全部不变量；非法事件拒绝后状态不变。
- [x] 可失败案例缩减到可重放输入并记录工具版本和种子。
- [x] 枚举范围外不宣称形式化证明；跳过或预算不足单列。

**任务验证命令：**

~~~text
uv run --locked pytest tests/property/test_system_properties.py tests/stateful/test_full_session.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 不得通过移除难生成的动作或降低硬约束使属性测试变绿。

**设计定位：** 架构文档 21.8。

**验证记录（2026-10-04）：** 八类性质各实际 200 样本，共 1600，覆盖独立可行见证、覆盖遗漏/重复、数量及来源篡改、人工冲突、固定热时长、已完成历史、通知身份/标签与序列化重放；`P6-system-properties-run1.xml` 8 通过、99.99 秒。跨模块真实规划/SQLite 序列 `P6-full-session-run1.xml` 1 通过、232.83 秒：106 条、4048 步，Hypothesis 每条生成上限 50 步；加菜、延迟、取消、完成/失败、设备停用/恢复、白名单修正、乱序版本、重复身份、提醒和重启均有实际计数。153 次成功发布及 196 次诚实规划失败均检查执行历史和真实消费保持，失败不被删除。两个明确合成独立会话分别验证物料与设备路径；非法释放及盘点前值拒绝后状态不变，合法释放/重做等已有任务级性质仍须在最终聚合中复跑。版本和实际数量见 `data/verification/P6-system-property-summary-run1.json`；当前后续缓冲实现不由该旧报告覆盖。

**当前聚合（2026-10-05）：** `P6-all-properties-run2.xml` 实际 16 通过、1 真实图环境门槛跳过，608.05 秒；十类 P6 性质各 200 样本，共 2000。完整会话状态机 109 条、4445 步，203 次发布与 145 次诚实规划失败均检查不变量；原事件/执行状态机分别 220 条/8749 步、223 条/10038 步。显式图补跑 `P6-real-graph-property-run2.xml` 因旧开发源引用用户已删除的文档失败，证据保留，不恢复原文件。测试改为通过 `load_release` 读取已完整校验的固定 P4 归档，所选原知识经独立知识门后真实投影和导出；`run3.xml` 1 通过、57.30 秒，显式种子 20261004。当前汇总 `P6-system-property-summary-run4.json` 为 PASSED，保留原跳过并引用同一性质的实际补跑证据，绑定全部 XML 和测试哈希；未把旧失败当作新输入成功。质量 `P6-quality-run3.json` Ruff、441 文件格式及 mypy app 254 文件分别真实退出 0；此前格式和生成副本检查失败记录保留。

**2026-10-05 当前完整属性实测：** `P6-all-properties-run3.xml` 实际 17 通过、0 失败、0 跳过，691.57 秒，显式种子 20261004；包含真实图投影/导出与全部原属性/状态机。十类系统性质各 200 个实际样本，完整会话 109 条/4445 步、203 次合法发布、145 次诚实规划失败、240 次重启；每一步均核对不变量，失败不伪装发布。`P6-system-property-summary-run5.json` 为 PASSED，绑定本轮 XML、当前应用和测试哈希；历史失败/跳过仍保存，本轮不借用旧补跑代替当前执行。

<a id="p6-05"></a>
### P6-05 完成全系统故障注入与恢复验证

**状态：IN_PROGRESS（当前源码对应技术复验已通过；完整 P6 依赖和阶段未全量验收，原开发证据保留）。** 前置任务：[P6-01](#p6-01)。

**输入：** 完整应用、真实 SQLite、工作进程、快照和 SSE，以及可控制注入点。

**输出：** 进程、锁、提交前后、快照、Neo4j、LLM、并发状态、通知及执行异常的故障矩阵报告。

**生产/数据文件范围：** `benchmarks/fault_injection.py`、`tests/fault_injection/test_system_faults.py`。

**测试文件：** `tests/fault_injection/test_system_faults.py`。

**接口约定：** 所有成功回退必须完整合法且已确认发布；故障失败保留真实事实和可查询状态。

**实现要点：** 分别注入求解退出、锁超时、提交响应丢失、错版知识、关闭 Neo4j 冷启动、LLM 非法返回和 SSE 断连。

**验收断言：**

- [x] 同事件只产生一次业务效果；没有半写入、提前释放或库存复活。
- [x] 过期结果拒绝发布，损坏知识不被加载，断图仍能使用有效发布完整运行。
- [x] 故障请求预算与恢复时间分开测量，无候选时诚实失败，后续请求可继续处理。

**任务验证命令：**

~~~text
uv run --locked pytest tests/fault_injection/test_system_faults.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 故障轨迹与正常性能分开统计；不宣称网络传输天然恰好一次。

**设计定位：** 架构文档 15.5、16.9、21.9。

**验证记录（2026-10-04）：** 真实停 Neo4j 的 `P6-fault-matrix-run1` 执行 53 项，50 通过、3 失败、0 跳过；两项误将合法 Greedy 发布但官方响应失败断言为无计划，另一项未调用已持久化恢复队列。修正测试契约后 `run2` 为 52 通过、1 独立 TCP 测试启动失败、0 跳过，源码和测试哈希前后一致。真实回环客户端显式禁用环境代理，并用 closing 管理已发出就绪请求的客户端；独立 SSE 断开 TCP 后重启同 SQLite 和游标重放通过，`P6-performance-tools-run1.xml` 合计 9 通过。第三轮 `P6-fault-matrix-run3/report.json` 整批实际 53 通过、0 失败/跳过，pytest 275.67 秒，监督器共 284.166 秒；源码和测试哈希前后一致，Neo4j 实际停止且结束恢复原 running 状态。求解退出/挂起请求 3138.744/3261.978ms，后续恢复 2614.877/3056.270ms，成功回退独立校验。故障请求与恢复计时分别保存，没有新增真实模型调用。后续仅为性能监督增加测试进程可选 factory 参数，默认生产方式继续由最终相关聚合覆盖。

**2026-10-05 当前完整故障实测：** `P6-fault-matrix-run4/report.json` 实际 53 通过、0 失败、0 跳过，监督耗时 284456.7779ms；当前代码与故障测试源码前后不变。知识维护服务实际停止、Bolt 端口关闭，结束恢复 running。求解退出/挂起请求分别 2255.9158/2028.9573ms，诚实 503 后分别用 3151.8328/2500.7471ms 恢复；后续请求正常。真实 TCP SSE 断开与重启、事务前后故障、锁冲突、投影失败、损坏/混版知识及无效语言事件均有实际记录；语言故障使用测试 Provider，新增真实 LLM 调用为 0。故障与恢复耗时单列，不混入正常请求性能统计。

<a id="p6-06"></a>
### P6-06 固定硬件进行端到端性能测试与调优

**状态：IN_PROGRESS（当前源码对应技术复验已通过；完整 P6 依赖和阶段未全量验收，原开发证据保留）。** 前置任务：[P6-02](#p6-02)、[P6-03](#p6-03)、[P6-04](#p6-04)、[P6-05](#p6-05)。

**输入：** 通过正确性检查的候选版本、最终策略、固定 4 vCPU/8 GB 参考环境或实际声明硬件。

**输出：** P50/P95/最大延迟、失败/超时/回退率、解质量、资源与版本记录；调优后的锁定策略。

**生产/数据文件范围：** `benchmarks/performance.py`、`benchmarks/scenarios/performance_profiles.json`、`app/config.py`。

**测试文件：** `tests/integration/test_performance_budget_contract.py`。

**接口约定：** 服务内目标 4200/2400 ms，官方优秀阈值初排 <5000 ms、重排 <3000 ms；分别记录服务内与客户端口径。

**实现要点：** 先预热再固定单并发测试，冷启动另测；对代表测试集重复运行至少 3 轮；依据模型统计调候选和预算，不改变工艺。

**验收断言：**

- [x] 目标验收依据本文件阶段门槛，完整报告 P50/P95/最大值，不能用平均耗时掩盖长尾。
- [x] 任何调优导致的计划不合法或成功率下降都进入对照报告，最终正确性检查重跑。
- [x] 若仅满足官方合格 <10000/<8000 ms 而未达项目目标，记录未达标，不能自动降级验收标准。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_performance_budget_contract.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 测得达标只针对声明环境与数据集，不保证隐藏数据或网络条件；新资源规格须重新测试。

**设计定位：** 架构文档 2.1、15.1、21.5。

**验证记录（2026-10-05，部分诊断）：** 评估器已固定 31 菜单及全部 20 重排脚本、三轮共 153 请求；实际硬件 i7-1165G7、8 逻辑 CPU、34043187200 字节内存，非 4 vCPU/8 GB 仿真。`P6-performance-debug-run1` 误用旧整数分钟契约，在成功 HTTP 预热后中断；`run2` 的三次重排因错误使用 SIMULATED 来源被 MANUAL 会话诚实拒绝，全部保留。运行器改为一位小数 DTO 及实际会话执行模式，`run3` 三轮共 6 次请求全部成功且源码一致，初排服务内 max 2891ms、重排 max 2328ms，客户端分别 2896.667/2337.607ms；只是一个固定取消场景，不能当作 153 请求完整性能认证。每次实验请求前的独立进程预热尝试/耗时另列，实验监督端口仅在 benchmarks 应用工厂中注册，不进入 Windows 生产包。记录本机服务端单调时钟 15.625ms 分辨率及客户端高精度计时差异。完整三轮采样待扰动实验结束后顺序执行。

**2026-10-05 当前完整性能实测：** `benchmarks/reports/P6-performance-run6/report.json` 为 TARGETS_MET，三轮 153/153 成功、0 次超预算。INITIAL 93 次 P95/最大 2516/2625ms，REPLAN 60 次 P95/最大 1953/2250ms；预算保持 4200/2400ms，客户端最大 2615.2178/2254.3229ms。该结果覆盖当前 Windows 在线主链，失败 run1–5、局部诊断和修复前反例均保留。新增待恢复 SQL 部分索引、前台/后台所有权协调及完整 JSON 工作进程传输；真实消息/进程/回滚回归 12 通过。完整阶段仍待当前全量、属性、故障与交付证据汇总后执行，暂不标记 DEVELOPMENT_VERIFIED。

<a id="p6-07"></a>
### P6-07 形成可复现 Windows 离线部署包

**状态：IN_PROGRESS（当前源码对应技术复验已通过；完整 P6 依赖和阶段未全量验收，原开发证据保留）。** 前置任务：[P6-06](#p6-06)。

**输入：** 通过测试的代码、依赖锁、正式知识、数据库 Schema 和最终策略。

**输出：** 固定 Python/Node/依赖版本和文件哈希、Windows 启停脚本、只读知识包、独立持久化状态目录、环境变量模板和部署检查结果。用户于 2026-10-04 选择“完全使用 Windows，调整 P6-07 验收要求”，此要求替代原 Linux 容器交付。

**生产/数据文件范围：** `deploy/windows/Install.ps1`、`deploy/windows/Start.ps1`、`deploy/windows/Stop.ps1`、`deploy/windows/Verify.ps1`、`deploy/.env.example`、`deploy/release_manifest.json`、`scripts/package_release.py`。

**测试文件：** `tests/integration/test_deployment_smoke.py`。

**接口约定：** 默认在线配置仅依赖快照、状态库和工作进程；Neo4j 知识维护独立启用，不成为 Windows 在线服务启动依赖。

**实现要点：** Windows 服务从绑定哈希的只读知识启动；运行库放入独立可写状态目录，迁移有版本；打包排除源密钥、临时日志和原始项目 ZIP。离线安装依赖来自锁定的 wheel 与固定运行时，独立目录实际验证，不把开发虚拟环境直接复制成可复现安装证据。

**验收断言：**

- [x] Windows 独立安装目录可从交付物离线启动，停止 Neo4j 仍可初排与内部重排。
- [x] 重启保留事实和活动知识版本，运行时/依赖/策略/知识及部署包哈希可核对。
- [x] 部署说明中的全部命令由此任务实际运行；包内不含 .env 密钥和测试会话数据库。

**任务验证命令：**

~~~text
uv run --locked pytest tests/integration/test_deployment_smoke.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 生成与本地验证部署包不等于公网发布或正式提交；外部发布在明确授权和目标环境下进行。

**设计定位：** 架构文档 20、22。

**用户修订（2026-10-04）：** 原容器与 Linux 验收替换为完全 Windows；知识离线、SQLite 持久化、真实启停和版本复现要求不降低。用户同时确认没有指定公网目标或新正式数据/官方资料，P6-08 外部条件保持待办。

**验证记录（2026-10-04，候选验证）：** `P6-Windows-candidate-run1.zip` 包含 3109 个绑定哈希文件和 32 个来自当前 uv.lock 的 wheel，ZIP SHA-256 `d506dffb565598da2fee4f5bd4af46b6608c9dc6c302d465e81bd0d41afdaa33`。打包边界测试 6 通过；Windows 集成命令 `pytest tests/integration/test_deployment_smoke.py --basetemp=data/verification/P6-windows-smoke-run2 --junitxml=data/verification/P6-windows-smoke-run2.xml` 实际 1 通过、0 跳过，80.23 秒；真实独立目录离线安装、HTTP 初排/单菜追加、前端访问、停止全进程树、重启后状态与原幂等响应保持一致。测试期间实际 Neo4j 为 exited，结束恢复 running，见 `data/verification/P6-windows-offline-supervisor-run2.json`。首轮捕获后台进程输出管道导致测试超时，失败 XML/日志保留；修正为独立日志文件后重跑。此包先于后续排程性能修复生成，不能作为最终当前代码交付身份；前置性能和全阶段验收完成后必须重新打包及重验。

**2026-10-05 当前包身份：** `data/delivery/P6-Windows-technical-run1.zip` 已由当前代码实际生成，3115 个文件、32 个锁文件 wheel；ZIP SHA-256 `7c5a886721c74efce3d1e20d16648fa384fab9a3fe71ba0547d5ee0c2a3b34db`，包内清单 SHA-256 `4f4cbf351e4c7051f4d6c1d7a38aa66ed5cc86e095820c6fbb256dd4d334de2a`。代码/静态前端/只读知识/策略/Windows 脚本与当前版本一致，包含新 SQL 索引迁移和在线 JSON 工作进程适配器；仍为 DEVELOPMENT_CANDIDATE / NOT_FINAL。此条仅记录生成与身份校验，独立目录安装、重启及当前阶段门槛结果随后登记。

**2026-10-05 当前包独立实测：** `data/verification/P6-windows-smoke-run3/report.json` 为 PASSED，1 通过、0 失败/跳过，pytest 74.23 秒、监督全过程 86963.6571ms。实际从当前 ZIP 在独立中文目录离线安装，逐条运行 Install/Start/Verify/Stop/Start/Stop；初排、单菜追加、静态前端和重启后的状态/知识版本/原幂等响应一致。知识维护服务实际停止且 Bolt 关闭，结束恢复原 running 状态；没有依赖开发虚拟环境或运行时 Node，新增真实语言模型调用为 0。源文件与 Windows 验证说明哈希前后不变，仍须由最终开发阶段入口汇总质量与全部前置证据。

<a id="p6-08"></a>
### P6-08 完成官方条件核对与最终验收交付

**状态：BLOCKED（用户确认没有新增正式/官方资料及公网目标，外部条件不可由本地模拟替代）。** 前置任务：[P6-07](#p6-07)。

**输入：** 全部阶段证据、官方动态协议确认、实际部署/提交信息和对应访问授权。

**输出：** 最终版本清单、技术/数据/接口/性能验收结论、可访问服务验证及外部问题闭环。

**生产/数据文件范围：** `task.md`、`deploy/release_manifest.json`、`benchmarks/reports/final/`。

**测试文件：** `tests/contract/test_official_dynamic_profile.py`、`tests/integration/test_public_endpoint_smoke.py`。

**接口约定：** 最终验收必须逐项引用证据；官方动态 profile 的成功条件与已确认协议一致。

**实现要点：** 核对 U01–U14 逐项处置，部署目标明确后验证公网入口、正式菜单及重排；保存提交版本与包指纹。

**2026-10-05 实际前提检查：** `data/verification/P6-external-preconditions-run1.xml` 为 2 项失败、0 跳过、1.58 秒：官方动态确认标志为 false，公网目标为 null。两项在任何公网请求之前即终止；没有新增真实模型调用或擅自发布。项目自主设计的 task_id 查询位置、累计响应与时间原点已实现并在开发主链验证，不冒充官方真实联调。正式 P1 full 数据发布和完整阶段入口同样保持未验证，不将固定 development 发布改标为正式。

**验收断言：**

- [ ] P0–P5 和 P1 full 全部 VERIFIED，P6 其余任务通过，100 道数据与最终代码/策略完全对应。
- [ ] 官方会话、增量菜单、状态来源和时间原点已确认并真实联调；未确认时仅技术完成，任务保持 BLOCKED。
- [ ] 公开服务、接口契约、性能证据和交付说明一致，未完成项目不写“全部通过”。

**任务验证命令：**

~~~text
uv run --locked pytest tests/contract/test_official_dynamic_profile.py tests/integration/test_public_endpoint_smoke.py -q
~~~

以上命令配合第 2 节质量检查执行；所需真实服务未提供或必需测试跳过时不能标记 VERIFIED。

**边界：** 本任务需要真实外部事实；缺部署目标、协议或发布授权时停在该边界，不能自行提交比赛或虚构确认。

**设计定位：** 架构文档 21.6、23。

**验证记录：** 未执行；无验收证据。

<a id="coverage"></a>
## 6. 设计覆盖与新增要求映射

| 架构章节或设计要求 | 主要实现任务 | 验收归属 |
| --- | --- | --- |
| 1–4 目标、规则、技术与模块边界 | P0-01–P0-07 | P0、各阶段依赖检查 |
| 5 离线整理、审核与发布 | P1-01–P1-09 | P1 core/full |
| 6 菜谱、物料与身份模型 | P0-02–P0-05、P2-01、P4-03 | P0、P2、P4 |
| 7 知识图谱、快照与索引 | P1-05–P1-08、P3-01 | P1 core、P5-08、P6-05 |
| 8 时间与资源，含 8.6 全设备竞争矩阵 | P0-03、P1-04、P2-02–P2-04、P2-06–P2-08、P3-04、P4-05 | P2、P3、P4、P5-02、P6-01 |
| 9 Compiler、剪枝、模型规模 | P2-01–P2-05、P3-06 | P2、P3、P6-02 |
| 10 跨菜共享前处理 | P3-01–P3-03 | P3-07、P4-03 |
| 11 共同热批次与转换 | P2-02、P3-04–P3-05 | P2、P3、P4-05 |
| 12 CP-SAT 与独立验证 | P2-06、P2-08、P3-06 | P2、P3、P6-04 |
| 13 目标与时长不确定性 | P2-09–P2-10、P6-03 | P2、P6 |
| 14 Greedy 插入与回退 | P2-07、P3-06 | P2、P3、P6-04 |
| 15 预算、失败与不可行诊断 | P2-11–P2-12、P4-07 | P2、P4、P6-05–P6-06 |
| 16 执行、库存、异常与重排 | P4-01–P4-10 | P4、P5-08、P6-04–P6-05 |
| 17 大模型、追溯与通知 | P0-05、P1-02–P1-03、P4-10、P5-04–P5-05 | P1、P4、P5 |
| 18 官方与内部接口 | P0-07、P5-01–P5-05 | P5、P6-08 |
| 19 存储、幂等与一致性 | P1-07–P1-08、P4-01–P4-03、P4-07 | P1、P4、P6-05 |
| 20 展示与部署 | P5-01、P5-06–P5-08、P6-07 | P5、P6 |
| 21 测试、消融、属性、故障和扰动 | 对应模块随任务测试，P6-01–P6-06 聚合 | 各阶段及 P6 |
| 22 建设阶段 | 本文件 P0—P6 | 逐阶段门槛 |
| 23 外部未明确事项 | P0-07、P5-08、P6-08 | 最终外部条件验收 |
| 24.2 八项评审补充 | 见下表 | 对应责任任务及 P6 |

| 评审补充 | 首次形成能力 | 最终验证 |
| --- | --- | --- |
| 异常执行与恢复 | P4-08、P4-09 | P4-10、P6-05 |
| 不可行诊断 | P2-05、P2-12 | P6-04、P6-05 |
| 时长不确定性 | P0 定义来源，P2 使用名义策略，P6-03 增可选缓冲 | P6-03、P6-06 |
| 知识快照与索引 | P1-06–P1-08 | P5-08、P6-05、P6-07 |
| 支配剪枝与模型规模 | P2-05、P2-08、P3-06 | P6-02、P6-04 |
| Greedy 具体算法 | P2-07 | P3-07、P6-04 |
| 大模型抽取重放 | P0-05、P1-02–P1-03 | P1-07、P1-09 |
| 属性测试与故障注入 | P2、P4 对应模块测试 | P6-04、P6-05 |

### 6.1 外部条件的具体归属

| 条件 | 取得方式和临时处理 | 不能越过的验收边界 |
| --- | --- | --- |
| 缺失工艺参数、设备冲突 | P0/P1 逐条建立问题，使用明确人工审核输入 | 不发布未经审核路径；P1 full 不通过 |
| 实际 LLM 供应商或凭据 | Provider 端口及固定返回测试可先做；live 调用需有效配置 | 不把固定响应回放说成真实模型服务验证 |
| U01–U04 官方会话与状态语义 | 9月30日答疑建议task_id、明确初排后单菜追加；传递细节、执行推进、动态响应及时间原点以显式开发profile和未决项登记 | P6-08 未验证前不宣称比赛动态兼容 |
| U05–U13 指标、名称、时间和设备解释 | 保留配置策略、完整内部数据与证据记录 | 必须说明采用口径及其验证范围，不能静默修改规则 |
| 实际部署资源与公网入口 | 先完成可审阅部署包，目标环境明确后验证 | 不虚构公网可用，也不自动正式提交比赛 |
| 性能未达到目标 | 用固定报告定位编译、模型、求解和序列化问题 | 不用删除硬约束或改称平均耗时达标结束任务 |

<a id="handoff-template"></a>
## 7. 任务交付摘要与文档维护

每次交付至少报告：

~~~text
任务：实际完成的任务 ID
结果：实现了哪些行为及本次边界
输入版本：知识、规则、策略、Schema 和代码身份
文件：实际新增/修改路径
验证：实际命令、测试数量、结果与报告引用
限制：尚未满足的外部条件、失败或跳过内容
下一步：在当前授权下已经具备前置条件的任务
~~~

任务实现中发现新边界时，优先补充所属任务的测试与说明。若拆分任务，使用保留原 ID 的子编号并在本节记录数量变化；若调整公共接口，同步当前实现、直接消费者、AGENTS.md 中相关边界和此处接口表。不要创建互相矛盾的第二套任务清单。

AGENTS.md 只在长期代码约束或边界变化时更新；task.md 记录进度和具体实现约定；技术架构文档记录领域语义和整体方案。三者承担不同职责，修订必须能追溯到用户要求、官方依据或具体实现证据。

### 7.1 修订记录

| 版本 | 日期 | 内容 |
| --- | --- | --- |
| 1.0 | 2026-09-22 | 根据架构 v1.1 建立 7 阶段、61 任务；明确 P1 core/full、公共契约、每任务验收和最终外部门槛；全部任务初始未执行 |
| 1.1 | 2026-09-22 | 根据用户要求及架构 v1.2 固定唯一单人人工资源；补充配置拒绝、跨菜人工互斥及被动设备并行验收，仍为 7 阶段 61 任务 |
| 1.2 | 2026-09-22 | 同步架构 v1.3 的全设备竞争、物理资源映射和严格跨菜蒸烤合批；补充 Compiler、Greedy、CP-SAT、Validator 与重排验收，仍为 7 阶段 61 任务，均未执行 |
| 1.5 | 2026-10-02 | 依据9月30日答疑准备P5：任务关联与单菜追加、执行事实来源、动态响应/时间冲突和开发profile；增加固定输入、38个设计场景及实际准备检查，八项P5功能尚未开始。按用户指示改用本机Node25.8.1/npm11.11.0，保留P4实际开发验收及原正式入口 |
| 1.6 | 2026-10-03 | 登记P5八任务DEVELOPMENT_VERIFIED及814次完整开发验收、17项前端单元和3条真实浏览器；记录第七轮当前源码性能、单次真实DeepSeek澄清测试及已观察的幂等503恢复。保留正式门槛、官方未决项与历史失败证据 |

当前需要补齐 P0-06 对已整理工艺的真实人工审核输入。P0 尚未通过，不进入 P1。待审入口为 [sample_review.md](../data/issues/sample_review.md)，审核齐全后重跑 P0 阶段验收。

### 7.2 P0 本次交付记录（2026-09-23）

- uv sync --locked 成功；P0 完整验收：122 项测试中 121 通过、1 失败、0 跳过，阶段退出码 1。唯一失败为 12 道真实样本缺少人工审核输入；P0-07 自身测试通过但前置未通过。
- 实际产物：app/domain/、app/pipeline/issues.py、tests/、scripts/、data/schemas/、data/issues/、工程配置与 README；逐文件指纹见阶段报告。首次依赖安装后已精确锁定，普通验收不升级。
- 原文样本为 REAL_SOURCE，独立合成例子为 SYNTHETIC；官方例子为 OFFICIAL_EXAMPLE，烘烤与装盘存在先后疑点，不作物理可行性依据。
- 实测环境为 Windows、Python 3.12.14、真实 OR-Tools 和 SQLite 内存库；无 Neo4j/LLM 实时调用。Linux 部署未运行，未建立业务 API、排程器、前端或运行数据库。
- docs/task.md 为唯一进度来源；根目录同名副本改为导航。修复本文原先不存在的架构链接及 P0 入口架构版本笔误。
- 原始 CSV、设备 JSON、Word、聊天图片不改写；项目 ZIP 未读取或解压。用户补回官方接口规范后已据其实现测试。
- uv 默认缓存与临时文件存在跨盘重命名问题，因此使用项目内 .uv-cache；Ruff 只处理源码和配置，不改写原始 UTF-16 的 docs/AGENTS.md。
- 当前外部缺口与下一步：按照 [逐工序审核工作单](../data/issues/p0_review_packet.md) 核对214个操作的AI时间、热参数、物料及依赖，保留真实审核者、依据、日期和工艺内容哈希；批准后再验收 P0-06、P0-07 及整个阶段。

| 版本 | 日期 | 本次修订 |
| --- | --- | --- |
| 1.3 | 2026-09-23 | 登记 P0 实现和实测结果，5 项 VERIFIED；人工审核缺失，阶段尚未通过，仍为 61 项任务 |


### 2026-09-23 数据修订与本轮交接

- 用户指定的完善版CSV和《数据问题.docx》保持只读；新增[调度版CSV](../data/revisions/recipes_v3/recipes_100_调度版.csv)、[结构化数据](../data/revisions/recipes_v3/scheduling_dataset.json)和[逐菜修订对照](../data/revisions/recipes_v3/修订对照.md)，活动开发版本由 data/active_recipe_dataset.json 固定。
- 100个原始ID及菜名未变，补全数值时间、前置加工、实际食物状态、物料份额、热参数、分批/介入与设备卸料边界；微波/240℃/上下火冲突有AI替代并保留原文。所有AI内容保持MODEL_SUGGESTION/NEEDS_REVIEW。
- 100单菜、20覆盖菜单、5边界菜单和20随机菜单共145/145通过；人工、设备、DAG、物料和介入独立核验通过，报告绑定当前JSON哈希。本结果仅为固定原配方开发路径的可行性，不标记P1或P2验收完成。
- P0完整验收122项：121通过、1失败、0跳过；Ruff检查、格式和mypy均通过。唯一失败是12道菜没有真实人工审核记录；P0-07自身21项接口测试通过但依赖未满足。
- 可读审核材料：[样本清单](../data/issues/sample_review.md)、[逐工序工作单](../data/issues/p0_review_packet.md)。没有将AI生成工艺自动批准，也没有修改人工审核门槛。


**2026-09-29执行续记：** Docker恢复且本地Neo4j容器运行；P3-04已接通真实求解、独立校验、Greedy及指标。Ruff检查与格式129文件通过，mypy app128文件通过。旧准备版本与P2运行指针保留；共同热边界尚须以新不可变开发版本归档。详见P3-04；P3目标继续active，不以专项通过替代P3-05～07及整阶段验收。


<a id="window-guard-fix"></a>
### 65 条动态窗口失败局部修复（2026-10-06）

用户授权按原因、可挽救反例、原成功保持、可信不可行判断、硬约束与质量/性能六项完成修复；明确不运行全量实验。范围为历史三菜单×10 轨迹×原四组 120 条；不是 65 个独立缺陷，也不是正式全部比赛验收。

**实际诊断与修复：** 原 65 条失败逐条保留具体任务、允许窗口、真实反馈、截止时刻人工占用与此前事件。全部首个失败涉及零间隔人工争用，失败现场窗口关闭不能证明整条轨迹不可挽救。仅保护下一人工介入的中间版本完成 113/120，残留 7 条涉及完整连续链的设备需求或风险策略在窗口打开后错误阻断接续；该中间报告与失败保留。最终可选 TIGHT_HUMAN_V1 在开工前保护完整有限间隔链、承载体及连续占用，补齐链内依赖桥接路径；外部前序完成、后续资源释放确认后再开工，已开启链继续接受原实际窗口和资源校验。保护只选择派发，不创建虚假占用、修改时长或覆盖执行事实。

**同预算实际配对：** timeSave 修复后先归档 286 份源码/配置，再重建 56 成功/64 失败的新基线。最终对照实际加载已归档旧模拟器，与修复版使用同硬件、知识、固定初排几何、种子及扰动分布，统一重排 3000ms、Solver 2100ms，初排 4200ms；原四组单次观察触发规则不改。历史报告额外三组持续反馈的描述元数据不属于本轮四组，分布、种子、触发阈值和事件上限逐项相同。

| 分组 | 当前基线成功/失败 | 开启保护后成功/失败 | 失败转成功 | 成功转失败 |
| --- | --- | --- | --- | --- |
| NOMINAL_SHIFT | 14/16 | 30/0 | 16 | 0 |
| NOMINAL_REPLAN | 15/15 | 30/0 | 15 | 0 |
| BUFFERED_SHIFT | 13/17 | 30/0 | 17 | 0 |
| BUFFERED_REPLAN | 14/16 | 30/0 | 16 | 0 |
| 合计 | 56/64 | 120/0 | 64 | 0 |

历史同身份 65 条失败全部有本版合法完成见证；原 55 条成功全部保持，没有新增失败。本轮新基线的净恢复是 64 条，不能把基线已恢复的一条也归因于保护策略。120 条均在归档时独立终态校验，随后另行重新编译真实终态、由 Validator 重扫，核对原失败依赖的实际间隔及固定热时长。全部可挽救，无需作“不可行”结论；未把 TIMEOUT/UNKNOWN 当作不可行。

**质量代价：** 以下为修复前后都完成的 56 条逐条差值中位数，不能把失败轨迹的未定义完成时间混入。完整逐条结果、最小/最大值及修复后全体 120 条分布见配对审计。

| 分组 | 配对数 | 总用时增加（秒） | 出菜差变化（秒） | 人工实际工作量变化（秒） | 最长连续工作块变化（秒） |
| --- | --- | --- | --- | --- | --- |
| NOMINAL_SHIFT | 14 | +2883 | +315.5 | 0 | -39 |
| NOMINAL_REPLAN | 15 | +2582 | -60 | 0 | -42 |
| BUFFERED_SHIFT | 13 | +684 | -60 | 0 | -42 |
| BUFFERED_REPLAN | 14 | +1114.5 | +344 | 0 | -3 |

所有原成功轨迹总用时都有增加（最少 462 秒），人工实际工作量逐条不变；出菜差和最长连续工作块并非逐条改善。修复后仅 34/120 条出菜差小于 300 秒，项目 240 秒及比赛优秀指标未全面达标。因此这是可选执行可靠性修复，不能宣称整体排程质量提升或比赛优秀验收通过。工作区原策略默认 NONE、JSON 身份保持；新增可读策略 data/policies/p6-window-guard-v1.json 为 OPTIMIZE、4200/3000ms，可通过 SMART_COOKING_POLICY_PATH 为新会话选用，已有会话不自动切换。

**实际验证：** 代表性反例第一轮 3 失败/1 通过，下一轮残留反例 4 失败；最终 8 项反例通过。真实 SQLite、真实 Compiler/CP-SAT/发布、偏移触发重排、幂等、重启和私有未来隔离另有 4 项通过。统一相关回归实际 286 通过/1 失败/2 项外部门槛明确排除，唯一失败是旧历史报告被错误绑定当前源码；改为绑定真实归档版本并增加当前 120 条证据检查后该模块 2 项通过。按相同生产源码及其余未变测试哈希汇总，当前相关回归 288 项通过，原失败 XML 保留；两个既有正式审核/官方动态门槛不算本轮通过。Ruff 与格式最终 497 文件通过，严格 mypy app 259 文件通过。

19 次实际观察重排全部成功，包含请求准备的耗时中位数 2310ms、最大 2547ms，均在 3000ms 内。三轮实际 HTTP 原三个菜单、保护开/关同预算配对 36 次请求，零失败/超预算；开启保护重排服务 P95/最大 2641ms，对照为 2672ms。HTTP 验证真实存储、独立计划扫描、五字段投影及 timeSave；这些是局部样本性能，不能替代全量门槛。

**Windows 交付：** v1 候选包虽通过真实离线安装和恢复，但交付核对发现打包器 AppSettings 默认构造未读取策略环境变量，实际仍为 NONE；原包、验证与失败核对记录均保留。新增显式 --policy 参数后生成 data/delivery/P6-Windows-window-guard-v2.zip，manifest 绑定实际 p6-window-guard-v1/TIGHT_HUMAN_V1 与 3000ms。v2 已在独立 Windows 目录实际离线安装，通过真实初排/追加、前端访问及两次启停后的 SQLite 恢复；测试 1 通过/0 失败/0 跳过，75 秒量级，知识维护服务实际停止后恢复原状态。交付核对额外确认包内策略、3000ms 与全部 app 文件哈希，第一份默认策略包不作为可选保护最终交付。

**证据位置：** [最终交付清单](../deploy/window_guard_fix_manifest_run1.json)、[逐项验收汇总](../benchmarks/reports/verification/P6-dynamic-window-fix-run1/verification-summary.json)、[逐条原因诊断](../benchmarks/reports/verification/P6-dynamic-window-fix-run1/historical-diagnosis.json)、[逐条配对与独立终态审计](../benchmarks/reports/verification/P6-dynamic-window-fix-run1/paired-terminal-audit.json)、[同版本驱动的旧模拟器对照](../benchmarks/reports/P6-dynamic-window-before-run3/report.json)、[开启保护实验](../benchmarks/reports/P6-dynamic-window-after-run2/report.json)、[实际 HTTP](../benchmarks/reports/P6-dynamic-window-http-run1/report.json)。最终 289 份源码/配置另外冷归档，原报告、资料、策略与旧 ZIP 未覆盖。完整 P6、正式比赛外部条件及用户省略的全量实验仍未验收。

### 用户请求的性能、约束、交互与解质量核查（2026-10-07）

依据用户所附验收表和补充的“多菜出锅差不超过5分钟为优秀”执行核查。本次不改阶段VERIFIED状态。

- 当前P4 development默认OPTIMIZE，固定9场景（8种初始菜单）三轮39次真实本机HTTP：27初排、12重排全部发布并通过独立约束/响应扫描。客户端P50/P95/max初排2328.6/2636.1/2934.2ms，重排1699.8/2121.5/2121.5ms；冷启动约10秒另记。
- 出菜差<=300秒：初排12/27、重排3/12；人工总量、连续忙碌和稳定性优化完成标记均0/39。四个原初排问题的12次离线对照均OPTIMAL且独立合法，均有五分钟出菜见证；三个原总时长不增，温州鱼饼等四菜由114分钟/22分钟差改进为93分钟/5分钟差。连续忙碌可变差，不称全指标改善。
- 专项213通过/0跳过，124秒；首轮70通过/143失败全部为旧P2同一manifest读取权限问题，后用现有身份及内容哈希校验一致的.tools/p2-regression/releases副本重跑，原失败保留。专项包含旧P2的100单菜路径，不冒充当前P4全菜单重跑。
- 实际 npm --prefix web run test:e2e 三场景断言通过，44.5秒；后台SQLite写锁导致SSE已开始后异常，不能算完整交互通过。独立API三次首帧后持锁均提前EOF，日志确认；解锁后新连接提醒查询200，执行事实未变。原503和复现工具中止证据保留。生产通知尚未修复。
- 本次仅新增核查脚本、报告和登记，app及基准源码哈希一致。正式工艺、新菜抽取准确率、公网和官方动态联调继续单列。

证据：[逐项核查](2026-10-07-性能约束与解质量核查.md)、[HTTP](../benchmarks/reports/2026-10-07-user-audit-http-run2/report.json)、[质量对照](../benchmarks/reports/2026-10-07-user-audit-quality/report.json)、[汇总](../benchmarks/reports/2026-10-07-user-audit-quality/summary.json)、[实际命令](../benchmarks/reports/2026-10-07-user-audit-quality/commands.json)、[专项XML](../benchmarks/reports/2026-10-07-user-audit-regression-run2.xml)、[提醒锁复现](../benchmarks/reports/2026-10-07-user-audit-quality/notification-lock-run3/report.json)。

### 用户请求的提醒流与排程质量修复（2026-10-07，实施中）

授权范围：修复已开始的 SSE 在 SQLite 写锁期间中断；改善多菜五分钟内出锅及总人工用时。用户已明确选择“五分钟出菜优先，其次总人工及总流程，报告必要代价”；追加要求初排五秒为优秀、最迟七秒完成且尽量稳定。切换前 p6-quality-v1 重排采用 2026-10-06 已授权的 3000ms 上限；2026-10-07 用户明确回复“现在可以切换了”，授权启用已验证的五秒重排方案，新默认另版本为 p6-quality-v2。原 P4 的 2400ms、v1 的3000ms策略与全部实验保留；已有会话绑定的旧策略不自动替换。

实施与验收依据：响应开始后的真实 TCP 写锁回归必须持续心跳、解锁补发、游标重放去重且执行事实不变；排程保持全部资源、物料、工艺及冻结事实约束，经独立 Validator 和五字段投影扫描。固定原反例及重排三轮实际 HTTP；同时记录出菜差、总人工、总流程、连续忙碌和硬时限，不以总人工必然下降作为无共享机会菜单的要求。修复前 294 份源码/策略已冷归档于 benchmarks/reports/2026-10-07-quality-fix/before-source，哈希清单独立保存。

当前已实现响应开始后的 SQLite BUSY/LOCKED 恢复、精确质量目标、基础模型克隆缓存、确认身份的 JSON 传输、合法逐道参考及未执行旧工序的软提示。真实加菜反例的建模剖析发现诊断目录逐条扫描开销，改为目录序号索引并保持重复诊断 ID 的原筛选语义；反例先 RED 后 GREEN，相关 32 项通过。expanded-v15 的 153 次全部正确且硬预算达标，初排 93/93 五分钟、客户端最长 3101.8ms；重排仅 58/60 五分钟，因此没有接受为最终通过。按单次而非累计校验开销预留，并将同一 3000ms 内的构造/求解/发布分配为 400/1650/900ms 后，targeted-v16 两个加菜反例三轮共 12 次全部正确、时限及五分钟达标；最长初排 3811.4ms、重排 2847.8ms。相关 24 项回归通过，完整相关回归与最后完整 HTTP 正在验收中。

完整 expanded-v17 的 153 次实际 HTTP 全部合法发布且满足各自 7000/3000ms 硬预算；初排 93/93 五分钟出菜，客户端 P50/P95/最大 1224.0/2953.6/3659.0ms，全部五秒内。重排仅 59/60 五分钟，客户端 P50/P95/最大 1735.5/2742.3/2876.2ms；首轮 replan-016 的出菜差 32820 秒、质量求解 UNKNOWN，因此报告为 TARGETS_NOT_MET。严格证据门槛 test_quality_acceptance_evidence 在此报告上 RED，未降低五分钟标准。benchmark 后续加入独立 --policy-path，应用源码未变，但生产者源码指纹已变；v17 不冒充当前全部源码证据。

提醒相关 final-sse.xml 共 11 项通过（含 3/7 秒心跳参数的真实 TCP 写锁、响应头前 503、解锁补发、游标去重及执行事实不变）。完整相关回归 final-regression.xml 是 635 通过、3 失败、2 排除，不能称为全绿：失败分别为历史报告错误绑定当前源码、Windows 测试服务启动探测遇到半就绪端口、未标注的离线测试遇到正在运行的 Neo4j。历史报告保留原件，从项目生成的交付包恢复 253 份完全匹配原 SHA 的源码并加强归档核对；启动探测限定处理 RemoteProtocolError；单独停止唯一测试 Neo4j 容器、真实离线端到端 1 项通过后 finally 恢复。补测 final-browser-and-evidence.xml 6 项通过，含历史证据 5 项及实际 3 条 Playwright 浏览器案例。未修改原 ZIP、旧计时或发布知识。

真实共享人工对照 real-shared-human-final.json：同一三菜菜单共享开/关均为总人工 4170 秒、总流程 48240 秒、出菜差 120 秒、最长连续人工 1230 秒；该发布知识的共享费用等于单独费用之和，不能宣称真实人工下降。合成可枚举反例 240→180 秒另行证明目标能正确选择省人工方案。

重排是否允许从 3 秒提高到 5 秒已提出偏好问题，尚无回复；默认仍为 p6-quality-v1/3000ms。--policy-path 隔离测试不激活默认，候选报告完整记录实际策略。candidate-5s-targeted-v18 的 12 请求有 1 次事务回滚超时，不接受；另版本 v3 候选增大发布预留继续实验。初排 7000ms 及五秒优秀标准不变。

候选 v3（重排 5000/400/2800/1400ms）已完成 targeted-v19 12 次全达标，以及 candidate-5s-expanded-v20 完整 153 次全正确、时限和五分钟达标，运行期间源码不变。初排 93 次客户端 P50/P95/最大 1170.9/2297.8/4502.2ms，全部五秒内；重排 60 次 1520.1/4123.1/4466.3ms，其中 12 次超过原 3 秒，不能算作默认 3 秒通过。最终严格双策略检查 final-default-and-candidate-evidence.xml 为 6 通过、1 失败：候选当前源码/策略/全部153原始计划的门槛通过，历史证据5项通过，默认v17的质量门槛如实失败。未跳过、xfail或放宽默认质量要求。Ruff检查及格式505文件、mypy应用259文件均实际通过；格式检查在只读提权后排除了沙箱对测试生成目录的访问限制。默认仍3秒，等待用户预算偏好，不标记整个请求完成。

paired-candidate-v20.json 以原39请求配对：总人工39次相同；总流程18次缩短、14次相同、7次增加（最大239秒）；最长连续人工9次减少、9次相同、21次增加（最大3990秒）。连续工作的增加作为集中出菜代价明确报告；不同预算及参考构造不用于严格因果归因。所有失败及中断材料仍保留。

最终源码/默认与候选策略/测试/文档冷归档为 final-current-source-v20，final-evidence-v20.json 记录 SHA、最终实际命令及原始 XML 统计；总状态仍为默认重排质量未达全部目标、等待用户预算选择。下次得到预算偏好后，再决定默认策略和相应真实 HTTP 验收，不以时间经过作为授权。

所有先前未达标、中断和失败证据保留，包括 expanded-final（文件名不代表通过）、expanded-v7 的中断说明以及 v11—v15 的偶发质量失败。不得删除失败分母或把局部通过当全量成功。验收说明：[提醒与排程质量修复验收](2026-10-07-提醒与排程质量修复验收.md)。正式外部门槛、真实厨房和此前未授权的全量实验仍未验收。

#### 2026-10-07 用户批准启用五秒重排（已切换，完整性能复测未通过）

发布 data/policies/p6-quality-v2.json，与通过 v20 的候选策略相比仅 policy_version 不同；初排 7000/300/5000/600ms，重排 5000/400/2800/1400ms。app/config.py 默认路径改为该版本；benchmark 无覆盖时也读取 AppSettings 默认，避免还固定旧 v1。当前 shell/.env 未设置 SMART_COOKING_POLICY_PATH 覆盖。未修改原运行库、旧会话或任何历史报告。

新增真实服务策略切换与重启恢复测试，切换前 policy-activation-red.xml 因实际默认仍旧策略而失败；修正一次测试调用名称后 policy-activation-green2.xml 1 项通过。真实 API 创建的新任务保存 v2/5000ms，重启后的旧会话仍绑定 v1/3000ms、新会话仍为 v2。静态检查 Ruff及格式506文件、mypy应用259文件通过。原候选完整证据门槛改为绑定其523文件冷归档及原报告 SHA；新的默认门槛仍要求当前生产者源码、v2实际策略、全部153请求、七/五秒预算与全部五分钟达标，不能拿旧候选报告替代。

已实际开始 after-enabled-v21 的完整31场景三轮153次真实 HTTP，不传 --policy-path，检查新默认配置路径；本次运行期间不修改 app/benchmarks 生产者源码或并行运行其他性能测试。完成前不宣称新版本完整验收通过。

after-enabled-v21 实际执行148/153后中断，状态ABORTED，未执行5请求保留：初排91次中90次合法/五分钟，完整HTTP90次在7秒内，最大7008.8681ms；重排57次中56次合法、54次五分钟，全部57次在5秒内，最大4760.6049ms。末轮初排发布阶段4266ms、编译最高2593ms，后续工作进程超时及未及时恢复；当时没有同步系统负载采样，根因未确认，不归因于外部压力或策略版本。7秒超限仍算失败，未放宽硬期限。

enabled-quality-evidence-v21.xml 是6通过/1失败，严格新默认门槛因完整报告ABORTED拒绝；候选绑定其原冷归档源码与原报告SHA、旧历史报告继续通过。用户允许初排7秒，5秒只作为优秀等级，门槛据此区分；未隐藏新默认失败或修改候选原始更严格的5秒优秀证据。新会话与重启策略切换测试已通过，但完整性能稳定性尚未通过，因此维持本修复条目实施中。默认配置按用户授权保持v2/5000ms。

切换后的291份生产者源码及相关策略/测试/文档冷归档 final-enabled-source-v21，final-enabled-evidence-v21.json 保存实际启用及未通过指标；旧v20索引、候选、3秒失败记录和本次中断材料均不覆盖。

### 用户请求打包运行所需代码与文档（2026-10-07，已打包并通过独立运行验证）

按用户当前明确请求构建新的 Windows 离线运行包，保留原始资料和全部历史交付包。内容为后端源码、已构建前端、固定知识发布、当前 p6-quality-v2 策略、锁定 CPython 和离线 wheel、PowerShell 安装/启停脚本及部署/接口/技术/验收文档。不复制开发虚拟环境、node_modules、真实 .env、运行 SQLite、模型调用日志或历史大批量证据。更新打包器使 docs/*.md 与工程约束入口纳入文件哈希清单；部署说明如实记录新默认和性能复测未通过，不以打包作为性能验收通过。后续交付物身份、离线安装/HTTP/重启恢复的实际结果另行登记，不变更原阶段状态。

新交付物为 [SmartCooking-Windows-20261007.zip](../data/delivery/SmartCooking-Windows-20261007.zip)，95985779 字节；SHA256 为 `863c5edc70ac4b57897d39d15d84462c6e4a53092ca3e38f6cbe18ac83c30a8d`。包内3132个受清单校验的文件，包含259份后端源码、13份Markdown说明、32个锁定wheel和CPython 3.12.14；包内策略为v2/初排7000ms/重排5000ms。ZIP全部成员哈希、CRC、路径边界、知识/策略身份与当前后端代码逐文件核对通过，见 [交付检查报告](../data/verification/Runtime-Windows-20261007-archive-check.json)。

实际运行 `uv export --offline --locked --no-dev` 和 `npm --prefix web run build` 均通过。打包边界测试首次因系统Temp目录读取权限出现6项setup error，保留 [原XML](../data/verification/Runtime-delivery-20261007-packaging.xml)；改用新的项目内basetemp后6项通过，见 [复测XML](../data/verification/Runtime-delivery-20261007-packaging-run2.xml)。首次打包因沙箱读取P4 manifest被拒，未生成ZIP；以只读知识访问权限执行新目录打包后成功，不覆盖失败目录或旧包。Ruff检查和格式实际通过。

以本新ZIP执行真实Windows独立目录离线安装、隐藏API启动、健康检查、初排、加菜重排、幂等重放及停止/重新启动恢复，1项完整集成测试通过，59.15秒，见 [部署XML](../data/verification/Runtime-Windows-20261007-smoke-run1.xml)；[安装命令与包身份记录](../data/verification/Runtime-Windows-20261007-smoke-run1/test_windows_offline_package_r0/windows-deployment-evidence.json)。执行期间观测Neo4j Bolt端口关闭；未启动Docker/Neo4j或调用模型。测试进程已停止，用户原运行库未修改。包内task.md冻结于打包时的“验证中”状态，本段完成登记在原项目；验证后没有修改已测试的ZIP或清单。交付验证摘要：[SmartCooking-Windows-20261007.verification.json](../data/delivery/SmartCooking-Windows-20261007.verification.json)。

本次只完成用户请求的运行物打包和交付验证。after-enabled-v21的性能/质量门槛仍未通过，不变更提醒与质量修复的实施中状态，也不宣称正式参赛、公网或全量性能验收通过。

### 用户反馈系统 PowerShell 无法解析安装脚本（2026-10-07，已修复并通过两版本独立安装验证）

用户在D:/smart_chu_test执行windows/Install.ps1，出现UnexpectedToken、TerminatorExpectedAtEndOfString、MissingEndCurlyBrace，伴随中文乱码。检查发现交付包四个ps1均为不带BOM的UTF-8；原部署测试优先选择pwsh，遗漏系统Windows PowerShell 5.1兼容性。真实5.1.26100.9444只读ParseFile回归在四脚本上均复现同样错误，见Windows-PS51-encoding-red2.xml。首次探针自身多了一个加号的失败也保留于Windows-PS51-encoding-red.xml，不冒充有效根因复现。

最小修复为四个源部署脚本添加UTF-8 BOM，保留原正文和换行。新增真实系统5.1解析回归；部署集成默认改为系统powershell，允许SMART_COOKING_DELIVERY_SHELL显式选择pwsh，并在报告记录真实shell版本。构建新SmartCooking-Windows-20261007-PS51-fix包及新哈希清单，旧包、原证据不覆盖。只修部署兼容性，不修改调度源码、默认策略、知识发布或用户测试目录；运行性能验收状态仍未通过。

复验：Windows-PS51-encoding-green.xml为4项通过；Windows-PS51-packaging.xml为6项通过；Windows-PS51-smoke-run1.xml为1项通过（77.21秒，系统5.1.26100.9444），Windows-PS7-smoke-run1.xml为1项通过（63.00秒，pwsh7.6.6）。两个版本均实际执行离线安装、启动、Verify、HTTP初排/加菜重排、停止、重启、状态恢复和幂等重放；测试API身份检查与端口关闭确认均通过。Ruff检查与格式检查通过。未执行全项目测试或新的性能全量实验，不扩大这些通过结果的范围。

修订[完整包](../data/delivery/SmartCooking-Windows-20261007-PS51-fix.zip)95987685字节，SHA256 `adacd7c061f24c53fcb7882775a5f093f4f78906d6fe57f5cc9a32a1d9dec1fe`；3132个清单文件全部ZIP CRC/成员SHA及目录核验通过。与原包仅7个载荷文件不同：四个ps1仅增加BOM，README和两份文档更新；后端、前端、知识、策略、运行时及wheel完全相同。原ZIP SHA保持不变。见[归档核对](../data/verification/Windows-PS51-archive-check.json)及[最终交付验证摘要](../data/delivery/SmartCooking-Windows-20261007-PS51-fix.verification.json)。

另提供[覆盖补丁](../data/delivery/SmartCooking-Windows-20261007-PS51-fix-patch.zip)262908字节，SHA256 `8d83d2af7292c835dfbc9de9f85656156c27020ac431065827d31f2796918104`，含7个修改载荷及新release_manifest.json。新目录中实际解压原SmartCooking-Windows-20261007.zip再覆盖补丁，核对全部3132个文件身份与已测试完整修订包一致，见[补丁等价核对](../data/verification/Windows-PS51-patch-check.json)。补丁不包含state、environment或知识，可由用户覆盖旧解压目录的同名文件后执行原安装命令；本次没有改写D:/smart_chu_test。完成登记在原项目，未修改已经实际验证的ZIP和清单。

### 用户请求最慢菜单及 CP-SAT 最优性说明（2026-10-07，查询与小范围复测完成）

当前工作区benchmarks/reports目录缺失；普通和只读提权检查均确认旧v17/v20/v21报告不存在，不能复原完整原始报告或将历史重排最大值猜测绑定菜单。仅从本线程当时的工具控制台输出恢复两项初排身份：v20第二轮replan-013初排4502.1817ms，对应韩式泡菜鸦片鱼头/家常鲈鱼/烤鱿鱼干/烹香酷炒汇；v21第三轮replan-016初排7008.8681ms，对应轻松一锅蒸/糯米烧麦/蒜香烤茄子/美式薯条。后者控制台保留E_QUALITY=OPTIMAL、目标/界23130，CP531ms、PUBLICATION4266ms；不把完整HTTP超限归因于求解耗时。原重排2876.1962/4466.2769/4760.6049ms的场景身份未恢复，只保留为历史汇总。

按既有两场景执行三轮小范围真实HTTP，不改代码/策略/知识：`python -X utf8 -m benchmarks.quality_acceptance --output data/verification/2026-10-07-user-slow-menu-probe-run1 --case-id replan-013 --case-id replan-016 --require-five-minute`，退出0。12/12合法发布、独立Validator和五字段投影通过、全部五分钟且各自7000/5000ms预算内，运行前后源码哈希一致。初排6次最大2814.3101ms，为replan-013第一轮；重排6次最大3564.4412ms，为replan-016第二轮加双椒鳙鱼头。原场景013重排为推迟首菜至690秒，016重排为未开始状态加菜；初排走比赛接口，重排走内部events，不能将计时直接冒充比赛加菜接口的完整开销。

逐个gzip归档核对SHA及独立校验证明，将实际发布assignments与原Solver候选比对；12次均匹配CP_SAT/E_QUALITY/OPTIMAL且目标值与最优界相等。本次最慢初排目标/界7425、最慢重排23372。最优性仅属于该运行状态、候选模型、五分钟/总人工/总流程目标和已估计工艺，不等同真实厨房全局最优；本次所有枚举完整且无候选截断。仅两场景不外推为完整31场景稳定性通过，旧完整性能未通过结论保留。资料：[新原始报告](../data/verification/2026-10-07-user-slow-menu-probe-run1/report.json)、[逐发布最优性核对](../data/verification/2026-10-07-user-slow-menu-probe-run1/summary-and-optimality.json)，同目录initial-A.json/initial-B.json/add-fish.json可直接复制请求数组。用户原运行库、部署包和测试目录未改动。

### 用户请求验收完成度核对与甘特图改进（2026-10-07，专项已验证）

对应P5-06局部改进与验收差距核对，不改变阶段状态。按用户要求实现菜品实例固定配色、颜色图例、同名身份后缀、共同批次成员分色、状态边框/文字、冻结与历史淡色灰色虚线；提示卡限宽、自动换行、分段、滚动和鼠标移入。未变化的每秒轮询不重绘，缩放预设捕获选择时刻，拖动窗口在轮询/实际状态更新时保留。历史菜单仅用于配色，历史状态仍忽略当前执行事实；web/dist已重新构建。

实际验证：unit-red2.xml先复现3项失败（包括提示卡DOM被销毁），unit-zoom-red.xml复现非零预设下500—1200秒被轮询重置成1—1801秒，unit-history-red3.xml复现历史白色虚线与图例不一致，各自修复后最终unit-reviewed-final.xml为22通过/0失败/0跳过；typecheck、format:check、build退出0。真实Edge/API浏览器最终4条全流程通过，32.1秒，browser-reviewed-final/results.xml；后端冲突/依赖/独立枚举优化/冻结/真实TCP提醒恢复/抽取归档/投影专项71通过，57.76秒，acceptance-regression.xml。依赖弃用警告及初始环境/测试夹具失败记录保留。首次浏览器因沙箱读取知识manifest失败，随后只读知识访问下复测通过；测试服务使用独立临时数据库并已关闭。只读代码审查确认两处反馈均修复，无剩余阻断；基线原有缩放网格裁剪/同菜谱多实例泳道聚合记录为未修改的显示边界。

本次逐份核对已有两场景三轮12份压缩存档SHA，291份后端/评估源码与当前匹配，重新执行独立Validator及计划/串行参考指标：12/12合法；严格<300秒仅6/12优秀，另6份恰好300秒。存档初排6/6<5秒（最大2814.3101ms），重排4/6<3秒、6/6<8秒（最大3564.4412ms）；压缩率35.373%—45.880%，最长连续人工1500—6705秒。当前默认优化总人工而非连续人工。本次不重跑性能，两个场景不能替代完整稳定性；原新默认31场景完整验收未通过结论保留。

仍缺四项解析准确率标注对比、完整耗时/理论最优对照、严格五分钟和连续人工优化验证、65%/35%正式评分及专家评审；官方动态、公网、正式数据与真实厨房外部验收单列。生产修改仅前端与必要文档；调度、策略、知识、原资料、用户运行库及已有ZIP未修改。详见[验收完成度报告](2026-10-07-验收完成度与甘特图改进.md)、[本次证据](../data/verification/2026-10-07-gantt-acceptance/verification-summary.json)、[已有证据复核](../data/verification/2026-10-07-gantt-acceptance/existing-evidence-audit.json)。


### 用户授权甘特图、出锅目标与连续人工优化修复（2026-10-07，专项修复已验证）

用户已授权执行两份修改计划，并新增默认最小化最长连续人工时长；随后明确去除总人工优化。因此默认目标定为出锅差、最长连续人工、总流程，累计人工仅统计。计划入口：docs/superpowers/plans/2026-10-07-gantt-switch.md 与 2026-10-07-cook-finish-spread.md。

执行记录：当前目录无Git或CodeGraph；按原地修复处理，已将源码/测试/策略基线保存到data/verification/2026-10-07-cook-finish-repair/baseline-source.zip，不新建Git仓库、不修改原始材料。计划中旧“仅定位”状态由本次用户授权替代。域/编译/求解/校验接口按版本兼容推进；新字段默认值从旧序列化中排除，防止历史哈希变化。

前端任务：新测试先复现切换数组越界（gantt-red.xml），实现绘制数据快照、先更新后resize、系列稳定身份/替换、子图形替换及绘图区裁剪。24项单元通过（gantt-unit-green2.xml），类型检查和build通过。首轮全套测试50次循环超过测试5秒超时，调整该压力用例为20秒后通过，断言不变。真实Edge/API独立数据库1项31.4秒通过，含50次切换、实际轴/彩色方框数量、轮询和长文本悬浮卡（gantt-browser/results.xml），非仅检查下拉框。

Ruling：新增默认策略移除TOTAL_HUMAN_WORK；历史策略保留其原语义，统计字段保留。出锅与整菜全流程分开，新目标采用版本化审核锚点，不能直接改UI标题或机械取最后HEAT。已读取原P4发布并生成100菜审核副本，源snapshot身份16e326334a7888de6d9b0e1e78f3c5f10f6e35dded8cc32c4afdf4ec57ef49ea；后端目标及新知识尚在实施，不能宣称本轮目标完成。

后端阶段：新增独立出锅锚点及指标，原全流程字段保留；反例600/1800出锅、3600/3660装盘已通过。新策略 p6-cook-continuous-v1 默认 SPREAD/HUMAN_BUSY/MAKESPAN，COOKING_FINISH/300秒，累计人工仅统计。100菜审核写入 data/preparations/cook-finish-v1/review.json（DELEGATED_AGENT，非实测），新不可变发布 delegated-v3-cook-finish-v1-all，snapshot-861f8a7cdcb6bfd4c8f681232a488c96f4eeea1f25f18f1e40cb278d31d918d0；原菜谱工序、时长和既有版本未改。

Ruling：每菜出锅记录放入 ScheduleMetrics，和出锅差一次计算、一次独立扫描，而非候选顶层重复字段；API仍返回全部每菜时刻。新发布由已审核规范化来源离线重建，exporter标记snapshot-canonical-v1、校验source_kind=CANONICAL，graph_hash只表示投影内容身份，不宣称访问实时Neo4j。旧GRAPH默认字段从历史序列化排除，旧快照和会话哈希不变。

Ruling：原文取出与点缀合并的操作使用整段结束边界，不编造拆分时长。珍珠爆浆蛋糕的珍珠末段缺独立盛出操作，采用该热处理结束代理并在审核和运行说明明示精度。无原出锅时刻的库存替代若覆盖锚点，编译和独立校验拒绝；保留普通前处理库存复用。

Ruling：截图五菜测试最初错误地假定任何菜单都能≤300秒。逐项核对发现三个必需蒸制分支共用单蒸腔，时长5400/900/720秒且不等长不能合批，出锅差下界至少1620秒。因此修正该测试为真实超标、不伪造达标且优于串行，另有可达合成案例严格检查≤300。前两轮联合搜索无解回退串行的证据保留；改为先轻量目标、后连续人工，共享同一预算，增加完整合法计划的顺序hint（无新增硬约束）。five-dish-run4.xml通过，5343ms为进程内编译+排程观测，不冒充完整HTTP；continuous=2010秒，人工阶段取得合法候选，不宣称全局最优。

测试：backend-regression.xml为47通过（81.14秒），含新旧目标、100菜发布、快照往返、共批运行事实、已完成出锅冻结、库存防伪、取消全菜及策略重启恢复。runtime-red中H02取出端口实际同刻，原测试误判需两个不同结束值，已改为逐实例端口核对；库存反例先复现误接收，再由新契约拒绝。旧tests/compiler_support依赖的data/releases/development-v3-rebased-v2-all/manifest.json当前读取被拒，先前7项指标回归因此失败，不算通过，也不以P4替换它的快照身份。新增专项使用可读取真实P4派生发布及明确合成夹具。

最终检查追加：前端24项单元、4条真实Edge/API全部通过（browser-final/results.xml），包括50次组别切换、实际SVG坐标和方框数量、轮询悬浮卡、每菜出锅表、比赛接口、加菜故障恢复和模拟设备释放。web/dist已构建；TypeScript首次发现E2E新Set推断unknown[]，显式Set<string>后类型、Prettier及build全部通过。100/100真实单菜独立编译通过，最大141ms，仅为编译覆盖证据，不替代100菜完整调度验收；all100-compiled.json。用修改前真实压缩归档独立复核problem_hash/candidate_hash均一致，旧版cooking=None且WORKFLOW_FINISH；legacy-hash-check.json。Ruff和mypy应用263文件通过。

Final: Ruling：独立审查指出纯主动单菜插入休息可降低连续人工，但会超过其串行时间。本次保留架构13.2既有T_parallel<=T_serial硬约束，用户未要求放宽负timeSave；新增目标在既有可行范围内优化，并在运行说明明确该限制及达标后5%/120秒上限。代价是某些纯主动单菜不能改善连续人工，不宣称无约束全局最优。

Final: fixed 轻量阶段生成无用O(n²)人工链hint且缺截止检查 — 两个新增回归review-hint-red.xml先失败；仅实际建人工链的模型生成提示，并在遍历中检查共享截止。最终backend-reviewed-final.xml为50/50通过（89.35秒），含真实五菜进程内编译+排程5297ms、连续人工1950秒、人工阶段取得合法候选；不是完整HTTP性能保证。Ruff269文件格式检查、lint及mypy应用263文件通过。无需重新运行前端测试的语义变化，前端源码保持其24单元/4真实浏览器通过时一致。

Final: Ruling：审查代理受manifest读取权限限制，未独立重做100菜语义审查。采用主代理已完成的真实加载、审核记录匹配操作描述/菜谱哈希、独立知识校验、100单菜编译及针对性调度测试；不将其称为第二次独立人工实测。残余代价是原段时间估计及代理边界的工艺精度仍需厨房实测。长空档在连续人工优先和限时回退下不自动构成错误，不声称所有返回计划全局最优。

本轮用户授权的两项修复及默认连续人工/去除总人工目标已完成，最终证据索引data/verification/2026-10-07-cook-finish-repair/verification-summary.json。没有更改旧发布、用户运行库或已交付ZIP；旧阶段和完整性能/正式评审未通过状态不变。重新启动本项目后端、强制刷新浏览器并创建“新的一桌”使用新默认；具体命令与环境覆盖见docs/P5运行与接口说明.md。

## 2026-10-08：收尾长空档与蒸烤设备三层复用修复

授权：用户要求修复截图五菜中松子枣泥拉糕人工收尾延后、检查设备复用并修正“蒸烤箱”泳道；明确确认“两台独立设备，各三层，允许按各菜时长分层进出”，补充“蒸箱和烤箱不同层只有相同温度才能复用”。用户随后要求“不用测试，改好了告诉我”。新要求优先于原S09只允许等时长STRICT_TOGETHER的默认策略；原知识与历史会话保留，新版本单独发布。

定位证据：只读读取data/runtime/p5.sqlite3中截图对应会话session-f3ebd2d326989eec430574a4780d3863e30a0b9a1926b6ed423d0ee5837887f6，原点2026-10-08 10:21:48，计划保存于data/verification/2026-10-08-idle-tail/original.plan.json，问题保存为original.problem.json。枣泥糕16:08:48取出结束，40分钟冷却却在21:25:19开始，额外空等18991秒；原总流程42391秒。用户禁止继续测试之前，手动前移冷却与收尾的完整见证经独立Validator接受，总流程27721秒，出锅差10386秒和最长连续人工1530秒均不变；manual-witness.json及manual-validation.json。这说明存在可消除的求解松弛，不将该见证当作新分层实现的实际排程成绩。

实现：新增tail_compaction.py，发布前在同一共享截止时间内最多用100ms尝试提前出锅后独立的被动或人工收尾；保持原工序时长、出锅锚点、实际事实、连续人工及候选排序。改善候选进入原候选池重新独立校验，失败保留原完整候选；单独记录TAIL_COMPACTION耗时，不改原Solver目标值、最优界或声称全局最优。

分层契约：DeviceInstance新增capacity（旧默认1不写入旧序列化），ResourceUse新增实际layer_index（缺省不写入旧序列化）。CP-SAT按每个完整预约选取1至3层、同层NoOverlap；不同层同温同模式且全部非时长配置一致才允许同时在场。Greedy扫描容量并为完整预约分配空闲层，历史明确层位和资源阻塞参与分配；配置变化的原预约仍整腔独占。Compiler与独立Validator允许多层设备上的更严格UNARY预约，不把设备的共享能力强制改写为共享工艺。

执行与展示：实际开始、占用、阶段跨度、通知及模拟器采用已发布assignment.resource_uses保留层位；连续预约和冻结事实保持层位，未确认释放仍占层，释放只针对相应execution_id。Validator独立检查层位、容量、完整预约配置和未释放实际占用。甘特图将steam_oven_1显示为“蒸箱”、oven_1为“烤箱”，每台使用时显示三层；服务端计算兼容不同层的真实交集，前端以金色内框、“复用”及悬浮时间展示。整机故障等执行控件继续使用设备身份。

知识发布：实际运行 `.venv\Scripts\python.exe -m scripts.publish_layered_devices`，退出0，经业务发布必需的知识校验与规范化快照导出，生成不可变development发布delegated-v3-layered-devices-v1-all、knowledge_version=delegated-v3-layered-devices-v1、snapshot-c66756cf0e4713cb0cb07a220fb746efd7a1b581fa6a40d585763db21e76d5a4，manifest_hash=2b7cd7cd708bad5e4d84f7fc154151af0f53ea378385e197428bcd6ce7702b0b。蒸箱/烤箱各三层；129个设备预约中124个配置稳定可跨层复用，5个中途改变温度或模式保留UNARY。100菜、原时长/温度/模式/湿度均保留，96菜的改变语义重新绑定DELEGATED_AGENT审核哈希，4菜沿用真实旧审核。来源、逐预约判定和执行记录在data/preparations/layered-devices-v1/；旧发布未覆盖，未连接实时Neo4j。首次直接运行脚本因app导入路径失败，尚未开始发布；改用模块入口后完成。

默认与交付：AppSettings切换新发布及p6-cook-layered-v1.json；出锅差→最长连续人工→总流程及原预算、硬上限保留，新策略关闭可选STRICT_TOGETHER，采用各菜独立层位进出。已实际运行 `D:\env\npm.cmd --prefix web run build`，退出0，更新web/dist；未改用户运行库和既有计划。重启本项目后端、强制刷新并创建“新的一桌”使用新默认；配置覆盖需同步更新发布ID及策略路径。

验证边界：用户要求不测试后未再运行测试或浏览器检查，本次实现不标VERIFIED，不更新既有P6验收状态。此前收尾专项red.xml复现失败，green-first.xml为1通过；green-safety.xml为4通过、1失败，失败来自夹具错误地同时移动首道等待与第二菜人工，已修正夹具且遵照用户要求未重跑。此前局部mypy已通过；新分层调度、执行与界面未做整链回归，不宣称截图五菜已得到新的实测总时长。不可变知识的实际发布校验和前端生产构建是完成交付所需的业务操作。

交付整理：对本轮28个Python生产文件实际运行Ruff格式化，退出0（5文件调整、23文件保持）；未运行格式检查、整应用类型检查或新增测试。逐文件静态核对补充了Compiler对多层设备严格独占工艺的合法映射，并让Greedy层位分配同时考虑明确层位和整机资源阻塞，避免把未释放占用当作空层。

## 2026-10-08：倒排、执行时钟与安全重排目标专项完成

授权：用户新增三个目标——倒排提示加CP-SAT、依据执行时钟仅重排未来并等待在途工序结束、工序完成向前端及比赛接口发提示；本轮明确要求运行测试，替代前一轮“不用测试”。用户确认首次初排成功后自动计时，并强调“新任务的时间在插入之后；插入时间点之前的任务不能动”。设计入口为docs/superpowers/plans/2026-10-08-clock-replan.md。本节仅登记这三个目标的实现与专项验证，不改变既有P6完整性能、正式资料、官方动态协议和公网验收状态。

优化实现：先构造完整合法种子，以最晚出锅为目标后移完整设备预约及必要依赖，独立校验后作为CP-SAT提示；共享既有求解预算，未达300秒时优先继续改善出锅差，保留完整回退候选和当前种子的流程上限。发布前同时压紧出锅后收尾和预约内加热空档。审查发现预热至烘烤具有max_lag=0，单步后移会互相锁住，因此加热压紧先整体搬移入炉、预热、烘烤，再按最新时刻细化；不移动出锅锚点、不缩短原时长、不动冻结预约，最后由独立Validator再次扫描。

真实截图复核：只读归档的original.problem.json/original.plan.json，绑定既有分层发布，未访问运行库写接口。原出锅差2500秒、总流程11220秒；最终真实CP-SAT两轮分别为出锅差1380/1515秒、总流程9570/9630秒，蛋糕烤后至取出空档均为0，独立校验均通过。引擎观测5360/5437ms不含完整HTTP链，不宣称全局最优或厨房实测。对中间候选的1013秒烤后空等，完整前缀搬移后为0；协作截止检查设100ms，该单次辅助调用实际110ms，仍须服从引擎共享总截止，非独立新增求解预算。四道最终蒸制共享三层且取出结束释放，最短必需加热900秒，形成保守出锅差下界900秒；因此本截图菜单不能在现有工艺/完整预约规则下承诺300秒。可达合成小菜单使用真实CP-SAT通过≤300秒且保持完整加热与总流程上界。完整证据为optimizer/reviewed-comparison.json、reviewed-engine-1/2.json和whole-reservation-compaction.json，均在本节证据目录内。

执行实现：新增显式SCHEDULE_CLOCK来源，首次成功发布才创建持久时钟锚点；重排和重启不重置。后台仅根据已经流逝的时间提交开始/完成/物料事件，推算来源清楚标记，人工和设备可修正当前进度。加菜/重排先同步当前进度，暂停新的独立工序；保留已经开始工艺的有限间隔、固定程序和载体内必要衔接，计算其最早安全边界。边界前返回PENDING而不快进未来事实，边界后编译剩余问题。所有新安排不早于边界，新菜不早于插入；已完成记录和历史计划内容完全保留，在途操作的开始、阶段时段、资源层位保持不变。异常设备不按旧预计时间伪造完成。

通知及接口：OPERATION_COMPLETED提示与执行事件同事务写入持久通知流，含菜名、操作、执行身份、来源及完成时刻，重复事件与重启不重复提示或扣料。比赛JSON/SSE按task_id和游标读取，保留计划响应原五字段。新增比赛/replan和内部/sessions/{id}/replan命令入口，均支持幂等键；纯重排在服务端同步时钟后读取版本，修复自动推进导致页面旧版本误拒重排的问题。人工执行事实仍通过原/events端点使用明确CAS版本，冲突只刷新核对，不静默改写版本。前端显示当前工序、等待边界、完成横幅与跨计划通知；web/dist已重新构建，运行说明和README同步更新。

审查补修：单个窗口失效会话曾中断整轮后台扫描，现按会话和请求隔离，持久保存失败与暂停原因；保存前核对作业入口revision/plan，旧失败不得覆盖前台已发布的新版本。库存替代原先未进入时钟前置判断，现与状态机及派发保护共用有效COMMITTED/当前PLANNED满足记录，不虚构备料执行。后台无新增时钟偏移时避免领取无效写锁。只读复审确认修复路径；新故障与版本竞争均由独立SQLite用例复现后通过。

验证结果：backend-reviewed-final.xml为47通过，optimizer-reviewed-final.xml为35通过，clock-command-final.xml为8通过；后者含重跑项，按相同测试身份及旧未参数化比赛重排用例归并后共83项后端专项。包括真实手工鸡蛋豆腐蒸制中途插入牛排、连续两次加菜、历史计划字节比较、新工序全部≥插入/安全边界、零间隔必要衔接、同层释放、故障停止、时长修正、重启/重复tick、库存复用、通知/SSE恢复和新旧版本竞争。frontend-command-final.xml为33通过；browser-command-final/results.xml为5条真实Edge/API流程全部通过（约1.4分钟），覆盖比赛连续加菜、50次甘特图切换与悬浮/缩放、人工故障重排、自动时钟冻结/完成横幅、模拟设备释放。浏览器使用独立8012端口及.tmp数据库，只有测试应用提供时钟推进/会话清理端点，生产路由不含这些控制接口。

实际检查：Ruff应用和修改测试检查通过；应用及指定修改测试282文件格式检查通过；mypy应用276文件通过；前端TypeScript、Prettier及Vite生产构建均退出0。先失败证据保留：clock-review-red-fixture-corrected.xml复现窗口隔离/库存3失败，optimizer/whole-heat-red.xml复现零间隔链与真实CP热等待2失败，session-replan-red.xml复现内部纯重排入口缺失；随后对应专项通过。早期浏览器夹具仍用旧泳道坐标、跨用例时钟与空字段假设，已修正；最终仍观察到一次暂时数据库锁失败，按原请求身份重试成功，不宣称运行中永无暂时错误。

验证边界：backend-scoped-final.xml宽回归为246通过3失败，其中旧通知测试未明确MANUAL_CONFIRM已修正并在47项专项通过，另两项读取data/releases/development-v3-rebased-v2-all/manifest.json的PermissionError在提权只读运行下仍存在。更早宽单元组37通过6旧快照权限失败亦保留，不替换知识身份、不改文件ACL来伪造通过。review_gate与官方动态确认测试为既有外部门槛，不在本地功能目标中宣称通过。中间浏览器失败、权限失败及中间引擎结果均保留，最终结论以明确列出的报告为准。

证据目录：[2026-10-08-clock-replan](../data/verification/2026-10-08-clock-replan/verification-summary.json)。索引保存实际报告计数、SHA256、限制和当前应用源码指纹，可用目录内write_summary.py及optimizer/verify_current.py重建对应核对。未修改用户data/runtime/p5.sqlite3、原始资料、既有不可变知识或部署ZIP。使用当前源码须重启后端并强制刷新；新建一桌默认自动计时，旧手动/模拟会话保持原模式。

## 2026-10-08：设备预约长空档、前置准备与重排展示修复

授权：用户报告糯米烧麦灶眼274.5分钟、美式薯条烤箱359分钟及悬停覆盖菜色；要求修复并将腌制冷藏等长准备移出调度，明确只移出开工前能做的准备，加工后中途冷却保留。随后要求必要测试一轮通过即可，不重复通过项；新增报告点击重排后甘特图长期不更新。本节为当前专项，不改变既有完整P6/官方外部验收结论。

定位及修复：截图旧会话只读归档至data/verification/2026-10-08-reservation-spans/original.*。原炒制实际780秒却跨16470秒，薯条操作1890秒却跨21540秒，来自预约成员间排入长等待。压紧处理扩展到中间设备预约、内部紧密前缀与必要人工休息，优先最长空档，保留锚点、工艺时长、层位及冻结执行，服从既有总预算。原计划只读复核经独立Validator通过，烧麦16470→780秒、薯条21540→2970秒、轻松一锅蒸17880→3060秒，125ms；出锅差300秒、流程23310秒、连续人工1500秒均不变。该记录是旧计划重放，不是已更新用户运行桌。19项压紧相关检查通过（slack-reviewed.xml）。灶眼按物理设备和部件分别显示，避免两灶合一；ECharts悬停保留菜色和提示卡，显示专项后端7项通过。

准备规则：新默认p6-cook-prepared-v1保持原分层知识，42道菜的148道准备及必要前置/零间隔备料操作通过绑定菜谱哈希的明确规则声明开工前备好。原100菜工序和时长不改，准备清单保留原时长及USER_POLICY_ASSUMPTION来源；不伪造ExecutionRecord或普通合格余料。原材料份额与准备输出衔接，独立校验来源和守恒。蒸炒烘烤、中途冷却、烤箱发酵和出锅锚点不移除。比赛五字段保留，准备只进入无加工参数的文字步骤，实际时间线完整覆盖其余加工。新规则逐一编译及独立来源扫描、CP-SAT准备路径、旧问题hash、追加后任务≥2000秒、完成历史逐对象一致、重放和重启声明恢复均已通过；投影契约1项通过。审查发现准备菜完成后仍可撤销，已先复现再将有效准备任务计入完成覆盖，静态复审无阻断。

新增重排问题定位：只读当前17:37:15截图会话，重排请求位于38秒，等待边界2790秒，来源是90—390秒蒸箱预热及390—2790秒必要蒸制衔接；无计划失败，旧图仍显示v1符合此前在途安全约束。改进等待原因/时间/版本提示，保留到边界后自动更新；另修复父历史计划获取失败不应阻塞已获取的新计划。实际证据latest-replan-state.json，未写运行库。

专项验证完成：前端原35项单元通过，新增等待提示/父历史慢和失败的3项相关用例通过；首轮一项App加载超出5秒，改该测试超时为15秒后仅重跑此项通过，不削弱断言。最后真实Edge/API两条浏览器流程通过（browser-final/results.xml，约1.1分钟），包含原四菜实际预约不再数小时、准备清单和中途冷却、50次切换/缩放/悬停色、在途冻结及v2发布后自动更新。实际TypeScript、Prettier、Vite生产构建通过，Ruff应用279文件检查/格式通过，mypy应用279文件通过。准备相关9项及比赛投影1项均有通过结果，旧中间失败报告不算全绿；只修并重跑失败项。未改用户运行库、旧知识、部署ZIP或重启8000服务。

后续需求（上述浏览器轮结束时新增）：用户明确“重调度不能等时间到再触发，给出你的方案”。这取代此前等待安全边界后再求解的要求。拟采用立即求解未来计划、局部冻结在途操作和必要工艺承诺，新增任务从插入/发布生效时刻起可用空闲资源；等待提示及旧等待语义的浏览器通过不代表新行为已经实现。当前先提供方案供讨论，尚未改动立即重排语义。

同期新增：用户确认轻松一锅蒸需要同时使用蒸箱第1、3层，现单层显示有误。已定位ResourceUse仅单layer_index，CP层分配要求units=1，Greedy按(task,resource_id)保存单层，不能只补前端条形。拟为同一工艺声明固定层集合[1,3]，资源占用/冻结/派发/独立校验/展示共同支持；不将两份资源占用误当两次工序，不复制人工或加热时长。剩余第2层仍仅允许配置兼容的菜复用。此新增双层语义尚未实施，不包含在上述已通过专项中。

## 2026-10-08：立即重排与固定双层占用专项完成

用户已接受上述方案，原文“按照你的意见开始修改”。执行规格：重排/追加立即计算未来计划；冻结已完成及正在执行的时间、资源和必要工艺承诺；新动作不得早于插入/发布生效时刻。轻松一锅蒸按原文第11步固定使用蒸架第1层、蒸盘第3层，一份工艺同时占两层，第2层仅同温且配置兼容复用。保留单人人工、工艺时长、同温规则、提前准备语义和旧历史身份。

分工及接口核对：A负责运行/编排即时发布，B负责领域/求解/日历/执行/独立校验多层集合，C负责不可变菜谱派生发布与策略/root默认，root负责展示/通知、浏览器及文档；B产出ResourceUse.occupied_layer_indices，C绑定[1,3]/units=2，root仅在显示投影展开两个层条。A不修改B的资源竞争实现；B不修改A的计划发布与时钟。各任务自洽检查：A的测试要求在途完成前已发布版本，B要求一次工序且占两层，C只改有原文依据的资源绑定，root检查真实双条及新图立即更新，无全局等待断言。Ruling：本目录无Git或CodeGraph，延用当前原地工作空间、独立.tmp测试库及此进度账，避免重复创建仓库/索引；不修改用户运行库或旧不可变版本。

Ruling：原LOAD30秒仅绑定人工，按原文确为托盘放入设备，故将其加入同一完整蒸制预约并绑定两层，全部原时长不变；否则入盘与启动之间设备层位会错误显示空闲。固定双层保存一份ResourceUse/实际占用，整体申请和释放，避免复制执行/扣料/完成通知。

当前已通过前端38项单元（frontend-final.xml），修复前ui-red.xml已复现旧界面仍要求等待边界。新界面显示正在重排、在途继续，父历史异步读取继续不阻塞新图。后端及真实浏览器验证待整合后登记。

立即重排实现：请求进入即同步真实已流逝时钟，冻结已完成、在途完整资源跨度及所有有限间隔必要后继，不再设置全局replan_not_before_sec。计算采用剩余请求预算预留未来起点；提交时重新核对实际时钟、revision/旧计划/知识CAS，若未冻结新操作已落后提交时刻则拒绝旧结果并在同一预算内重算。没有快进事实、等到加热结束或重启执行时钟。119秒请求、120秒在途结束且后继最多间隔2秒的边界先复现失败，已将有限间隔必要后继纳入保留范围并通过。

固定双层实现：ResourceUse新增occupied_layer_indices，空集合不写入旧序列化；固定集合与旧layer_index互斥，units等于层数。CP、Greedy、完整预约、实际占用、冻结编译、释放与独立Validator共用集合语义；一份工序和执行身份，服务端显示投影展开第1、3层两条，通知仅一份并写“第1、3层”。同一任务两条不标为彼此复用，第2层只有兼容配置的其他菜真实重叠才标复用。

新不可变发布delegated-v3-multilayer-onepot-v1-all，snapshot-b7dea7f0cdd251693b8474d214fa8eb625474dfd78dbc9be0c35edb1329d8281，manifest_hash=441b0a29bdc69250590355947224b3f4f66fbf31e7a6d6b325d30e1f13a01214。仅一锅蒸1菜/1上下文的5个设备资源绑定改变，原操作、人工、时间、温度与其他99菜保留。新默认p6-cook-prepared-multilayer-v1保留42道菜148项提前准备，目标菜的新哈希同步绑定原规则。业务发布校验/导出/重载退出0；真实快照→Compiler→Greedy→Validator及单次双层通知集成1项通过（knowledge-integration.xml）。旧知识、运行库和现有桌的绑定未迁移。

主体专项结果：后端51项不同测试身份分别通过（即时重排22、层集合19、显示9、真实知识1）；前端38项通过。中间夹具/权限失败保留，权限失败仅按同一真实知识只读重跑，不替换知识身份；报告位于data/verification/2026-10-08-immediate-multilayer/。真实Edge/API三条首轮两条通过：比赛加菜立即v2和执行时钟在当前加热结束前重排v2；四菜甘特图回归失败于薯条7800秒占炉，归档failed-browser-plan.json，当前只处理并补跑这一项，未重复已通过浏览器项。mypy应用279文件、Ruff应用与本次测试、前端TypeScript/Prettier/Vite构建已通过；独立主体只读审查无阻断，新增压紧修复另行补审。

补充回归修复：固定双层改变真实四菜布局后，原压紧只移动预约成员无法腾出足够人工空档。新增有限辅助倒排：只选设备预约前可后移的备料链，优先较晚预约，整条原子移动成功后即回到最长空档主循环；辅助最多占剩余清理预算一半，主循环优先尝试内部紧链后缀，再压紧前缀。共享原有总截止和200ms辅助上限，不改变工艺时长、出锅锚点、冻结任务或原目标排序，所有最终候选仍经独立Validator扫描。中间逐步移动版本在截止处只移动半条备料链，实际浏览器仍为5820秒，失败保留browser-gantt-fixed/及failed-browser-plan-2.json；原子链修复解决该问题。两个真实归档分别已有通过结果：原归档最终为compaction-atomic-final.xml 1通过，第二归档为compaction-two-archives.xml中对应通过项；该报告内原归档旧失败已由前者替代。compaction-timing.json仅为中间逐步版本203ms回放，不作为最终原子版本耗时证据。

最终浏览器：只补跑失败的 `D:\env\npm.cmd --prefix web run test:e2e -- tests/e2e/gantt_readability.spec.ts`，独立8012端口/临时库/隐藏Edge，退出0，browser-gantt-final/results.xml为1通过（用例39.6秒、含启动总58.3秒）。与首轮已经通过的两条合并，本轮三条真实浏览器流程均有通过结果。实际四菜最终薯条占炉2970秒（49.5分钟），一锅蒸同一完整预约显示第1、3层，出锅差300秒、总流程8880秒、最长连续人工1500秒；这是该回归菜单的真实API排程结果，不保证任意菜单都可达300秒或全局最优。甘特图实际SVG、准备清单、保留中途冷却、灶眼分离、同温复用、悬停不改菜色与50次视图切换均通过，并只读查看最终gantt-panel.png确认双层显示。final-browser-plan.json保存实际计划，verification-summary.json索引实际报告及当前源码SHA256。

最后检查：新增压紧文件及对应回归Ruff/格式/mypy通过；最终原子链/有限预算/冻结与层位约束经独立只读复审无阻断。按用户要求没有再次运行已通过套件，没有运行完整P6或官方外部门槛，不把中间失败报告称为全绿。前端生产构建已更新。未修改用户data/runtime/p5.sqlite3、原始CSV、旧不可变发布、部署ZIP或重启8000服务；重启当前后端并强制刷新使用新代码，新建一桌使用固定双层的新知识/策略，旧桌保留其原知识与事实。用户本轮授权的立即重排与固定双层问题已完成。

## 2026-10-09：GitHub 源码提交准备

用户授权将当前项目目录直接提交至 haoyue12366-boop/smart_chu，用于后续 Render 公网部署。以本目录为仓库根目录，保留应用、前端、测试、脚本、依赖锁、配置和默认完整知识发布；本机 .env、运行数据库、缓存、历史交付包、验证产物及原始比赛附件通过 .gitignore 排除，原文件保留不删除。其他历史知识发布不随本次上传，依赖它们的历史验收需另行取得对应数据。

新增 .gitattributes 禁止对 data 文件作换行转换，保护知识 manifest 的字节哈希；README 补充当前源码的 Render 构建、启动、持久磁盘及可选大模型环境变量说明。当前仓库尚无 Render 实际部署验收，不改变既有 Windows 验收边界。

本次提交范围核对为 743 个文件，其中默认知识发布 41 个文件；常见令牌、API 密钥、私钥及 URL 内嵌凭据特征扫描未发现匹配项。暂存树通过 git archive 导出，41 个知识文件与工作目录原始字节逐一一致；从导出目录实际运行 scripts.check_snapshot --deny-network，通过加载 100 道菜、1562 道工序，网络调用 0 次。证据保存在本机 .tmp/github-upload-20261009/upload-audit.json 与 snapshot-check.json，不将本机验证产物提交到远端。本轮未修改调度业务代码，未重复运行完整应用测试。

## 2026-10-09：Render 求解进程冷启动修复

用户提供 Render 首次部署失败日志并要求继续处理：构建成功，ServiceContainer.start 在预热阶段报“求解进程未就绪”。定位到 SolverWorker 同时将默认等待和单个进程累计预热上限写死为 10 秒。以真实 JSON 工作进程合成延迟 11 秒启动，旧实现确实抛出相同 TimeoutError；失败证据为 .tmp/render-startup-20261009/red.xml（1 项失败），未伪造就绪消息或求解结果。

在线服务独立设置启动等待，默认 120 秒，可由 SMART_COOKING_SOLVER_STARTUP_TIMEOUT_SEC 配置有限正秒数（最大 600）。保留单个进程累计启动上限、真实 ready 消息及健康检查，进程超时或提前退出仍回收并拒绝就绪，新增日志区分启动超时与退出码。请求内恢复继续取请求共享截止和启动上限中的较早者，不扩大初排、重排求解预算，不改变时钟、编译和求解计时口径。直接构造的离线 SolverWorker/JsonSolverWorker 仍默认 10 秒。README 与部署环境变量示例同步说明免费实例路径和该启动配置。

验证：启动专项、应用生命周期、JSON 传输、进程故障注入和启动上下文共 26 项不同测试身份已有通过结果。首次相关回归 green.xml 为 16 通过、10 项旧知识目录 PermissionError；按既有测试机制使用 .tools/p2-regression/releases 同身份快照，SMART_COOKING_TEST_RELEASE_ROOT 的既有校验核对原 snapshot_id/release_id 后，仅重跑失败项，green-retry.xml 为 10 通过（65.03 秒）。未修改原知识权限、快照身份或通过项断言。实际慢启动后检查 /health/ready、100 道菜目录、真实求解和独立 Validator；同时验证短请求截止仍有效、多次预热不重置启动上限、超时进程回收和失败启动不发布 ready。Ruff 检查与格式检查通过，4 个修改应用文件的 mypy 通过。证据均保留于本机 .tmp/render-startup-20261009/，不提交缓存及报告。只有既有 Starlette/AnyIO 弃用警告，无新增验证失败。

本次验证在 Windows 完成，未重复运行完整历史验收。Render 公网启动情况须以推送后新部署及实际 /health/ready 响应为准；本地回归不代表免费云实例已经通过性能或完整业务验收。未修改用户运行数据库、不可变知识发布和既有交付 ZIP。

## 2026-10-09：Render 运行时锁竞争和读取错误恢复

用户报告公网初排后的读取错误、菜谱工艺读取失败及加菜/纯重排不稳定，提供 SQLite database is locked / BEGIN IMMEDIATE 原始日志。实际浏览器也捕获新建会话 HTTP 503 JSON SERVICE_NOT_READY（状态库暂不可用）；独立图谱读取及已有会话读取另有成功记录，因此不把所有历史非 JSON 响应断言为同一故障。仅修复当前代码路径，不改不可变知识、用户运行数据库或初排/重排求解预算；时钟同步与编译仍不计入求解预算。

根因与修改：通知 SSE 空轮询原来每次申请写事务，现无到期通知及历史补档时只读游标流；需落账时仍在写事务内重新检查并原子归档。后台计划时钟原来每秒写回整桌空闲进度，现只在真实开始/完成事件时持久化，前台请求仍同步当前时间并保存偏移；缓存不能跳过前台同步。前端 GET 对暂时网络、网关及不可解析响应最多共尝试 3 次，写请求不自动重试；成功状态刷新清理对应的旧读取错误，后台明确排程失败时清理过时的“正在重排”成功横幅，不隐藏实际失败或声称旧方案有效。

先失败证据：.tmp/render-read-20261009/backend-red.xml 和 clock-red.xml 分别在真实 SQLite 写锁下复现通知轮询、后台时钟的锁冲突。frontend-red.xml 复现暂时读取失败不恢复及过时错误/请求已接受提示。修复后 notification-green.xml 为 10 通过，clock-green.xml 为 12 通过（包含重叠的既有时钟用例，不能相加当作独立总数）。前端整组 46 项中首轮 45 通过、1 项在并行检查期间超时；该项原断言及超时不变，frontend-retry.xml 单独重跑通过。新测试仅合成菜谱或 HTTP 边界，真实运行路径另行验收。

真实 Windows Edge/API：隔离 8012 端口及临时运行库，npm --prefix web run test:e2e -- tests/e2e/schedule_clock.spec.ts tests/e2e/competition_flow.spec.ts，2 项通过，33.3 秒；初排、同任务增量追加立即 v2、在途完成前重排、冻结记录不改、完成通知及刷新后版本保持均验证。报告 .tmp/render-read-20261009/browser-local/。修改 Python 文件 Ruff 检查/格式通过，3 个应用文件 mypy 通过；前端 TypeScript 与 Vite 生产构建通过。已检查变更范围，不提交本机运行证据、数据库或构建目录。

公网验收待本次推送后执行；本地成功不代表免费实例任意负载都能满足预算。保留有界预算、独立校验及提交截止检查，不能用超时后发布冒充成功。Render 免费临时磁盘的部署/重启丢失数据问题仍属平台限制。

补充公网证据：cf7c385 上线后（前端 index-BmQGgK5h.js）以酿苦瓜、金龙吐瑞、轻松一锅蒸真实初排，找到合法候选但发布回滚；归档 .tmp/render-read-20261009/cloud-new-deploy/post-1.json。编译2391ms、引擎5614ms、发布1107ms，系统总9505ms；求解预算仍7000ms且编译排除，不能将结果误报成功。旧固定600ms发布预留不足，还存在最后候选校验占用发布窗口的问题。

引擎补充修复：每次独立候选扫描后，根据本请求最大实测校验/指标耗时，另外保留两次发布复核时间，并在Solver截止前再留最后候选扫描时间；所有截止只缩短，不扩总预算或放宽校验，已验证完整候选保留为合法回退。合成慢扫描200ms/次的反例在修改前只剩404ms，reserve-red.xml失败；修改后该项通过。相关慢SQLite通知事务与两条版本竞争累计预算回归最终3通过（reserve-final.xml），中间沙箱快照/临时目录PermissionError如实保留，仅提权读原测试快照重跑。新的引擎修改后两条真实浏览器再通过（browser-reserve/results.xml，32.9秒）。Ruff/格式和引擎mypy通过。继续等待补充提交的公网验收，不把第一轮已部署当作全面解决。

最终本轮公网结论（尚未全面通过）：ae0ecfd 的新构建已上线（主页 Last-Modified 2026-10-09 08:31:46 UTC）。同一真实三菜再次初排仍因发布事务截止回滚，.tmp/render-read-20261009/cloud-reserve/post-1.json 保存失败；编译780ms、引擎5002ms、发布2294ms、总12091ms，不能报告稳定或性能达标。因为初排失败，本轮公网加菜与纯重排步骤未执行，不能用本地成功替代。相同三菜的隔离本地性能剖析成功发布，发布阶段485ms（含剖析开销），主要为独立复核和运行状态写入。证据支持低算力/实际开销是剩余部署限制，但没有 Render 主机监控，不能断言全部未来故障只有此原因。

已向用户说明未完全解决，并询问保持比赛预算转腾讯云，或继续免费 Render 而明确允许演示放宽预算；未经答复不擅自增加预算、不购买资源。三个本轮自建公网诊断桌已清理：前两桌因部署已不存在，最后一桌 RESET_SESSION 返回 NO_REPLAN；cleanup-cloud.json 留证。用户原有会话未操作。代码最新已推送 ae0ecfd，本段只追加本地验收记录，避免纯记录推送又触发免费实例重部署。

## 2026-10-09：Render 免费实例宽预算与运行开销优化

授权：用户继续报告四道菜以上初排及重排失败，明确允许放宽时间预算，要求修复部署并检查加速空间。官方 Render compute-plans 当前列出免费 Web 服务为 0.1 CPU、512 MB；这说明本机五秒不能直接作为免费实例的耗时保证，尚无主机监控，不将全部耗时归因于单一因素。

公网先失败：以轻松一锅蒸、美式薯条、蒜香烤茄子、糯米烧麦创建独立诊断桌，HTTP 200 但业务 FAILED，总耗时约16.27秒；编译3300ms、Greedy296ms、串行构造1193ms、倒排提示2004ms、独立候选校验2578ms、引擎4295ms、发布6402ms，发布截止回滚。原阶段还有固定1.2秒串行参考及400ms构造上限，即使只增加整体预算也不会扩大这些阶段。原始响应归档 .tmp/render-budget-20261009/cloud-red.json。

实现：RENDER=true 自动选用版本后缀 :render-v1 的部署策略，初排/重排均为90000ms共享求解发布总限、Greedy10000ms、Solver累计45000ms、发布预留25000ms，编译独立20000ms；显式 STANDARD 或本机保留原策略。串行参考及构造按配置增大但仍扣共享余额，求解保持单线程。未来动作生效时刻使用独立编译上限，过去与在途事实仍冻结；已绑定会话不改策略，原知识发布不改。

真实链路又发现候选已成功发布但求解进程被150ms结果传输预留误杀，健康检查随后503。云端结果编码及IPC预留改为3秒且仍在共享截止内，异常后的后台空闲循环渐进预热唯一工作进程；ready 要求进程存活并收到真实就绪消息，不接受陈旧结果，不以伪造就绪绕过检查。

加速：RuntimeRepository 在同一原子事务内按表 executemany 批量写入，继续验证身份归属、不可变历史和CAS版本；不变行不重写。ServiceContainer.for_session 用SQLite JSON字段读取当前知识绑定、执行模式及偏移，避免每次解析整桌状态；绑定和可变偏移仍实时读取。对真实四菜70项assignment的同一发布回放，旧版0.750秒、新版0.578秒，SQL调用357降至18，save累计146ms降至40ms；独立复核均保留。此约23%的改进只代表本机同计划发布回放，不是公网端到端提速比例。证据为 profile-results.json 与剖析日志。

验证：本轮107个不同测试身份最终都有通过结果；包含两组截图菜单的真实知识初排、加菜、纯重排，8菜/10菜完整初排与独立Validator，历史计划及冻结事实、时钟原点保留，真实进程退出恢复、慢结果传输、批量存储、会话读取、物料账本和事件幂等。Windows本机真实云配置链路4项305.89秒、8/10菜2项110.11秒；这些是整组测试时间，不是每请求耗时。中间失败及沙箱旧知识目录PermissionError全部保留，测试快照只经既有身份校验回退/提权读取；未改原目录权限和知识字节。另修正两个旧质量夹具显式采用其断言要求的TOTAL_HUMAN_WORK目标，以及旧协调夹具转发已有preparation_budget参数，原断言保留。Ruff检查/格式、mypy app 283文件与git diff --check通过。

证据保留于本机 .tmp/render-budget-20261009/，临时运行库与报告不提交。当前仅完成本地验证，须推送后对新部署再执行公网初排、追加、纯重排及刷新；不替代完整P6/比赛实时预算验收，也不宣称免费磁盘具备持久恢复保障。


首轮部署实测：d68b6c7 已推送并在 Render 上线（主页 Last-Modified 2026-10-09 13:41:17 UTC），上述四菜 HTTP200/PUBLISHED v1，独立Validator再次核验通过，worker ready200，约56.51秒；编译3789ms、引擎49217ms、发布2231ms。其中SERIAL_REFERENCE与C_MAKESPAN均OPTIMAL，E_QUALITY搜索约28002ms仍UNKNOWN、未返回候选。随后加菜被409拒绝，原始回复 state_revision 已自动推进；不能把初排成功报告为整链成功。本轮自建桌已RESET_SESSION结束，用户会话未操作；证据 public-api/。

补充修复：联合质量阶段设置可选quality_ms上限，Render取10000ms，严格目标与放宽重试共享同一截止，仍扣原Solver累计余额；STANDARD不增加该字段，保持历史序列化和原阶段行为。策略升级为 :render-v2，已有v1桌不改绑定。新增工作台加菜命令 /api/v1/sessions/{session_id}/recipes 复用持久HTTP准入：服务器同步时钟后读取当前状态，客户端仍核对base_plan_version；同一event_id及请求体重试观察首次结果，不重复追加，重复菜与旧计划版本继续409。原/events中的人工事实反馈保持严格版本核对。前端勾选仅在桌次或菜单改变时清空，计时推进不清空选择。

先失败证据：command-red.xml 的加菜命令404与联合质量调用超出200ms共享限时反例，frontend-command-red.xml 的计时刷新清空勾选反例。修复后command-green.xml的12项通过；其余5项质量回归因旧知识沙箱PermissionError，只读提权重跑command-quality-retry.xml为5通过。真实知识、JSON求解进程的最终Render配置6项通过（render-v2-green.xml，258.00秒），包括两组截图菜单初排、追加、重排、8/10菜完整初排、旧会话策略保留及进程恢复。本轮后端111个不同测试身份已有通过结果，前端47项通过，TypeScript、Vite生产构建、Ruff与mypy app通过。第二轮公网验收尚待新提交部署，不把本地成功写为已完全修复公网。


最终公网验收（48c6b62）：代码已推送 main，新构建主页 Last-Modified 2026-10-09 14:16:04 UTC，新桌绑定 :render-v2。遵照用户最新要求，公网只排3至5道菜，未执行8/10菜公网请求。七次业务排程均HTTP200/PUBLISHED：截图一四菜初排27.85秒、追加酿苦瓜至五菜60.36秒、五菜纯重排55.01秒；同五菜单独初排59.33秒；截图二苹果芒果派/鸡仔饼/酿苦瓜三菜初排系统开销16.95秒、追加栗子冰皮月饼至四菜58.25秒、四菜纯重排30.80秒。秒数为本轮请求实际开销，不同菜单/负载不可作为统一性能保证，不宣称达到本地五秒或原比赛实时预算。

公网浏览器首轮在追加成功v2后，检查读取捕获HTML HTTP502，原始失败与cleanup失败保留于public-browser-v2/；页面已读到v2且未显示过时派发停止横幅。随后health/ready及原会话均200，原会话仍ACTIVE v2，没有证据表明该次失败来自菜谱或计划不可行。重新连接同一诊断桌，页面读取恢复，点击纯重排成功v3，刷新后仍v3，历史v1原文与计时原点保留，页面脚本错误0；恢复读取有界重试，写请求不自动重试。public-browser-resume-v2/final.json记录通过，不覆盖第一轮失败。免费网关瞬时可用性无法由代码承诺永不出现故障。

最终独立复核：verify_public_evidence.py从公网归档的7个problem/plan完整解码，每个候选及当前问题的serial_reference均由独立ScheduleValidator再次扫描通过，浏览器已开始记录task_spans/started_at及计时原点保留，证据public-independent-validation.json。三桌均只为本轮自建诊断桌，最终全部RESET_SESSION结束并读取确认ENDED；用户会话未操作。收尾health/ready为200，public-cleanup-final.json留证，原知识data树与ae0ecfd逐文件版本无修改。

补充本地浏览器证据：两项真实Edge/API用例通过，操作用例分别13.28秒与约6.3秒，覆盖比赛五字段、追加、立即纯重排、冻结与通知。Windows沙箱的测试服务收尾遗留进程使整组等待9.4分钟；核对启动命令tests.browser_clock_app、8016端口及项目解释器路径后仅结束本轮24416及其子进程4144，退出0并生成browser-v2/results.xml（2通过），不把等待时间作为应用排程开销。该隔离测试服务已经关闭。

最终代码48c6b62已部署验证；此补充实测记录只更新本地task.md，避免纯记录推送再次触发免费实例重部署。原失败证据保留，不将本轮样本成功改写为100菜全量、P6正式预算或全局最优已达标。

## 2026-10-10：初排与重排尽量五秒的性能优化（进行中）

当前目标：尽量使初排、加菜重排及纯重排在五秒内完成。保留用户要求的Render初排/重排90000ms预算，不设置五秒强制中断；独立校验、冻结事实、精确物料、版本检查及原子发布不削弱。公网仍只验证三至五道菜，不能用本地耗时或小样本替代完整部署结论。

开始状态：当前HEAD为48c6b62；工作区原有task.md补充记录及不可变发布目录的缺失/不可读项保持不动，不纳入性能改动。真实问题回放来自上一轮公网归档，知识从同身份的baseline目录加载并校验manifest/snapshot绑定；未修改知识字节、用户数据库或公网会话。剖析证据保留于.tmp/performance-20261010/。

首轮证据：截图一四菜原问题的Greedy完整构造失败，剖析约2.17秒；引擎仍通过CP-SAT得到独立验证候选，剖析请求约14.95秒（包含首次导入及10秒联合质量搜索），不作为预热性能成绩。发现提前准备声明被插入器当作冻结整菜的锚点，阻止尚未执行任务错峰；先补双菜合成反例及硬窗口保护，再修复并重测真实菜单。目标尚未达成。

用户追加选择“优先保留充分优化，5秒作为尽量达到的目标”。本轮没有设置五秒停止条件，也未缩短90000ms整体预算、10000ms已有联合质量阶段上限或45000ms求解累计额度；不是以首次可行解冒充充分优化。

实现中的等价优化：提前准备只声明合法供应就绪，不冻结尚未执行的整菜；真实完成/在途事实仍冻结。Greedy内部日历改用不可变dataclass，公开候选、时间、资源与物料仍用严格Pydantic契约，完整撤销指纹保留。独立物料扫描按全部字段值比较，不复制端口模型或依赖深层模型比较；保留数量类型、单位、缩放、上下界、份额、身份和来源引用，额外拒绝内部布尔/浮点数量篡改。零时长人工段保留资源/覆盖约束，但不进入连续人工目标，避免虚构占用把真实休息接成一个长段。

人工目标改为连续块起点传播：NoOverlap仍限制一个人工；两主动段间不足休息阈值时，后段块起点不得晚于前段起点。相邻段的传递闭包产生真实连续块；任意合法排程按真实块起点赋值即可精确取目标，因此不固定未来顺序、不删除非等价加工候选，也不降低优化优先级。只以无条件依赖、互斥覆盖和安全时间域排除不可能的先后。单线程连续人工阶段关闭可选探测和线性松弛以减少前处理，仍保留全部约束、目标、最优界和原截止。其它阶段维持原参数，全面关闭预处理的对照回放较慢，未采用。

先失败证据：material-keys-red.xml四例证明旧物料比较误接受bool/float与整数相等的内部篡改；zero-human-red.xml证明零时长历史把两段30秒且间隔60秒的真实人工误算为120秒，未来必需60秒加热的正确全局目标应为60秒。修复后物料/Validator单元30通过，真实共享与全系统性质测试16通过（每类至少200个实际生成样本，82.18秒）；人工/目标/编译/预算回归55通过（block-model-green.xml，11.16秒），另9组独立穷举的全部时刻、窗口、可选加工和休息阈值通过（human-oracle.xml）。Ruff与mypy app 285文件通过。原失败报告保留。

日历、撤销、错峰与投影33项通过；两条旧展示契约在完整HEAD隔离副本中也复现相同失败（legacy-contract-head-guarded.xml）。仅修正夹具语义：执行时间线与提前准备清单的互斥并集必须覆盖全部任务；模拟推进到计划中设备真实开始后一秒再核对实际占用，保留占用身份、状态及重读相等断言。修正后2通过（contract-current-policy.xml）。第一次隔离HEAD脚本因未保护Windows子进程入口失败，修正后才取得有效基线证据，不将首次失败计为产品缺陷。

性能回放不是公网成绩：同四菜Greedy构造从修复冻结后的约0.95秒降到约0.62秒（lean-calendar剖析），但不同首轮导入及剖析开销不能直接套用端到端比例。等价连续块模型在该问题上证明联合阶段OPTIMAL，最长连续人工1200秒、总流程8940秒、出锅极差270秒；旧原模型10秒搜索仅得到可行解（连续人工1920秒）。单线程参数对照联合阶段约3.4–3.5秒，包括阶段建模，完整剖析引擎仍约8.35–9.57秒；位置排序模型和全阶段关闭预处理都未证明整体提速，实验未作为生产策略。真实JSON工作进程、发布与Render新部署尚待验收，不能声明已达到五秒。

完整链路本地验收：render-runtime.xml共19项，16项通过（242.51秒），其中真实Render JSON进程两组截图菜单初排/追加/纯重排、8/10菜完整初排、重启预算绑定、进程恢复、立即重排及冻结均通过；3项只因历史P2目录PermissionError未执行，用既有SMART_COOKING_TEST_RELEASE_ROOT相对路径只读加载同一快照后3项通过（runtime-access.xml，11.37秒）。没有修改发布知识、目录权限或原90秒策略。本轮完整链路未发现业务失败。

当前生产代码无剖析预热回放：截图二三菜的引擎1.184秒，串行参考、集中出锅/总流程及联合质量阶段均OPTIMAL，指标连续人工750秒、总流程4470秒、出锅极差300秒（production-warm-3）；截图一四菜引擎10.840秒，联合阶段5625ms，不能只引用较快实验数值宣称四菜五秒达标（production-warm-fixed）。预热回放只测内存引擎，没有编译、IPC、HTTP和最终发布的完整开销。

发布微回放使用同一公网归档四菜70项assignment、相同知识及隔离SQLite，候选和不同串行参考都做新的独立扫描；为了隔离数据库外键明确预置SIMULATED合成事件，不是原事件账重放或HTTP端到端证据。HEAD约0.418秒，当前约0.439秒，物料子扫描0.106降至0.082秒但总发布未显示显著提速，不能把局部节约夸大为发布整体改善。首次旧数据库不可读及夹具外键/事件契约错误如实保留，最后完整事务才计为有效微回放。源码已经过相关回归、Ruff/mypy及限定目标文件的diff检查；准备部署并只对公网三至五菜实测。

## 2026-10-10：问题对象复用、容量下界与阶段提示专项

用户再次选择“优先保留充分优化，5秒作为尽量达到的目标”。继续保留Render初排/重排90000ms共享总限、累计45000ms求解、已有10000ms联合质量上限及完整独立核验；没有五秒停止条件、删加工候选或固定未来菜序。

上一轮35eb0e9已推送并部署，主页Last-Modified为2026-10-09 17:52:47 UTC。公网五个请求成功发布，随后五菜纯重排遇HTTP502：三菜初排/重排服务内分别11.135/10.740秒；四菜初排/重排26.275/38.497秒；四菜加到五菜83.110秒。成功项的选中候选及不同串行参考均重新独立扫描通过，不能把HTTP200或健康恢复当作整个流程成功。五菜重排没有取得可核对的回执，原清理读请求也遇502；恢复后两次查询本轮自建四菜桌均为404，ready200且主页构建时刻未变，没有发送RESET，记录为NOT_FOUND_AFTER_502_NO_RESET_SENT。三菜自建桌此前已RESET并确认ENDED；用户原有桌未操作。502及会话消失的原因没有日志证据，不能宣称已证明OOM或成功恢复已提交会话。证据在.tmp/performance-20261010/public/。

对象复用：首条跨进程问题仍按完整WorkerJob JSON严格校验；后续消息只验证本轮job/hint/deadline/stage参数，核对完整问题哈希，并连接该进程已经核验的同一个不可变问题。内部信封用model_construct连接已验证参数，不能用该路径构造、批准候选；拒绝替换problem、非法类型和未知字段。避免Pydantic每阶段重验证并深层复制整个问题，也使基础CP模型及问题哈希缓存复用真实同一对象。完整候选的自报指标经独立Validator扫描后直接保留，不再重复Solver侧计算、复制及深比较；错误makespan/人工指标仍拒绝。五次缓存解码微回放从旧48c6b62隔离副本0.380秒降至0.058秒，仅为内部微测试，不代表公网加速比例。

解析容量下界：仅对单一真实物理资源、所有合法备选均保证占用、无固定/共批替代、连续预约结束恰为单一出锅锚点的末段推导。每道菜同一物理设备只计一份保守占用；沿无条件依赖取最短必需末段长度，多菜累计保证层数超过设备容量时得到安全的出锅极差下界。跳过可等待的预约后锚点、容量1兼容共享设备、实际历史及无法证明的设备备选，不把共享冰箱当成单路独占。下界只强化已有约束，不删顺序或加工路径。五菜截图菜单可证明极差至少1380秒，原300秒目标因此不可行；求解时间的五秒目标与烹饪出锅极差是两个不同指标。

阶段提示：已知完整提示违反当前makespan/人工/极差界时，跳过整份提示，父进程仍持有原独立合法回退。B_SPREAD只提示容量下界，不将其变成硬上界；下界不一定可达。正超标量同时被安全下界与阶段上界固定时，单线程连续人工联合阶段启用基础线性松弛；其余连续人工阶段维持原关闭线性松弛/探测设置。全阶段关闭前处理和三/四菜全面启用线性松弛的对照较慢，未采用。

测试证据：cached-job-red.xml先复现重复问题对象，修复后cached-job-green.xml共13项JSON传输/缓存/重启/进程故障通过。verified-metrics-green.xml的24项通过及原知识访问失败5项，用相同快照只读提权后的verified-metrics-access.xml补齐5通过。stage-hint-red.xml先复现违背阶段界仍提示载体；终态spread-hint-green.xml共33项模型、连续人工独立枚举与容量边界通过，含9项末段容量检查。spread-hint-red.xml先复现没有容量目标提示；容量1的独立穷举证明下界3、真正最优值5，生产提示3后仍返回OPTIMAL 5，防止把提示当硬约束。其它容量小例逐秒穷举所有起点、检查超载，并与原生CP最优值及独立Validator对照；实际时长缩短检查属于显式合成历史，不冒充真实事件重放。

完整链路floor-render-access.xml为20通过1失败（204.61秒），通过项包含两组真实截图菜单的Render JSON工作进程初排/加菜/纯重排、8/10菜本地完整初排、时钟与冻结、CAS共享预算、进程恢复和总人工边界。失败是旧五菜测试仍假定蒸箱单层互斥、要求极差至少1620秒；在完整旧提交35eb0e9隔离副本中同样复现1620<=300失败（head-35eb-cooking/old-five-test.xml）。仅将该测试改为真实三层设备、真实出锅极差<=现策略300秒、优于串行及新的完整独立核验，cooking-final.xml为1通过；没有改菜谱、设备容量或产品约束。第一轮floor-render-runtime.xml因原知识及系统临时目录权限失败，原报告保留；提权只读读取原知识、使用新隔离临时目录，不改ACL或知识字节。Ruff应用和本轮测试检查通过，mypy app 286文件通过；最终提交前再核对文件范围与格式。

性能范围：真实旧问题预热引擎的五菜B_SPREAD从约4.9秒降到0.3秒；组合实验一次9.341秒且联合目标OPTIMAL，随后生产参数回放12.550秒、联合目标FEASIBLE，不能挑较快实验宣称稳定最优或五秒达标。生产参数的本地真实SystemClock/API/JSON/SQLite链路五菜初排10.594秒、纯重排28.601秒，均PUBLISHED；证据memory-7134bd1f04a14e71831f8eb21d98c942/。之前同菜单独立运行28.405/44.137秒来自不同请求身份及重排事实，不当作严格控制的提速比例。当前合计Windows进程峰值RSS约402.23MB（之前404.45MB），Windows内存值不能证明Render/Linux OOM原因。代码本轮尚未部署，公网新验证待推送后完成；目标仍为尽量五秒，尚未达标。

## 2026-10-10：连续排程内存与完整报告归档

f3fa2bf已推送，Render主页构建时刻更新至2026-10-09 19:26:29 UTC且ready200。公网再次有五个成功发布并通过新独立候选/串行扫描的请求：三菜初排12.989秒、纯重排10.409秒；四菜初排27.826秒、纯重排38.630秒；加到五菜56.202秒。五菜纯重排在11.673秒时遇HTTP502，随后清理读取及ready也502；原始HTTP状态、响应字节和耗时先归档，清理错误未覆盖原重排错误。恢复后ready200，本轮自建四菜桌404，主页构建时刻未变；没有发送RESET，不能宣称该桌ENDED或五菜重排已提交。三菜诊断桌已确认ENDED，独立五菜初排尚未执行，用户桌未操作。证据public-bounds/，本轮公网完整验收未通过，五秒目标未达成。

只读调查和本地复现：同一应用以真实Render策略、JSON工作进程、Compiler、Validator及SQLite依次执行三菜初排/重排/结束、四菜初排/重排、加五菜及五菜重排。时钟使用显式合成的公网观察偏移1/60/116秒，不是原事件账重放或公网性能。Windows均成功，峰值两进程合计RSS472.36MB；worker无界保留22份建模报告，Python唯一引用堆约34.96MB，序列化约7.97MB，过程继续增加。确认存在无界报告持有，不以此单独认定Linux/Render OOM；没有取得Render退出日志。证据sequence-memory-d398fbe392844f85b9dc7f50e6425a8d/。Docker命令存在但守护进程未运行，未安装或启动容器实验，也没有Linux受限内存结果。

实现：storage.SolverReportArchive将每份完整SolverBuildReport（含问题/阶段、参数、实际模型规模和全部proto映射）写入数据库目录旁solver-build-reports/，文件名绑定build引用摘要及原JSON内容哈希，临时文件flush/fsync后同目录原子替换；相同内容去重，不覆盖不同状态/内容。在线容器通过注入接收端归档，再只保留最近一份报告；原离线SolverWorker不设限，既有实验导出不变。归档失败明确记录诊断并保留全部未保存报告，不能为限制内存默默丢证据。核对报告问题身份和结果引用后才归档；处理报告的时间仍扣共享截止，迟到结果返回UNKNOWN而保持已空闲的工作进程。没有缩短90000ms预算、质量目标、独立校验或改变SQLite事实事务。

知识加载只减少内部LoadedRelease信封的重复深复制：manifest、snapshot、index分别严格JSON解码；内容哈希、索引、版本、来源、证明及抽取绑定全部核对后连接原不可变引用。LoadedRelease普通构造和JSON契约仍严格，MenuKnowledgeView、Compiler与计划验证路径保持原完整检查，不重用旧运行proof。loaded-envelope-red.xml先复现重复快照对象，report-retention-red.xml先复现缺少归档/有界保留接口。

专项检查：report-envelope-green.xml为28通过6显式外部检查跳过（真实Neo4j及导出未执行），覆盖损坏/混版知识、固定旧版本、JSON完整传输与缓存、归档失效仍保留报告、进程崩溃/挂起及完整原子文件归档。一个合成夹具使用字符串更新枚举产生序列化警告，已改用SolveStatus值；对应文件检查与新错身份、错引用、慢归档共享截止的archive-fault-final.xml共5通过。archive-render-green.xml共6项真实Render全链通过（179.04秒），含两组截图菜单初排/加菜/纯重排及本地8/10菜；成功阶段引用的每份完整归档都从磁盘重新解码并核对problem_hash/build_id/映射，worker只保留一份。Ruff应用及对应测试检查通过，mypy app 287文件通过，提交前核对格式和限定文件diff。

改后同一结构的连续链路实验sequence-memory-101a2adbe0b247579f82d9d5de3f1624/全部PUBLISHED/正确结束，Windows合计RSS峰值404.20MB，保留1份最近报告、唯一引用堆约2.71MB；完整22份约7.97MB JSON仍在归档。两次请求身份及实际求解布局不同，内存差用于诊断，不套用为公网提速比例或Linux内存保证。对应本地三菜初排/重排1.879/1.933秒，四菜7.491/7.745秒，加五菜12.772秒，五菜纯重排13.205秒；均包含HTTP/API路径、完整核验、归档及SQLite发布，使用合成观察时钟。目标仍未达标，此轮代码待推送后对公网3–5菜再验收。

da6e3c9已推送，Render主页构建时刻更新为2026-10-09 20:08:42 UTC，ready200；健康接口不暴露SHA，部署证据为新构建时刻加实际行为。public-memory/归档本轮原始HTTP回复和七次请求的候选/问题/串行参考，全部HTTP200/PUBLISHED并重新独立完整扫描通过：三菜初排/纯重排10.268/12.100秒；四菜初排/纯重排25.812/29.111秒；加到五菜32.894秒、五菜纯重排43.105秒；独立五菜初排31.178秒。计时原点、已开始task_spans及历史v1保留。三个自建诊断桌均RESET并读取确认ENDED，收尾ready200；用户桌未操作。这一轮未再出现502，不将单轮成功认定为永无网关故障，也没有日志能证明前两轮502必由OOM造成。五秒目标仍未达成，90秒共享上限保持不变。

后续范围：本轮三个五菜请求均重复执行两次B_SPREAD且两次都OPTIMAL 1080。准备只在同一问题第一轮B_SPREAD已独立接纳、OPTIMAL目标值/最优界与当前候选的真实超标量相等，且候选满足后续更紧总流程界时省去第二次重复求解；后续联合人工/总流程优化和最终新独立核验仍执行。缺界、未证明最优、拒绝的候选或更紧界不满足时继续原搜索。先用真实CP-SAT加独立小例枚举及故意损坏候选暴露边界，再实施；此项尚未实现。

阶段复用已实现：redundant-spread-red-access.xml先复现同一已证明最优极差仍调用两次B_SPREAD（1失败5通过），最初red.xml仅因发布目录只读权限未执行。改后redundant-spread-green.xml共47通过，覆盖真实CP-SAT、小容量逐秒穷举、缺界/松下界/FEASIBLE/坏候选保留原搜索，以及后续紧流程界排除种子时不能省搜索。redundant-render-green.xml共6项真实Render JSON/完整归档/发布回归通过（170.96秒），含两组截图菜单及本地8/10菜，联合质量阶段继续执行。没有伪造额外OPTIMAL结果或省独立校验。Ruff与mypy app 287文件通过，代码尚未部署。

发布CPU专项准备：当前生产结构的隔离四菜发布剖析0.372秒，包含70份assignment、两次新独立扫描及实际SQLite事务，SQL事件仍为显式SIMULATED合成外键夹具；不是公网HTTP耗时。约两万次RootModel身份比较是已观测CPU开销，准备只对同一具体类型、无私有/额外元数据的字符串ID使用类型与root值比较，扩展类型继续原Pydantic语义，哈希和公共构造约束保持一致。先与未修改的RootModel.__eq__对照300个确定性生成样例；首次标签生成器误包含纯空格，已修正为NonEmpty本来要求的含非空白字符，identity-baseline-valid.xml共10通过，原失败保留。这是等价性能试验，尚未采用或部署。

身份快路径未采用：identity-fast-green.xml语义对照10通过，但三次完整发布剖析的原逻辑为0.378/0.468/0.438秒（中位0.438），快路径0.519/0.534/0.509秒（中位0.519），没有整体改善。生产ids.py已按原字节撤回且git diff为空；实验源码及对照测试仅保留.tmp/performance-20261010/，不纳入生产提交，复测原逻辑0.448秒。不能把局部调用数减少当成实际发布提速，也不依据单机剖析推算公网达标。当前新增生产范围只有engine.py中的已证明极差阶段复用及对应测试、任务记录；准备推送后只验公网3–5菜。
