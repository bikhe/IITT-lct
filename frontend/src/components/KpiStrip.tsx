import type { ReactNode } from 'react';
import { CircleAlert, CircleCheck, TriangleAlert } from 'lucide-react';
import type { Dispatcher } from '../state/useDispatcher';
import type { PlanMetrics, Transport, WorkType } from '../types';
import { DeltaChip, TRANSPORT_ICONS, WORK_ICONS } from './ui';
import { countWord, num0, num1, plural } from '../lib/format';
import { TRANSPORT_LABELS, WORK_TYPE_LABELS, WORK_TYPE_ORDER, workType } from '../lib/labels';
import { WORK_COLORS } from '../lib/colors';

const Tile = ({
  label,
  value,
  unit,
  sub,
  delta,
  foot,
  title,
  status,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  sub?: ReactNode;
  delta?: ReactNode;
  foot?: ReactNode;
  title?: string;
  status?: 'ok' | 'bad';
}) => (
  <div className="flex min-w-0 flex-1 flex-col gap-0.5 border-r border-line px-4 py-2.5" title={title}>
    <span className="flex items-center gap-1 truncate text-caption text-ink-3">
      {status === 'ok' && <CircleCheck className="size-3.5 shrink-0 text-ok" aria-label="в норме" />}
      {status === 'bad' && <CircleAlert className="size-3.5 shrink-0 text-bad" aria-label="есть отклонения" />}
      {label}
    </span>
    <span className="flex min-w-0 items-baseline gap-1.5 whitespace-nowrap">
      <span className="text-metric font-semibold tracking-[-0.02em]">
        {value}
        {unit && <span className="ml-0.5 text-body font-medium text-ink-3">{unit}</span>}
      </span>
      {sub && <span className="text-caption text-ink-3">{sub}</span>}
      {delta && <span className="ml-0.5 self-center">{delta}</span>}
    </span>
    {foot && <span className="flex min-h-5 items-center gap-2 whitespace-nowrap">{foot}</span>}
  </div>
);

export const KpiStrip = ({ d, onShowUnassigned }: { d: Dispatcher; onShowUnassigned: () => void }) => {
  if (!d.solve || !d.plan) return null;
  const m = d.plan.metrics;
  const isBase = d.variant === 'baseline';
  const hasEvents = d.events.length > 0;
  // До событий сравниваем с базовым вариантом ТЗ; после — с утренним планом (базовый события не обрабатывает)
  const ref: PlanMetrics | null = isBase ? null : hasEvents ? d.solve.morning.metrics : d.solve.baseline.metrics;
  const kmPct = ref && ref.total_distance_km > 0 ? ((m.total_distance_km - ref.total_distance_km) / ref.total_distance_km) * 100 : 0;
  const kmPerOrder = m.assigned_orders ? m.total_distance_km / m.assigned_orders : 0;

  return (
    <div className="flex shrink-0 items-stretch border-b border-line bg-surface">
      <Tile
        label="Назначено заявок"
        value={m.assigned_orders}
        sub={`из ${m.total_orders - m.cancelled_orders}`}
        delta={ref && <DeltaChip value={m.assigned_orders - ref.assigned_orders} good="up" />}
        foot={
          <>
            {m.unassigned_orders > 0 ? (
              <button
                type="button"
                onClick={onShowUnassigned}
                className="-mx-1 inline-flex h-6 items-center gap-1 rounded px-1 text-caption font-medium text-bad hover:bg-bad-soft"
              >
                <TriangleAlert className="size-3.5" aria-hidden />
                {m.unassigned_orders} без исполнителя
              </button>
            ) : (
              <span className="inline-flex items-center gap-1 text-caption text-ok">
                <CircleCheck className="size-3.5" aria-hidden /> все назначены
              </span>
            )}
            {m.cancelled_orders > 0 && (
              <span className="text-caption text-ink-3">
                {countWord(m.cancelled_orders, 'отменена', 'отменены', 'отменено')}
              </span>
            )}
          </>
        }
      />
      <Tile
        label="Бригад на линии"
        value={m.active_engineers_count}
        sub={`из ${m.total_engineers_count}`}
        delta={ref && <DeltaChip value={m.active_engineers_count - ref.active_engineers_count} good="down" />}
      />
      <Tile
        label="Пробег всех бригад"
        value={num1(m.total_distance_km)}
        unit="км"
        delta={ref && <DeltaChip value={kmPct} digits={1} suffix=" %" good="down" />}
        title={`${num1(kmPerOrder)} км на одну назначенную заявку`}
      />
      <Tile
        label="Аварии начаты за 2 ч"
        value={m.emergency_orders ? m.emergency_within_sla : '—'}
        sub={m.emergency_orders ? `из ${m.emergency_orders}` : undefined}
        status={m.emergency_orders ? (m.emergency_within_sla === m.emergency_orders ? 'ok' : 'bad') : undefined}
        title={
          m.emergency_avg_reaction_min != null
            ? `От поступления до начала работ: в среднем ${num0(m.emergency_avg_reaction_min)} мин, максимум ${m.emergency_max_reaction_min} мин`
            : undefined
        }
      />
      <div className="flex w-[168px] shrink-0 items-center px-4 text-caption text-ink-3">
        {isBase ? 'Показан базовый вариант ТЗ' : hasEvents ? 'Стрелки — к утреннему плану' : 'Стрелки — к базовому варианту ТЗ'}
      </div>
    </div>
  );
};

const IconCount = ({ icon: Icon, n, label, color }: { icon: typeof WORK_ICONS.emergency; n: number; label: string; color?: string }) => (
  <span className="inline-flex items-center gap-1 text-caption text-ink-2" title={label}>
    <Icon className="size-3.5" style={color ? { color } : undefined} strokeWidth={2.25} aria-hidden />
    <span className="sr-only">{label}:</span>
    <span className="tabular">{n}</span>
  </span>
);

/** Сводка исходных данных до расчёта (шаг 1 демо: «открыть набор данных»). */
export const DataStrip = ({ d }: { d: Dispatcher }) => {
  const byType = new Map<WorkType, number>();
  d.orders.forEach((o) => byType.set(workType(o), (byType.get(workType(o)) ?? 0) + 1));
  const byTransport = new Map<Transport, number>();
  d.engineers.forEach((e) => byTransport.set(e.transport, (byTransport.get(e.transport) ?? 0) + 1));
  const needTransport = d.orders.filter((o) => o.required_transport).length;
  const minW = d.orders.length ? Math.min(...d.orders.map((o) => o.window.start_min)) : 0;
  const maxW = d.orders.length ? Math.max(...d.orders.map((o) => o.window.end_min)) : 0;
  const hh = (m: number) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;

  return (
    <div className="flex shrink-0 items-stretch border-b border-line bg-surface">
      <Tile
        label="Заявок на день"
        value={d.orders.length}
        foot={WORK_TYPE_ORDER.filter((t) => byType.get(t)).map((t) => (
          <IconCount key={t} icon={WORK_ICONS[t]} n={byType.get(t)!} label={WORK_TYPE_LABELS[t]} color={WORK_COLORS[t]} />
        ))}
      />
      <Tile
        label="Бригад в регионе"
        value={d.engineers.length}
        foot={(['car', 'transit', 'foot', 'bike'] as const)
          .filter((t) => byTransport.get(t))
          .map((t) => (
            <IconCount key={t} icon={TRANSPORT_ICONS[t]} n={byTransport.get(t)!} label={TRANSPORT_LABELS[t]} />
          ))}
      />
      <Tile label="Окна визитов" value={`${hh(minW)}–${hh(maxW)}`} foot={<span className="text-caption text-ink-3">по 2 часа</span>} />
      <Tile
        label="Требуют определённый транспорт"
        value={needTransport}
        sub={plural(needTransport, 'заявка', 'заявки', 'заявок')}
      />
    </div>
  );
};
