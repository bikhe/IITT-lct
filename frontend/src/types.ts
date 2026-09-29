export type Skill = 'local' | 'connection' | 'emergency';
export type Transport = 'car' | 'foot' | 'bike' | 'transit';
export type Priority = 'normal' | 'urgent';
export type WorkType = 'emergency' | 'connection' | 'local' | 'addon';
export type JobStatus = 'planned' | 'en_route' | 'in_progress' | 'done' | 'cancelled';
export type EventType = 'new_order' | 'urgent_order' | 'cancel_order' | 'engineer_unavailable' | 'manual_assign';
export type ChangeStatus = 'added' | 'assigned' | 'reassigned' | 'shifted' | 'unassigned' | 'cancelled';
export type ReasonCode =
  | 'skill'
  | 'transport'
  | 'shift_window'
  | 'unavailable'
  | 'busy'
  | 'window_late'
  | 'shift_end'
  | 'window_passed';

export interface TimeWindow {
  start: string;
  end: string;
  start_min: number;
  end_min: number;
}

export interface Location {
  lat: number;
  lon: number;
  address: string;
  district: string;
}

export interface TechInfo {
  product?: string | null;
  gbit: boolean;
}

export interface Order {
  id: string;
  skills: Skill[];
  priority: Priority;
  work_type?: WorkType | null;
  window: TimeWindow;
  duration_min: number;
  required_transport?: Transport | null;
  location: Location;
  tech?: TechInfo | null;
  bk_type?: string | null;
  hd_type?: string | null;
  released_min?: number | null;
  /** Авария на узле: ID других заявок из файла, которые закрывает этот выезд. */
  covers?: string[];
  status?: string;
}

export interface Engineer {
  id: string;
  name: string;
  skills: Skill[];
  transport: Transport;
  shift: TimeWindow;
  depot: Location;
}

export interface AssignedJob {
  order_id: string;
  departure_time_min: number;
  arrival_time_min: number;
  start_time_min: number;
  end_time_min: number;
  travel_time_min: number;
  travel_dist_km: number;
  waiting_time_min: number;
  status: JobStatus;
  is_frozen: boolean;
  departure_time: string;
  arrival_time: string;
  start_time: string;
  end_time: string;
}

export interface Route {
  engineer_id: string;
  jobs: AssignedJob[];
  total_distance_km: number;
  total_travel_time_min: number;
  total_work_time_min: number;
  total_waiting_time_min: number;
  unavailable_from_min?: number | null;
  is_active: boolean;
}

export interface UnassignedInfo {
  code: ReasonCode;
  text: string;
  hint?: string | null;
  engineer_codes: Record<string, number>;
  context?: string | null;
}

export interface PlanMetrics {
  total_orders: number;
  assigned_orders: number;
  unassigned_orders: number;
  cancelled_orders: number;
  assignment_rate_pct: number;
  active_engineers_count: number;
  total_engineers_count: number;
  total_distance_km: number;
  car_distance_km: number;
  total_travel_time_min: number;
  total_work_time_min: number;
  emergency_orders: number;
  emergency_within_sla: number;
  emergency_avg_reaction_min?: number | null;
  emergency_max_reaction_min?: number | null;
  extra_crews_needed: number;
  /** (дорога + работа) / длина смен бригад на линии, % */
  avg_load_pct?: number | null;
  /** бригад на линии с загрузкой ниже 50 % */
  low_load_crews?: number;
  engineer_distances: Record<string, number>;
  engineer_order_counts: Record<string, number>;
}

export interface Plan {
  routes: Route[];
  unassigned_orders: Record<string, string>;
  unassigned_details: Record<string, UnassignedInfo>;
  cancelled_orders: Record<string, string>;
  metrics: PlanMetrics;
  as_of_min?: number | null;
}

export interface ComparisonBlock {
  assigned_orders: number;
  total_orders: number;
  assignment_rate_pct: number;
  active_crews: number;
  total_crews: number;
  total_distance_km: number;
  km_per_order: number;
  total_travel_time_min: number;
  emergency_avg_reaction_min?: number | null;
  emergency_within_sla: number;
  emergency_orders: number;
}

export interface ComparisonDiff {
  optimized: ComparisonBlock;
  baseline: ComparisonBlock;
  delta: {
    crew_count: number;
    crew_savings_pct: number;
    total_distance_km: number;
    distance_savings_pct: number;
    km_per_order: number;
    assigned_orders: number;
    travel_time_min: number;
  };
}

export interface ReassignedJob {
  order_id: string;
  old_engineer_id?: string | null;
  new_engineer_id?: string | null;
  old_engineer_name?: string | null;
  new_engineer_name?: string | null;
  old_start_time?: string | null;
  new_start_time?: string | null;
  shift_min?: number | null;
  status: ChangeStatus;
  reason?: string | null;
}

export interface EngineerKm {
  engineer_id: string;
  engineer_name: string;
  km_before: number;
  km_after: number;
  orders_before: number;
  orders_after: number;
}

export interface ReplanEvent {
  event_type: EventType;
  event_time: string;
  order_id?: string | null;
  engineer_id?: string | null;
  new_order?: Order | null;
  description?: string | null;
}

export interface PlanDiff {
  event: ReplanEvent;
  added_order_ids: string[];
  cancelled_order_ids: string[];
  changes: ReassignedJob[];
  reassigned_orders: ReassignedJob[];
  unassigned_after_event: Array<{ order_id: string; reason: string }>;
  called_in_engineer_ids: string[];
  engineer_km: EngineerKm[];
  frozen_jobs_count: number;
  metrics_delta: {
    assigned_delta?: number;
    unassigned_delta?: number;
    cancelled_delta?: number;
    distance_km_delta?: number;
    travel_time_delta_min?: number;
    active_crews_delta?: number;
  };
  summary_ru: string;
}

export interface SolveResponse {
  region: string;
  region_name: string;
  optimized: Plan;
  baseline: Plan;
  morning: Plan;
  diff: ComparisonDiff;
  orders: Order[];
  engineers: Engineer[];
  explanations: Record<string, string>;
  baseline_explanations: Record<string, string>;
  route_explanations: Record<string, string>;
  replan_diff?: PlanDiff | null;
  events: PlanDiff[];
}

export interface DatasetMeta {
  id: string;
  name: string;
  orders_count: number;
  engineers_count: number;
  solved: boolean;
}

export interface DatasetResponse {
  region: string;
  region_name: string;
  orders: Order[];
  engineers: Engineer[];
  solved: boolean;
}

export interface EventResponse {
  region: string;
  optimized: Plan;
  morning: Plan;
  diff: ComparisonDiff;
  plan_diff: PlanDiff;
  orders: Order[];
  engineers: Engineer[];
  explanations: Record<string, string>;
  route_explanations: Record<string, string>;
  events: PlanDiff[];
}

export interface Candidate {
  engineer_id: string;
  engineer_name: string;
  is_current: boolean;
  is_active: boolean;
  code: ReasonCode | null;
  reason: string | null;
  delta_km: number | null;
  start_time: string | null;
  shift_min: number | null;
}

export interface AlternativesResponse {
  order_id: string;
  as_of_min: number | null;
  locked: string | null;
  current_engineer_id: string | null;
  candidates: Candidate[];
}

export interface ScenarioItem {
  id: string;
  title: string;
  event_type: EventType;
  description: string;
  event: ReplanEvent;
}
