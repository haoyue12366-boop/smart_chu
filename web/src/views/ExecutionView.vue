<script setup lang="ts">
import { computed, ref } from 'vue';
import type {
  Catalog,
  ExecutionPayload,
  FeedbackProposal,
  Notice,
  PlanEnvelope,
  RuntimeSession,
} from '../api/types';
import { feedback } from '../api/client';
import { fullDate, taskStatus } from '../model/presentation';
import { visibleDevices } from '../model/execution';
import DeviceStatus from '../components/DeviceStatus.vue';
import FeedbackDialog from '../components/FeedbackDialog.vue';
import NotificationList from '../components/NotificationList.vue';
const props = defineProps<{
  session: RuntimeSession;
  envelope: PlanEnvelope | null;
  catalog: Catalog | null;
  busy: boolean;
  notices: Notice[];
  clarification: { question: string; options: string[] } | null;
}>();
const emit = defineEmits<{
  event: [
    kind: string,
    payload: object,
    versions?: { state_revision: number; plan_version: number },
  ];
  language: [text: string];
}>();
const proposal = ref<FeedbackProposal | null>(null);
const title = ref('');
const localError = ref('');
const preparing = ref(false);
const advance = ref(60);
const languageText = ref('');
const remaining = ref<Record<string, number>>({});
const reasons = ref<Record<string, string>>({});
const shortage = ref<Record<string, number>>({});
const shortageReasons = ref<Record<string, string>>({});
const labels = computed(() =>
  Object.fromEntries(
    props.envelope?.presentation.resources.map((r) => [
      r.resource_id,
      r.device_label ?? r.resource_label,
    ]) ?? [],
  ),
);
const operations = computed(() => props.envelope?.presentation.operations ?? []);
const stockNames = computed(() =>
  Object.fromEntries(
    props.session.runtime.details?.lots?.flatMap((lot) =>
      lot.material_spec ? [[lot.lot_id, lot.material_spec.name]] : [],
    ) ?? [],
  ),
);
const unstartedMenu = computed(() =>
  props.session.menu.filter((item) => {
    if (props.session.runtime.details?.cancelled_instance_ids.includes(item.recipe_instance_id))
      return false;
    const tasks = operations.value
      .filter((operation) => operation.recipe_instance_id === item.recipe_instance_id)
      .map((operation) => operation.task_id);
    return (
      tasks.length > 0 &&
      !props.session.runtime.executions.some(
        (execution) =>
          execution.started_at && execution.task_ids.some((task) => tasks.includes(task)),
      )
    );
  }),
);
const devices = computed(
  () =>
    props.session.device_presentation ??
    visibleDevices(props.catalog?.devices ?? [], props.session.runtime.device_states),
);
async function prepare(task: string, text: string, completed: boolean) {
  if (props.busy || preparing.value) return;
  preparing.value = true;
  localError.value = '';
  try {
    proposal.value = await feedback(props.session.runtime.session_id, task, completed);
    title.value = text;
  } catch (reason) {
    localError.value = reason instanceof Error ? reason.message : '无法准备反馈。';
  } finally {
    preparing.value = false;
  }
}
function submitFeedback(
  payload: ExecutionPayload,
  versions: { state_revision: number; plan_version: number },
) {
  if (!proposal.value) return;
  emit(
    'event',
    proposal.value.completed ? 'OPERATION_COMPLETED' : 'OPERATION_STARTED',
    payload,
    versions,
  );
  proposal.value = null;
}
function updateDuration(execution: string, task: string) {
  const value = remaining.value[execution];
  const reason = reasons.value[execution]?.trim();
  if (!props.busy && value !== undefined && Number.isSafeInteger(value) && value >= 0 && reason)
    emit('event', 'DURATION_UPDATED', {
      task_id: task,
      execution_id: execution,
      remaining_sec: value,
      reason,
    });
}
function reportShortage(lotId: string) {
  const lot = props.session.runtime.material_lots.find((l) => l.lot_id === lotId);
  const value = shortage.value[lotId];
  const reason = shortageReasons.value[lotId]?.trim();
  if (!lot || value === undefined || !reason || props.busy) return;
  const scaled = value * lot.quantity_available.scale;
  if (!Number.isSafeInteger(scaled) || scaled < 0 || scaled >= lot.quantity_available.value) {
    localError.value = '实有量须低于当前可用量，并符合原单位精度。';
    return;
  }
  emit('event', 'MATERIAL_SHORTAGE', {
    lot_id: lotId,
    before: lot.quantity_available,
    after: { ...lot.quantity_available, value: scaled },
    reason,
    evidence_refs: [`manual-count:${crypto.randomUUID()}`],
  });
}
</script>
<template>
  <section class="panel">
    <div class="section-heading">
      <div>
        <p class="eyebrow">执行</p>
        <h2>按实际操作更新进度</h2>
      </div>
      <span class="tag">{{
        session.runtime.execution_mode === 'SCHEDULE_CLOCK'
          ? '计划时钟推算 · 可人工修正'
          : session.runtime.execution_mode === 'SIMULATED'
            ? 'SIMULATED · 模拟演示'
            : '人工确认'
      }}</span>
    </div>
    <p v-if="session.runtime.execution_mode === 'SCHEDULE_CLOCK'" class="muted">
      操作按服务端计划时钟自动推进。若实际情况有变化，可确认实际开始、完成或更新剩余时长；人工反馈保留独立来源。
    </p>
    <p v-else-if="session.runtime.execution_mode !== 'SIMULATED'" class="muted">
      计划时间用于提醒。开始、完成、产物合格与设备清空均由实际反馈确认。
    </p>
    <div v-else class="notice">
      <p>
        模拟时间
        {{
          fullDate(
            session.runtime.time_origin.start_at,
            session.runtime.now_offset_sec,
            catalog?.timezone,
          )
        }}，模拟事件带 SIMULATED 来源。
      </p>
      <div class="toolbar">
        <button
          :disabled="busy"
          data-testid="simulation-start"
          @click="emit('event', 'ADVANCE_SIMULATION', { advance_sec: 0 })"
        >
          模拟执行当前可开始操作</button
        ><label
          >推进秒数
          <input
            v-model.number="advance"
            type="number"
            min="0"
            step="1"
            aria-label="模拟推进秒数" /></label
        ><button
          :disabled="busy || !Number.isSafeInteger(advance) || advance < 0"
          data-testid="simulation-advance"
          @click="emit('event', 'ADVANCE_SIMULATION', { advance_sec: advance })"
        >
          推进模拟时间
        </button>
      </div>
    </div>
    <p v-if="localError" class="error" role="alert">{{ localError }}</p>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>菜品与操作</th>
            <th>计划开始</th>
            <th>
              {{ session.runtime.execution_mode === 'SCHEDULE_CLOCK' ? '推算状态' : '实际状态' }}
            </th>
            <th>反馈</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="op in operations" :key="op.task_id" :data-task="op.task_id">
            <td>
              {{ op.recipe_name }}<br />{{ op.title }}
              <span v-if="op.shared" class="tag">共同执行</span
              ><span v-if="op.frozen" class="tag">冻结事实</span>
            </td>
            <td>
              {{ fullDate(session.runtime.time_origin.start_at, op.start_sec, catalog?.timezone) }}
            </td>
            <td>{{ op.inventory_supplied ? '库存满足' : taskStatus([op.task_id], session) }}</td>
            <td v-if="session.runtime.execution_mode !== 'SIMULATED'">
              <button
                v-if="!op.inventory_supplied && taskStatus([op.task_id], session) === '待执行'"
                :disabled="busy || preparing || session.dispatch_blocked"
                @click="prepare(op.task_id, `${op.recipe_name} · ${op.title}`, false)"
              >
                确认开始</button
              ><button
                v-if="taskStatus([op.task_id], session) === '执行中'"
                :disabled="busy || preparing"
                @click="prepare(op.task_id, `${op.recipe_name} · ${op.title}`, true)"
              >
                确认完成
              </button>
            </td>
            <td v-else>由模拟推进生成反馈</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
  <DeviceStatus
    :devices="devices"
    :labels="labels"
    :busy="busy"
    @event="(kind, payload) => emit('event', kind, payload)"
  />
  <section v-if="session.runtime.executions.some((e) => e.status === 'RUNNING')" class="panel">
    <h2>运行中的时长反馈</h2>
    <div
      v-for="execution in session.runtime.executions.filter((e) => e.status === 'RUNNING')"
      :key="execution.execution_id"
      class="duration-row"
    >
      <p>
        {{ operations.find((o) => execution.task_ids.includes(o.task_id))?.title ?? '运行中操作' }}
      </p>
      <div class="toolbar">
        <input
          v-model.number="remaining[execution.execution_id]"
          type="number"
          min="0"
          step="1"
          aria-label="实际剩余秒数"
          placeholder="剩余秒数"
        /><input
          v-model="reasons[execution.execution_id]"
          aria-label="时长更新原因"
          placeholder="例如：实际火力较低"
        /><button
          :disabled="busy || !reasons[execution.execution_id]?.trim()"
          @click="updateDuration(execution.execution_id, execution.task_ids[0]!)"
        >
          更新剩余时长并重排
        </button>
      </div>
    </div>
  </section>
  <NotificationList :notices="notices" />
  <section class="panel">
    <h2>未开始菜品的偏好</h2>
    <p v-if="!unstartedMenu.length" class="muted">当前没有可取消的未开始菜品。</p>
    <div v-for="item in unstartedMenu" :key="item.recipe_instance_id" class="toolbar menu-action">
      <span>{{ item.name }}</span
      ><button
        :disabled="busy"
        @click="emit('event', 'CANCEL_RECIPE', { recipe_instance_id: item.recipe_instance_id })"
      >
        取消未开始菜品
      </button>
    </div>
  </section>
  <section class="panel">
    <h2>物料盘点</h2>
    <p class="muted">保留实际消费；实有量不足会撤销受影响的未来分配并触发重排。</p>
    <details>
      <summary>查看现有批次并报告不足</summary>
      <div
        v-for="lot in session.runtime.material_lots.filter((l) => l.quantity_available.value > 0)"
        :key="lot.lot_id"
        class="stock-row"
      >
        <small
          >{{ stockNames[lot.lot_id] ?? '物料名称未记录' }} · 批次 {{ lot.lot_id.slice(-8) }} ·
          {{ lot.quality_status === 'QUALIFIED' ? '已确认合格' : '尚未确认合格' }} · 可用
          {{ lot.quantity_available.value / lot.quantity_available.scale }}
          {{ lot.quantity_available.unit }}</small
        >
        <div class="toolbar">
          <input
            v-model.number="shortage[lot.lot_id]"
            type="number"
            min="0"
            :step="1 / lot.quantity_available.scale"
            aria-label="盘点实有量"
            placeholder="实有数量"
          /><input
            v-model="shortageReasons[lot.lot_id]"
            aria-label="物料不足原因"
            placeholder="盘点原因"
          /><button
            :disabled="busy || !shortageReasons[lot.lot_id]?.trim()"
            @click="reportShortage(lot.lot_id)"
          >
            提交不足反馈
          </button>
        </div>
      </div>
    </details>
  </section>
  <section class="panel">
    <h2>用一句话调整</h2>
    <p v-if="!catalog?.language_enabled" class="muted">
      自然语言服务未启用，可以继续使用上面的结构化操作。
    </p>
    <form
      class="toolbar"
      @submit.prevent="languageText.trim() && emit('language', languageText.trim())"
    >
      <input
        v-model="languageText"
        aria-label="自然语言调整"
        placeholder="例如：再加一道低温牛排"
        :disabled="!catalog?.language_enabled"
      /><button :disabled="busy || !catalog?.language_enabled || !languageText.trim()">
        提交指令
      </button>
    </form>
    <div v-if="clarification" class="notice">
      <p>{{ clarification.question }}</p>
      <ul>
        <li v-for="option in clarification.options" :key="option">{{ option }}</li>
      </ul>
      <p>请在输入框补充明确指令后提交。</p>
    </div>
  </section>
  <FeedbackDialog
    v-if="proposal"
    :proposal="proposal"
    :title="title"
    :busy="busy"
    @close="proposal = null"
    @submit="submitFeedback"
  />
</template>
