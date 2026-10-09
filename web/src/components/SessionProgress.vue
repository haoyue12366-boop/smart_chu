<script setup lang="ts">
import { computed } from 'vue';
import type { PlanEnvelope, RuntimeSession } from '../api/types';
import { fullDate, taskStatus } from '../model/presentation';
import ReplanStatusNotice from './ReplanStatusNotice.vue';

const props = defineProps<{
  session: RuntimeSession;
  envelope: PlanEnvelope | null;
  busy: boolean;
  timezone?: string;
}>();
const emit = defineEmits<{ replan: [] }>();
const clock = computed(() => props.session.clock_progress);
const automatic = computed(() => props.session.runtime.execution_mode === 'SCHEDULE_CLOCK');
const operations = computed(() => props.envelope?.presentation.operations ?? []);
const running = computed(() =>
  operations.value.filter(
    (operation) => taskStatus([operation.task_id], props.session) === '执行中',
  ),
);
const completed = computed(
  () =>
    operations.value.filter(
      (operation) =>
        operation.inventory_supplied || taskStatus([operation.task_id], props.session) === '已完成',
    ).length,
);
const offset = computed(
  () => clock.value?.current_offset_sec ?? props.session.runtime.now_offset_sec,
);
</script>
<template>
  <section class="panel session-progress">
    <div class="section-heading">
      <h2>当前进度</h2>
      <span class="tag">{{
        automatic
          ? '计划时钟推算'
          : session.runtime.execution_mode === 'SIMULATED'
            ? '模拟反馈'
            : '实际反馈'
      }}</span>
    </div>
    <p v-if="automatic" class="muted">
      首次发布后自动推进；开始和完成由服务端计划时钟推算，实际情况可在执行与反馈中修正。
    </p>
    <div class="progress-values">
      <p data-testid="clock-progress">
        <span>{{ automatic ? '计划时钟' : '会话进度' }}</span>
        <strong>{{ Math.floor(offset / 60) }} 分 {{ offset % 60 }} 秒</strong>
        <small v-if="automatic && !clock?.started_at">等待首次计划发布后启动</small>
        <small v-else-if="automatic && clock?.started_at"
          >计时起点 {{ fullDate(clock.started_at, 0, timezone) }}</small
        >
      </p>
      <p data-testid="execution-progress">
        <span>完成操作（含库存满足）</span
        ><strong>{{ completed }} / {{ operations.length }}</strong>
        <progress
          :value="completed"
          :max="Math.max(1, operations.length)"
          aria-label="已完成操作进度"
        />
      </p>
    </div>
    <div data-testid="running-operations">
      <strong>当前执行</strong>
      <ul v-if="running.length" class="notice-list">
        <li v-for="operation in running" :key="operation.task_id">
          {{ operation.recipe_name }} · {{ operation.title }}
        </li>
      </ul>
      <p v-else class="muted">
        {{
          completed === operations.length && operations.length
            ? '本轮操作已完成。'
            : '当前没有执行中的操作。'
        }}
      </p>
    </div>
    <ReplanStatusNotice
      :session="session"
      :plan-version="envelope?.plan.plan_version ?? session.runtime.current_plan_version"
    />
    <button
      data-testid="replan-remaining"
      :disabled="busy || !envelope || (session.requires_replan && !session.last_planning_failure)"
      @click="emit('replan')"
    >
      {{ busy ? '正在处理…' : '重新优化剩余操作' }}
    </button>
  </section>
</template>
