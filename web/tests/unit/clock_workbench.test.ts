// 明确合成的 HTTP/SSE 替身，仅验证真实 Vue 界面消费契约，不替代后端整链验收。
import { mount, flushPromises } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../../src/App.vue';
import ExecutionView from '../../src/views/ExecutionView.vue';
import type { Notice, PlanEnvelope, RuntimeSession } from '../../src/api/types';

class NotificationStream {
  static opened: NotificationStream[] = [];
  listener?: (event: MessageEvent<string>) => void;
  closed = false;
  constructor(readonly url: string) {
    NotificationStream.opened.push(this);
  }
  addEventListener(_kind: string, listener: (event: MessageEvent<string>) => void) {
    this.listener = listener;
  }
  close() {
    this.closed = true;
  }
  receive(notice: Notice) {
    this.listener?.({ data: JSON.stringify(notice) } as MessageEvent<string>);
  }
}

const catalog = {
  knowledge_version: 'synthetic-clock-ui',
  timezone: 'Asia/Shanghai',
  language_enabled: false,
  recipes: [{ recipe_id: 'toy-recipe', name: '合成菜', ingredient_names: [], operation_count: 2 }],
  devices: [],
};

function state(version = 1, offset = 30, waiting = false): RuntimeSession {
  return {
    runtime: {
      session_id: 'toy-clock',
      state_revision: version + 1,
      current_plan_version: version,
      knowledge_version: 'synthetic-clock-ui',
      time_origin: { start_at: '2026-10-08T08:00:00+08:00' },
      now_offset_sec: offset,
      execution_mode: 'SCHEDULE_CLOCK',
      executions: [
        {
          execution_id: 'toy-execution',
          task_ids: ['toy-cut'],
          started_task_ids: ['toy-cut'],
          completed_task_ids: [],
          status: 'RUNNING',
          source: 'SCHEDULE_CLOCK',
          started_at: '2026-10-08T08:00:00+08:00',
          resource_ids: [],
        },
      ],
      device_states: [],
      material_lots: [],
    },
    menu: [{ recipe_id: 'toy-recipe', recipe_instance_id: 'toy-instance', name: '合成菜' }],
    requires_replan: waiting,
    dispatch_blocked: waiting,
    last_planning_failure: null,
    replan_reasons: waiting ? ['REPLAN_REQUESTED'] : [],
    clock_progress: {
      enabled: true,
      started_at: '2026-10-08T08:00:00+08:00',
      current_offset_sec: offset,
      replan_requested_sec: waiting ? 30 : null,
      replan_not_before_sec: null,
      replan_not_before_at: null,
      waiting_for_boundary: false,
    },
  };
}

function plan(version = 1): PlanEnvelope {
  return {
    plan: {
      session_id: 'toy-clock',
      plan_version: version,
      parent_plan_version: version - 1,
      state_revision: version,
      knowledge_version: 'synthetic-clock-ui',
      snapshot_id: 'toy',
      committed_at: '2026-10-08T08:00:00+08:00',
      planning_overhead: {
        kind: version === 1 ? 'INITIAL' : 'REPLAN',
        elapsed_ms: version === 1 ? 1234 : 567,
      },
      validated: {
        candidate: {
          metrics: {
            makespan_sec: 120,
            completion_spread_sec: 0,
            max_continuous_human_sec: 60,
            serial_reference_sec: null,
            total_human_work_sec: 60,
          },
        },
      },
    },
    problem: {},
    presentation: {
      time_origin: { start_at: '2026-10-08T08:00:00+08:00' },
      range_end_sec: 120,
      resources: [],
      operations: [
        {
          task_id: 'toy-cut',
          carrier_id: 'toy-carrier-a',
          recipe_id: 'toy-recipe',
          recipe_instance_id: 'toy-instance',
          recipe_name: '合成菜',
          title: '切配',
          action: 'CUT',
          start_sec: 0,
          end_sec: 60,
          shared: false,
          frozen: version > 1,
        },
        {
          task_id: 'toy-cook',
          carrier_id: 'toy-carrier-b',
          recipe_id: 'toy-recipe',
          recipe_instance_id: 'toy-instance',
          recipe_name: '合成菜',
          title: '烹饪',
          action: 'HEAT',
          start_sec: 60,
          end_sec: 120,
          shared: false,
          frozen: false,
        },
      ],
    },
  };
}

const completion: Notice = {
  cursor: 7,
  notification_id: 'toy-notice',
  event_id: 'toy-complete',
  plan_version: 1,
  kind: 'OPERATION_COMPLETED',
  text: '合成菜：切配已完成（计划时钟推算）',
  data: {
    source: 'SCHEDULE_CLOCK',
    task_ids: ['toy-cut'],
    execution_id: 'toy-execution',
    completed_at: '2026-10-08T08:01:00+08:00',
  },
};

let current: RuntimeSession;
let writes: { path: string; body: Record<string, unknown>; headers?: HeadersInit }[];
let competitionPending: boolean;
let rejectEvent: boolean;
const fiveFields = {
  overview: {},
  cookingTimeline: [],
  ingredientsSummary: [],
  detailTimeline: [],
  recipeDetail: [],
};
function mountApp() {
  return mount(App, { global: { stubs: { ScheduleGantt: true, KnowledgeGraph: true } } });
}

beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
  localStorage.setItem('cook.session', 'toy-clock');
  NotificationStream.opened = [];
  current = state();
  writes = [];
  competitionPending = false;
  rejectEvent = false;
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date', 'performance'] });
  vi.setSystemTime('2026-10-08T00:00:30Z');
  vi.stubGlobal('EventSource', NotificationStream);
  vi.stubGlobal('fetch', async (input: string, init?: RequestInit) => {
    const path = String(input).split('?')[0]!;
    if (init?.method === 'POST') {
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      writes.push({ path: String(input), body, headers: init.headers });
      if (path.startsWith('/api/competition/plan')) {
        if (competitionPending) {
          current = state(1, 30, true);
          return Response.json(
            { error: { code: 'PLANNING_PENDING', message: '正在计算新计划' } },
            { status: 503 },
          );
        }
        return Response.json(fiveFields);
      }
      if (rejectEvent) {
        current = state(1, 31);
        current.runtime.state_revision = 7;
        rejectEvent = false;
        return Response.json(
          {
            session_id: 'toy-clock',
            status: 'EVENT_REJECTED',
            event: {
              status: 'REJECTED',
              first_applied: false,
              rejection_reason: '状态版本已变化，请刷新后确认。',
            },
          },
          { status: 409 },
        );
      }
      current = state(1, 30, true);
      return Response.json(
        {
          session_id: 'toy-clock',
          status: 'PENDING',
          replan_requested_sec: 30,
          replan_not_before_sec: 60,
          event: { status: 'ACCEPTED', first_applied: true },
        },
        { status: 202 },
      );
    }
    if (path === '/api/v1/recipes') return Response.json(catalog);
    if (path === '/api/v1/sessions/toy-clock') return Response.json(current);
    if (path.startsWith('/api/v1/competition-tasks/'))
      return Response.json({ session_id: 'toy-clock', task_id: 'toy/task', profile: {} });
    if (path.includes('/plans/')) return Response.json(plan(Number(path.split('/').at(-1))));
    throw new Error(`未定义的合成请求 ${path}`);
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('authoritative plan clock workbench', () => {
  it('uses healthy SSE to avoid per-second reads while the display clock advances locally', async () => {
    const originalFetch = globalThis.fetch;
    const reads = vi.fn((input: string, init?: RequestInit) => originalFetch(input, init));
    vi.stubGlobal('fetch', reads);
    const wrapper = mountApp();
    await flushPromises();
    const stream = NotificationStream.opened[0] as unknown as { onopen?: () => void };
    expect(stream.onopen).toBeTypeOf('function');
    stream.onopen?.();
    await flushPromises();
    const before = reads.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10_000);
    await flushPromises();
    expect(reads.mock.calls.length).toBe(before);
    expect(wrapper.get('[data-testid=clock-progress]').text()).toContain('0 分 40 秒');
    expect(current.runtime.now_offset_sec).toBe(30);
    wrapper.unmount();
  });
  it('resynchronizes on SSE reconnect without waiting for the slow healthy-stream timer', async () => {
    const wrapper = mountApp();
    await flushPromises();
    const stream = NotificationStream.opened[0] as unknown as {
      onopen?: () => void;
      onerror?: () => void;
    };
    stream.onopen?.();
    await flushPromises();
    stream.onerror?.();
    current = state(2, 61);
    stream.onopen?.();
    await flushPromises();
    expect(wrapper.get('[data-testid=session-version]').text()).toContain('v2');
    wrapper.unmount();
  });
  it('distinguishes the outstanding request from loading its published plan', async () => {
    const wrapper = mountApp();
    await flushPromises();
    const originalFetch = globalThis.fetch;
    let accept!: (value: Response) => void;
    let publish!: (value: Response) => void;
    const response = new Promise<Response>((resolve) => {
      accept = resolve;
    });
    const display = new Promise<Response>((resolve) => {
      publish = resolve;
    });
    vi.stubGlobal('fetch', (input: string, init?: RequestInit) => {
      if (init?.method === 'POST') return response;
      if (String(input).split('?')[0]?.endsWith('/plans/2')) return display;
      return originalFetch(input, init);
    });
    await wrapper.get('[data-testid=replan-remaining]').trigger('click');
    expect(wrapper.get('[data-testid=request-phase]').attributes('data-phase')).toBe('REQUESTING');
    current = state(2, 61);
    accept(Response.json({ status: 'PUBLISHED', session_id: 'toy-clock', plan: plan(2).plan }));
    await flushPromises();
    expect(wrapper.get('[data-testid=request-phase]').attributes('data-phase')).toBe(
      'WAITING_PUBLICATION',
    );
    expect(wrapper.get('[data-testid=session-version]').text()).toContain('v1');
    publish(Response.json(plan(2)));
    await flushPromises();
    expect(wrapper.get('[data-testid=request-phase]').attributes('data-phase')).toBe('SUCCEEDED');
    expect(wrapper.get('[data-testid=session-version]').text()).toContain('v2');
    wrapper.unmount();
  });
  it('keeps manual progress authoritative while local display timers run', async () => {
    current.runtime.execution_mode = 'MANUAL_CONFIRM';
    current.clock_progress = undefined;
    const wrapper = mountApp();
    await flushPromises();
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    expect(wrapper.get('[data-testid=clock-progress]').text()).toContain('0 分 30 秒');
    wrapper.unmount();
  });
  it('keeps an addition selected during clock updates and submits a menu command', async () => {
    const originalFetch = globalThis.fetch;
    vi.stubGlobal('fetch', (input: string, init?: RequestInit) =>
      String(input) === '/api/v1/recipes'
        ? Promise.resolve(
            Response.json({
              ...catalog,
              recipes: [
                ...catalog.recipes,
                {
                  recipe_id: 'toy-addition',
                  name: '合成追加菜',
                  ingredient_names: [],
                  operation_count: 1,
                },
              ],
            }),
          )
        : originalFetch(input, init),
    );
    const wrapper = mountApp();
    await flushPromises();
    await wrapper.get('input[value="toy-addition"]').setValue(true);
    current = { ...state(1, 31), runtime: { ...state(1, 31).runtime, state_revision: 8 } };
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(wrapper.get<HTMLInputElement>('input[value="toy-addition"]').element.checked).toBe(true);
    await wrapper.get('[data-testid=create-plan]').trigger('click');
    await flushPromises();
    expect(writes[0]?.path).toBe('/api/v1/sessions/toy-clock/recipes');
    expect(writes[0]?.body).toMatchObject({
      base_plan_version: 1,
      recipes: [{ id: 'toy-addition', name: '合成追加菜' }],
    });
    expect(writes[0]?.body.expected_state_revision).toBeUndefined();
    expect(writes[0]?.body.event_id).toBeTruthy();
    wrapper.unmount();
  });
  it('clears a recovered state-read error without keeping a stale red banner', async () => {
    const realFetch = globalThis.fetch;
    let offline = true;
    vi.stubGlobal('fetch', (input: string, init?: RequestInit) =>
      offline && String(input).split('?')[0] === '/api/v1/sessions/toy-clock'
        ? Promise.resolve(
            Response.json(
              { error: { code: 'NOT_FOUND', message: '临时读取失败' } },
              { status: 404 },
            ),
          )
        : realFetch(input, init),
    );
    const wrapper = mountApp();
    await flushPromises();
    expect(wrapper.text()).toContain('临时读取失败');
    offline = false;
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(wrapper.get('[data-testid=session-version]').text()).toContain('v1');
    expect(wrapper.text()).not.toContain('临时读取失败');
    wrapper.unmount();
  });
  it('replaces a pending-success message when background replanning fails', async () => {
    const wrapper = mountApp();
    await flushPromises();
    await wrapper.get('[data-testid=replan-remaining]').trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('请求已接受');
    current = {
      ...state(1, 35),
      requires_replan: true,
      dispatch_blocked: true,
      last_planning_failure: '最终结果超过请求截止时间',
    };
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(wrapper.text()).toContain('最终结果超过请求截止时间');
    expect(wrapper.get('[data-testid=request-phase]').attributes('data-phase')).toBe('FAILED');
    expect(wrapper.text()).not.toContain('请求已接受，正在重排剩余操作，当前操作继续。');
    wrapper.unmount();
  });
  it('does not erase an unresolved write failure when state reads recover', async () => {
    const realFetch = globalThis.fetch;
    vi.stubGlobal('fetch', (input: string, init?: RequestInit) =>
      init?.method === 'POST'
        ? Promise.resolve(
            Response.json(
              { error: { code: 'SERVICE_NOT_READY', message: '本次写入结果尚未确认' } },
              { status: 503 },
            ),
          )
        : realFetch(input, init),
    );
    const wrapper = mountApp();
    await flushPromises();
    await wrapper.get('[data-testid=replan-remaining]').trigger('click');
    await flushPromises();
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(wrapper.text()).toContain('本次写入结果尚未确认');
    expect(wrapper.get('[data-testid=session-version]').text()).toContain('v1');
    wrapper.unmount();
  });
  it('shows persisted system overhead for initial and updated plans', async () => {
    const wrapper = mountApp();
    await flushPromises();
    expect(wrapper.get('[data-testid=planning-overhead]').text()).toContain('系统初排开销');
    expect(wrapper.get('[data-testid=planning-overhead]').text()).toContain('1234');
    current = state(2, 45);
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(wrapper.get('[data-testid=planning-overhead]').text()).toContain('系统重调度开销');
    expect(wrapper.get('[data-testid=planning-overhead]').text()).toContain('567');
  });
  it('explains immediate optimization without waiting for active cooking to finish', async () => {
    current = state(1, 159, true);
    const wrapper = mountApp();
    await flushPromises();
    const notice = wrapper.get('[data-testid=replan-status]');
    expect(notice.text()).toContain('正在重排剩余操作');
    expect(notice.text()).toContain('当前操作继续');
    expect(notice.text()).toContain('当前仍显示 v1');
    expect(notice.text()).toContain('自动更新');
    expect(notice.text()).not.toContain('最早重排时间');
    expect(wrapper.get('[data-testid=addition-waiting]').text()).toContain('正在重排');
    expect(wrapper.get('[data-testid=create-plan]').text()).toBe('已接收，正在重排');
    await wrapper.get('input[type=checkbox]').setValue(true);
    expect(wrapper.get('[data-testid=create-plan]').text()).toBe('追加并重排');
    current = state(2, 159);
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(wrapper.find('[data-testid=replan-status]').exists()).toBe(false);
    expect(wrapper.find('[data-testid=addition-waiting]').exists()).toBe(false);
    expect(wrapper.get('[data-testid=session-version]').text()).toContain('v2');
    expect(wrapper.get('[data-testid=running-operations]').text()).toContain('合成菜 · 切配');
  }, 15000);

  it('publishes current plan and keeps polling while optional parent history is slow or fails', async () => {
    const wrapper = mountApp();
    await flushPromises();
    const originalFetch = globalThis.fetch;
    let rejectHistory!: (reason: Error) => void;
    const history = new Promise<Response>((_resolve, reject) => {
      rejectHistory = reject;
    });
    vi.stubGlobal('fetch', async (input: string, init?: RequestInit) => {
      const path = String(input).split('?')[0]!;
      if (path.endsWith('/plans/2')) return history;
      if (path.endsWith('/plans/3')) {
        const value = plan(3);
        value.presentation.operations[1]!.title = '重新安排烹饪';
        return Response.json(value);
      }
      return originalFetch(input, init);
    });
    current = state(3, 61);
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(wrapper.get('[data-testid=session-version]').text()).toContain('v3');
    current = state(3, 62);
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(wrapper.get('[data-testid=clock-progress]').text()).toContain('2 秒');
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '执行与反馈')!
      .trigger('click');
    expect(wrapper.getComponent(ExecutionView).text()).toContain('重新安排烹饪');
    rejectHistory(new Error('历史接口临时不可用'));
    await flushPromises();
    expect(wrapper.get('[data-testid=session-version]').text()).toContain('v3');
    expect(wrapper.getComponent(ExecutionView).text()).toContain('重新安排烹饪');
  });

  it('shows active server executions while a requested replan computes, then reflects publication', async () => {
    const wrapper = mountApp();
    await flushPromises();
    expect(wrapper.get('[data-testid=clock-progress]').text()).toContain('30 秒');
    expect(wrapper.get('[data-testid=running-operations]').text()).toContain('合成菜 · 切配');
    await wrapper.get('[data-testid=replan-remaining]').trigger('click');
    await flushPromises();
    expect(wrapper.get('[data-testid=request-phase]').attributes('data-phase')).toBe('SOLVING');
    expect(writes[0]?.path).toBe('/api/v1/sessions/toy-clock/replan');
    expect(writes[0]?.body).toEqual({});
    expect(writes[0]?.headers).toHaveProperty('Idempotency-Key');
    expect(wrapper.get('[data-testid=replan-status]').text()).toContain('正在重排剩余操作');
    expect(wrapper.get('[data-testid=replan-status]').text()).toContain('当前操作继续');
    expect(wrapper.find('[data-testid=stale-plan]').exists()).toBe(false);
    expect(wrapper.get('[data-testid=replan-remaining]').attributes('disabled')).toBeDefined();
    expect(wrapper.text()).not.toContain('完整计划已发布');
    current = state(2, 61);
    current.runtime.executions[0]!.status = 'COMPLETED';
    current.runtime.executions[0]!.completed_task_ids = ['toy-cut'];
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(wrapper.get('[data-testid=session-version]').text()).toContain('v2');
    expect(wrapper.find('[data-testid=replan-status]').exists()).toBe(false);
    expect(wrapper.get('[data-testid=execution-progress]').text()).toContain('1 / 2');
    expect(wrapper.text()).toContain('剩余操作的新计划已发布');
    expect(wrapper.get('[data-testid=request-phase]').attributes('data-phase')).toBe('SUCCEEDED');
    expect(wrapper.findAll('button').some((button) => button.text() === '查询原请求结果')).toBe(
      false,
    );
  });

  it('uses server progress even when the browser clock or schedule time has advanced', async () => {
    const wrapper = mountApp();
    await flushPromises();
    vi.setSystemTime('2026-10-08T05:00:00Z');
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(wrapper.get('[data-testid=clock-progress]').text()).toContain('30 秒');
    current = state(1, 100);
    current.runtime.executions = [];
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(wrapper.get('[data-testid=execution-progress]').text()).toContain('0 / 2');
  });

  it('refreshes a rejected event before a user retries and never silently rewrites its version', async () => {
    rejectEvent = true;
    const wrapper = mountApp();
    await flushPromises();
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '执行与反馈')!
      .trigger('click');
    wrapper.getComponent(ExecutionView).vm.$emit('event', 'DURATION_UPDATED', {
      execution_id: 'toy-execution',
      task_id: 'toy-cut',
      remaining_sec: 10,
    });
    await flushPromises();
    expect(wrapper.text()).toContain('状态版本已变化');
    expect(wrapper.get('[data-testid=session-version]').text()).toContain('状态 7');
    expect(writes).toHaveLength(1);
    expect(writes[0]?.body.expected_state_revision).toBe(2);
    wrapper.getComponent(ExecutionView).vm.$emit('event', 'DURATION_UPDATED', {
      execution_id: 'toy-execution',
      task_id: 'toy-cut',
      remaining_sec: 10,
    });
    await flushPromises();
    expect(writes[1]?.body.expected_state_revision).toBe(7);
    expect(writes[1]?.body.event_id).not.toBe(writes[0]?.body.event_id);
  });

  it('loads a newly published plan from its notification before the next polling tick', async () => {
    const wrapper = mountApp();
    await flushPromises();
    current = state(2, 61);
    NotificationStream.opened.at(-1)!.receive({
      ...completion,
      cursor: 9,
      notification_id: 'toy-publish',
      event_id: 'toy-publish-event',
      kind: 'PLAN_CHANGED',
      plan_version: 2,
      text: '新计划已发布',
    });
    await flushPromises();
    expect(wrapper.get('[data-testid=session-version]').text()).toContain('v2');
  });

  it('announces each completion once, retains it after replan and restores the reconnect cursor', async () => {
    current = state(2, 61);
    const wrapper = mountApp();
    await flushPromises();
    NotificationStream.opened.at(-1)!.receive(completion);
    await flushPromises();
    expect(wrapper.get('[data-testid=completion-announcement]').text()).toContain(completion.text);
    await wrapper.get('[aria-label=关闭完成提示]').trigger('click');
    NotificationStream.opened.at(-1)!.receive({ ...completion, cursor: 8 });
    await flushPromises();
    expect(wrapper.find('[data-testid=completion-announcement]').exists()).toBe(false);
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '执行与反馈')!
      .trigger('click');
    expect(wrapper.findAll('[data-notice=toy-notice]')).toHaveLength(1);
    wrapper.unmount();
    const restored = mountApp();
    await flushPromises();
    expect(NotificationStream.opened.at(-1)!.url).toContain('after=8');
    await restored
      .findAll('button')
      .find((button) => button.text() === '执行与反馈')!
      .trigger('click');
    expect(restored.findAll('[data-notice=toy-notice]')).toHaveLength(1);
    expect(restored.find('[data-testid=completion-announcement]').exists()).toBe(false);
  });

  it('attaches the competition task stream and presents actual progress, notices and replan', async () => {
    const wrapper = mountApp();
    await flushPromises();
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '接口测试')!
      .trigger('click');
    await wrapper.get('[aria-label=比赛任务标识]').setValue('toy/task');
    await wrapper.get('[aria-label=比赛幂等键]').setValue('toy-request');
    await wrapper
      .get('[aria-label=比赛请求数组]')
      .setValue('[{"id":"toy-recipe","name":"合成菜"}]');
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(JSON.parse(wrapper.get('[data-testid=competition-response]').text())).toEqual(
      fiveFields,
    );
    expect(NotificationStream.opened.at(-1)!.url).toBe(
      '/api/competition/notifications/stream?task_id=toy%2Ftask&after=0',
    );
    expect(wrapper.get('[data-testid=competition-progress]').text()).toContain('计划时钟推算');
    NotificationStream.opened.at(-1)!.receive(completion);
    await flushPromises();
    expect(wrapper.get('[data-testid=competition-notifications]').text()).toContain(
      completion.text,
    );
    await wrapper.get('[data-testid=replan-remaining]').trigger('click');
    await flushPromises();
    expect(writes.at(-1)?.path).toBe('/api/competition/replan?task_id=toy%2Ftask');
    expect(writes.at(-1)?.body).toEqual({});
    expect(writes.at(-1)?.headers).toHaveProperty('Idempotency-Key');
    expect(wrapper.get('[data-testid=replan-status]').text()).toContain('正在重排剩余操作');
  });

  it('keeps an accepted competition addition pending until the new plan publishes and retrieves it by the same identity', async () => {
    competitionPending = true;
    const wrapper = mountApp();
    await flushPromises();
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '接口测试')!
      .trigger('click');
    await wrapper.get('[aria-label=比赛任务标识]').setValue('toy/task');
    await wrapper.get('[aria-label=比赛幂等键]').setValue('toy-addition');
    await wrapper
      .get('[aria-label=比赛请求数组]')
      .setValue('[{"id":"toy-recipe","name":"合成菜"}]');
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(wrapper.get('[data-testid=replan-status]').text()).toContain('当前操作继续');
    expect(wrapper.find('[role=alert]').exists()).toBe(false);
    expect(wrapper.find('[data-testid=competition-response]').exists()).toBe(false);
    competitionPending = false;
    current = state(2, 61);
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(JSON.parse(wrapper.get('[data-testid=competition-response]').text())).toEqual(
      fiveFields,
    );
    expect(writes).toHaveLength(2);
    expect(writes[1]).toEqual(writes[0]);
  });

  it('clears the previous five-field response when connecting a different competition task', async () => {
    const wrapper = mountApp();
    await flushPromises();
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '接口测试')!
      .trigger('click');
    await wrapper.get('[aria-label=比赛任务标识]').setValue('toy/task');
    await wrapper.get('[aria-label=比赛幂等键]').setValue('toy-request');
    await wrapper
      .get('[aria-label=比赛请求数组]')
      .setValue('[{"id":"toy-recipe","name":"合成菜"}]');
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(wrapper.find('[data-testid=competition-response]').exists()).toBe(true);
    await wrapper.get('[aria-label=比赛任务标识]').setValue('toy-other');
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '查看该任务进度')!
      .trigger('click');
    await flushPromises();
    expect(wrapper.get('[data-testid=competition-progress]').text()).toContain('toy-other');
    expect(wrapper.find('[data-testid=competition-response]').exists()).toBe(false);
  });
});
