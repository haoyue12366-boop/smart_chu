import { expect, test } from './fixtures';
const steak = { id: '61e6c51fec6e1d65587067e1', name: '低温牛排' };
test('explicit simulated progress keeps recovery separate from release of the occupied execution', async ({
  page,
  request,
}) => {
  await page.goto('/');
  await expect(page.getByText('100 道已发布菜谱')).toBeVisible();
  await page.getByLabel('执行方式').selectOption('SIMULATED');
  await page.getByRole('searchbox', { name: '搜索菜谱' }).fill(steak.id);
  await page.getByRole('checkbox', { name: `${steak.name} ${steak.id}` }).check();
  await page.getByTestId('create-plan').click();
  await expect(page.getByTestId('session-version')).toContainText('v1');
  const sid = await page.evaluate(() => localStorage.getItem('cook.session'));
  const initial = await (await request.get(`/api/v1/sessions/${sid}/plans/1`)).json();
  const resource = initial.presentation.resources
    .filter((r: { resource_id: string }) => r.resource_id !== 'human_1')
    .sort((a: { start_sec: number }, b: { start_sec: number }) => a.start_sec - b.start_sec)[0];
  expect(resource).toBeTruthy();
  await page.getByRole('button', { name: '执行与反馈', exact: true }).click();
  await expect(page.getByText('SIMULATED · 模拟演示', { exact: true })).toBeVisible();
  await page.getByLabel('模拟推进秒数').fill(String(resource.start_sec + 1));
  await page.getByTestId('simulation-advance').click();
  await expect
    .poll(async () => {
      const value = await (await request.get(`/api/v1/sessions/${sid}`)).json();
      return value.device_presentation.find(
        (d: { physical_resource_id: string; occupancy_status: string }) =>
          d.physical_resource_id === resource.resource_id && d.occupancy_status === 'OCCUPIED',
      );
    })
    .toBeTruthy();
  const advanced = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  expect(
    advanced.runtime.executions.every((e: { source: string }) => e.source === 'SIMULATED'),
  ).toBeTruthy();
  const cards = page.locator('[data-device]');
  const occupied = cards.filter({ hasText: '占用待确认' }).first();
  const id = await occupied.getAttribute('data-device');
  const device = page.locator(`[data-device=${JSON.stringify(id)}]`);
  await device.getByRole('textbox').fill('模拟验收：占用时故障');
  await device.getByRole('button', { name: '报告故障' }).click();
  await expect(device.getByText('不可用 / 未知', { exact: true })).toBeVisible();
  await device.getByRole('button', { name: '确认设备恢复' }).click();
  await expect(device.getByText('可用', { exact: true })).toBeVisible();
  await expect(device.getByText('占用待确认', { exact: true })).toBeVisible();
  const recovered = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  const observed = recovered.runtime.device_states.find(
    (d: { device_instance_id: string }) => d.device_instance_id === id,
  );
  expect(observed.active_execution_id).toBeTruthy();
  expect(observed.occupancy_status).not.toBe('FREE');
  await device.getByRole('checkbox', { name: '已检查并取出本次执行的物品，设备已清空' }).check();
  await device.getByRole('button', { name: '确认本次执行已释放设备' }).click();
  await expect(device.getByText('空闲', { exact: true })).toBeVisible();
  const released = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  const after = released.runtime.device_states.find(
    (d: { device_instance_id: string }) => d.device_instance_id === id,
  );
  expect(after.active_execution_id).toBeNull();
  expect(after.occupancy_status).toBe('FREE');
  expect(
    released.runtime.executions.find(
      (e: { execution_id: string }) => e.execution_id === observed.active_execution_id,
    ).status,
  ).not.toBe('COMPLETED');
  const reminder = page.locator('[data-notice]').first();
  await expect(reminder).toBeVisible();
  const reminderId = await reminder.getAttribute('data-notice');
  const reminderText = await reminder.textContent();
  await page.reload();
  await page.getByRole('button', { name: '执行与反馈', exact: true }).click();
  await expect(page.locator(`[data-notice=${JSON.stringify(reminderId)}]`)).toHaveText(
    reminderText ?? '',
  );
  const reloaded = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  expect(reloaded.runtime.executions).toEqual(released.runtime.executions);
  await page.screenshot({ path: '../data/verification/P5-browser/execution.png', fullPage: true });
});
