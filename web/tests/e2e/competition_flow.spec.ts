import { expect, test } from './fixtures';
const ids = ['5d54bae2a9114174727c8b20', '6510fb3545256c3ad7c25c9c', '5c8646fe98d5bc7e184a90bc'];
test('direct name + id competition requests use persistent task context and exact five fields', async ({
  page,
  request,
}) => {
  const catalog = await (await request.get('/api/v1/recipes')).json();
  const choices = ids
    .map((id) => catalog.recipes.find((r: { recipe_id: string }) => r.recipe_id === id))
    .filter(Boolean)
    .map((r: { recipe_id: string; name: string }) => ({ id: r.recipe_id, name: r.name }));
  // 初排使用真实目录选三道，找不到固定 ID 时明确失败，禁止成功样板。
  expect(choices).toHaveLength(3);
  await page.goto('/');
  await page.getByRole('button', { name: '接口测试', exact: true }).click();
  const task = `browser-task-${Date.now()}`;
  await page.getByLabel('比赛任务标识').fill(task);
  await page.getByLabel('比赛幂等键').fill('initial');
  await page.getByLabel('比赛请求数组').fill(JSON.stringify(choices));
  await page.getByTestId('competition-submit').click();
  await expect(
    page
      .getByTestId('competition-response')
      .or(page.getByRole('button', { name: '用原请求身份重试' })),
  ).toBeVisible();
  if (await page.getByRole('button', { name: '用原请求身份重试' }).isVisible())
    await page.getByRole('button', { name: '用原请求身份重试' }).click();
  await expect(page.getByTestId('competition-response')).toBeVisible();
  const first = JSON.parse(await page.getByTestId('competition-response').innerText()) as {
    overview: { recipeCount: number };
  };
  expect(Object.keys(first).sort()).toEqual(
    ['cookingTimeline', 'detailTimeline', 'ingredientsSummary', 'overview', 'recipeDetail'].sort(),
  );
  expect(first.overview.recipeCount).toBe(3);
  await page.getByTestId('competition-submit').click();
  await expect(page.getByTestId('session-version')).toContainText('v1');
  const extra = catalog.recipes.find(
    (r: { recipe_id: string; name: string }) =>
      r.recipe_id === '6492a7a5933a4b7277dee0cf' && r.name === '酿苦瓜',
  );
  await page.getByLabel('比赛幂等键').fill('addition');
  await page
    .getByLabel('比赛请求数组')
    .fill(JSON.stringify([{ id: extra.recipe_id, name: extra.name }]));
  await page.getByTestId('competition-submit').click();
  // 追加立即计算；不用测试时钟快进到在途结束来触发重排。
  await expect(
    page
      .getByTestId('competition-response')
      .or(page.getByRole('button', { name: '用原请求身份重试' })),
  ).toBeVisible();
  if (await page.getByRole('button', { name: '用原请求身份重试' }).isVisible())
    await page.getByRole('button', { name: '用原请求身份重试' }).click();
  await expect(page.getByTestId('session-version')).toContainText('v2');
  await expect(page.getByTestId('competition-response')).toBeVisible();
  const last = JSON.parse(await page.getByTestId('competition-response').innerText()) as {
    overview: { recipeCount: number; timeSpent: string };
  };
  expect(last.overview.recipeCount).toBe(4);
  expect(last.overview.timeSpent).toMatch(/^\d+\.\d$/);
  const sid = await page.evaluate(() => localStorage.getItem('cook.session'));
  const state = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  expect(state.runtime.execution_mode).toBe('SCHEDULE_CLOCK');
  expect(state.runtime.executions.length).toBeGreaterThan(0);
  await page.reload();
  await expect(page.getByTestId('session-version')).toContainText('v2');
});
