// 仅界面单元使用明确合成目录；浏览器接口流程将使用固定100菜真实后端。
import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import PlanningView from '../../src/views/PlanningView.vue';
import type { Catalog } from '../../src/api/types';

const catalog: Catalog = {
  knowledge_version: 'synthetic-ui-only',
  timezone: 'Asia/Shanghai',
  language_enabled: false,
  devices: [],
  recipes: [
    {
      recipe_id: 'toy-a',
      name: '同名合成菜',
      operation_count: 1,
      ingredient_names: ['合成原料甲'],
    },
    {
      recipe_id: 'toy-b',
      name: '同名合成菜',
      operation_count: 1,
      ingredient_names: ['合成原料乙'],
    },
  ],
};

describe('planning selection', () => {
  it('preserves two identities with the same name in one initial request', async () => {
    const wrapper = mount(PlanningView, {
      props: { catalog, busy: false, error: '', session: null },
    });
    const boxes = wrapper.findAll('input[type=checkbox]');
    await boxes[0]?.setValue(true);
    await boxes[1]?.setValue(true);
    await wrapper.get('[data-testid=create-plan]').trigger('click');
    const request = wrapper.emitted('create')?.[0]?.[0];
    expect(request).toEqual([
      { id: 'toy-a', name: '同名合成菜' },
      { id: 'toy-b', name: '同名合成菜' },
    ]);
    expect(wrapper.emitted('create')?.[0]?.[1]).toBe('SCHEDULE_CLOCK');
    expect(wrapper.text()).toContain('初次计划发布后自动计时');
    expect(wrapper.findAll('option').map((option) => option.element.value)).toEqual([
      'SCHEDULE_CLOCK',
      'MANUAL_CONFIRM',
      'SIMULATED',
    ]);
  });
  it('disables repeated submission while a request is pending', () => {
    const wrapper = mount(PlanningView, {
      props: { catalog, busy: true, error: '', session: null },
    });
    expect(wrapper.get('[data-testid=create-plan]').attributes('disabled')).toBeDefined();
  });
  it('shows unavailable data instead of a successful example meal', () => {
    const wrapper = mount(PlanningView, {
      props: { catalog: null, busy: false, error: '服务暂不可用', session: null },
    });
    expect(wrapper.text()).toContain('服务暂不可用');
    expect(wrapper.findAll('input[type=checkbox]')).toHaveLength(0);
  });
});
