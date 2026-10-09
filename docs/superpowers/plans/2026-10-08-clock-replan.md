# 倒排优化、执行时钟与安全重排实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Independent optimizer and frontend work uses superpowers:dispatching-parallel-agents.

**Goal:** 实现用户提出的倒排提示与CP-SAT优化、基于当前执行时钟的安全重排，以及前端和比赛接口可接收的操作完成通知，并运行测试。

**Architecture:** 继续使用版本化知识、独立Validator、事件状态机和持久通知流。新增明确的SCHEDULE_CLOCK执行来源，首次成功发布后启动时钟，按计划边界产生可追溯事件；重排触发后停止新操作派发，仅保留已开始工艺的有限间隔等必要衔接，在安全边界处理剩余问题。已有人工/设备/模拟模式保留其来源语义。

**Tech Stack:** Python 3.12、FastAPI、SQLite、Pydantic、OR-Tools CP-SAT、Vue 3、TypeScript、ECharts。

**Spec:** 本轮用户三项目标，以及“初排成功后自动计时”的明确答复。用户新要求替代本次范围内旧PLAN_ONLY默认；本轮明确要求运行测试。

## Global Constraints

- 蒸箱和烤箱是独立设备，各三层；跨层必须同温且配置兼容，时长不统一。
- 真实运行库data/runtime/p5.sqlite3只读；测试使用独立临时库；旧不可变知识不覆盖。
- 每个操作持续时间、物料守恒、单人人工、设备层位、依赖和独立校验保留。
- 过去操作冻结，运行中操作不可打断；未到边界不得伪造未来完成。
- 自动推算明确标记SCHEDULE_CLOCK，不冒充设备实测；人工或设备反馈可修正。
- 比赛原五字段计划响应保留，通过task_id关联的JSON/SSE通知接口传输完成提示。
- 当前菜单四道最终蒸制共享三层，不承诺所有菜单都能达到300秒出锅差；保留诚实不可达结果。

## Review Focus

- 多个并行运行操作结束不同：安全边界取已开始且不可打断部分的最晚结束。
- 临界同秒完成与新开始：完成先落账，等待重排时只继续已开始工艺的必要衔接。
- 重启、重复请求、SSE断线：时钟原点持久化，操作和通知不重复扣料、释放或发送。
- 人工修改剩余时长和设备故障：不能按旧预计结束自动覆盖修正或越过未释放占用。
- 初排耗时、正在求解时的事件和旧计划通知：时钟从发布开始，CAS和通知游标保持一致。

## Task 1: 倒排提示与CP-SAT

Files: app/scheduling/engine.py、新增倒排辅助模块、tests中的优化专项。

- [ ] 先用可后移烤制块及不可达容量菜单复现当前搜索不足。
- [ ] 按出锅窗口移动完整预约和必要依赖，保留时长和已执行历史；候选独立校验后作为CP提示。
- [ ] 在共享预算内组织出锅差优先的搜索，保留已校验回退方案。
- [ ] 运行专项与调度回归，记录真实菜单、可达与不可达场景证据。

## Task 2: 时钟与安全重排

Files: app/domain/events.py、runtime_clock.py、runtime_session.py、runtime_planning.py；app/runtime/执行推进和状态机；app/services/planning.py、publishing.py。

Interfaces: EventSource.SCHEDULE_CLOCK；RuntimeSession.schedule_clock持久化首次started_at、start_offset_sec、processed_until_sec及replan_requested_sec/replan_not_before_sec、replan_continuation_task_ids；clock_offset(session, now)；ClockExecutionService(runtime).advance(session_id, until_sec=None, deadline=None)。新事件REPLAN_REQUESTED使用ReplanPayload，允许空JSON并提供默认原因；RESET_SESSION仍要求SessionPayload的明确原因。

- [ ] 写中途请求、并行边界、重启、重复tick和显式反馈修正的失败测试。
- [ ] 复用已发布载体的执行与物料链产生时钟事件，首次发布启动、后续发布不重置。
- [ ] 请求先同步当前时钟，等待正在执行部分结束，再解剩余问题；返回PENDING及可重排时间。
- [ ] 完成运行事实冻结、层位保留和异常边界回归。

## Task 3: 通知与接口编排

Files: app/services/container.py、http_requests.py、session_queries.py；app/api/events.py、contracts.py、competition相关路由；app/runtime/notifications.py及模板；app/main.py。

Interfaces: read_session新增clock_progress={enabled,started_at,current_offset_sec,replan_requested_sec,replan_not_before_sec,replan_not_before_at,waiting_for_boundary}。OPERATION_COMPLETED通知带task_ids、execution_id、source、completed_at。GET /api/competition/notifications及/stream用task_id和游标关联；POST /api/competition/replan使用task_id与幂等键。

- [ ] 写完成通知幂等、重启补发、比赛JSON/SSE、按边界重排API测试。
- [ ] 在事件事务内写入完成消息；后台推进时钟和待重排队列，通知读取无伪造完成。
- [ ] 为新会话和比赛入口启用时钟模式，显式模式兼容；接口提供当前进度和等待原因。
- [ ] 验证原比赛五字段计划响应、事件CAS和后台恢复。

## Task 4: 前端与端到端交付

Files: web/src及相应组件、浏览器测试；README和运行文档；进度与证据仅docs/task.md。

- [ ] 显示时钟来源、当前操作、重排等待边界，加入重排剩余步骤入口。
- [ ] 展示完成消息并保持跨计划、断线重连去重，比赛接口面板直接接收通知。
- [ ] 运行类型检查、组件测试、浏览器/API完整流程及生产构建。
- [ ] 按三个用户目标逐项核对实际测试覆盖和当前源码，记录限制与启动方式。
