<script setup lang="ts">
import { ref } from 'vue';
import type { DeviceState } from '../api/types';
import { deviceActions } from '../model/execution';
const props = defineProps<{
  devices: DeviceState[];
  busy: boolean;
  labels: Record<string, string>;
}>();
const emit = defineEmits<{ event: [kind: string, payload: object] }>();
const reasons = ref<Record<string, string>>({});
const cleared = ref<Record<string, boolean>>({});
function fault(device: DeviceState) {
  const reason = reasons.value[device.device_instance_id]?.trim();
  if (!props.busy && reason)
    emit('event', 'DEVICE_UNAVAILABLE', { device_id: device.device_instance_id, reason });
}
function release(device: DeviceState) {
  if (props.busy || !cleared.value[device.device_instance_id] || !device.active_execution_id)
    return;
  emit('event', 'DEVICE_RELEASE_CONFIRMED', {
    device_id: device.device_instance_id,
    execution_id: device.active_execution_id,
  });
  cleared.value[device.device_instance_id] = false;
}
</script>
<template>
  <section class="panel">
    <div class="section-heading">
      <h2>设备状态</h2>
      <span class="muted">恢复与清空分别确认</span>
    </div>
    <div class="device-grid">
      <article
        v-for="device in devices"
        :key="device.device_instance_id"
        class="device-card"
        :data-device="device.device_instance_id"
      >
        <strong>{{
          labels[device.physical_resource_id ?? device.device_instance_id] ??
          device.device_instance_id
        }}</strong>
        <p>
          <span class="tag" :class="{ danger: device.availability_status === 'UNAVAILABLE' }">{{
            device.availability_status === 'AVAILABLE'
              ? '可用'
              : device.availability_status === 'UNOBSERVED'
                ? '可用性尚未观测'
                : '不可用 / 未知'
          }}</span>
          <span class="tag">{{
            device.occupancy_status === 'FREE'
              ? '空闲'
              : device.occupancy_status === 'UNOBSERVED'
                ? '占用尚未观测'
                : '占用待确认'
          }}</span>
        </p>
        <div v-if="device.availability_status !== 'UNAVAILABLE'" class="toolbar">
          <input
            v-model="reasons[device.device_instance_id]"
            :aria-label="`${device.device_instance_id} 故障原因`"
            placeholder="填写故障原因"
          /><button
            :disabled="busy || !reasons[device.device_instance_id]?.trim()"
            @click="fault(device)"
          >
            报告故障
          </button>
        </div>
        <button
          v-if="deviceActions(device, device.active_execution_id).includes('recover')"
          :disabled="busy"
          @click="emit('event', 'DEVICE_RECOVERED', { device_id: device.device_instance_id })"
        >
          确认设备恢复
        </button>
        <div
          v-if="deviceActions(device, device.active_execution_id).includes('release')"
          class="release-control"
        >
          <label
            ><input v-model="cleared[device.device_instance_id]" type="checkbox" :disabled="busy" />
            已检查并取出本次执行的物品，设备已清空</label
          ><button :disabled="busy || !cleared[device.device_instance_id]" @click="release(device)">
            确认本次执行已释放设备
          </button>
        </div>
        <small v-if="device.configuration?.length">{{
          device.configuration.map((c) => `${c.parameter}: ${c.value}`).join('，')
        }}</small>
      </article>
    </div>
    <p v-if="!devices.length" class="muted">还没有设备观测记录。</p>
  </section>
</template>
