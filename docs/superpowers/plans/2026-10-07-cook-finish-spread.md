# 出锅时间差目标修订 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 初排/重排优化出锅时间差，目标300秒，独立校验和前端展示保持同一口径。

**Architecture:** 版本化菜谱上下文存放有证据的出锅操作锚点，Compiler映射到任务，Solver按锚点结束时间建模，Validator独立核算。完整流程完成时间仍包含收尾，出锅字段单独传递，旧会话不重解释。

**Tech Stack:** Windows、Python3.12、Pydantic2、OR-Tools CP-SAT、FastAPI、Vue/TypeScript，依赖沿用现有锁定版本。

**Spec:** `docs/superpowers/specs/2026-10-07-gantt-and-cook-finish.md`

## Global Constraints

- 用户已授权实施；新增默认连续人工目标、移除总人工目标，执行证据见 docs/task.md。
- 固定100个recipe_id；单人工、设备互斥、全部工序和已完成事实保留。
- Validator不调用求解器指标函数验证自身；原始材料只读。
- 出锅目标300秒；优秀严格小于300秒；资源限制导致超标时如实显示。
- 新知识/策略绑定新会话；原历史兼容。代表样本不等于全量/正式验收。

## Review Focus

- 推迟装盘使上桌差达标但出锅超标的反例。
- 多次加热、多分支、辅助热油、月饼冷却和冷菜。
- 共批成员偏移、同名不同实例。
- 已完成/运行中、库存供应、取消菜单及重排不虚构出锅时间。
- 缺标注及旧历史不能默认按全步骤最大值冒充出锅时间。

## Task 1: 版本化锚点与工艺审核

**Files:** 新建 `app/domain/cooking_completion.py`、`tests/unit/test_cooking_completion.py`、`scripts/audit_cooking_completion.py`；修改 `app/domain/recipe_context.py`、`app/domain/scheduling_problem.py`、`app/compiler/compiler.py` 和直接调用的知识校验/快照导出加载；审核结果写新 `data/preparations/` 发布目录。

**Interfaces:** `CookingCompletionRule(operation_ids: tuple[OperationId, ...], kind: Literal['OUT_OF_POT','NO_HEAT_READY'], evidence_refs: tuple[NonEmpty, ...])`；上下文可选 `cooking_completion`；`RecipeCookingCompletion(recipe_instance_id, task_ids: tuple[TaskId, ...], kind, evidence_refs)`；问题字段 `cooking_completions`。出锅时刻取标注锚点任务结束时间最大值。新策略要求完整锚点；缺省仅用于旧版本兼容。

- [ ] 写非法/缺失操作锚点、同名实例、快照往返、新策略缺标注和旧版本兼容测试，先确认失败。
- [ ] 根据依赖及成品物料逐菜审核，先截图五菜及鱿鱼反例；输出100道的锚点、口径、证据和歧义表。
- [ ] 合并“取出＋调味”的操作保留有依据的整段边界并注明精度；若需纯取出时刻，先取得细分依据，不能编造时长。
- [ ] 实现契约、Compiler映射和知识校验，新发布目录保留旧版；新目标缺标注时不能激活。
- [ ] 单测、快照测试与100道审核通过；缺依据的记录单列，不宣称标注完成。

## Task 2: 指标、优化和独立校验

**Files:** 修改 `app/domain/schedule.py`、`app/domain/policy.py`、`app/scheduling/objectives.py`、`metrics.py`、`ranking.py`、`engine.py`、`candidate_pool.py`、`cp_sat.py`、`greedy.py`、`solution_mapping.py`；修改 `app/validation/metrics.py`、`dependencies.py`；扩展 `tests/unit/test_schedule_metrics.py`、`test_objective_stages.py`；新建 `tests/unit/test_cooking_completion_validation.py`。

**Interfaces:** 新增 `RecipeCookingFinish(recipe_instance_id, cooking_finish_sec)`、指标内 `recipe_cooking_finishes`、指标 `cooking_finish_spread_sec: NonNegativeInt | None = None`及剩余同口径字段。保留原 `recipe_completions`/`completion_spread_sec`的流程完成语义。调度端纯领域工具 `cooking_finish_times(problem: SchedulingProblem, intervals: Mapping[TaskId, Interval]) -> dict[RecipeInstanceId, int]`；Validator按锚点另行扫描。策略明确出锅目标基准，旧策略仍按原语义。

- [ ] 反例测试：两菜出锅600/1800秒、FINISH3600/3660秒，应得到出锅差1200秒、上桌差60秒、makespan3660秒，旧代码需失败。
- [ ] 测第二次加工、成员偏移、冷却1800秒、冷菜、实际完成冻结、取消和库存；篡改自报值需被Validator拒绝。
- [ ] 新指标独立核算，SPREAD改为 `max(cooking_finishes)-min(cooking_finishes)` 的超300秒部分；不能使用全流程makespan作最晚出锅。
- [ ] ranking、阶段cap、候选选择、Greedy及降级路径同步新口径；保留全步骤依赖、NoOverlap、人工和流程总时间，已完成锚点固定为实际事实。
- [ ] 运行指标、目标、Validator、重排冻结和官方协议相关既有测试，Ruff/mypy通过；协议中的流程完成含义不被破坏。

## Task 3: 前端与验收

**Files:** 修改 `web/src/api/types.ts`、`web/src/App.vue`及相关通知展示；新建 `data/policies/p6-quality-cook-finish-v1.json`；扩展 `tests/integration/test_policy_activation.py`、`test_replan_freezing.py`和前端E2E；更新架构目标章节与 `docs/task.md`。

**Interfaces:** API包含新指标和每菜出锅时刻，前端只展示后端权威值；新主指标“出锅时间差”。旧会话显示原上桌口径，不伪装成新目标结果。

- [ ] 独立数据库测试新策略、历史兼容和实际完成后重排；检查出锅及流程时间均可追溯。
- [ ] 展示每菜出锅时刻、实际差值，300秒不标优秀；通知对应实际工艺步骤。
- [ ] 对用户五菜、鱿鱼反例、单菜、共批及冻结重排做代表性测量；独立核验冲突、依赖、事实和出锅差。
- [ ] 记录初排<5s/<10s、重排<3s/<8s的实际响应，超标及最优性证据如实列明。需全量验收时沿用固定用例集，样本不代替全量。
- [ ] 类型检查、目标测试、构建成功后保存证据，登记新知识/策略的启动方法。
