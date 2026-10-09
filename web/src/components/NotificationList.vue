<script setup lang="ts">
import type { Notice } from '../api/types';
defineProps<{ notices: Notice[] }>();
</script>
<template>
  <section class="panel">
    <h2>完成通知与提醒</h2>
    <p class="muted">保留最近 200 条通知；完成记录跨计划保留，失效的旧计划提醒不再展示。</p>
    <p v-if="!notices.length" class="muted">暂无通知，操作完成后会在这里显示。</p>
    <ul class="notice-list">
      <li
        v-for="notice in notices"
        :key="notice.notification_id"
        :data-notice="notice.notification_id"
      >
        <span class="tag">v{{ notice.plan_version }}</span> {{ notice.text }}
      </li>
    </ul>
  </section>
</template>
