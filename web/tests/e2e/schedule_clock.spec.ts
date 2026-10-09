import { expect, test } from './fixtures';

test('replan publishes before the active step finishes, freezes history and announces completion', async ({
  page,
  request,
}) => {
  const taskId = `clock-browser-${Date.now()}`;
  const initial = await request.post(`/api/competition/plan?task_id=${taskId}`, {
    data: [{ id: '61e6c51fec6e1d65587067e1', name: '低温牛排' }],
    headers: { 'Idempotency-Key': 'initial' },
  });
  expect(initial.status()).toBe(200);
  const mapping = await (await request.get(`/api/v1/competition-tasks/${taskId}`)).json();
  const sid = mapping.session_id as string;
  await page.addInitScript(
    (identity: string) => localStorage.setItem('cook.session', identity),
    sid,
  );
  await page.goto('/');
  await expect(page.getByTestId('clock-progress')).toBeVisible();
  await expect(page.getByTestId('running-operations')).toContainText('低温牛排');
  const before = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  type ActiveExecution = {
    status: string;
    execution_id: string;
    started_task_ids: string[];
    completed_task_ids?: string[];
    task_spans: { task_id: string; interval: { end_sec: number } }[];
  };
  const activeEnd = (execution: ActiveExecution) =>
    Math.min(
      ...execution.task_spans
        .filter(
          (span) =>
            execution.started_task_ids.includes(span.task_id) &&
            !(execution.completed_task_ids ?? []).includes(span.task_id),
        )
        .map((span) => span.interval.end_sec),
    );
  // 取最早的在途完成边界，确保中点前没有其他并行步骤已经结束。
  const active = before.runtime.executions
    .filter((e: ActiveExecution) => e.status === 'RUNNING')
    .sort((a: ActiveExecution, b: ActiveExecution) => activeEnd(a) - activeEnd(b))[0];
  expect(active).toBeTruthy();
  const end = activeEnd(active);
  const advance = end - before.clock_progress.current_offset_sec;
  expect(advance).toBeGreaterThan(1);
  const midpoint = await request.post('/__test/clock/advance', {
    data: { seconds: Math.floor(advance / 2) },
  });
  expect(midpoint.status()).toBe(200);
  await page.getByTestId('replan-remaining').click();
  // 不推进到在途结束边界，必须已发布新版本并刷新真实甘特图。
  await expect(page.getByTestId('session-version')).toContainText('v2');
  const pending = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  expect(pending.runtime.current_plan_version).toBe(2);
  expect(pending.clock_progress.current_offset_sec).toBeLessThan(end);
  expect(pending.clock_progress.waiting_for_boundary).toBe(false);
  expect(
    pending.runtime.executions.find(
      (e: { execution_id: string }) => e.execution_id === active.execution_id,
    ).status,
  ).toBe('RUNNING');
  const currentPlan = await (await request.get(`/api/v1/sessions/${sid}/plans/2`)).json();
  for (const assignment of currentPlan.plan.validated.candidate.assignments)
    expect(assignment.interval.start_sec).toBeGreaterThanOrEqual(
      pending.clock_progress.current_offset_sec,
    );
  await expect(page.getByLabel('烹饪安排图').locator('svg')).toBeVisible();
  await expect(page.getByTestId('replan-status')).toHaveCount(0);
  for (const prior of before.runtime.executions) {
    const retained = pending.runtime.executions.find(
      (e: { execution_id: string }) => e.execution_id === prior.execution_id,
    );
    expect(retained.started_at).toBe(prior.started_at);
    expect(retained.task_spans).toEqual(prior.task_spans);
    expect(retained.scheduled_resource_spans).toEqual(prior.scheduled_resource_spans);
  }
  const advanced = await request.post('/__test/clock/advance', {
    data: {
      seconds: end - pending.clock_progress.current_offset_sec,
    },
  });
  expect(advanced.status()).toBe(200);
  await expect(page.getByTestId('session-version')).toContainText('v2');
  await expect(page.getByTestId('completion-announcement')).toContainText('低温牛排');
  await expect(page.getByTestId('completion-announcement')).toContainText('已完成');
  await expect(page.getByTestId('completion-announcement')).toContainText('计划时钟推算');
  await expect(page.getByTestId('replan-status')).toHaveCount(0);
  await expect(page.getByTestId('gantt-replan-status')).toHaveCount(0);
  const after = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  const frozen = after.runtime.executions.find(
    (e: { execution_id: string }) => e.execution_id === active.execution_id,
  );
  expect(frozen.started_at).toBe(active.started_at);
  expect(frozen.task_spans).toEqual(active.task_spans);
  expect(frozen.scheduled_resource_spans).toEqual(active.scheduled_resource_spans);
  expect(frozen.status).toBe('COMPLETED');
  const feed = await (await request.get(`/api/competition/notifications?task_id=${taskId}`)).json();
  expect(
    feed.messages.some(
      (m: { kind: string; text: string }) =>
        m.kind === 'OPERATION_COMPLETED' && m.text.includes('低温牛排'),
    ),
  ).toBe(true);
  await page.reload();
  await expect(page.getByTestId('session-version')).toContainText('v2');
  await expect(page.getByTestId('execution-progress')).not.toContainText('0 /');
});
