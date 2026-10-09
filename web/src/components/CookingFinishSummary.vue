<script setup lang="ts">
import { computed } from 'vue';
import type { PlanEnvelope } from '../api/types';
import { fullDate } from '../model/presentation';
const props = defineProps<{ envelope: PlanEnvelope; timezone?: string }>();
const finishes = computed(
  () => props.envelope.plan.validated.candidate.metrics?.recipe_cooking_finishes ?? [],
);
const names = computed(
  () =>
    new Map(
      props.envelope.presentation.operations.map((op) => [op.recipe_instance_id, op.recipe_name]),
    ),
);
</script>
<template>
  <details v-if="finishes.length" class="panel" data-testid="cooking-finishes">
    <summary>各菜预计出锅时间</summary>
    <p class="muted">
      按菜谱出锅工序完成计算；冷却、装盘等后续步骤仍按完整流程执行。取出与调味合并的工序按整段结束计。
    </p>
    <table>
      <thead>
        <tr>
          <th>菜品</th>
          <th>预计出锅</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in finishes" :key="item.recipe_instance_id">
          <td>{{ names.get(item.recipe_instance_id) ?? item.recipe_instance_id }}</td>
          <td>
            {{
              fullDate(
                envelope.presentation.time_origin.start_at,
                item.cooking_finish_sec,
                timezone,
              )
            }}
          </td>
        </tr>
      </tbody>
    </table>
  </details>
</template>
