import { Building2, Lock, Shuffle, Timer, UserX } from 'lucide-react';
import type { Dispatcher } from '../../state/useDispatcher';
import type { Engineer } from '../../types';
import { crewName, isAllDayEmergency, shortAddress, TRANSPORT_LABELS, WORK_TYPE_LABELS, windowLabel, workType } from '../../lib/labels';
import { activeJobs, isAtRisk, shiftLoadPct, slackMin } from '../../lib/plan';
import { duration, num1, toHHMM } from '../../lib/format';
import { inkOn, WORK_COLORS } from '../../lib/colors';
import { Badge, Button, CrewSwatch, PanelSection, SkillChips, TransportIcon, WORK_ICONS } from '../ui';
import { cx } from '../../lib/cx';
import type { EventPreset } from './OrderPanel';

export const CrewPanel = ({
  d,
  engineer,
  color,
  onEvent,
}: {
  d: Dispatcher;
  engineer: Engineer;
  color: string;
  onEvent: (p: EventPreset) => void;
}) => {
  const route = d.index.routes.get(engineer.id);
  const jobs = activeJobs(route);
  const planned = d.stage === 'plan';
  const optimized = planned && d.variant === 'optimized';
  const off = route?.unavailable_from_min != null;
  // Переезды Москва ↔ пригород — единственное, чего нет в цифрах и маршруте ниже
  const zones = optimized ? d.solve?.route_explanations[engineer.id]?.match(/Переезды между зонами: ([^.]+)\./)?.[1] : undefined;

  return (
    <div className="animate-in">
      <div className="border-b border-line px-4 pt-3 pb-3.5">
        <div className="flex items-center gap-2">
          <CrewSwatch color={color} size={14} />
          <span className="text-heading font-semibold tracking-[-0.01em]">{crewName(engineer.name)}</span>
          {off && (
            <Badge tone="bad" icon={UserX}>
              сошла в {toHHMM(route!.unavailable_from_min!)}
            </Badge>
          )}
        </div>
        <p className="mt-1.5 flex items-center gap-1.5 text-caption text-ink-2">
          <TransportIcon transport={engineer.transport} />
          {TRANSPORT_LABELS[engineer.transport]}
          <span className="text-ink-4" aria-hidden>
            ·
          </span>
          смена <span className="tabular">{engineer.shift.start}–{engineer.shift.end}</span>
        </p>
        <div className="mt-2">
          <SkillChips skills={engineer.skills} />
        </div>
      </div>

      {planned && route && jobs.length > 0 && (
        <dl className="grid grid-cols-4 border-b border-line text-center">
          {(
            [
              ['Заявок', String(jobs.length)],
              ['Пробег', `${num1(route.total_distance_km)} км`],
              ['В пути', duration(route.total_travel_time_min)],
              ['Загрузка', `${shiftLoadPct(route, engineer)} %`],
            ] as const
          ).map(([label, value]) => (
            <div key={label} className="border-r border-line px-1 py-2.5 last:border-r-0">
              <dt className="text-micro text-ink-3">{label}</dt>
              <dd className="text-title font-semibold whitespace-nowrap">{value}</dd>
            </div>
          ))}
        </dl>
      )}

      {zones && (
        <p className="flex items-center gap-2 border-b border-line bg-warn-soft/60 px-4 py-2 text-caption text-warn">
          <Shuffle className="size-3.5 shrink-0" aria-hidden />
          Переезды между зонами: {zones}
        </p>
      )}

      {planned && route && route.jobs.length > 0 && (
        <PanelSection title="Маршрут по порядку">
          <ol className="relative">
            <span className="absolute top-3 bottom-3 left-[11px] w-[2px] rounded" style={{ background: `${color}55` }} aria-hidden />
            <li className="relative flex items-center gap-3 pb-1.5">
              <span className="z-10 grid size-6 place-items-center rounded-md bg-ink text-white" aria-hidden>
                <Building2 className="size-3.5" />
              </span>
              <span className="min-w-0 text-caption text-ink-3">
                <b className="font-semibold text-ink tabular">{engineer.shift.start}</b> старт ·{' '}
                {shortAddress(engineer.depot.address)}
              </span>
            </li>
            {route.jobs.map((job) => {
              const order = d.index.orders.get(job.order_id);
              if (!order) return null;
              const st = d.index.state.get(order.id);
              const visit = st?.kind === 'assigned' ? st.visit : null;
              const cancelled = job.status === 'cancelled';
              const wt = workType(order);
              const Icon = WORK_ICONS[wt];
              const risk = !cancelled && isAtRisk(order, job);
              return (
                <li key={job.order_id} className="relative">
                  <button
                    onClick={() => d.setSelection({ kind: 'order', id: order.id })}
                    className="flex w-full items-start gap-3 rounded-lg py-1.5 pr-1 text-left hover:bg-sunken"
                  >
                    <span
                      className={cx(
                        'z-10 grid size-6 shrink-0 place-items-center text-micro font-semibold tabular',
                        wt === 'emergency' ? 'rounded-[5px]' : 'rounded-full',
                      )}
                      style={{
                        background: cancelled ? '#a6acb6' : color,
                        color: cancelled ? '#fff' : inkOn(color),
                        boxShadow: '0 0 0 2px #fff',
                      }}
                      aria-hidden
                    >
                      {cancelled ? '×' : visit}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5 text-body">
                        <b className="font-semibold tabular">{job.start_time}</b>
                        <span className="font-medium">#{order.id}</span>
                        <Icon className="size-3.5 shrink-0" style={{ color: WORK_COLORS[wt] }} strokeWidth={2.25} aria-label={WORK_TYPE_LABELS[wt]} />
                        <span className="min-w-0 truncate text-caption text-ink-3">{order.location.district}</span>
                        {job.is_frozen && <Lock className="ml-auto size-3 shrink-0 text-ink-3" aria-label="не переносится" />}
                      </span>
                      <span className="block text-caption text-ink-3">
                        {cancelled ? (
                          'отменена клиентом на месте'
                        ) : (
                          <>
                            {isAllDayEmergency(order) ? windowLabel(order) : `окно ${windowLabel(order)}`} · дорога{' '}
                            {job.travel_time_min} мин, {num1(job.travel_dist_km)} км
                          </>
                        )}
                      </span>
                      {risk && (
                        <span className="mt-0.5 inline-flex items-center gap-1 text-caption text-warn">
                          <Timer className="size-3" aria-hidden /> риск опоздания: запас {slackMin(order, job)} мин
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </PanelSection>
      )}

      {planned && jobs.length === 0 && !off && (
        <PanelSection>
          <p className="text-body text-ink-2">В этом плане бригада не выходит на смену.</p>
          <p className="mt-1 text-caption text-ink-3">Вывести её на линию можно из карточки заявки — кнопка «Назначить».</p>
        </PanelSection>
      )}

      {optimized && !off && (
        <div className="px-4 py-3">
          <Button
            tone="ghost"
            size="sm"
            icon={UserX}
            className="-ml-2.5 text-bad hover:bg-bad-soft hover:text-bad"
            onClick={() => onEvent({ type: 'engineer_unavailable', engineerId: engineer.id })}
          >
            Бригада сошла с линии…
          </Button>
        </div>
      )}
    </div>
  );
};
