<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from 'vue';
import { useWorkbench } from './composables/useWorkbench';
import { graph as readGraph } from './api/client';
import type { RecipeGraph } from './api/types';
import { displayMinutes, fullDate } from './model/presentation';
import PlanningView from './views/PlanningView.vue';
import ExecutionView from './views/ExecutionView.vue';
import HistoryView from './views/HistoryView.vue';
const ScheduleGantt = defineAsyncComponent(() => import('./components/ScheduleGantt.vue'));
import PlanDiff from './components/PlanDiff.vue';
import CookingFinishSummary from './components/CookingFinishSummary.vue';
const KnowledgeGraph = defineAsyncComponent(() => import('./components/KnowledgeGraph.vue'));
import CompetitionPanel from './components/CompetitionPanel.vue';
import SessionProgress from './components/SessionProgress.vue';
const work = useWorkbench();
const {
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
} = work;
const tab = ref<'plan' | 'execute' | 'history' | 'competition'>('plan');
const recipeGraph = ref<RecipeGraph | null>(null);
const graphError = ref('');
const graphLoading = ref(false);
const graphOpen = ref(false);
const metrics = computed(() => currentPlan.value?.plan.validated.candidate.metrics);
const saving = computed(() => {
  const serial = currentPlan.value?.plan.serial_reference?.candidate.metrics?.makespan_sec;
  return serial !== undefined && serial !== null && metrics.value
    ? displayMinutes(Math.max(0, serial - metrics.value.makespan_sec))
    : '—';
});
async function showGraph(id: string) {
  graphOpen.value = true;
  graphLoading.value = true;
  recipeGraph.value = null;
  graphError.value = '';
  try {
    recipeGraph.value = await readGraph(id, session.value?.runtime.session_id);
  } catch (reason) {
    graphError.value = reason instanceof Error ? reason.message : '无法读取工艺。';
  } finally {
    graphLoading.value = false;
  }
}
</script>
<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#" @click.prevent="tab = 'plan'"
        ><span class="brand-mark">◷</span><span>烹饪工作台<small>把每一步安排清楚</small></span></a
      >
      <div class="header-actions">
        <span class="connection-dot" :class="{ offline: !catalog }" />
        {{ catalog ? '知识库已就绪' : '正在连接服务'
        }}<button
          v-if="session"
          :disabled="busy"
          @click="
            work.newMeal();
            tab = 'plan';
          "
        >
          新的一桌
        </button>
      </div>
    </header>
    <main>
      <div class="page-heading">
        <div>
          <p class="eyebrow">{{ session ? '这桌菜的安排' : '从一桌好菜开始' }}</p>
          <h1>{{ session ? `${session.menu.length} 道菜，按步推进` : '今天的厨房，有序开场' }}</h1>
          <p class="muted">
            {{
              session
                ? session.menu.map((r) => r.name).join(' · ')
                : '选择菜谱，发布计划后自动计时，随时反馈实际情况。'
            }}
          </p>
        </div>
        <div v-if="session" class="session-version" data-testid="session-version">
          <strong>v{{ session.runtime.current_plan_version }}</strong
          ><small
            >状态 {{ session.runtime.state_revision }} ·
            {{
              session.runtime.execution_mode === 'SCHEDULE_CLOCK'
                ? '计划时钟推算'
                : session.runtime.execution_mode === 'SIMULATED'
                  ? '模拟演示'
                  : '人工确认'
            }}</small
          >
        </div>
      </div>
      <nav class="tabs" aria-label="工作台页面">
        <button :class="{ active: tab === 'plan' }" @click="tab = 'plan'">安排</button
        ><button
          :disabled="!session"
          :class="{ active: tab === 'execute' }"
          @click="tab = 'execute'"
        >
          执行与反馈</button
        ><button
          :disabled="!currentPlan"
          :class="{ active: tab === 'history' }"
          @click="tab = 'history'"
        >
          历史版本</button
        ><button :class="{ active: tab === 'competition' }" @click="tab = 'competition'">
          接口测试
        </button>
      </nav>
      <div v-if="error" class="banner error" role="alert">
        <strong>{{ error }}</strong
        ><small v-if="requestId">请求 {{ requestId }}</small
        ><button v-if="pending" :disabled="busy" @click="work.retry()">用原请求身份重试</button
        ><button v-else :disabled="busy" @click="work.refresh()">刷新状态</button>
      </div>
      <p v-if="resultText" class="banner" role="status">
        {{ resultText
        }}<button v-if="pending && !error" :disabled="busy" @click="work.retry()">
          查询原请求结果
        </button>
      </p>
      <div
        v-if="latestCompletion"
        class="banner completion-banner"
        role="status"
        aria-live="polite"
        data-testid="completion-announcement"
      >
        <strong>{{ latestCompletion.text }}</strong>
        <button aria-label="关闭完成提示" @click="latestCompletion = null">关闭</button>
      </div>
      <div v-if="stale" class="banner warning" data-testid="stale-plan">
        <strong>新操作派发已暂停；上次发布的安排仅供核对。</strong>
        <p>
          {{
            session?.last_planning_failure
              ? session.last_planning_failure
              : '状态已改变，等待有效计划发布。'
          }}
        </p>
      </div>
      <SessionProgress
        v-if="session && tab !== 'competition'"
        :session="session"
        :envelope="currentPlan"
        :busy="busy"
        :timezone="catalog?.timezone"
        @replan="work.replan()"
      />
      <div v-if="currentPlan && metrics" class="metrics">
        <article>
          <small>预计结束</small
          ><strong>{{
            fullDate(
              currentPlan.presentation.time_origin.start_at,
              metrics.makespan_sec,
              catalog?.timezone,
            )
          }}</strong>
        </article>
        <article>
          <small>总流程</small
          ><strong>{{ displayMinutes(metrics.makespan_sec) }} <em>分钟</em></strong>
        </article>
        <article>
          <small>较串行节省</small><strong>{{ saving }} <em>分钟</em></strong>
        </article>
        <article>
          <small>{{
            metrics.cooking_finish_spread_sec == null ? '全流程完成差（旧版）' : '出锅时间差'
          }}</small
          ><strong
            >{{
              displayMinutes(metrics.cooking_finish_spread_sec ?? metrics.completion_spread_sec)
            }}
            <em>分钟</em></strong
          >
          <small v-if="metrics.cooking_finish_spread_sec != null">{{
            metrics.cooking_finish_spread_sec <= 300 ? '满足 5 分钟目标' : '超过 5 分钟目标'
          }}</small>
        </article>
        <article>
          <small>最长连续人工</small
          ><strong>{{ displayMinutes(metrics.max_continuous_human_sec) }} <em>分钟</em></strong>
        </article>
        <article data-testid="planning-overhead">
          <small>{{
            currentPlan.plan.planning_overhead?.kind === 'REPLAN'
              ? '系统重调度开销'
              : '系统初排开销'
          }}</small>
          <strong>{{ currentPlan.plan.planning_overhead?.elapsed_ms ?? '—' }} <em>毫秒</em></strong>
        </article>
      </div>
      <template v-if="tab === 'plan'"
        ><CookingFinishSummary
          v-if="currentPlan"
          :envelope="currentPlan"
          :timezone="catalog?.timezone" /><ScheduleGantt
          v-if="currentPlan"
          :envelope="currentPlan"
          :session="session"
          :timezone="catalog?.timezone"
          :historical="stale" /><PlanDiff
          v-if="currentPlan && previousPlan"
          :before="previousPlan"
          :after="currentPlan"
          :timezone="catalog?.timezone" /><PlanningView
          :catalog="catalog"
          :session="session"
          :busy="busy"
          :error="error"
          @create="work.create"
          @add="work.add"
          @graph="showGraph"
      /></template>
      <ExecutionView
        v-if="tab === 'execute' && session"
        :session="session"
        :envelope="currentPlan"
        :catalog="catalog"
        :busy="busy"
        :notices="notices"
        :clarification="clarification"
        @event="work.event"
        @language="work.language"
      />
      <HistoryView
        v-if="tab === 'history' && session && currentPlan"
        :key="session.runtime.session_id"
        :session="session"
        :timezone="catalog?.timezone"
      />
      <CompetitionPanel
        v-if="tab === 'competition'"
        :busy="busy"
        :response="competitionResult"
        :active-task-id="competitionTaskId"
        :session="session"
        :envelope="currentPlan"
        :notices="notices"
        :timezone="catalog?.timezone"
        @submit="work.competition"
        @load="work.loadCompetitionTask"
        @replan="work.competitionReplan"
      />
    </main>
    <footer>固定知识快照 · 计划经独立校验后发布 · 进度注明计划时钟推算或反馈来源</footer>
  </div>
  <div v-if="graphOpen" class="drawer-backdrop">
    <aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="graph-title">
      <div class="section-heading">
        <h2 id="graph-title">{{ recipeGraph?.name ?? '菜谱工艺' }}</h2>
        <button aria-label="关闭工艺图" @click="graphOpen = false">✕</button>
      </div>
      <p v-if="graphLoading">正在读取固定快照…</p>
      <p v-if="graphError" class="error" role="alert">{{ graphError }}</p>
      <KnowledgeGraph v-if="recipeGraph" :key="recipeGraph.recipe_id" :graph="recipeGraph" />
    </aside>
  </div>
</template>
