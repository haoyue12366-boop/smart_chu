// 真实四菜、API、Solver、Validator 与浏览器；仅测试独立库中新建的会话。
import type {
  AdvancePreparationPresentation,
  OperationPresentation,
  ResourcePresentation,
} from '../../src/api/types';
import { expect, test } from './fixtures';

test('dish legend, wrapped hover card and zoom survive live session polling', async ({
  page,
  request,
}, testInfo) => {
  const created = await request.post('/api/v1/sessions', {
    data: {
      event_id: `gantt-${Date.now()}`,
      mode: 'MANUAL_CONFIRM',
      recipes: [
        { id: '66715324bfbee338853895c7', name: '轻松一锅蒸' },
        { id: '5cc7e0404a21a4301960aef1', name: '糯米烧麦' },
        { id: '5a38d6b3f254230b76e2b50f', name: '蒜香烤茄子' },
        { id: '5f73fee8a6daaa3b53ba043c', name: '美式薯条' },
      ],
    },
  });
  expect(created.status()).toBe(200);
  const result = await created.json();
  expect(result.status).toBe('PUBLISHED');
  const sid = result.session_id as string;
  const syncView = () =>
    Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === 'GET' &&
          new URL(response.url()).pathname === `/api/v1/sessions/${sid}`,
      ),
      page.evaluate(() => window.dispatchEvent(new Event('focus'))),
    ]);
  const state = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  const envelope = await (await request.get(`/api/v1/sessions/${sid}/plans/1`)).json();
  await testInfo.attach('scheduled-plan.json', {
    body: JSON.stringify(envelope, null, 2),
    contentType: 'application/json',
  });
  // 实际多菜排程不能再把13分钟炒馅、31.5分钟薯条程序拉成数小时预约。
  const ricePan = envelope.presentation.resources.find(
    (row: { title: string; resource_id: string }) =>
      row.resource_id === 'stove_1' && row.title.includes('炒锅烧热'),
  );
  const friesOven = envelope.presentation.resources.find(
    (row: { recipe_names: string[]; resource_id: string }) =>
      row.recipe_names.includes('美式薯条') && row.resource_id === 'oven_1',
  );
  expect(ricePan).toBeTruthy();
  expect(friesOven).toBeTruthy();
  expect(ricePan.end_sec - ricePan.start_sec).toBeLessThanOrEqual(3600);
  expect(friesOven.end_sec - friesOven.start_sec).toBeLessThanOrEqual(3600);
  const display = envelope.presentation;
  const onePotLayers = (display.resources as ResourcePresentation[]).filter(
    (row) => row.recipe_names.includes('轻松一锅蒸') && row.resource_id === 'steam_oven_1',
  );
  expect(onePotLayers.map((row) => row.layer_index).sort()).toEqual([1, 3]);
  expect(onePotLayers[0]!.start_sec).toBe(onePotLayers[1]!.start_sec);
  expect(onePotLayers[0]!.end_sec).toBe(onePotLayers[1]!.end_sec);
  expect(onePotLayers[0]!.task_ids).toEqual(onePotLayers[1]!.task_ids);

  const preparations = (display.advance_preparations ?? []) as AdvancePreparationPresentation[];
  const soakedRice = preparations.find(
    (item) => item.recipe_name === '糯米烧麦' && /浸泡.*糯米|糯米.*浸泡/.test(item.description),
  );
  expect(soakedRice).toBeTruthy();
  expect(soakedRice!.source_kind).toBe('USER_POLICY_ASSUMPTION');
  expect(soakedRice!.original_duration_sec).toBeGreaterThanOrEqual(14400);
  const preparedTasks = new Set(preparations.flatMap((item) => item.task_ids));
  expect(
    (display.operations as OperationPresentation[]).filter((item) =>
      preparedTasks.has(item.task_id),
    ),
  ).toEqual([]);
  expect(
    (display.resources as ResourcePresentation[]).flatMap((row) =>
      row.task_ids.filter((id) => preparedTasks.has(id)),
    ),
  ).toEqual([]);
  const cooling = (display.operations as OperationPresentation[]).filter(
    (item) => item.recipe_name === '糯米烧麦' && /放凉|冷却/.test(item.title),
  );
  expect(cooling.map((item) => item.title)).toEqual(
    expect.arrayContaining(['将糯米放凉。', '将松子糯米馅心冷却。']),
  );
  for (const item of cooling) expect(item.end_sec - item.start_sec).toBe(1200);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.addInitScript((id) => localStorage.setItem('cook.session', id), sid);
  await page.goto('/');
  await expect(page.getByText('全流程完成差', { exact: true })).toBeVisible();
  await expect(page.getByTestId('optimization-strategy')).toContainText('总流程优先');
  const checklist = page.getByTestId('advance-preparations');
  await expect(checklist.locator('summary')).toContainText('开工前准备');
  await checklist.locator('summary').click();
  await expect(checklist.locator('li')).toHaveCount(preparations.length);
  await expect(checklist).toContainText('按已提前备好排程，不计入本轮用时');
  await expect(checklist).toContainText(soakedRice!.description);
  await expect(checklist).toContainText(
    `原准备时长：${(soakedRice!.original_duration_sec / 60).toFixed(1)} 分钟`,
  );
  await checklist.screenshot({ path: testInfo.outputPath('advance-preparations.png') });
  await testInfo.attach('advance-preparations.json', {
    body: JSON.stringify({ preparations, retained_cooling: cooling }, null, 2),
    contentType: 'application/json',
  });
  await checklist.locator('summary').click();
  const cooking = page.getByTestId('cooking-finishes');
  await cooking.locator('summary').click();
  await expect(cooking.locator('tbody tr')).toHaveCount(4);
  expect(envelope.plan.validated.candidate.metrics.recipe_cooking_finishes).toHaveLength(4);
  const legend = page.getByLabel('菜品颜色图例');
  await expect(legend.locator('li')).toHaveCount(4);
  const colors = await legend
    .locator('.gantt-recipe-swatch')
    .evaluateAll((els) => els.map((el) => getComputedStyle(el).backgroundColor));
  expect(new Set(colors).size).toBe(4);
  const chart = page.getByLabel('烹饪安排图');
  await chart.scrollIntoViewIfNeeded();
  await expect(chart.locator('svg')).toBeVisible();
  const coloredPaths = await chart
    .locator('svg path')
    .evaluateAll((els) => els.map((el) => getComputedStyle(el).fill));
  colors.forEach((color) => expect(coloredPaths).toContain(color));
  const blockColors = () =>
    chart.locator('svg path').evaluateAll(
      (els, palette) =>
        els
          .filter((el) => palette.includes(getComputedStyle(el).fill))
          .map((el) => ({ shape: el.getAttribute('d'), fill: getComputedStyle(el).fill }))
          .sort((a, b) => `${a.shape}:${a.fill}`.localeCompare(`${b.shape}:${b.fill}`)),
      colors,
    );
  const beforeHover = await blockColors();

  const longRow = display.resources.find(
    (r: { title: string; end_sec: number; start_sec: number }) =>
      r.title.length > 70 && r.end_sec - r.start_sec >= 120,
  );
  expect(longRow).toBeTruthy();
  const lanes: string[] = display.resource_lanes?.length
    ? display.resource_lanes
    : [
        ...new Set<string>(
          display.resources.map((r: { resource_label: string }) => r.resource_label),
        ),
      ];
  const box = (await chart.boundingBox())!;
  const burnerRows = (display.resources as ResourcePresentation[]).filter(
    (row) => row.resource_id === 'stove_1',
  );
  expect(burnerRows.length).toBeGreaterThan(0);
  const burnerLabels: Record<string, string> = { burner_1: '灶眼一', burner_2: '灶眼二' };
  const recipeColors = await legend
    .locator('li')
    .evaluateAll((els) =>
      Object.fromEntries(
        els.map((el) => [
          el.getAttribute('data-recipe-id'),
          getComputedStyle(el.querySelector('.gantt-recipe-swatch')!).backgroundColor,
        ]),
      ),
    );
  const renderedBlocks = await chart.locator('svg path').evaluateAll(
    (els, palette) =>
      els
        .filter((el) => palette.includes(getComputedStyle(el).fill))
        .map((el) => {
          const rect = el.getBoundingClientRect();
          return { fill: getComputedStyle(el).fill, x: rect.x, y: rect.y + rect.height / 2 };
        }),
    colors,
  );
  const centers = new Map<string, number>();
  for (const row of burnerRows) {
    const label = burnerLabels[row.component_id];
    expect(label).toBeTruthy();
    expect(row.resource_label).toBe(label);
    await expect(chart.locator('svg text').getByText(label!, { exact: true })).toHaveCount(1);
    const expectedX = box.x + 130 + (row.start_sec / display.range_end_sec) * (box.width - 160);
    const expectedY =
      box.y + 24 + ((lanes.indexOf(label!) + 0.5) / lanes.length) * (box.height - 104);
    const block = renderedBlocks.find(
      (item) =>
        item.fill === recipeColors[row.recipe_instance_ids[0]!] &&
        Math.abs(item.x - expectedX) < 1 &&
        Math.abs(item.y - expectedY) < 1,
    );
    expect(block, `${row.component_id}: ${row.title}`).toBeTruthy();
    centers.set(row.component_id, block!.y);
  }
  // 求解器可以只选一个灶眼；实际选择两个时，必须存在两个不同的 SVG 行坐标。
  expect(new Set(centers.values()).size).toBe(centers.size);
  await testInfo.attach('burner-lanes.json', {
    body: JSON.stringify({ centers: Object.fromEntries(centers), resources: burnerRows }, null, 2),
    contentType: 'application/json',
  });
  const x =
    box.x +
    130 +
    ((longRow.start_sec + longRow.end_sec) / 2 / display.range_end_sec) * (box.width - 160);
  const y =
    box.y +
    24 +
    ((lanes.indexOf(longRow.resource_label) + 0.5) / lanes.length) * (box.height - 104);
  await page.mouse.move(x, y);
  const tip = chart.locator('.gantt-tooltip');
  await expect(tip).toBeVisible();
  await expect(tip.locator('.gantt-tooltip-title')).toContainText(longRow.title);
  // 连续三次真实状态同步不销毁悬浮卡；健康SSE下由返回页面触发读取。
  for (let i = 0; i < 3; i++) {
    await syncView();
    await expect(tip).toBeVisible();
  }
  expect(await blockColors()).toEqual(beforeHover);
  const layout = await tip.evaluate((el) => {
    const title = el.querySelector<HTMLElement>('.gantt-tooltip-title')!;
    return {
      width: el.getBoundingClientRect().width,
      overflow: el.scrollWidth > el.clientWidth + 1,
      titleHeight: title.getBoundingClientRect().height,
      lineHeight: parseFloat(getComputedStyle(title).lineHeight),
    };
  });
  expect(layout.width).toBeLessThan(420);
  expect(layout.overflow).toBe(false);
  expect(layout.titleHeight).toBeGreaterThan(layout.lineHeight * 2);
  await tip.hover();
  await page.waitForResponse(
    (r) =>
      r.request().method() === 'GET' && new URL(r.url()).pathname === `/api/v1/sessions/${sid}`,
  );
  await expect(tip).toBeVisible();
  await chart.screenshot({ path: testInfo.outputPath('gantt-hover.png') });
  await chart.locator('..').screenshot({ path: testInfo.outputPath('gantt-panel.png') });
  await page.locator('.gantt-legend').screenshot({ path: testInfo.outputPath('dish-legend.png') });
  await page.mouse.move(20, 20);
  await expect(tip).toBeHidden();
  for (let i = 0; i < 50; i++) {
    const recipeView = i % 2 === 0;
    await page.getByLabel('甘特图分组').selectOption(recipeView ? 'recipe' : 'resource');
    const expectedLabels: string[] = recipeView
      ? [...new Set<string>(display.operations.map((o: { recipe_name: string }) => o.recipe_name))]
      : (lanes as string[]);
    for (const label of expectedLabels) {
      await expect(chart.locator('svg text').getByText(label, { exact: true })).toHaveCount(1);
    }
    const expectedBlocks = recipeView
      ? display.operations.length
      : display.resources.reduce(
          (sum: number, r: { recipe_instance_ids: string[] }) =>
            sum + Math.max(1, r.recipe_instance_ids.length),
          0,
        );
    await expect
      .poll(() =>
        chart
          .locator('svg path')
          .evaluateAll(
            (els, palette) =>
              els.filter((el) => palette.includes(getComputedStyle(el).fill)).length,
            colors,
          ),
      )
      .toBe(expectedBlocks);
    expect(errors).toEqual([]);
  }
  await chart.screenshot({ path: testInfo.outputPath('gantt-after-50-switches.png') });
  await page.getByLabel('甘特图分组').selectOption('recipe');
  await expect(legend.locator('.gantt-recipe-swatch')).toHaveCount(4);
  expect(
    await legend
      .locator('.gantt-recipe-swatch')
      .evaluateAll((els) => els.map((el) => getComputedStyle(el).backgroundColor)),
  ).toEqual(colors);
  await page.getByLabel('时间缩放').selectOption('1800');
  await syncView();
  await expect(page.getByLabel('时间缩放')).toHaveValue('1800');
  const after = await (await request.get(`/api/v1/sessions/${sid}`)).json();
  expect(after.runtime.executions).toEqual([]);
  expect(after.runtime.state_revision).toBe(state.runtime.state_revision);
  expect(errors).toEqual([]);
});
