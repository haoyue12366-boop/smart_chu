<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { Catalog, Mode, RecipeChoice, RuntimeSession } from '../api/types';
const props = defineProps<{
  catalog: Catalog | null;
  busy: boolean;
  error: string;
  session: RuntimeSession | null;
}>();
const emit = defineEmits<{
  create: [recipes: RecipeChoice[], mode: Mode];
  add: [recipe: RecipeChoice];
  graph: [id: string];
}>();
const search = ref('');
const selected = ref<string[]>([]);
const mode = ref<Mode>('SCHEDULE_CLOCK');
const optimizing = computed(
  () => !!props.session?.requires_replan && !props.session.last_planning_failure,
);
const recipes = computed(() =>
  (props.catalog?.recipes ?? []).filter((r) =>
    `${r.name} ${r.ingredient_names.join(' ')} ${r.recipe_id}`.includes(search.value.trim()),
  ),
);
const choices = computed(() =>
  (props.catalog?.recipes ?? [])
    .filter((r) => selected.value.includes(r.recipe_id))
    .map((r) => ({ id: r.recipe_id, name: r.name })),
);
watch(
  [
    () => props.session?.runtime.session_id,
    () => props.session?.menu.map((recipe) => recipe.recipe_instance_id).join(','),
  ],
  () => {
    selected.value = [];
  },
);
function submit() {
  if (props.busy || !choices.value.length) return;
  if (props.session) {
    if (choices.value.length === 1 && choices.value[0]) emit('add', choices.value[0]);
  } else emit('create', choices.value, mode.value);
}
</script>
<template>
  <section class="panel">
    <div class="section-heading">
      <div>
        <p class="eyebrow">菜单</p>
        <h2>{{ session ? '为这桌菜追加一道' : '今天准备做什么？' }}</h2>
      </div>
      <span class="tag">{{ catalog?.recipes.length ?? 0 }} 道已发布菜谱</span>
    </div>
    <p v-if="error && !catalog" role="alert" class="error">{{ error }}</p>
    <p v-if="!catalog && !error" class="muted">正在读取菜谱库…</p>
    <template v-if="catalog">
      <div class="toolbar">
        <input
          v-model="search"
          aria-label="搜索菜谱"
          placeholder="搜索菜名、食材或菜谱 ID"
          type="search"
        />
        <label v-if="!session"
          >执行方式
          <select v-model="mode" aria-label="执行方式">
            <option value="SCHEDULE_CLOCK">自动计划时钟</option>
            <option value="MANUAL_CONFIRM">人工确认</option>
            <option value="SIMULATED">模拟演示</option>
          </select></label
        >
      </div>
      <p v-if="mode === 'SCHEDULE_CLOCK' && !session" class="notice">
        初次计划发布后自动计时，按计划推进操作并提示完成。进度标记为计划时钟推算，可通过实际反馈修正。
      </p>
      <p v-if="mode === 'SIMULATED' && !session" class="notice">
        模拟演示的执行记录均标记 SIMULATED；通过执行页按钮推进。
      </p>
      <div class="recipe-grid">
        <article
          v-for="recipe in recipes"
          :key="recipe.recipe_id"
          class="recipe-card"
          :class="{ selected: selected.includes(recipe.recipe_id) }"
        >
          <label
            ><input
              v-model="selected"
              type="checkbox"
              :value="recipe.recipe_id"
              :disabled="busy"
              :aria-label="`${recipe.name} ${recipe.recipe_id}`"
            /><span
              ><strong>{{ recipe.name }}</strong
              ><small
                >{{ recipe.operation_count }} 道工序 · {{ recipe.recipe_id.slice(-8) }}</small
              ></span
            ></label
          >
          <p class="ingredients">{{ recipe.ingredient_names.join(' · ') }}</p>
          <button class="text-button" type="button" @click="emit('graph', recipe.recipe_id)">
            查看工艺 →
          </button>
        </article>
      </div>
      <p v-if="!recipes.length" class="empty">没有匹配的菜谱。</p>
      <p v-if="optimizing" class="notice" data-testid="addition-waiting">
        变更已接收，正在重排剩余操作；当前操作继续，新计划发布后甘特图自动更新。
      </p>
      <div class="selection-bar">
        <span>已选 {{ selected.length }} 道{{ session ? '，每次追加一道' : '' }}</span
        ><button
          class="primary"
          data-testid="create-plan"
          :disabled="busy || !choices.length || (!!session && choices.length !== 1)"
          @click="submit"
        >
          {{
            busy
              ? '正在处理…'
              : session
                ? optimizing && !choices.length
                  ? '已接收，正在重排'
                  : '追加并重排'
                : '生成烹饪计划'
          }}
        </button>
      </div>
    </template>
  </section>
</template>
