import { mount } from '@vue/test-utils';
import { reactive } from 'vue';
import { expect, it } from 'vitest';
import FeedbackDialog from '../../src/components/FeedbackDialog.vue';
import type { FeedbackProposal } from '../../src/api/types';
it('keeps a reactive draft readonly until explicit confirmation and preserves unknown output', async () => {
  const proposal = reactive<FeedbackProposal>({
    requires_confirmation: true,
    notice: '合成草稿测试',
    source: 'MANUAL_CONFIRM',
    state_revision: 3,
    plan_version: 2,
    completed: true,
    task_ids: ['toy-task'],
    release_eligible: true,
    movement_names: {},
    payload: {
      task_id: 'toy-task',
      execution_id: 'toy-execution',
      consumed: [],
      produced: [],
      output_status: 'UNKNOWN',
      resource_release_status: 'UNCONFIRMED',
    },
  });
  const wrapper = mount(FeedbackDialog, { props: { proposal, title: '合成操作', busy: false } });
  expect(wrapper.get('button.primary').attributes('disabled')).toBeDefined();
  await wrapper.findAll('input[type=checkbox]')[1]?.setValue(true);
  await wrapper.get('button.primary').trigger('click');
  expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({
    output_status: 'UNKNOWN',
    resource_release_status: 'UNCONFIRMED',
  });
  expect(proposal.payload.resource_release_status).toBe('UNCONFIRMED');
});
it('keeps an empty quantity unconfirmed instead of silently submitting zero', async () => {
  const proposal: FeedbackProposal = {
    requires_confirmation: true,
    notice: '合成实际数量测试',
    source: 'MANUAL_CONFIRM',
    state_revision: 1,
    plan_version: 1,
    completed: false,
    task_ids: ['toy-task'],
    release_eligible: false,
    movement_names: { raw: '合成原料' },
    payload: {
      task_id: 'toy-task',
      execution_id: 'toy-execution',
      consumed: [
        { lot_id: 'toy-lot', spec_id: 'raw', quantity: { value: 10, scale: 1, unit: 'g' } },
      ],
      produced: [],
      output_status: 'UNKNOWN',
      resource_release_status: 'UNCONFIRMED',
    },
  };
  const wrapper = mount(FeedbackDialog, { props: { proposal, title: '合成操作', busy: false } });
  await wrapper.get('input[type=checkbox]').setValue(true);
  await wrapper.get('input[type=number]').setValue('');
  expect(wrapper.get('button.primary').attributes('disabled')).toBeDefined();
  await wrapper.get('button.primary').trigger('click');
  expect(wrapper.emitted('submit')).toBeUndefined();
  await wrapper.get('input[type=number]').setValue('0');
  expect(wrapper.get('button.primary').attributes('disabled')).toBeUndefined();
});
