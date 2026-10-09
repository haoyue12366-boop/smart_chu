import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import ExecutionView from '../../src/views/ExecutionView.vue';
import type { PlanEnvelope, RuntimeSession } from '../../src/api/types';
import { NotificationInbox } from '../../src/model/notifications';
import { deviceActions } from '../../src/model/execution';

describe('execution event safety', () => {
  it('keeps device recovery separate from clearing the concrete execution', () => {
    expect(
      deviceActions(
        {
          device_instance_id: 'toy-oven',
          availability_status: 'UNAVAILABLE',
          occupancy_status: 'OCCUPIED',
        },
        'toy-execution',
      ),
    ).toEqual(['recover', 'release']);
    expect(
      deviceActions(
        {
          device_instance_id: 'toy-oven',
          availability_status: 'AVAILABLE',
          occupancy_status: 'OCCUPIED',
        },
        'toy-execution',
      ),
    ).toEqual(['release']);
  });
  it('deduplicates replay and keeps old plan messages out of current instructions', () => {
    const inbox = new NotificationInbox();
    const message = {
      cursor: 10,
      notification_id: 'toy-n',
      event_id: 'toy-event',
      plan_version: 1,
      kind: 'START',
      text: '合成旧提醒',
      data: {},
    };
    expect(inbox.receive(message, 2)).toBe(false);
    expect(inbox.current(2)).toEqual([]);
    expect(
      inbox.receive(
        {
          ...message,
          cursor: 11,
          notification_id: 'toy-new',
          event_id: 'toy-new-event',
          plan_version: 2,
        },
        2,
      ),
    ).toBe(true);
    expect(
      inbox.receive(
        {
          ...message,
          cursor: 11,
          notification_id: 'toy-new',
          event_id: 'toy-new-event',
          plan_version: 2,
        },
        2,
      ),
    ).toBe(false);
    expect(inbox.current(2)).toHaveLength(1);
  });
  it('restores displayed reminders with the reconnect cursor after a hard reload', () => {
    const inbox = new NotificationInbox();
    const message = {
      cursor: 12,
      notification_id: 'toy-current',
      event_id: 'toy-current-event',
      plan_version: 2,
      kind: 'START',
      text: '合成当前提醒',
      data: {},
    };
    inbox.receive(message, 2);
    const restored = NotificationInbox.restore(inbox.serialize());
    expect(restored.cursor).toBe(12);
    expect(restored.current(2)).toEqual([message]);
    expect(restored.receive(message, 2)).toBe(false);
    expect(restored.current(3)).toEqual([]);
    expect(NotificationInbox.restore('broken JSON').cursor).toBe(0);
    expect(NotificationInbox.restore('{"cursor":12,"notices":[]}').cursor).toBe(0);
  });
  it('retains completed operations across replans and deduplicates completion replay', () => {
    const inbox = new NotificationInbox();
    const completed = {
      cursor: 15,
      notification_id: 'toy-completed',
      event_id: 'toy-completed-event',
      plan_version: 1,
      kind: 'OPERATION_COMPLETED',
      text: '合成菜：切配已完成（计划时钟推算）',
      data: { execution_id: 'toy-execution', source: 'SCHEDULE_CLOCK' },
    };
    expect(inbox.receive(completed, 2)).toBe(true);
    expect(inbox.current(2)).toEqual([completed]);
    const restored = NotificationInbox.restore(inbox.serialize());
    expect(restored.current(3)).toEqual([completed]);
    expect(restored.receive({ ...completed, cursor: 16 }, 3)).toBe(false);
    expect(restored.cursor).toBe(16);
    expect(restored.current(3)).toEqual([completed]);
  });
  it('offers cancellation only for an unstarted active recipe', () => {
    const session: RuntimeSession = {
      runtime: {
        session_id: 'toy-session',
        state_revision: 2,
        current_plan_version: 1,
        knowledge_version: 'toy',
        time_origin: { start_at: '2026-10-03T08:00:00+08:00' },
        now_offset_sec: 0,
        execution_mode: 'MANUAL_CONFIRM',
        executions: [
          {
            execution_id: 'toy-execution',
            task_ids: ['toy-started'],
            status: 'RUNNING',
            source: 'MANUAL_CONFIRM',
            started_at: '2026-10-03T08:00:00+08:00',
            resource_ids: [],
          },
        ],
        device_states: [],
        material_lots: [],
      },
      menu: [
        { recipe_id: 'toy-a', recipe_instance_id: 'toy-active', name: '合成已开始' },
        { recipe_id: 'toy-b', recipe_instance_id: 'toy-waiting', name: '合成未开始' },
      ],
      requires_replan: false,
      dispatch_blocked: false,
      last_planning_failure: null,
      replan_reasons: [],
    };
    const envelope: PlanEnvelope = {
      plan: {
        session_id: 'toy-session',
        plan_version: 1,
        parent_plan_version: 0,
        state_revision: 1,
        knowledge_version: 'toy',
        snapshot_id: 'toy',
        committed_at: '2026-10-03T08:00:00+08:00',
        validated: { candidate: { metrics: null } },
      },
      problem: {},
      presentation: {
        time_origin: session.runtime.time_origin,
        range_end_sec: 120,
        resources: [],
        operations: [
          {
            task_id: 'toy-started',
            carrier_id: 'toy-carrier-a',
            recipe_id: 'toy-a',
            recipe_instance_id: 'toy-active',
            recipe_name: '合成已开始',
            title: '合成操作',
            action: 'CUT',
            start_sec: 0,
            end_sec: 60,
            shared: false,
            frozen: false,
          },
          {
            task_id: 'toy-future',
            carrier_id: 'toy-carrier-b',
            recipe_id: 'toy-b',
            recipe_instance_id: 'toy-waiting',
            recipe_name: '合成未开始',
            title: '合成操作',
            action: 'CUT',
            start_sec: 60,
            end_sec: 120,
            shared: false,
            frozen: false,
          },
        ],
      },
    };
    const wrapper = mount(ExecutionView, {
      props: { session, envelope, catalog: null, busy: false, notices: [], clarification: null },
    });
    const actions = wrapper.findAll('.menu-action');
    expect(actions).toHaveLength(1);
    expect(actions[0]!.text()).toContain('合成未开始');
  });
});
