import type { AssignedJob, Engineer, Order, Plan, Route, UnassignedInfo } from '../types';
import { workType } from './labels';

export const SLA_MIN = 120;
/** Запас до конца окна, при котором визит помечается риском опоздания. */
export const RISK_SLACK_MIN = 15;

export type OrderState =
  | {
      kind: 'assigned';
      engineerId: string;
      job: AssignedJob;
      visit: number; // номер визита среди неотменённых
      total: number;
    }
  | { kind: 'unassigned'; reason: string; info?: UnassignedInfo }
  | { kind: 'cancelled'; note: string; engineerId?: string; job?: AssignedJob };

export interface PlanIndex {
  orders: Map<string, Order>;
  engineers: Map<string, Engineer>;
  routes: Map<string, Route>;
  state: Map<string, OrderState>;
  dayStart: number;
  dayEnd: number;
}

export const dayBounds = (engineers: Engineer[]): [number, number] => {
  if (!engineers.length) return [600, 1320];
  const start = Math.min(...engineers.map((e) => e.shift.start_min));
  const end = Math.max(...engineers.map((e) => e.shift.end_min));
  return [Math.floor(start / 60) * 60, Math.ceil(end / 60) * 60];
};

export const indexPlan = (plan: Plan | null, orders: Order[], engineers: Engineer[]): PlanIndex => {
  const [dayStart, dayEnd] = dayBounds(engineers);
  const idx: PlanIndex = {
    orders: new Map(orders.map((o) => [o.id, o])),
    engineers: new Map(engineers.map((e) => [e.id, e])),
    routes: new Map(),
    state: new Map(),
    dayStart,
    dayEnd,
  };
  if (!plan) return idx;
  for (const route of plan.routes) {
    idx.routes.set(route.engineer_id, route);
    const total = route.jobs.filter((j) => j.status !== 'cancelled').length;
    let visit = 0;
    for (const job of route.jobs) {
      if (job.status === 'cancelled') {
        idx.state.set(job.order_id, {
          kind: 'cancelled',
          note: plan.cancelled_orders[job.order_id] ?? 'Отменена клиентом',
          engineerId: route.engineer_id,
          job,
        });
        continue;
      }
      visit += 1;
      idx.state.set(job.order_id, { kind: 'assigned', engineerId: route.engineer_id, job, visit, total });
    }
  }
  for (const [id, reason] of Object.entries(plan.unassigned_orders)) {
    idx.state.set(id, { kind: 'unassigned', reason, info: plan.unassigned_details[id] });
  }
  for (const [id, note] of Object.entries(plan.cancelled_orders)) {
    if (!idx.state.has(id)) idx.state.set(id, { kind: 'cancelled', note });
  }
  return idx;
};

export const activeJobs = (route: Route | undefined): AssignedJob[] =>
  route ? route.jobs.filter((j) => j.status !== 'cancelled') : [];

/** От какого момента считается реакция на аварию (как на сервере). */
export const releaseMin = (order: Order, dayStart: number): number =>
  order.released_min ?? Math.max(order.window.start_min, dayStart);

export const reactionMin = (order: Order, job: AssignedJob, dayStart: number): number | null =>
  workType(order) === 'emergency' ? job.start_time_min - releaseMin(order, dayStart) : null;

/** Запас до конца окна: насколько визит может опоздать и всё ещё начаться в окне. */
export const slackMin = (order: Order, job: AssignedJob): number => order.window.end_min - job.start_time_min;

export const isAtRisk = (order: Order, job: AssignedJob): boolean =>
  !job.is_frozen && job.status === 'planned' && workType(order) !== 'emergency' && slackMin(order, job) < RISK_SLACK_MIN;

export const shiftLoadPct = (route: Route | undefined, engineer: Engineer): number => {
  if (!route) return 0;
  const len = Math.max(1, engineer.shift.end_min - engineer.shift.start_min);
  return Math.round((100 * (route.total_work_time_min + route.total_travel_time_min)) / len);
};
