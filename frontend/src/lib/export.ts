import type { Engineer, Order, Plan } from '../types';
import { crewName, SKILL_LABELS, TRANSPORT_LABELS, WORK_TYPE_LABELS, workType } from './labels';

const download = (name: string, text: string, type: string) => {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

/** Набор в формате, который принимает загрузка: его можно поправить и загрузить обратно. */
export const downloadDataset = (name: string, orders: Order[], engineers: Engineer[]) =>
  download(
    `набор-${name}.json`,
    JSON.stringify({ name, orders, engineers }, null, 2),
    'application/json',
  );

const cell = (v: string | number) => {
  const s = String(v);
  return /[;"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

/** План в CSV для Excel: по строке на визит и на каждую заявку без исполнителя. */
export const downloadPlanCsv = (name: string, plan: Plan, orders: Order[], engineers: Engineer[]) => {
  const om = new Map(orders.map((o) => [o.id, o]));
  const em = new Map(engineers.map((e) => [e.id, e]));
  const head = [
    'Бригада', 'Транспорт', 'Визит', 'Заявка', 'Вид работ', 'Навык', 'Район', 'Адрес', 'Окно',
    'Выезд', 'Прибытие', 'Начало', 'Окончание', 'Дорога, мин', 'Км от предыдущей точки', 'Статус',
  ];
  const rows: Array<Array<string | number>> = [head];
  for (const r of plan.routes) {
    const e = em.get(r.engineer_id);
    let visit = 0;
    for (const j of r.jobs) {
      const o = om.get(j.order_id);
      if (!o || !e) continue;
      const cancelled = j.status === 'cancelled';
      if (!cancelled) visit += 1;
      rows.push([
        crewName(e.name), TRANSPORT_LABELS[e.transport], cancelled ? '' : visit, o.id, WORK_TYPE_LABELS[workType(o)],
        o.skills.map((s) => SKILL_LABELS[s]).join(', '), o.location.district, o.location.address,
        `${o.window.start}–${o.window.end}`, j.departure_time, j.arrival_time, j.start_time, j.end_time,
        j.travel_time_min, j.travel_dist_km.toFixed(2).replace('.', ','), cancelled ? 'отменена клиентом' : 'назначена',
      ]);
    }
  }
  for (const [id, reason] of Object.entries(plan.unassigned_orders)) {
    const o = om.get(id);
    if (!o) continue;
    rows.push([
      '', '', '', o.id, WORK_TYPE_LABELS[workType(o)], o.skills.map((s) => SKILL_LABELS[s]).join(', '),
      o.location.district, o.location.address, `${o.window.start}–${o.window.end}`, '', '', '', '', '', '',
      `не назначена: ${reason}`,
    ]);
  }
  const text = '﻿' + rows.map((r) => r.map(cell).join(';')).join('\r\n');
  download(`план-${name}.csv`, text, 'text/csv;charset=utf-8');
};
