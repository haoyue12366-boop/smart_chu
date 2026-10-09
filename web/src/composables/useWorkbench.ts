import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue';
import * as api from '../api/client';
import { eventFor } from '../api/events';
import type {
  Catalog,
  EventRequest,
  Mode,
  Notice,
  PlanEnvelope,
  PlanningResult,
  RecipeChoice,
  RuntimeSession,
} from '../api/types';
import { NotificationInbox } from '../model/notifications';

type Pending = {
  path: string;
  body: object;
  kind: 'create' | 'event' | 'language' | 'competition' | 'competition-replan';
  sid?: string;
  headers?: Record<string, string>;
  taskId?: string;
};
export function useWorkbench() {
  const catalog = shallowRef<Catalog | null>(null);
  const session = shallowRef<RuntimeSession | null>(null);
  const currentPlan = shallowRef<PlanEnvelope | null>(null);
  const previousPlan = shallowRef<PlanEnvelope | null>(null);
  const busy = ref(false);
  const error = ref('');
  const resultText = ref('');
  const requestId = ref('');
  const pending = shallowRef<Pending | null>(null);
  const notices = ref<Notice[]>([]);
  const latestCompletion = shallowRef<Notice | null>(null);
  const competitionResult = ref('');
  const competitionTaskId = ref<string | null>(null);
  const clarification = ref<{ question: string; options: string[] } | null>(null);
  let inbox = new NotificationInbox();
  let stream: EventSource | undefined;
  let interval: ReturnType<typeof setInterval> | undefined;
  let refreshActive = false;
  let errorSource: 'action' | 'refresh' = 'action';
  let selectedSid: string | null = null;
  let awaitingPublication: { sid: string; version: number; retry?: Pending } | null = null;
  const stale = computed(
    () =>
      !!session.value &&
      ((session.value.dispatch_blocked && !session.value.requires_replan) ||
        !!session.value.last_planning_failure),
  );
  function failure(reason: unknown, source: 'action' | 'refresh' = 'action') {
    errorSource = source;
    error.value = reason instanceof Error ? reason.message : '操作没有完成，请查看当前状态。';
    requestId.value = reason instanceof api.ApiError ? reason.requestId : '';
  }
  function connect(sid: string, taskId: string | null) {
    stream?.close();
    inbox = NotificationInbox.restore(localStorage.getItem(`cook.inbox.${sid}`));
    notices.value = inbox.current(session.value?.runtime.current_plan_version ?? 0);
    stream = new EventSource(
      taskId
        ? `/api/competition/notifications/stream?task_id=${encodeURIComponent(taskId)}&after=${inbox.cursor}`
        : `/api/v1/sessions/${encodeURIComponent(sid)}/notifications/stream?after=${inbox.cursor}`,
    );
    stream.addEventListener('notification', (event: MessageEvent<string>) => {
      if (selectedSid !== sid) return;
      try {
        const message = JSON.parse(event.data) as Notice;
        const fresh = inbox.receive(message, session.value?.runtime.current_plan_version ?? 0);
        localStorage.setItem(`cook.inbox.${sid}`, inbox.serialize());
        notices.value = inbox.current(session.value?.runtime.current_plan_version ?? 0);
        if (fresh && message.kind === 'OPERATION_COMPLETED') latestCompletion.value = message;
        if (
          (fresh && message.kind === 'OPERATION_COMPLETED') ||
          (message.kind === 'PLAN_CHANGED' &&
            message.plan_version > (session.value?.runtime.current_plan_version ?? 0))
        )
          void refresh();
      } catch {
        failure(new Error('有一条通知无法读取，请刷新会话。'));
      }
    });
  }
  async function refresh(sid = selectedSid): Promise<void> {
    if (!sid || refreshActive) return;
    refreshActive = true;
    try {
      const value = await api.session(sid);
      if (sid !== selectedSid) return;
      if (session.value && value.runtime.state_revision < session.value.runtime.state_revision)
        return;
      const version = value.runtime.current_plan_version;
      if (
        version > 0 &&
        (currentPlan.value?.plan.plan_version !== version ||
          currentPlan.value.plan.session_id !== sid)
      ) {
        const next = await api.plan(sid, version);
        if (sid !== selectedSid) return;
        const loaded = currentPlan.value;
        currentPlan.value = next;
        previousPlan.value =
          loaded?.plan.session_id === sid &&
          loaded.plan.plan_version === next.plan.parent_plan_version
            ? loaded
            : null;
        // 当前计划与轮询不等待用于对比的历史版本。
        if (next.plan.parent_plan_version && !previousPlan.value)
          void api
            .plan(sid, next.plan.parent_plan_version)
            .then((previous) => {
              if (sid === selectedSid && currentPlan.value === next) previousPlan.value = previous;
            })
            .catch((reason: unknown) => {
              if (sid !== selectedSid || currentPlan.value !== next) return;
              failure(reason);
              error.value = `当前计划已更新；上一版本暂时无法读取：${error.value}`;
            });
      }
      session.value = value;
      notices.value = inbox.current(version);
      if (errorSource === 'refresh' && !pending.value) {
        error.value = '';
        requestId.value = '';
      }
      if (awaitingPublication?.sid === sid && value.last_planning_failure) {
        awaitingPublication = null;
        resultText.value = '';
      }
      if (
        awaitingPublication?.sid === sid &&
        !value.requires_replan &&
        !value.dispatch_blocked &&
        !value.last_planning_failure &&
        (version > awaitingPublication.version || awaitingPublication.retry)
      ) {
        if (!awaitingPublication.retry || !busy.value) {
          const accepted = awaitingPublication;
          awaitingPublication = null;
          if (accepted.retry) void send(accepted.retry);
          else resultText.value = '剩余操作的新计划已发布。';
        }
      }
    } catch (reason) {
      if (sid === selectedSid) failure(reason, 'refresh');
    } finally {
      refreshActive = false;
    }
  }
  async function attach(sid: string, taskId: string | null = null) {
    const changed = sid !== selectedSid || taskId !== competitionTaskId.value;
    if (sid !== selectedSid) {
      selectedSid = sid;
      session.value = null;
      currentPlan.value = null;
      previousPlan.value = null;
      latestCompletion.value = null;
      awaitingPublication = null;
      localStorage.setItem('cook.session', sid);
    }
    competitionTaskId.value = taskId;
    if (taskId) localStorage.setItem('cook.competitionTask', JSON.stringify({ sid, taskId }));
    else localStorage.removeItem('cook.competitionTask');
    if (changed) connect(sid, taskId);
    await refresh(sid);
  }
  async function attachTask(taskId: string) {
    const mapping = await api.request<{ session_id: string }>(
      `/api/v1/competition-tasks/${encodeURIComponent(taskId)}`,
    );
    await attach(mapping.session_id, taskId);
  }
  async function loadCompetitionTask(taskId: string) {
    if (busy.value || !taskId.trim()) return;
    error.value = '';
    if (taskId.trim() !== competitionTaskId.value) {
      competitionResult.value = '';
      resultText.value = '';
    }
    try {
      await attachTask(taskId.trim());
    } catch (reason) {
      failure(reason);
    }
  }
  async function send(write: Pending): Promise<void> {
    if (busy.value) return;
    if (pending.value && pending.value !== write) {
      error.value = '上一条请求的结果尚未确认，请先用原身份查询。';
      return;
    }
    busy.value = true;
    pending.value = write;
    error.value = '';
    requestId.value = '';
    clarification.value = null;
    resultText.value = '';
    if (write.kind === 'competition') competitionResult.value = '';
    try {
      if (write.kind === 'competition') {
        const response = await api.post<unknown>(write.path, write.body, write.headers);
        competitionResult.value = JSON.stringify(response, null, 2);
        if (write.taskId) await attachTask(write.taskId);
        resultText.value = '比赛接口已返回五字段结果。';
        pending.value = null;
      } else {
        const result = await api.post<
          | PlanningResult
          | { status: 'NEEDS_CLARIFICATION'; question?: string; options?: string[] }
          | { status: 'NOT_UNDERSTOOD'; question?: string; options?: string[] }
        >(write.path, write.body, write.headers);
        if (result.status === 'NEEDS_CLARIFICATION' || result.status === 'NOT_UNDERSTOOD') {
          clarification.value = {
            question: result.question ?? '请补充具体菜品或明确时间。',
            options: result.options ?? [],
          };
          resultText.value = '尚未执行，请澄清指令。';
          pending.value = null;
        } else {
          const sid = result.session_id ?? write.sid;
          if (write.kind === 'competition-replan' && write.taskId) await attachTask(write.taskId);
          else if (sid) await attach(sid, sid === selectedSid ? competitionTaskId.value : null);
          resultText.value =
            {
              PUBLISHED: '完整计划已发布。',
              NO_REPLAN: '反馈已保存。',
              PENDING: '请求已接受，正在重排剩余操作，当前操作继续。',
              FAILED: '事实已保存，本次未发布合法计划。',
              EVENT_REJECTED: result.event?.rejection_reason ?? '事件被拒绝，请核对最新状态。',
            }[result.status] ?? '请查看当前状态。';
          if (result.status === 'PENDING' && session.value)
            awaitingPublication = {
              sid: session.value.runtime.session_id,
              version: session.value.runtime.current_plan_version,
            };
          pending.value = null;
        }
      }
    } catch (reason) {
      if (
        write.kind === 'competition' &&
        write.taskId &&
        reason instanceof api.ApiError &&
        reason.code === 'PLANNING_PENDING'
      ) {
        try {
          await attachTask(write.taskId);
          if (session.value)
            awaitingPublication = {
              sid: session.value.runtime.session_id,
              version: session.value.runtime.current_plan_version,
              retry: write,
            };
          resultText.value = '加菜请求已接受，等待当前操作结束后优化；发布后自动查询原请求结果。';
        } catch (mappingReason) {
          failure(mappingReason);
        }
        return;
      }
      failure(reason);
      if (reason instanceof api.ApiError && reason.status >= 400 && reason.status < 500)
        pending.value = null;
      if (write.sid) await refresh(write.sid);
    } finally {
      busy.value = false;
    }
  }
  const create = (recipes: RecipeChoice[], mode: Mode) =>
    send({
      path: '/api/v1/sessions',
      kind: 'create',
      body: { recipes, mode, event_id: crypto.randomUUID() },
    });
  const event = (
    event_type: string,
    payload: object,
    versions?: { state_revision: number; plan_version: number },
  ) => {
    if (!session.value) return Promise.resolve();
    const sid = session.value.runtime.session_id;
    const body: EventRequest = eventFor(session.value, event_type, payload);
    if (versions) {
      body.expected_state_revision = versions.state_revision;
      body.base_plan_version = versions.plan_version;
    }
    return send({
      path: `/api/v1/sessions/${encodeURIComponent(sid)}/events`,
      body,
      kind: 'event',
      sid,
    });
  };
  const add = (recipe: RecipeChoice) => event('ADD_RECIPE', { recipes: [recipe] });
  const replan = () => {
    if (!session.value) return Promise.resolve();
    const sid = session.value.runtime.session_id;
    return send({
      path: `/api/v1/sessions/${encodeURIComponent(sid)}/replan`,
      body: {},
      kind: 'event',
      sid,
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    });
  };
  const retry = () => (pending.value ? send(pending.value) : Promise.resolve());
  const language = (text: string) => {
    if (!session.value) return Promise.resolve();
    const sid = session.value.runtime.session_id;
    return send({
      path: `/api/v1/sessions/${encodeURIComponent(sid)}/language-events`,
      kind: 'language',
      sid,
      body: {
        text,
        event_id: crypto.randomUUID(),
        expected_state_revision: session.value.runtime.state_revision,
        base_plan_version: session.value.runtime.current_plan_version,
      },
    });
  };
  const competition = (recipes: RecipeChoice[], taskId: string, key: string) =>
    send({
      kind: 'competition',
      taskId,
      path: `/api/competition/plan?task_id=${encodeURIComponent(taskId)}`,
      body: recipes,
      headers: { 'Idempotency-Key': key },
    });
  const competitionReplan = () => {
    if (!competitionTaskId.value || !session.value) return Promise.resolve();
    return send({
      kind: 'competition-replan',
      taskId: competitionTaskId.value,
      sid: session.value.runtime.session_id,
      path: `/api/competition/replan?task_id=${encodeURIComponent(competitionTaskId.value)}`,
      body: {},
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    });
  };
  const newMeal = () => {
    stream?.close();
    selectedSid = null;
    session.value = null;
    currentPlan.value = null;
    previousPlan.value = null;
    pending.value = null;
    notices.value = [];
    latestCompletion.value = null;
    competitionTaskId.value = null;
    competitionResult.value = '';
    awaitingPublication = null;
    resultText.value = '';
    error.value = '';
    localStorage.removeItem('cook.session');
    localStorage.removeItem('cook.competitionTask');
  };
  onMounted(async () => {
    try {
      catalog.value = await api.catalog();
    } catch (reason) {
      failure(reason);
    }
    const saved = localStorage.getItem('cook.session');
    let taskId: string | null = null;
    try {
      const association = JSON.parse(localStorage.getItem('cook.competitionTask') ?? 'null') as {
        sid?: string;
        taskId?: string;
      } | null;
      if (association?.sid === saved && typeof association.taskId === 'string')
        taskId = association.taskId;
    } catch {
      localStorage.removeItem('cook.competitionTask');
    }
    if (saved) await attach(saved, taskId);
    interval = setInterval(() => {
      if (!busy.value) void refresh();
    }, 1000);
  });
  onBeforeUnmount(() => {
    stream?.close();
    if (interval) clearInterval(interval);
  });
  return {
    catalog,
    session,
    currentPlan,
    previousPlan,
    busy,
    error,
    requestId,
    resultText,
    pending,
    stale,
    notices,
    latestCompletion,
    competitionResult,
    competitionTaskId,
    clarification,
    create,
    add,
    replan,
    event,
    retry,
    refresh,
    language,
    competition,
    competitionReplan,
    loadCompetitionTask,
    newMeal,
  };
}
