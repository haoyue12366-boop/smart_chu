<script setup lang="ts">
import { computed } from 'vue';
import type { PlanEnvelope } from '../api/types';
import { fullDate } from '../model/presentation';
const props = defineProps<{
  before: PlanEnvelope | null;
  after: PlanEnvelope;
  timezone?: string;
}>();
const changes = computed(() => {
  const old = new Map(props.before?.presentation.operations.map((o) => [o.task_id, o]) ?? []);
  const next = new Map(props.after.presentation.operations.map((o) => [o.task_id, o]));
  return [...new Set([...old.keys(), ...next.keys()])].flatMap((id) => {
    const a = old.get(id);
    const b = next.get(id);
    if (
      a &&
      b &&
      a.start_sec === b.start_sec &&
      a.end_sec === b.end_sec &&
      a.carrier_id === b.carrier_id &&
      a.frozen === b.frozen
    )
      return [];
    return [
      {
        id,
        title: `${(b ?? a)!.recipe_name} · ${(b ?? a)!.title}`,
        kind: !a ? '新增' : !b ? '已移出' : b.frozen ? '执行事实已冻结' : '安排调整',
        oldTime:
          a && props.before
            ? fullDate(props.before.presentation.time_origin.start_at, a.start_sec, props.timezone)
            : '—',
        newTime: b
          ? fullDate(props.after.presentation.time_origin.start_at, b.start_sec, props.timezone)
          : '—',
      },
    ];
  });
});
</script>
<template>
  <section class="panel" data-testid="plan-diff">
    <div class="section-heading">
      <h2>计划变化</h2>
      <span class="tag"
        >v{{ before?.plan.plan_version ?? 0 }} → v{{ after.plan.plan_version }}</span
      >
    </div>
    <p v-if="!changes.length" class="muted">操作安排保持一致。</p>
    <ul class="change-list">
      <li v-for="change in changes" :key="change.id">
        <span class="tag">{{ change.kind }}</span> {{ change.title
        }}<small>{{ change.oldTime }} → {{ change.newTime }}</small>
      </li>
    </ul>
  </section>
</template>
