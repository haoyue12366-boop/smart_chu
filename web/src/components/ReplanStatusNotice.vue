<script setup lang="ts">
import type { RuntimeSession } from '../api/types';

defineProps<{
  session: RuntimeSession;
  planVersion: number;
  testId?: string;
}>();
</script>

<template>
  <div
    v-if="session.requires_replan && !session.last_planning_failure"
    class="notice"
    :data-testid="testId ?? 'replan-status'"
    role="status"
  >
    <strong>正在重排剩余操作，当前操作继续。</strong>
    <p>已完成和执行中的操作保持原位，正在重新安排其他操作。</p>
    <p>当前仍显示 v{{ planVersion }} 计划；新计划发布后，甘特图会自动更新。</p>
  </div>
</template>
