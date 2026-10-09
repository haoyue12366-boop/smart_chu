<script setup lang="ts">
import { computed, ref } from 'vue';
import type { ExecutionPayload, FeedbackProposal, Movement } from '../api/types';
const props = defineProps<{ proposal: FeedbackProposal; title: string; busy: boolean }>();
const emit = defineEmits<{
  close: [];
  submit: [payload: ExecutionPayload, versions: { state_revision: number; plan_version: number }];
}>();
const clonePayload = (payload: ExecutionPayload): ExecutionPayload =>
  JSON.parse(JSON.stringify(payload)) as ExecutionPayload;
const draft = ref<ExecutionPayload>(clonePayload(props.proposal.payload));
const confirmed = ref(false);
const released = ref(false);
const amounts = computed(() => [...draft.value.consumed, ...draft.value.produced]);
const valid = computed(() =>
  amounts.value.every((m) =>
    m.quantity
      ? Number.isSafeInteger(m.quantity.value) && m.quantity.value >= 0 && m.quantity.scale > 0
      : !!m.batch_share &&
        Number.isSafeInteger(m.batch_share.numerator) &&
        m.batch_share.numerator >= 0 &&
        (m.batch_share.denominator ?? 1) > 0,
  ),
);
function amount(m: Movement): string {
  return m.quantity
    ? String(m.quantity.value / m.quantity.scale)
    : String((m.batch_share?.numerator ?? 0) / (m.batch_share?.denominator ?? 1));
}
function update(m: Movement, event: Event) {
  const text = (event.target as HTMLInputElement).value;
  if (!text.trim()) {
    if (m.quantity) m.quantity.value = Number.NaN;
    else if (m.batch_share) m.batch_share.numerator = Number.NaN;
    return;
  }
  // 按原单位精度校验，不截断输入；原配方份额保存精确整数比。
  if (m.quantity) m.quantity.value = Number(text) * m.quantity.scale;
  else if (m.batch_share) {
    const [whole, tail = ''] = text.split('.');
    const denominator = 10 ** tail.length;
    m.batch_share = { numerator: Number(`${whole ?? ''}${tail}`), denominator };
  }
}
function submit() {
  if (props.busy || !confirmed.value || !valid.value) return;
  draft.value.resource_release_status = released.value ? 'CONFIRMED' : 'UNCONFIRMED';
  emit('submit', clonePayload(draft.value), {
    state_revision: props.proposal.state_revision,
    plan_version: props.proposal.plan_version,
  });
}
</script>
<template>
  <div class="modal-backdrop">
    <section class="dialog" role="dialog" aria-modal="true" aria-labelledby="feedback-title">
      <div class="section-heading">
        <h2 id="feedback-title">{{ proposal.completed ? '确认实际完成' : '确认实际开始' }}</h2>
        <button :disabled="busy" aria-label="关闭确认" @click="emit('close')">✕</button>
      </div>
      <h3>{{ title }}</h3>
      <p class="notice">{{ proposal.notice }}</p>
      <p v-if="proposal.task_ids.length > 1">
        本次共同执行 {{ proposal.task_ids.length }} 个成员，请一起核对。
      </p>
      <fieldset v-for="group in ['consumed', 'produced'] as const" :key="group">
        <legend>{{ group === 'consumed' ? '本次实际投入' : '本次实际产出' }}</legend>
        <p v-if="!draft[group].length" class="muted">
          本次无新增{{ group === 'consumed' ? '投入' : '产出' }}记录。
        </p>
        <label
          v-for="movement in draft[group]"
          :key="`${group}:${movement.lot_id}:${movement.spec_id}`"
          class="amount-row"
          ><span>{{ proposal.movement_names[movement.spec_id] ?? '已绑定批次' }}</span
          ><input
            :value="amount(movement)"
            type="number"
            min="0"
            :step="movement.quantity ? 1 / movement.quantity.scale : 'any'"
            :aria-label="`${group} ${proposal.movement_names[movement.spec_id] ?? movement.spec_id} 数量`"
            @input="update(movement, $event)"
          /><span>{{ movement.quantity?.unit ?? '原配方整批份额' }}</span></label
        >
      </fieldset>
      <label v-if="proposal.completed"
        >产物状态
        <select v-model="draft.output_status" aria-label="产物状态">
          <option value="UNKNOWN">尚未确认合格</option>
          <option value="QUALIFIED">已确认合格</option>
          <option value="WASTE">损耗 / 报废</option>
        </select></label
      >
      <label v-if="proposal.release_eligible" class="check-row"
        ><input v-model="released" type="checkbox" /> 已核对本次执行的设备均已清空</label
      >
      <label class="check-row"
        ><input v-model="confirmed" type="checkbox" /> 已按实际情况核对数量和本次操作</label
      >
      <p v-if="!valid" class="error">数量须非负并符合原单位精度。</p>
      <button class="primary" :disabled="busy || !confirmed || !valid" @click="submit">
        {{ proposal.completed ? '提交完成确认' : '提交开始确认' }}
      </button>
    </section>
  </div>
</template>
