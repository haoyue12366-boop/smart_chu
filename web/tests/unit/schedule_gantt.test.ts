// 明确合成的显示边界；真实计划覆盖由服务与浏览器集成另行验证。
import { describe, expect, it } from 'vitest';
import { ganttRows, fullDate, displayMinutes } from '../../src/model/presentation';
import type { PlanPresentation, RuntimeSession } from '../../src/api/types';

const display: PlanPresentation = {
  time_origin: { start_at: '2026-10-02T23:59:00+08:00' },
  range_end_sec: 90000,
  operations: [
    {
      task_id: 'toy-step',
      carrier_id: 'toy-batch',
      recipe_instance_id: 'toy-instance',
      recipe_id: 'toy-recipe',
      recipe_name: '合成边界',
      title: '合成等待',
      action: 'WAIT',
      start_sec: 86400,
      end_sec: 90000,
      shared: true,
      frozen: false,
    },
  ],
  resources: [
    {
      entry_id: 'toy-occupation',
      resource_id: 'human_1',
      component_id: 'human_1',
      resource_label: '人工',
      title: '合成介入',
      task_ids: ['toy-step'],
      recipe_instance_ids: ['toy-instance'],
      recipe_names: ['合成边界'],
      start_sec: 86400,
      end_sec: 86430,
      shared: true,
      frozen: false,
      configuration: [],
    },
  ],
};

describe('server timeline display', () => {
  it('preserves a next-day interval rather than folding to HH:mm offsets', () => {
    const row = ganttRows(display, 'recipe', null)[0];
    expect(row?.start_sec).toBe(86400);
    expect(row?.end_sec).toBe(90000);
    expect(fullDate(display.time_origin.start_at, 86400)).toContain('2026/10/03');
  });
  it('uses physical resource windows and keeps shared intervention visible', () => {
    const row = ganttRows(display, 'resource', null)[0];
    expect(row?.lane).toBe('人工');
    expect(row?.end_sec).toBe(86430);
    expect(row?.shared).toBe(true);
  });
  it('uses actual completed task identities rather than the passage of time', () => {
    const session = {
      runtime: {
        executions: [
          { completed_task_ids: ['toy-step'], started_task_ids: ['toy-step'], status: 'COMPLETED' },
        ],
      },
    } as unknown as RuntimeSession;
    expect(ganttRows(display, 'recipe', session)[0]?.status).toBe('已完成');
    expect(ganttRows(display, 'recipe', null)[0]?.status).toBe('待执行');
  });
  it('formats integer seconds as one decimal minute using half-up', () => {
    expect(displayMinutes(3)).toBe('0.1');
    expect(displayMinutes(33)).toBe('0.6');
    expect(displayMinutes(75)).toBe('1.3');
  });
  it('keeps same-name recipe instances in distinguishable lanes', () => {
    const item = display.operations[0]!;
    const value = {
      ...display,
      operations: [
        item,
        {
          ...item,
          task_id: 'toy-step-2',
          recipe_id: 'toy-recipe-2',
          recipe_instance_id: 'toy-instance-2',
        },
      ],
    };
    expect(new Set(ganttRows(value, 'recipe', null).map((r) => r.lane)).size).toBe(2);
  });
});
