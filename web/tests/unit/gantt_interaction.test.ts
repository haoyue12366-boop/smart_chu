// 合成显示用例；ECharts 使用真实 SVG 渲染，不替代后端验收数据。
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getInstanceByDom } from 'echarts/core';
import ScheduleGantt from '../../src/components/ScheduleGantt.vue';
import type { PlanEnvelope, RuntimeSession } from '../../src/api/types';

const envelope: PlanEnvelope = {
  plan: {
    session_id: 'toy-session',
    plan_version: 1,
    parent_plan_version: 0,
    state_revision: 0,
    knowledge_version: 'synthetic',
    snapshot_id: 'synthetic',
    committed_at: '',
    validated: { candidate: { metrics: null } },
  },
  problem: null,
  presentation: {
    time_origin: { start_at: '2026-10-07T23:59:00+08:00' },
    range_end_sec: 1200,
    operations: ['a', 'b'].map((id) => ({
      task_id: id,
      carrier_id: 'shared',
      recipe_id: `recipe-${id}`,
      recipe_instance_id: `instance-${id}`,
      recipe_name: '同名合成菜',
      title: `切配 ${id}`,
      action: 'CUT',
      start_sec: 0,
      end_sec: 600,
      shared: true,
      frozen: false,
    })),
    resources: [
      {
        entry_id: 'shared-human',
        resource_id: 'human_1',
        component_id: 'human_1',
        resource_label: '人工',
        title: '共同切配',
        task_ids: ['a', 'b'],
        recipe_instance_ids: ['instance-a', 'instance-b'],
        recipe_names: ['同名合成菜', '同名合成菜'],
        start_sec: 0,
        end_sec: 600,
        shared: true,
        frozen: false,
        configuration: [],
      },
    ],
  },
};
const session: RuntimeSession = {
  runtime: {
    session_id: 'toy-session',
    state_revision: 0,
    current_plan_version: 1,
    knowledge_version: 'synthetic',
    time_origin: envelope.presentation.time_origin,
    now_offset_sec: 0,
    execution_mode: 'MANUAL_CONFIRM',
    executions: [],
    device_states: [],
    material_lots: [],
  },
  menu: ['a', 'b'].map((id) => ({
    recipe_id: `recipe-${id}`,
    recipe_instance_id: `instance-${id}`,
    name: '同名合成菜',
  })),
  requires_replan: false,
  dispatch_blocked: false,
  last_planning_failure: null,
  replan_reasons: [],
};
const containers: HTMLElement[] = [];
beforeEach(() => {
  // jsdom 没有布局或 canvas；只替代尺寸/字体测量，保留真实图表与提示生命周期。
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1000);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(400);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
});
afterEach(() => {
  containers.splice(0).forEach((el) => el.remove());
  vi.restoreAllMocks();
});
function mounted() {
  const container = document.createElement('div');
  document.body.append(container);
  containers.push(container);
  const errors: unknown[] = [];
  const wrapper = mount(ScheduleGantt, {
    props: { envelope, session },
    attachTo: container,
    global: { config: { errorHandler: (error) => errors.push(error) } },
  });
  const host = wrapper.get('.gantt').element as HTMLElement;
  const chart = getInstanceByDom(host)!;
  chart.resize({ width: 1000, height: 400 });
  return { wrapper, host, chart, errors };
}

describe('gantt colors and hover lifecycle', () => {
  it('shows optimization progress without waiting for a cooking boundary', async () => {
    const { wrapper, chart } = mounted();
    const before = chart.getOption().series;
    await wrapper.setProps({
      session: {
        ...session,
        requires_replan: true,
        dispatch_blocked: true,
        clock_progress: {
          enabled: true,
          started_at: '2026-10-08T08:00:00+08:00',
          current_offset_sec: 159,
          replan_requested_sec: 38,
          replan_not_before_sec: 2790,
          replan_not_before_at: '2026-10-08T08:46:30+08:00',
          waiting_for_boundary: true,
        },
      },
    });
    const notice = wrapper.get('[data-testid=gantt-replan-status]');
    expect(notice.text()).toContain('正在重排剩余操作');
    expect(notice.text()).not.toContain('最早重排时间');
    expect(notice.text()).toContain('当前仍显示 v1');
    expect(chart.getOption().series).toEqual(before);
    await wrapper.setProps({ historical: true });
    expect(wrapper.find('[data-testid=gantt-replan-status]').exists()).toBe(false);
  });
  it('lists assumed advance preparation separately and clears it for an older plan', async () => {
    const { wrapper } = mounted();
    expect(wrapper.find('[data-testid="advance-preparations"]').exists()).toBe(false);
    await wrapper.setProps({
      envelope: {
        ...envelope,
        presentation: {
          ...envelope.presentation,
          advance_preparations: [
            {
              preparation_id: 'prepared-a',
              recipe_id: 'recipe-a',
              recipe_instance_id: 'instance-a',
              recipe_name: '提前腌制合成菜',
              task_ids: ['before-start'],
              description: '食材提前冷藏腌制4小时',
              original_duration_sec: 14400,
              source_kind: 'USER_POLICY_ASSUMPTION',
            },
          ],
        },
      },
    });
    const checklist = wrapper.find('[data-testid="advance-preparations"]');
    expect(checklist.exists()).toBe(true);
    expect(checklist.get('summary').text()).toContain('开工前准备');
    await checklist.get('summary').trigger('click');
    expect(checklist.text()).toContain('按已提前备好排程，不计入本轮用时');
    expect(checklist.text()).toContain('提前腌制合成菜');
    expect(checklist.text()).toContain('食材提前冷藏腌制4小时');
    expect(checklist.text()).toContain('原准备时长：240.0 分钟');
    expect(wrapper.findAll('tbody tr')).toHaveLength(1);
    expect(wrapper.find('tbody').text()).not.toContain('已完成');
    await wrapper.setProps({ envelope });
    expect(wrapper.find('[data-testid="advance-preparations"]').exists()).toBe(false);
  });
  it('keeps both burner rows and their dish colors while a real pointer hovers a long block', async () => {
    const { wrapper, host, chart } = mounted();
    await wrapper.setProps({
      envelope: {
        ...envelope,
        presentation: {
          ...envelope.presentation,
          resource_lanes: ['灶眼一', '灶眼二'],
          resources: ['a', 'b'].map((id, i) => ({
            ...envelope.presentation.resources[0]!,
            entry_id: `burner-${id}`,
            resource_id: 'stove_1',
            component_id: `burner_${i + 1}`,
            resource_label: i === 0 ? '灶眼一' : '灶眼二',
            task_ids: [id],
            recipe_instance_ids: [`instance-${id}`],
            recipe_names: ['同名合成菜'],
            title: i === 0 ? '短时烹饪' : '长时烹饪',
            start_sec: i === 0 ? 200 : 0,
            end_sec: i === 0 ? 400 : 1200,
            shared: false,
          })),
        },
      },
    });
    await nextTick();
    chart.getZr().flush();
    const coloredBlocks = () =>
      [...host.querySelectorAll('svg path')]
        .filter((el) => ['#437fbd', '#cf783c'].includes(el.getAttribute('fill') ?? ''))
        .map((el) => ({ fill: el.getAttribute('fill'), shape: el.getAttribute('d') }));
    const before = coloredBlocks();
    expect(before).toHaveLength(2);
    const labels = [...host.querySelectorAll('svg text')].map((el) => el.textContent);
    expect(labels).toContain('灶眼一');
    expect(labels).toContain('灶眼二');
    const point = chart.convertToPixel({ seriesIndex: 0 }, [300, 1]);
    const zr = chart.getZr();
    zr.handler.dispatch('mousemove', { zrX: point[0], zrY: point[1] });
    zr.animation.update();
    zr.flush();
    expect(host.querySelector('.gantt-tooltip')?.textContent).toContain('长时烹饪');
    expect(coloredBlocks()).toEqual(before);
  });
  it('switches axes and removes old blocks through fifty unequal-row view changes', async () => {
    const { wrapper, host, chart, errors } = mounted();
    for (let i = 0; i < 50; i++) {
      const recipe = i % 2 === 0;
      await wrapper.get('[aria-label="甘特图分组"]').setValue(recipe ? 'recipe' : 'resource');
      await nextTick();
      chart.getZr().flush();
      expect(errors).toEqual([]);
      const labels = [...host.querySelectorAll('svg text')].map((el) => el.textContent);
      expect(labels.includes('人工')).toBe(!recipe);
      expect(labels.some((label) => label?.includes('同名合成菜'))).toBe(recipe);
      const series = chart.getOption().series as { data: unknown[] }[];
      expect(series).toHaveLength(1);
      expect(series[0]?.data).toHaveLength(recipe ? 2 : 1);
      const colored = [...host.querySelectorAll('svg path')].filter((el) =>
        ['#437fbd', '#cf783c'].includes(el.getAttribute('fill') ?? ''),
      );
      expect(colored).toHaveLength(2);
      chart.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: 0 });
      expect(host.querySelector('.gantt-tooltip')?.textContent).toContain(
        recipe ? '切配 a' : '共同切配',
      );
    }
  }, 20000);
  it('removes former shared-member rectangles when the next plan has fewer members', async () => {
    const { wrapper, host, chart, errors } = mounted();
    await wrapper.setProps({
      envelope: {
        ...envelope,
        presentation: {
          ...envelope.presentation,
          operations: envelope.presentation.operations.slice(0, 1),
          resources: envelope.presentation.resources.map((r) => ({
            ...r,
            recipe_instance_ids: ['instance-a'],
            recipe_names: ['同名合成菜'],
            task_ids: ['a'],
            shared: false,
          })),
        },
      },
    });
    await nextTick();
    chart.getZr().flush();
    expect(errors).toEqual([]);
    expect(host.querySelectorAll('svg path[fill="#cf783c"]')).toHaveLength(0);
    expect(host.querySelectorAll('svg path[fill="#437fbd"]')).toHaveLength(1);
  });
  it('uses visible dashed outlines for historical blocks', async () => {
    const { wrapper, host, chart } = mounted();
    await wrapper.setProps({ historical: true });
    await nextTick();
    chart.getZr().flush();
    const dashed = [...host.querySelectorAll('svg [stroke-dasharray]')];
    expect(dashed.length).toBeGreaterThan(0);
    expect(dashed.every((p) => p.getAttribute('stroke') !== '#fff')).toBe(true);
    expect(wrapper.get('[aria-label="菜品颜色图例"]').text()).toContain('同名合成菜');
  });
  it('keeps a dragged preset zoom window during polling and status updates', async () => {
    const { wrapper, chart } = mounted();
    await wrapper.setProps({
      envelope: { ...envelope, presentation: { ...envelope.presentation, range_end_sec: 7200 } },
    });
    await wrapper.get('[aria-label="时间缩放"]').setValue(1800);
    chart.dispatchAction({ type: 'dataZoom', startValue: 500, endValue: 1200 });
    const beforeZoom = chart.getOption().dataZoom;
    await wrapper.setProps({
      session: { ...session, runtime: { ...session.runtime, now_offset_sec: 1 } },
    });
    expect(chart.getOption().dataZoom).toEqual(beforeZoom);
    await wrapper.setProps({
      session: {
        ...session,
        runtime: {
          ...session.runtime,
          now_offset_sec: 2,
          executions: [
            {
              execution_id: 'running',
              task_ids: ['a'],
              started_task_ids: ['a'],
              status: 'RUNNING',
              source: 'MANUAL_CONFIRM',
              resource_ids: ['human_1'],
            },
          ],
        },
      },
    });
    expect(chart.getOption().dataZoom).toEqual(beforeZoom);
  });
  it('keeps a visible tooltip during unchanged one-second session polling', async () => {
    const { wrapper, host, chart } = mounted();
    chart.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: 0 });
    const tip =
      host.querySelector<HTMLElement>('.gantt-tooltip') ??
      host.querySelector<HTMLElement>('div[style*="position: absolute"]');
    expect(tip).not.toBeNull();
    expect(tip?.style.visibility).not.toBe('hidden');
    chart.dispatchAction({ type: 'dataZoom', start: 25, end: 75 });
    const beforeZoom = chart.getOption().dataZoom;
    await wrapper.setProps({
      session: { ...session, runtime: { ...session.runtime, now_offset_sec: 1 } },
    });
    await nextTick();
    expect(tip?.isConnected).toBe(true);
    expect(tip?.style.visibility).not.toBe('hidden');
    expect(tip?.style.opacity).not.toBe('0');
    expect(chart.getOption().dataZoom).toEqual(beforeZoom);
  });
  it('distinguishes same-name dishes and paints every shared member with its legend color', async () => {
    const { wrapper, host } = mounted();
    const swatches = wrapper.findAll('.gantt-recipe-swatch');
    expect(swatches).toHaveLength(2);
    const colors = swatches.map((s) => (s.element as HTMLElement).style.backgroundColor);
    expect(new Set(colors).size).toBe(2);
    expect(wrapper.get('[aria-label="菜品颜色图例"]').text()).toContain('recipe-a');
    for (const color of colors) {
      const probe = document.createElement('span');
      const fills = [...host.querySelectorAll('svg path')].map((path) => {
        probe.style.color = path.getAttribute('fill') ?? '';
        return probe.style.color;
      });
      expect(fills).toContain(color);
    }
    await wrapper.get('[aria-label="甘特图分组"]').setValue('recipe');
    expect(
      wrapper
        .findAll('.gantt-recipe-swatch')
        .map((s) => (s.element as HTMLElement).style.backgroundColor),
    ).toEqual(colors);
  });
  it('preserves recipe colors while recording actual completion and replacing the plan', async () => {
    const { wrapper, chart } = mounted();
    const before = wrapper
      .findAll('.gantt-recipe-swatch')
      .map((s) => (s.element as HTMLElement).style.backgroundColor);
    expect(before).toHaveLength(2);
    await wrapper.setProps({
      envelope: {
        ...envelope,
        presentation: {
          ...envelope.presentation,
          operations: [...envelope.presentation.operations].reverse(),
        },
      },
      session: {
        ...session,
        runtime: {
          ...session.runtime,
          executions: [
            {
              execution_id: 'completed',
              task_ids: ['a', 'b'],
              completed_task_ids: ['a', 'b'],
              status: 'COMPLETED',
              source: 'MANUAL_CONFIRM',
              resource_ids: ['human_1'],
            },
          ],
        },
      },
    });
    expect(
      wrapper
        .findAll('.gantt-recipe-swatch')
        .map((s) => (s.element as HTMLElement).style.backgroundColor),
    ).toEqual(before);
    chart.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: 0 });
    expect(wrapper.element.textContent).toContain('已完成');
  });
});
