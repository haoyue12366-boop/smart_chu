// 合成显示反例；可视化须揭示冲突，不能用图层覆盖隐藏它。
import { expect, it } from 'vitest';
import { humanOverlaps } from '../../src/model/presentation';
import type { ResourcePresentation } from '../../src/api/types';
const row: ResourcePresentation = {
  entry_id: 'toy-a',
  resource_id: 'human_1',
  component_id: 'human_1',
  resource_label: '人工',
  title: '合成人工任务',
  task_ids: ['toy-a'],
  recipe_instance_ids: ['toy-i'],
  recipe_names: ['合成菜'],
  start_sec: 0,
  end_sec: 30,
  shared: false,
  frozen: false,
  configuration: [],
};
it('reveals overlapping human intervals while accepting adjacent half-open intervals', () => {
  expect(
    humanOverlaps([
      row,
      { ...row, entry_id: 'toy-b', task_ids: ['toy-b'], start_sec: 20, end_sec: 40 },
    ]),
  ).toEqual([['toy-a', 'toy-b']]);
  expect(humanOverlaps([row, { ...row, entry_id: 'toy-b', start_sec: 30, end_sec: 40 }])).toEqual(
    [],
  );
});
