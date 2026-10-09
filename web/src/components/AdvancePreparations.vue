<script setup lang="ts">
import type { AdvancePreparationPresentation } from '../api/types';
import { displayMinutes } from '../model/presentation';

defineProps<{ preparations: AdvancePreparationPresentation[] }>();
</script>

<template>
  <details v-if="preparations.length" data-testid="advance-preparations">
    <summary>开工前准备（{{ preparations.length }} 项）</summary>
    <p class="muted">按已提前备好排程，不计入本轮用时。</p>
    <ul>
      <li v-for="item in preparations" :key="item.preparation_id">
        <strong>{{ item.recipe_name }}</strong>
        <p>{{ item.description }}</p>
        <p class="muted">原准备时长：{{ displayMinutes(item.original_duration_sec) }} 分钟</p>
      </li>
    </ul>
  </details>
</template>
