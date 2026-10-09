<script setup lang="ts">
import { ref, watch } from 'vue';
import type { Notice, PlanEnvelope, RecipeChoice, RuntimeSession } from '../api/types';
import SessionProgress from './SessionProgress.vue';
import NotificationList from './NotificationList.vue';
const props = defineProps<{
  busy: boolean;
  response: string;
  activeTaskId: string | null;
  session: RuntimeSession | null;
  envelope: PlanEnvelope | null;
  notices: Notice[];
  timezone?: string;
}>();
const emit = defineEmits<{
  submit: [recipes: RecipeChoice[], taskId: string, key: string];
  load: [taskId: string];
  replan: [];
}>();
const taskId = ref(props.activeTaskId ?? '');
watch(
  () => props.activeTaskId,
  (value) => {
    if (value) taskId.value = value;
  },
);
const key = ref(crypto.randomUUID());
const body = ref('[]');
const error = ref('');
const newKey = () => {
  key.value = crypto.randomUUID();
};
function send() {
  error.value = '';
  try {
    const data: unknown = JSON.parse(body.value);
    if (!taskId.value.trim() || !key.value.trim()) throw new Error('请填写任务标识和幂等键。');
    if (
      !Array.isArray(data) ||
      !data.length ||
      data.some(
        (r: unknown) =>
          !r ||
          typeof r !== 'object' ||
          typeof (r as RecipeChoice).id !== 'string' ||
          typeof (r as RecipeChoice).name !== 'string',
      )
    )
      throw new Error('请求须为非空 name + id 数组，ID 使用真实字符串。');
    emit('submit', data as RecipeChoice[], taskId.value.trim(), key.value.trim());
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'JSON 无法读取。';
  }
}
</script>
<template>
  <section class="panel">
    <div class="section-heading">
      <h2>比赛接口测试</h2>
      <span class="tag">POST /api/competition/plan</span>
    </div>
    <p class="muted">
      直接提交 name + id
      数组。同任务首次初排，后续单菜追加。查询参数、累计响应和固定会话原点采用本项目已获授权的设计；官方动态联调状态单列。
    </p>
    <form @submit.prevent="send">
      <div class="toolbar">
        <label>task_id <input v-model="taskId" aria-label="比赛任务标识" required /></label
        ><label>Idempotency-Key <input v-model="key" aria-label="比赛幂等键" required /></label
        ><button type="button" :disabled="busy" @click="newKey">生成新请求身份</button>
        <button
          type="button"
          :disabled="busy || !taskId.trim()"
          @click="emit('load', taskId.trim())"
        >
          查看该任务进度
        </button>
      </div>
      <label class="block-label"
        >请求数组 <textarea v-model="body" aria-label="比赛请求数组" rows="5" spellcheck="false" />
      </label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="primary" data-testid="competition-submit" :disabled="busy">
        {{ busy ? '正在调用…' : '调用比赛接口' }}
      </button>
    </form>
    <details v-if="response" open>
      <summary>接口响应（overview 包含系统排程开销，单位毫秒）</summary>
      <pre data-testid="competition-response">{{ response }}</pre>
    </details>
  </section>
  <div v-if="session && activeTaskId" data-testid="competition-progress">
    <p class="muted">
      已连接任务 {{ activeTaskId }}；进度与完成通知通过该任务的会话和通知接口持续更新。
    </p>
    <SessionProgress
      :session="session"
      :envelope="envelope"
      :busy="busy"
      :timezone="timezone"
      @replan="emit('replan')"
    />
    <NotificationList :notices="notices" data-testid="competition-notifications" />
  </div>
</template>
