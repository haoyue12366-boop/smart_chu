import type {
  MenuItem,
  PlanPresentation,
  ResourcePresentation,
  RuntimeSession,
} from '../api/types';

const dateFormatters = new Map<string, Intl.DateTimeFormat>();
export function fullDate(origin: string, seconds: number, timezone = 'Asia/Shanghai'): string {
  let formatter = dateFormatters.get(timezone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('zh-CN', {
      timeZone: timezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    });
    if (dateFormatters.size >= 8) dateFormatters.delete(dateFormatters.keys().next().value!);
    dateFormatters.set(timezone, formatter);
  }
  return formatter.format(new Date(Date.parse(origin) + seconds * 1000));
}
export const displayMinutes = (seconds: number): string =>
  (Math.floor(seconds / 6 + 0.5) / 10).toFixed(1);
export interface GanttRow {
  id: string;
  lane: string;
  title: string;
  task_ids: string[];
  recipe_instance_ids: string[];
  start_sec: number;
  end_sec: number;
  shared: boolean;
  frozen: boolean;
  status: string;
  configuration: string;
  reuse_intervals: { start_sec: number; end_sec: number }[];
}
export interface GanttRecipe {
  id: string;
  label: string;
  color: string;
}
const dishColors = [
  '#437fbd',
  '#cf783c',
  '#27876d',
  '#9464b5',
  '#c45170',
  '#818b2d',
  '#248f9c',
  '#a46646',
  '#5c68bb',
  '#b58a22',
  '#ad5395',
  '#507e49',
];
export function ganttLegend(
  display: PlanPresentation,
  menu: MenuItem[] = [],
  assigned = new Map<string, string>(),
): GanttRecipe[] {
  const visible = new Set([
    ...display.operations.map((o) => o.recipe_instance_id),
    ...display.resources.flatMap((r) => r.recipe_instance_ids),
  ]);
  const recipes = new Map(menu.map((r) => [r.recipe_instance_id, r]));
  for (const op of display.operations)
    if (!recipes.has(op.recipe_instance_id))
      recipes.set(op.recipe_instance_id, {
        recipe_instance_id: op.recipe_instance_id,
        recipe_id: op.recipe_id,
        name: op.recipe_name,
      });
  for (const resource of display.resources)
    resource.recipe_instance_ids.forEach((id, i) => {
      if (!recipes.has(id))
        recipes.set(id, {
          recipe_instance_id: id,
          recipe_id: id,
          name: resource.recipe_names[i] ?? '菜品',
        });
    });
  const entries = [...recipes.values()];
  return entries.flatMap((r) => {
    if (!assigned.has(r.recipe_instance_id)) {
      const index = assigned.size;
      assigned.set(
        r.recipe_instance_id,
        dishColors[index] ?? `hsl(${(index * 137.508) % 360}, 55%, 43%)`,
      );
    }
    if (!visible.has(r.recipe_instance_id)) return [];
    const sameName = entries.filter((other) => other.name === r.name);
    const suffix = sameName.some((other) => other !== r && other.recipe_id === r.recipe_id)
      ? r.recipe_instance_id
      : r.recipe_id;
    return [
      {
        id: r.recipe_instance_id,
        label: sameName.length > 1 ? `${r.name} · ${suffix.slice(-8)}` : r.name,
        color: assigned.get(r.recipe_instance_id)!,
      },
    ];
  });
}
export function humanOverlaps(resources: ResourcePresentation[]): [string, string][] {
  const rows = resources
    .filter((r) => r.resource_id === 'human_1' && r.end_sec > r.start_sec)
    .sort((a, b) => a.start_sec - b.start_sec);
  const conflicts: [string, string][] = [];
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]!;
    for (let j = i + 1; j < rows.length && rows[j]!.start_sec < row.end_sec; j++)
      conflicts.push([row.entry_id, rows[j]!.entry_id]);
  }
  return conflicts;
}
export function taskStatus(tasks: string[], session: RuntimeSession | null): string {
  const executions = session?.runtime.executions ?? [];
  const completed = new Set(
    executions.flatMap((e) =>
      e.status === 'COMPLETED'
        ? (e.task_ids ?? e.completed_task_ids ?? [])
        : (e.completed_task_ids ?? []),
    ),
  );
  const running = new Set(
    executions.flatMap((e) =>
      (e.started_task_ids ?? []).filter(
        (t) => !(e.completed_task_ids ?? []).includes(t) && e.status === 'RUNNING',
      ),
    ),
  );
  if (tasks.length && tasks.every((t) => completed.has(t))) return '已完成';
  if (tasks.some((t) => running.has(t))) return '执行中';
  if (executions.some((e) => e.status === 'FAILED' && e.task_ids.some((t) => tasks.includes(t))))
    return '失败待处理';
  return '待执行';
}
export function ganttRows(
  display: PlanPresentation,
  scope: 'recipe' | 'resource',
  session: RuntimeSession | null,
): GanttRow[] {
  const instances = new Map<string, Set<string>>();
  for (const op of display.operations) {
    const identities = instances.get(op.recipe_name) ?? new Set<string>();
    identities.add(op.recipe_instance_id);
    instances.set(op.recipe_name, identities);
  }
  if (scope === 'recipe')
    return display.operations.map((o) => ({
      id: o.task_id,
      lane:
        (instances.get(o.recipe_name)?.size ?? 0) > 1
          ? `${o.recipe_name} · ${o.recipe_id.slice(-8)}`
          : o.recipe_name,
      title: o.title,
      task_ids: [o.task_id],
      recipe_instance_ids: [o.recipe_instance_id],
      start_sec: o.start_sec,
      end_sec: o.end_sec,
      shared: o.shared,
      frozen: o.frozen,
      status: o.inventory_supplied ? '库存满足' : taskStatus([o.task_id], session),
      configuration: '',
      reuse_intervals: [],
    }));
  return display.resources.map((r) => ({
    id: r.entry_id,
    lane: r.resource_label,
    title: `${r.recipe_names.join('、')} · ${r.title}`,
    task_ids: r.task_ids,
    recipe_instance_ids: r.recipe_instance_ids,
    start_sec: r.start_sec,
    end_sec: r.end_sec,
    shared: r.shared,
    frozen: r.frozen,
    status: taskStatus(r.task_ids, session),
    configuration: r.configuration.map((c) => `${c.parameter}: ${c.value}`).join('，'),
    reuse_intervals: r.reuse_intervals ?? [],
  }));
}
