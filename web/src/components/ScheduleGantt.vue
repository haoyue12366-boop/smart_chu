<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { init, use } from 'echarts/core';
import type { EChartsType } from 'echarts/core';
import type { CustomSeriesRenderItemAPI, CustomSeriesRenderItemParams } from 'echarts';
import { CustomChart } from 'echarts/charts';
import { DataZoomComponent, GridComponent, TooltipComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import type { PlanEnvelope, RuntimeSession } from '../api/types';
import AdvancePreparations from './AdvancePreparations.vue';
import ReplanStatusNotice from './ReplanStatusNotice.vue';
import {
  displayMinutes,
  fullDate,
  ganttLegend,
  ganttRows,
  humanOverlaps,
} from '../model/presentation';
use([CustomChart, DataZoomComponent, GridComponent, TooltipComponent, SVGRenderer]);
const props = defineProps<{
  envelope: PlanEnvelope;
  session: RuntimeSession | null;
  timezone?: string;
  historical?: boolean;
}>();
const scope = ref<'resource' | 'recipe'>('resource');
const zoom = ref(0);
const host = ref<HTMLDivElement>();
const rows = computed(() =>
  ganttRows(props.envelope.presentation, scope.value, props.historical ? null : props.session),
);
const origin = computed(() => props.envelope.presentation.time_origin.start_at);
const formatTime = computed(() => {
  const originValue = origin.value;
  const timezone = props.timezone;
  const labels = new Map<number, string>();
  return (seconds: number) => {
    let label = labels.get(seconds);
    if (label === undefined) {
      label = fullDate(originValue, seconds, timezone);
      if (labels.size >= 2048) labels.clear();
      labels.set(seconds, label);
    }
    return label;
  };
});
const lanes = computed(() =>
  scope.value === 'resource' && props.envelope.presentation.resource_lanes?.length
    ? props.envelope.presentation.resource_lanes
    : [...new Set(rows.value.map((r) => r.lane))],
);
const conflicts = computed(() => humanOverlaps(props.envelope.presentation.resources));
const assignedColors = new Map<string, string>();
let colorSession = '';
const recipes = computed(() => {
  if (colorSession !== props.envelope.plan.session_id) {
    assignedColors.clear();
    colorSession = props.envelope.plan.session_id;
  }
  return ganttLegend(props.envelope.presentation, props.session?.menu, assignedColors);
});
const recipeById = computed(() => new Map(recipes.value.map((r) => [r.id, r])));
const zoomAnchor = ref(0);
watch(
  zoom,
  () => {
    zoomAnchor.value = props.session?.runtime.now_offset_sec ?? 0;
  },
  { flush: 'sync' },
);
const windowEnd = computed(() =>
  zoom.value && !props.historical
    ? Math.min(props.envelope.presentation.range_end_sec, zoomAnchor.value + zoom.value)
    : props.envelope.presentation.range_end_sec,
);
// 只在实际显示内容变化时更新，保留悬浮卡与用户缩放。
const renderKey = computed(() =>
  JSON.stringify([
    scope.value,
    rows.value,
    lanes.value,
    recipes.value,
    origin.value,
    props.envelope.presentation.range_end_sec,
    props.timezone,
    props.historical,
    zoom.value,
    windowEnd.value,
  ]),
);
let chart: EChartsType | undefined;
let observer: ResizeObserver | undefined;
let drawnZoomKey = '';
const escapeHtml = (text: string) =>
  text.replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] ?? c,
  );
function draw() {
  if (!chart) return;
  // resize、缩放或旧系列的回调只能读取所属轮次的数据，不能混用切换后的 rows。
  const drawRows = rows.value;
  const drawLanes = lanes.value;
  const drawRecipes = recipeById.value;
  const drawTime = formatTime.value;
  const drawHistorical = props.historical;
  const range = Math.max(1, props.envelope.presentation.range_end_sec);
  const end = windowEnd.value;
  const start = zoom.value ? Math.max(0, end - zoom.value) : 0;
  const zoomKey = JSON.stringify([props.envelope.plan.session_id, range, zoom.value, end]);
  const zoomBounds = zoomKey !== drawnZoomKey ? { startValue: start, endValue: end } : {};
  chart.setOption(
    {
      animation: false,
      grid: { left: 130, right: 30, top: 24, bottom: 80 },
      tooltip: {
        trigger: 'item',
        renderMode: 'html',
        className: 'gantt-tooltip',
        confine: true,
        enterable: true,
        showDelay: 0,
        hideDelay: 800,
        extraCssText:
          'width:360px;max-width:calc(100vw - 48px);max-height:360px;max-height:min(360px,65vh);overflow-y:auto;white-space:normal;line-height:1.65;',
        formatter: (param: unknown) => {
          const item = drawRows[(param as { dataIndex: number }).dataIndex];
          if (!item) return '';
          const dishes = item.recipe_instance_ids
            .flatMap((id) => {
              const recipe = drawRecipes.get(id);
              return recipe
                ? [
                    `<span class="gantt-tooltip-dish"><i style="background:${recipe.color}"></i>${escapeHtml(recipe.label)}</span>`,
                  ]
                : [];
            })
            .join('');
          const reuse = item.reuse_intervals
            .map(
              (interval) =>
                `${escapeHtml(drawTime(interval.start_sec))} → ${escapeHtml(drawTime(interval.end_sec))}`,
            )
            .join('<br />');
          return `${dishes}<div class="gantt-tooltip-title">${escapeHtml(item.title)}</div>
            <div>${escapeHtml(item.lane)} · ${item.status}${item.shared ? ' · 共同批次' : ''}${reuse ? ' · 复用' : ''}${item.frozen ? ' · 冻结' : ''}${drawHistorical ? ' · 历史计划' : ''}</div>
            <div>开始：${escapeHtml(drawTime(item.start_sec))}</div>
            <div>结束：${escapeHtml(drawTime(item.end_sec))}</div>
            <div>时长：${displayMinutes(item.end_sec - item.start_sec)} 分钟</div>
            ${reuse ? `<div>设备复用区间：<br />${reuse}</div>` : ''}
            ${item.configuration ? `<div class="gantt-tooltip-configuration">${escapeHtml(item.configuration)}</div>` : ''}`;
        },
      },
      xAxis: {
        type: 'value',
        min: 0,
        max: range,
        axisLabel: {
          formatter: (n: number) => drawTime(n).replace(' ', '\n'),
          fontSize: 10,
        },
      },
      yAxis: { type: 'category', data: drawLanes, inverse: true, axisTick: { show: false } },
      dataZoom: [
        {
          type: 'slider',
          xAxisIndex: 0,
          ...zoomBounds,
          filterMode: 'weakFilter',
          bottom: 6,
        },
        { type: 'inside', xAxisIndex: 0, filterMode: 'weakFilter' },
      ],
      series: [
        {
          id: `gantt-${scope.value}`,
          type: 'custom',
          clip: true,
          encode: { x: [1, 2], y: 0 },
          renderItem: (params: CustomSeriesRenderItemParams, api: CustomSeriesRenderItemAPI) => {
            const row = drawRows[params.dataIndex]!;
            const a = api.coord([api.value(1), api.value(0)]);
            const b = api.coord([api.value(2), api.value(0)]);
            const pixelSize = api.size?.([0, 1]);
            const height = Math.max(8, Array.isArray(pixelSize) ? Number(pixelSize[1]) * 0.58 : 20);
            const width = Math.max(2, b[0]! - a[0]!);
            const colors = row.recipe_instance_ids.map(
              (id) => drawRecipes.get(id)?.color ?? '#7d8885',
            );
            if (!colors.length) colors.push('#7d8885');
            const stroke =
              row.status === '已完成'
                ? '#18865a'
                : row.status === '执行中'
                  ? '#263f48'
                  : row.status === '失败待处理'
                    ? '#b33d35'
                    : '#fff';
            const faded = drawHistorical || row.frozen;
            const reuseMarkers = row.reuse_intervals.flatMap((interval) => {
              const reuseStart = api.coord([interval.start_sec, api.value(0)]);
              const reuseEnd = api.coord([interval.end_sec, api.value(0)]);
              const reuseWidth = Math.max(2, reuseEnd[0]! - reuseStart[0]!);
              return [
                {
                  type: 'rect' as const,
                  shape: {
                    x: reuseStart[0]! + 1,
                    y: a[1]! - height / 2 + 3,
                    width: Math.max(1, reuseWidth - 2),
                    height: Math.max(2, height - 6),
                  },
                  style: {
                    fill: 'rgba(255, 205, 75, 0.12)',
                    stroke: '#ffcd4b',
                    lineWidth: 2,
                    opacity: faded ? 0.65 : 1,
                  },
                },
                ...(reuseWidth >= 32
                  ? [
                      {
                        type: 'text' as const,
                        style: {
                          x: (reuseStart[0]! + reuseEnd[0]!) / 2,
                          y: a[1]!,
                          text: '复用',
                          align: 'center' as const,
                          verticalAlign: 'middle' as const,
                          fontSize: 10,
                          fill: '#604300',
                          backgroundColor: '#fff4cd',
                          padding: [1, 3],
                        },
                      },
                    ]
                  : []),
              ];
            });
            return {
              type: 'group',
              // 默认悬停会改菜色并抬高整条预约；仅保留工具提示。
              emphasisDisabled: true,
              $mergeChildren: false,
              children: [
                ...colors.map((color, i) => ({
                  type: 'rect' as const,
                  shape: {
                    x: a[0]! + (width * i) / colors.length,
                    y: a[1]! - height / 2,
                    width: width / colors.length,
                    height,
                  },
                  style: { fill: color, opacity: faded ? 0.55 : 1 },
                })),
                {
                  type: 'rect',
                  shape: { x: a[0], y: a[1]! - height / 2, width, height },
                  style: {
                    fill: 'transparent',
                    stroke: faded && stroke === '#fff' ? '#7d8885' : stroke,
                    lineWidth: faded || stroke !== '#fff' ? 2 : 1,
                    lineDash: faded ? [4, 3] : undefined,
                  },
                },
                ...reuseMarkers,
              ],
            };
          },
          data: drawRows.map((r) => ({
            id: `${scope.value}:${r.id}`,
            value: [drawLanes.indexOf(r.lane), r.start_sec, r.end_sec],
          })),
        },
      ],
    },
    { notMerge: false, replaceMerge: ['series'] },
  );
  drawnZoomKey = zoomKey;
}
onMounted(() => {
  if (host.value) {
    chart = init(host.value, undefined, { renderer: 'svg' });
    observer = new ResizeObserver(() => chart?.resize());
    observer.observe(host.value);
    draw();
  }
});
watch(renderKey, async () => {
  await nextTick();
  draw();
  chart?.resize();
});
onBeforeUnmount(() => {
  observer?.disconnect();
  chart?.dispose();
});
</script>
<template>
  <section class="panel">
    <div class="section-heading">
      <div>
        <p class="eyebrow">时间安排</p>
        <h2>{{ historical ? '历史计划' : '烹饪甘特图' }}</h2>
      </div>
      <div class="toolbar">
        <select v-model="scope" aria-label="甘特图分组">
          <option value="resource">设备与人工</option>
          <option value="recipe">按菜品</option></select
        ><select v-model="zoom" aria-label="时间缩放">
          <option :value="0">完整流程</option>
          <option :value="1800">30 分钟</option>
          <option :value="14400">4 小时</option>
          <option :value="86400">1 天</option>
        </select>
      </div>
    </div>
    <p class="muted">
      {{ fullDate(origin, 0, timezone) }} →
      {{ fullDate(origin, envelope.presentation.range_end_sec, timezone) }}
    </p>
    <AdvancePreparations :preparations="envelope.presentation.advance_preparations ?? []" />
    <ReplanStatusNotice
      v-if="session && !historical"
      :session="session"
      :plan-version="envelope.plan.plan_version"
      test-id="gantt-replan-status"
    />
    <div class="gantt-legend" aria-label="菜品颜色图例">
      <strong>菜品颜色</strong>
      <ul>
        <li v-for="recipe in recipes" :key="recipe.id" :data-recipe-id="recipe.id">
          <span
            class="gantt-recipe-swatch"
            :style="{ backgroundColor: recipe.color }"
            aria-hidden="true"
          />
          {{ recipe.label }}
        </li>
      </ul>
      <p class="muted">
        同一道菜在各泳道使用相同颜色；共同批次按成员菜品分色。蒸箱与烤箱按实际层位显示，空层保留。
      </p>
    </div>
    <p class="gantt-status-legend muted">
      <span><i class="gantt-status-swatch" />待执行</span>
      <span><i class="gantt-status-swatch running" />执行中</span>
      <span><i class="gantt-status-swatch completed" />已完成</span>
      <span><i class="gantt-status-swatch failed" />失败待处理</span>
      <span><i class="gantt-status-swatch frozen" />淡色虚线：冻结或历史</span>
      <span
        ><i
          class="gantt-status-swatch"
          style="border: 2px solid #ffcd4b"
        />金色框：设备复用区间</span
      >
    </p>
    <p v-if="conflicts.length" class="error" role="alert">
      显示数据包含 {{ conflicts.length }} 处人工时间重叠，请核对服务状态；完整区间表保留各项明细。
    </p>
    <div
      ref="host"
      class="gantt"
      :style="{ height: `${Math.max(320, lanes.length * 52 + 120)}px` }"
      aria-label="烹饪安排图"
    />
    <details>
      <summary>查看完整操作和资源区间（{{ rows.length }} 项）</summary>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>泳道 / 操作</th>
              <th>完整时间</th>
              <th>时长</th>
              <th>状态与参数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id">
              <td>{{ row.lane }}<br />{{ row.title }}</td>
              <td>{{ formatTime(row.start_sec) }}<br />{{ formatTime(row.end_sec) }}</td>
              <td>{{ displayMinutes(row.end_sec - row.start_sec) }} 分钟</td>
              <td>
                {{ row.status }}{{ row.shared ? ' · 共同批次' : ''
                }}{{ row.reuse_intervals.length ? ' · 复用' : '' }}{{ row.frozen ? ' · 冻结' : ''
                }}<br />{{ row.configuration }}
                <div v-for="interval in row.reuse_intervals" :key="interval.start_sec">
                  复用：{{ formatTime(interval.start_sec) }} →
                  {{ formatTime(interval.end_sec) }}
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  </section>
</template>
