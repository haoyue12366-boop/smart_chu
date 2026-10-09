# 甘特图视图切换修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复多次切换菜品/设备人工后的旧轴、错位和残留，保留悬浮卡和缩放。

**Architecture:** 每轮 draw 捕获固定的行、颜色和时间快照，旧回调不读取新视图。先更新系列及坐标再 resize；图形采用稳定身份，视图变化明确替换旧系列。轮询内容不变继续跳过更新。

**Tech Stack:** Windows、Node 25.8.1、Vue 3.5.43、TypeScript 5.9.3、ECharts 6.1.0、Vitest、Playwright。

**Spec:** `docs/superpowers/specs/2026-10-07-gantt-and-cook-finish.md`

## Global Constraints

- 以下待实施；当前仅做定位和计划。
- 不升级依赖，不改后端排程，不写用户测试会话。
- 保留图例、菜品颜色、换行悬浮卡、800ms 隐藏延迟和缩放。
- 使用独立运行库，旧验收不覆盖；无Git不能虚构提交。

## Review Focus

- 两视图行数不同，减少行数时不能越界。
- 共批成员减少不残留旧分色图形。
- 轮询、状态变化、重排及视图变化交错时图形和tooltip一致。
- 缩放边界及历史页面无残留或越界。
- 无变轮询保留悬浮卡，状态更新保留已拖动范围。

## Task 1: 快照和更新生命周期

**Files:** 修改 `web/src/components/ScheduleGantt.vue`；扩展 `web/tests/unit/gantt_interaction.test.ts`。

**Interfaces:** props不变；`draw(): void` 固定本轮 `GanttRow[]`、颜色映射和时间。renderItem/formatter仅读该快照；data使用 `id: scope + ':' + row.id`；不同视图明确替换旧系列并同步轴。

- [ ] 新用例 `switches_axes_and_removes_old_blocks_with_unequal_row_counts`：真实ECharts SVG连续50次切换，断言SVG轴文字、条目数量、彩色方框数量和无异常。
- [ ] 补共同批次多成员变单成员、重排减少步骤、tooltip与当前任务一致的断言。
- [ ] 旧代码运行新用例确认失败：`npm run test:unit -- tests/unit/gantt_interaction.test.ts --outputFile.junit=../data/verification/2026-10-07-gantt-switch-diagnosis/unit-red.xml`。
- [ ] 固定draw快照，更新option后resize；补稳定身份、旧子图形移除。不能只用越界return掩盖旧系列。
- [ ] 同一用例、既有悬浮卡/缩放用例通过，green报告单独保存；`npm run typecheck`通过。

## Task 2: 浏览器验证与构建

**Files:** 扩展 `web/tests/e2e/gantt_readability.spec.ts`；重建 `web/dist`；登记 `docs/task.md`。

**Interfaces:** 现有公开API和独立测试数据库，无新API。

- [ ] 真实菜单连续50次切换，穿插拖动缩放、三轮轮询和状态变化；断言实际SVG纵坐标、图形边界、pageerror，不能只检查下拉框选值。
- [ ] 同时检查菜品颜色一致、悬浮卡换行可进入、历史页正常。
- [ ] 运行目标Playwright用例并保存截图；新变化或失败才扩大测试范围。
- [ ] `npm run typecheck`、`npm run format:check`、`npm run build`全部成功。
- [ ] 登记本轮源码/构建哈希、实际命令和证据；不把上次验收改成此次修复已通过。
