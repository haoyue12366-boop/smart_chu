<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { init, use } from 'echarts/core';
import type { EChartsType } from 'echarts/core';
import { GraphChart } from 'echarts/charts';
import { TooltipComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import type { RecipeGraph } from '../api/types';
use([GraphChart, TooltipComponent, SVGRenderer]);
const props = defineProps<{ graph: RecipeGraph }>();
const host = ref<HTMLDivElement>();
let chart: EChartsType | undefined;
let observer: ResizeObserver | undefined;
onMounted(() => {
  if (!host.value) return;
  chart = init(host.value, undefined, { renderer: 'svg' });
  chart.setOption({
    tooltip: { trigger: 'item', renderMode: 'richText' },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        force: { repulsion: 180, edgeLength: 100 },
        symbolSize: 20,
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 8,
        label: { show: true, position: 'bottom', fontSize: 11, width: 110, overflow: 'break' },
        data: props.graph.operations.map((o) => ({
          id: o.operation_id,
          name: o.description || o.action,
          itemStyle: { color: '#357b68' },
        })),
        links: props.graph.dependencies.map((d) => ({
          source: d.predecessor_id,
          target: d.successor_id,
          name: d.reason,
        })),
        lineStyle: { color: '#a3b9b0', width: 2, curveness: 0.12 },
        emphasis: { focus: 'adjacency' },
      },
    ],
  });
  observer = new ResizeObserver(() => chart?.resize());
  observer.observe(host.value);
});
onBeforeUnmount(() => {
  observer?.disconnect();
  chart?.dispose();
});
</script>
<template>
  <div>
    <p class="muted">只读工艺依赖图 · 固定知识版本 {{ graph.knowledge_version }}</p>
    <div ref="host" class="knowledge-graph" aria-label="菜谱工序依赖图" />
    <ol class="graph-steps">
      <li v-for="op in graph.operations" :key="op.operation_id">
        <strong>{{ op.description || op.action }}</strong
        ><small
          >{{ op.duration.execution_sec ?? '未知' }} 秒 ·
          {{ op.provenance_refs.length }} 项来源引用</small
        >
        <p
          v-for="dep in graph.dependencies.filter((d) => d.successor_id === op.operation_id)"
          :key="dep.predecessor_id"
        >
          前置：{{
            graph.operations.find((o) => o.operation_id === dep.predecessor_id)?.description ??
            dep.predecessor_id
          }}
          · {{ dep.reason }}
        </p>
      </li>
    </ol>
  </div>
</template>
