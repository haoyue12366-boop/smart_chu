// 实际浏览器 + FastAPI + 固定100菜发布 + Compiler/Solver/Validator/SQLite；无网络 mock。
import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';
const steak = { id: '61e6c51fec6e1d65587067e1', name: '低温牛排' };

async function confirmWrite(page: Page, submit: () => Promise<void>) {
  const matches = (url: string) =>
    /\/api\/v1\/sessions(?:\/[^/]+\/events)?$/.test(new URL(url).pathname);
  const received = page.waitForResponse(
    (response) => response.request().method() === 'POST' && matches(response.url()),
  );
  await submit();
  let response = await received;
  if (response.status() === 503 || response.status() === 202) {
    const original = response.request().postDataJSON();
    const uncertain = await response.json();
    if (response.status() === 503)
      expect(['SERVICE_NOT_READY', 'PLANNING_TIMEOUT', 'PLANNING_PENDING']).toContain(
        uncertain.error?.code,
      );
    else expect(uncertain.status).toBe('PENDING');
    // 通过页面现有恢复入口查询原身份；不能创建另一个事件来掩盖回执失败。
    const retry = page.getByRole('button', { name: /^(用原请求身份重试|查询原请求结果)$/ });
    await expect(retry).toBeVisible();
    const repeated = page.waitForResponse(
      (value) => value.request().method() === 'POST' && value.url() === response.url(),
    );
    await retry.click();
    response = await repeated;
    expect(response.request().postDataJSON()).toEqual(original);
  }
  expect(response.status()).toBe(200);
  const result = await response.json();
  expect(result.event.status).toBe('APPLIED');
  expect(['PUBLISHED', 'NO_REPLAN', 'FAILED']).toContain(result.status);
  await expect(
    page.getByRole('button', { name: /^(用原请求身份重试|查询原请求结果)$/ }),
  ).toHaveCount(0);
  return result;
}
test('manual start, add, fault, recover, history and readonly recipe graph', async ({
  page,
  request,
}) => {
  await page.goto('/');
  await expect(page.getByText('100 道已发布菜谱')).toBeVisible();
  await page.getByLabel('执行方式').selectOption('MANUAL_CONFIRM');
  await page.getByRole('searchbox', { name: '搜索菜谱' }).fill(steak.id);
  await page.getByRole('checkbox', { name: `${steak.name} ${steak.id}` }).check();
  const created = await confirmWrite(page, () => page.getByTestId('create-plan').click());
  expect(created.status).toBe('PUBLISHED');
  await expect(page.getByTestId('session-version')).toContainText('v1');
  await expect(page.locator('.metrics')).toContainText('分钟');
  await page.getByRole('button', { name: '执行与反馈', exact: true }).click();
  await page.getByRole('button', { name: '确认开始', exact: true }).first().click();
  await page.getByRole('checkbox', { name: '已按实际情况核对数量和本次操作' }).check();
  await confirmWrite(page, () => page.getByRole('button', { name: '提交开始确认' }).click());
  await expect(page.getByRole('cell', { name: '执行中', exact: true }).first()).toBeVisible();
  const sid = await page.evaluate(() => localStorage.getItem('cook.session'));
  expect(sid).toBeTruthy();
  const state = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  expect(
    state.runtime.executions.some(
      (e: { source: string; status: string }) =>
        e.source === 'MANUAL_CONFIRM' && e.status === 'RUNNING',
    ),
  ).toBeTruthy();
  await page.getByRole('button', { name: '安排', exact: true }).click();
  const catalog = await (await request.get('/api/v1/recipes')).json();
  const additional = catalog.recipes.find(
    (r: { recipe_id: string; name: string }) =>
      r.recipe_id === '6492a7a5933a4b7277dee0cf' && r.name === '酿苦瓜',
  );
  expect(additional).toBeTruthy();
  await page.getByRole('searchbox', { name: '搜索菜谱' }).fill(additional.recipe_id);
  await page.getByRole('checkbox', { name: `${additional.name} ${additional.recipe_id}` }).check();
  const added = await confirmWrite(page, () => page.getByTestId('create-plan').click());
  expect(added.status).toBe('PUBLISHED');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('2 道菜');
  await expect(page.getByTestId('plan-diff')).toContainText('新增');
  const afterAdd = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  expect(afterAdd.runtime.state_revision).toBe(state.runtime.state_revision + 1);
  expect(afterAdd.runtime.current_plan_version).toBe(added.plan.plan_version);
  expect(afterAdd.menu).toHaveLength(2);
  await page.getByRole('button', { name: '执行与反馈', exact: true }).click();
  const candidate = page
    .locator('[data-device]')
    .filter({ has: page.getByRole('button', { name: '报告故障' }) })
    .first();
  const deviceId = await candidate.getAttribute('data-device');
  expect(deviceId).toBeTruthy();
  const device = page.locator(`[data-device=${JSON.stringify(deviceId)}]`);
  await device.getByRole('textbox').fill('浏览器验收：设备暂不可用');
  await confirmWrite(page, () => device.getByRole('button', { name: '报告故障' }).click());
  await expect(device.getByText('不可用 / 未知', { exact: true })).toBeVisible();
  await confirmWrite(page, () => device.getByRole('button', { name: '确认设备恢复' }).click());
  await expect(device.getByText('可用', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '历史版本', exact: true }).click();
  await page.getByLabel('历史计划版本').selectOption('1');
  await expect(page.getByTestId('history-version')).toContainText('v1');
  await page.getByRole('button', { name: '安排', exact: true }).click();
  await page.getByRole('button', { name: '查看工艺 →' }).first().click();
  await expect(page.getByRole('dialog')).toContainText('固定知识版本');
  await page.getByLabel('关闭工艺图').click();
  await page.screenshot({ path: '../data/verification/P5-browser/workbench.png', fullPage: true });
});
