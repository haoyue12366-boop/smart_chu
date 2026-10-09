<script setup lang="ts">
import { defineAsyncComponent, ref, watch } from 'vue';
import type { PlanEnvelope, RuntimeSession } from '../api/types';
import { plan } from '../api/client';
const ScheduleGantt = defineAsyncComponent(() => import('../components/ScheduleGantt.vue'));
import PlanDiff from '../components/PlanDiff.vue';
const props = defineProps<{ session: RuntimeSession; timezone?: string }>();
const version = ref(props.session.runtime.current_plan_version);
const selected = ref<PlanEnvelope | null>(null);
const parent = ref<PlanEnvelope | null>(null);
const error = ref('');
const loading = ref(false);
let generation = 0;
async function read() {
  const token = ++generation;
  loading.value = true;
  error.value = '';
  try {
    const value = await plan(props.session.runtime.session_id, version.value);
    const previous = value.plan.parent_plan_version
      ? await plan(props.session.runtime.session_id, value.plan.parent_plan_version)
      : null;
    if (token === generation) {
      selected.value = value;
      parent.value = previous;
    }
  } catch (reason) {
    if (token === generation)
      error.value = reason instanceof Error ? reason.message : '无法读取历史。';
  } finally {
    if (token === generation) loading.value = false;
  }
}
watch(version, read, { immediate: true });
</script>
<template>
  <section class="panel">
    <div class="section-heading">
      <h2>计划历史</h2>
      <label
        >版本
        <select v-model.number="version" aria-label="历史计划版本">
          <option v-for="v in session.runtime.current_plan_version" :key="v" :value="v">
            v{{ v }}
          </option>
        </select></label
      >
    </div>
    <p class="muted">历史只读。查看其他版本会保留当前会话的计划和执行状态。</p>
    <p v-if="loading">正在读取…</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="selected" data-testid="history-version">
      v{{ selected.plan.plan_version }} · 发布于 {{ selected.plan.committed_at }}
    </p>
  </section>
  <template v-if="selected"
    ><PlanDiff :before="parent" :after="selected" :timezone="timezone" /><ScheduleGantt
      :envelope="selected"
      :session="session"
      historical
      :timezone="timezone"
  /></template>
</template>
