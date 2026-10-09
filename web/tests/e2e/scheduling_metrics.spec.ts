import { expect, test } from './fixtures';
import type { OperationPresentation, PlanEnvelope } from '../../src/api/types';

test('adding cheesecake retains serial saving, system timing and compact final human work', async ({
  page,
  request,
}, testInfo) => {
  const taskId = `metrics-${Date.now()}`;
  const url = `/api/competition/plan?task_id=${taskId}`;
  const first = await request.post(url, {
    data: [
      { id: '66715324bfbee338853895c7', name: '轻松一锅蒸' },
      { id: '5cc7e0404a21a4301960aef1', name: '糯米烧麦' },
      { id: '5a38d6b3f254230b76e2b50f', name: '蒜香烤茄子' },
      { id: '5f73fee8a6daaa3b53ba043c', name: '美式薯条' },
    ],
    headers: { 'Idempotency-Key': 'initial' },
  });
  expect(first.status(), await first.text()).toBe(200);
  expect((await first.json()).overview.planningOverhead.kind).toBe('INITIAL');
  const sid = (await (await request.get(`/api/v1/competition-tasks/${taskId}`)).json()).session_id;
  await page.addInitScript(
    ({ id, task }) => {
      localStorage.setItem('cook.session', id);
      localStorage.setItem('cook.competitionTask', JSON.stringify({ sid: id, taskId: task }));
    },
    { id: sid, task: taskId },
  );
  await page.goto('/');
  await expect(page.getByTestId('planning-overhead')).toContainText('系统初排开销');
  await request.post('/__test/clock/advance', { data: { seconds: 60 } });
  await page.getByRole('button', { name: '接口测试', exact: true }).click();
  await page
    .getByLabel('比赛请求数组')
    .fill(JSON.stringify([{ id: '5d565e87a9114174727c8bad', name: '半熟芝士蛋糕' }]));
  await page.getByLabel('比赛幂等键').fill('cheesecake');
  const additionResponse = page.waitForResponse(
    (value) => value.url().includes('/api/competition/plan') && value.request().method() === 'POST',
  );
  await page.getByTestId('competition-submit').click();
  let added = await additionResponse;
  if (added.status() === 503) {
    await testInfo.attach('first-add-response.json', {
      body: await added.text(),
      contentType: 'application/json',
    });
    // 已提交计划的响应超时按原身份查询，禁止重复加菜或增加同次求解预算。
    const replay = page.waitForResponse(
      (value) =>
        value.url().includes('/api/competition/plan') && value.request().method() === 'POST',
    );
    await page.getByRole('button', { name: '用原请求身份重试', exact: true }).click();
    added = await replay;
  }
  expect(added.status(), await added.text()).toBe(200);
  const response = await added.json();
  expect(response.overview.planningOverhead.kind).toBe('REPLAN');
  expect(response.overview.planningOverhead.elapsed_ms).toBeGreaterThan(0);
  expect(Number(response.overview.timeSave)).toBeGreaterThan(0);
  const envelope: PlanEnvelope = await (
    await request.get(`/api/v1/sessions/${sid}/plans/2`)
  ).json();
  expect(envelope.plan.serial_reference).toBeTruthy();
  const finalWork = envelope.presentation.operations
    .filter((row: OperationPresentation) => /确认全部必需分支完成/.test(row.title))
    .sort((a, b) => a.end_sec - b.end_sec);
  expect(finalWork).toHaveLength(5);
  const last = finalWork.at(-1)!;
  const previous = finalWork.at(-2)!;
  // 等待本菜烘烤等必要工艺是合法的；只拒绝工艺和人工都已释放后的无效尾空档。
  const ready = Math.max(
    ...envelope.presentation.operations
      .filter(
        (row) => row.recipe_instance_id === last.recipe_instance_id && row.task_id !== last.task_id,
      )
      .map((row) => row.end_sec),
  );
  const humanEnd = Math.max(
    previous.end_sec,
    ...envelope.presentation.resources
      .filter((row) => row.resource_id === 'human_1' && row.end_sec <= last.start_sec)
      .map((row) => row.end_sec),
  );
  expect(last.start_sec - Math.max(ready, humanEnd)).toBeLessThanOrEqual(60);
  await expect(page.getByTestId('session-version')).toContainText('v2');
  await expect(page.getByTestId('planning-overhead')).toContainText('系统重调度开销');
  const saving = page.locator('.metrics article').filter({ hasText: '较串行节省' });
  await expect(saving).not.toContainText('—');
  await testInfo.attach('plan-and-response.json', {
    body: JSON.stringify({ envelope, response }, null, 2),
    contentType: 'application/json',
  });
  await page.locator('.metrics').screenshot({ path: testInfo.outputPath('metrics.png') });
});
