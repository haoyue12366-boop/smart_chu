export type Mode = 'SCHEDULE_CLOCK' | 'MANUAL_CONFIRM' | 'SIMULATED';
export interface RecipeChoice {
  id: string;
  name: string;
}
export interface Recipe {
  recipe_id: string;
  name: string;
  operation_count: number;
  ingredient_names: string[];
}
export interface CatalogDevice {
  device_instance_id: string;
  physical_resource_id?: string | null;
  component_id?: string | null;
}
export interface Catalog {
  knowledge_version: string;
  timezone: string;
  language_enabled: boolean;
  recipes: Recipe[];
  devices: CatalogDevice[];
}
export interface Configuration {
  parameter: string;
  value: string | number;
}
export interface OperationPresentation {
  task_id: string;
  carrier_id: string;
  recipe_id: string;
  recipe_instance_id: string;
  recipe_name: string;
  title: string;
  action: string;
  start_sec: number;
  end_sec: number;
  shared: boolean;
  frozen: boolean;
  inventory_supplied?: boolean;
}
export interface ResourcePresentation {
  entry_id: string;
  resource_id: string;
  component_id: string;
  resource_label: string;
  device_label?: string | null;
  layer_index?: number | null;
  title: string;
  task_ids: string[];
  recipe_instance_ids: string[];
  recipe_names: string[];
  start_sec: number;
  end_sec: number;
  shared: boolean;
  frozen: boolean;
  configuration: Configuration[];
  reuse_intervals?: { start_sec: number; end_sec: number }[];
}
export interface AdvancePreparationPresentation {
  preparation_id: string;
  recipe_id: string;
  recipe_instance_id: string;
  recipe_name: string;
  task_ids: string[];
  description: string;
  original_duration_sec: number;
  source_kind: 'USER_POLICY_ASSUMPTION';
}
export interface PlanPresentation {
  time_origin: { start_at: string };
  range_end_sec: number;
  operations: OperationPresentation[];
  resources: ResourcePresentation[];
  resource_lanes?: string[];
  advance_preparations?: AdvancePreparationPresentation[];
}
export interface ScheduleMetrics {
  makespan_sec: number;
  completion_spread_sec: number;
  cooking_finish_spread_sec?: number | null;
  remaining_cooking_finish_spread_sec?: number | null;
  recipe_cooking_finishes?: { recipe_instance_id: string; cooking_finish_sec: number }[];
  max_continuous_human_sec: number;
  serial_reference_sec: number | null;
  total_human_work_sec: number;
}
export interface PublishedPlan {
  planning_overhead?: { kind: 'INITIAL' | 'REPLAN'; elapsed_ms: number };
  session_id: string;
  plan_version: number;
  parent_plan_version: number;
  state_revision: number;
  knowledge_version: string;
  snapshot_id: string;
  committed_at: string;
  validated: { candidate: { metrics: ScheduleMetrics | null } };
  serial_reference?: { candidate: { metrics: ScheduleMetrics | null } };
}
export interface PlanEnvelope {
  plan: PublishedPlan;
  problem?: unknown;
  optimization?: {
    strategy: 'FULL_QUALITY' | 'FT_KITCHEN';
    spread_basis: 'WORKFLOW_FINISH' | 'COOKING_FINISH';
    rest_gap_sec: number;
  };
  presentation: PlanPresentation;
}
export interface DeviceState {
  device_instance_id: string;
  physical_resource_id?: string;
  component_id?: string;
  availability_status: string;
  occupancy_status: string;
  active_execution_id?: string | null;
  configuration?: Configuration[];
}
export interface Quantity {
  value: number;
  scale: number;
  unit: string;
}
export interface Fraction {
  numerator: number;
  denominator?: number;
}
export interface Movement {
  lot_id: string;
  spec_id: string;
  quantity?: Quantity | null;
  batch_share?: Fraction;
}
export interface Execution {
  execution_id: string;
  task_ids: string[];
  started_task_ids?: string[];
  completed_task_ids?: string[];
  status: string;
  source: string;
  started_at?: string | null;
  finished_at?: string | null;
  remaining_sec?: number | null;
  resource_ids: string[];
}
export interface MaterialLot {
  lot_id: string;
  spec_id: string;
  quantity_available: Quantity;
  quality_status: string;
}
export interface MenuItem {
  recipe_id: string;
  recipe_instance_id: string;
  name: string;
  status?: string;
}
export interface RuntimeSession {
  status?: 'CREATED' | 'ACTIVE' | 'ENDED';
  clock_progress?: {
    enabled: boolean;
    started_at: string | null;
    current_offset_sec: number;
    replan_requested_sec: number | null;
    replan_not_before_sec: number | null;
    replan_not_before_at: string | null;
    waiting_for_boundary: boolean;
  };
  device_presentation?: DeviceState[];
  runtime: {
    session_id: string;
    state_revision: number;
    current_plan_version: number;
    knowledge_version: string;
    time_origin: { start_at: string };
    now_offset_sec: number;
    execution_mode: string;
    executions: Execution[];
    device_states: DeviceState[];
    material_lots: MaterialLot[];
    details?: {
      cancelled_instance_ids: string[];
      lots?: { lot_id: string; material_spec?: { name: string; state?: string } | null }[];
    };
  };
  menu: MenuItem[];
  requires_replan: boolean;
  dispatch_blocked: boolean;
  last_planning_failure: string | null;
  replan_reasons: string[];
}
export interface ExecutionPayload {
  task_id: string;
  execution_id: string;
  consumed: Movement[];
  produced: Movement[];
  output_status: 'QUALIFIED' | 'UNKNOWN' | 'WASTE';
  resource_release_status: 'CONFIRMED' | 'UNCONFIRMED';
  reason?: string;
  remaining_sec?: number;
  recovery_rule_id?: string;
  resource_ids?: string[];
}
export interface FeedbackProposal {
  requires_confirmation: boolean;
  notice: string;
  source: 'MANUAL_CONFIRM';
  state_revision: number;
  plan_version: number;
  completed: boolean;
  task_ids: string[];
  payload: ExecutionPayload;
  movement_names: Record<string, string>;
  release_eligible: boolean;
}
export interface EventRequest {
  event_id: string;
  event_type: string;
  expected_state_revision: number;
  base_plan_version: number;
  source: string;
  payload: object;
  occurred_at?: string;
}
export interface PlanningResult {
  planning?: { timings?: { stage: string; elapsed_ms: number }[] } | null;
  session_id?: string;
  status: 'PUBLISHED' | 'NO_REPLAN' | 'PENDING' | 'FAILED' | 'EVENT_REJECTED';
  event?: { status: string; first_applied: boolean; rejection_reason?: string | null } | null;
  plan?: PublishedPlan | null;
  replan_requested_sec?: number | null;
  replan_not_before_sec?: number | null;
}
export interface Notice {
  cursor: number;
  notification_id: string;
  event_id: string;
  plan_version: number;
  kind: string;
  text: string;
  data: Record<string, unknown>;
}
export interface RecipeGraph {
  recipe_id: string;
  name: string;
  knowledge_version: string;
  operations: {
    operation_id: string;
    action: string;
    description: string;
    duration: { execution_sec: number | null };
    provenance_refs: string[];
  }[];
  dependencies: {
    predecessor_id: string;
    successor_id: string;
    reason: string;
    min_lag_sec: number;
    max_lag_sec: number | null;
  }[];
  materials: { spec_id: string; name: string; state?: string }[];
}
export interface LanguageResult {
  status: string;
  question?: string;
  options?: string[];
  event_accepted?: boolean;
}
