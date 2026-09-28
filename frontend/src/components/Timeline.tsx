import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, GanttChartSquare, Maximize2, Minimize2 } from 'lucide-react';
import type { Dispatcher } from '../state/useDispatcher';
import type { AssignedJob, ChangeStatus, Order } from '../types';
import { CHANGE_HEX, inkOn } from '../lib/colors';
import { crewShort, isAllDayEmergency, JOB_STATUS_LABELS, WORK_TYPE_LABELS, windowLabel, workType } from '../lib/labels';
import { toHHMM } from '../lib/format';
import { isAtRisk, slackMin } from '../lib/plan';
import { cx } from '../lib/cx';

interface TimelineProps {
  d: Dispatcher;
  colors: Record<string, string>;
  changes: Map<string, ChangeStatus> | null;
  onHoverCrew: (id: string | null) => void;
}

interface Hover {
  x: number;
  y: number;
  order: Order;
  job: AssignedJob;
  crew: string;
}

const ROW = 25;

export const Timeline = ({ d, colors, changes, onHoverCrew }: TimelineProps) => {
  const [open, setOpen] = useState(true);
  const [tall, setTall] = useState(false);
  const [hover, setHover] = useState<Hover | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const axisRef = useRef<HTMLDivElement>(null);
  const [axisW, setAxisW] = useState(600);
  useEffect(() => {
    const el = axisRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setAxisW(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, [open]);
  const { dayStart, dayEnd } = d.index;
  const span = Math.max(60, dayEnd - dayStart);
  const pct = (t: number) => `${((Math.min(Math.max(t, dayStart), dayEnd) - dayStart) / span) * 100}%`;
  const width = (a: number, b: number) => `${(Math.max(0, Math.min(b, dayEnd) - Math.max(a, dayStart)) / span) * 100}%`;
  const hours = useMemo(() => {
    const out: number[] = [];
    for (let t = dayStart; t <= dayEnd; t += 60) out.push(t);
    return out;
  }, [dayStart, dayEnd]);
  // подписи часов через одну, если на час меньше 44 px
  const labelEvery = axisW / Math.max(1, hours.length - 1) < 44 ? 2 : 1;

  const rows = d.engineers.filter((e) => (d.index.routes.get(e.id)?.jobs.length ?? 0) > 0);
  const asOf = d.plan?.as_of_min ?? null;
  const selOrder = d.selection?.kind === 'order' ? d.index.orders.get(d.selection.id) : undefined;
  const selState = selOrder ? d.index.state.get(selOrder.id) : undefined;
  const selCrew =
    d.selection?.kind === 'crew'
      ? d.selection.id
      : selState && 'engineerId' in selState
        ? selState.engineerId ?? null
        : null;

  return (
    <section className="flex shrink-0 flex-col border-t border-line bg-surface">
      <div className="flex h-9 shrink-0 items-center gap-3 pr-2 pl-4">
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex shrink-0 items-center gap-2 text-left text-caption font-semibold text-ink-2 hover:text-ink"
        >
          <GanttChartSquare className="size-4 shrink-0 text-ink-3" aria-hidden />
          Расписание бригад
          <ChevronDown className={cx('size-4 shrink-0 text-ink-3 transition-transform', !open && 'rotate-180')} aria-hidden />
        </button>
        {open && (
          <span className="flex min-w-0 items-center gap-3 overflow-hidden text-caption whitespace-nowrap text-ink-3" aria-label="Обозначения расписания">
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-5 rounded-sm bg-hover ring-1 ring-line" aria-hidden /> смена
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-[2px] w-5 bg-ink-3" aria-hidden /> дорога
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-5 rounded-sm bg-ink-3" aria-hidden /> работа у клиента
            </span>
          </span>
        )}
        {open && (
          <button
            onClick={() => setTall((v) => !v)}
            title={tall ? 'Уменьшить' : 'Показать все бригады'}
            aria-label={tall ? 'Уменьшить' : 'Показать все бригады'}
            className="ml-auto grid size-7 shrink-0 place-items-center rounded-md text-ink-3 hover:bg-hover hover:text-ink"
          >
            {tall ? <Minimize2 className="size-3.5" aria-hidden /> : <Maximize2 className="size-3.5" aria-hidden />}
          </button>
        )}
      </div>
      {open && (
        <div ref={boxRef} className={cx('scroll-thin relative overflow-y-auto pb-2', tall ? 'max-h-[46vh]' : 'max-h-[172px]')} onMouseLeave={() => setHover(null)}>
          <div className="sticky top-0 z-20 flex h-5 bg-surface">
            <div className="w-[150px] shrink-0" />
            <div ref={axisRef} className="relative mr-4 flex-1">
              {hours.filter((_, i) => i % labelEvery === 0).map((t) => (
                <span
                  key={t}
                  className={cx(
                    'absolute top-0 -translate-x-1/2 text-micro text-ink-3 tabular',
                    // подпись часа не должна прятаться под плашкой «сейчас» (~76 px)
                    asOf != null && Math.abs(t - asOf) * (axisW / span) < 58 && 'opacity-0',
                  )}
                  style={{ left: pct(t) }}
                >
                  {toHHMM(t)}
                </span>
              ))}
              {asOf != null && (
                <span
                  className="absolute top-0 -translate-x-1/2 rounded bg-ink px-1 text-micro leading-4 font-semibold text-white tabular"
                  style={{ left: pct(asOf) }}
                  title="Момент последнего события"
                >
                  сейчас {toHHMM(asOf)}
                </span>
              )}
            </div>
          </div>
          <div className="relative">
            {rows.map((e) => {
              const route = d.index.routes.get(e.id)!;
              const color = colors[e.id];
              const dim = selCrew !== null && selCrew !== e.id;
              return (
                <div
                  key={e.id}
                  className={cx('flex items-center', dim && 'opacity-45')}
                  style={{ height: ROW }}
                  onMouseEnter={() => onHoverCrew(e.id)}
                  onMouseLeave={() => onHoverCrew(null)}
                >
                  <button
                    onClick={() => d.setSelection({ kind: 'crew', id: e.id })}
                    className="flex w-[150px] shrink-0 items-center gap-1.5 truncate pr-2 pl-4 text-left text-caption text-ink-2 hover:text-ink"
                  >
                    <span className="size-2 shrink-0 rounded-[2px]" style={{ background: color }} />
                    <span className={cx('truncate', selCrew === e.id && 'font-semibold text-ink')}>{crewShort(e.name)}</span>
                  </button>
                  <div className="relative mr-4 h-full flex-1">
                    {hours.map((t) => (
                      <span key={t} className="absolute inset-y-0 w-px bg-line/60" style={{ left: pct(t) }} />
                    ))}
                    <span
                      className="absolute top-1/2 h-[18px] -translate-y-1/2 rounded bg-hover"
                      style={{ left: pct(e.shift.start_min), width: width(e.shift.start_min, e.shift.end_min) }}
                    />
                    {route.unavailable_from_min != null && (
                      <span
                        className="absolute top-1/2 h-[18px] -translate-y-1/2 rounded bg-[repeating-linear-gradient(45deg,#fdeceb_0_4px,#fff_4px_8px)]"
                        title={`Сошла с линии в ${toHHMM(route.unavailable_from_min)}`}
                        style={{ left: pct(route.unavailable_from_min), width: width(route.unavailable_from_min, e.shift.end_min) }}
                      />
                    )}
                    {selOrder && selState?.kind === 'assigned' && selState.engineerId === e.id && (
                      <span
                        className="absolute top-0.5 bottom-0.5 rounded border border-dashed border-ink-3 bg-info-soft/60"
                        style={{ left: pct(selOrder.window.start_min), width: width(selOrder.window.start_min, selOrder.window.end_min) }}
                        title="Окно клиента"
                      />
                    )}
                    {route.jobs.map((job) => {
                      const order = d.index.orders.get(job.order_id);
                      if (!order) return null;
                      const cancelled = job.status === 'cancelled';
                      const selected = selOrder?.id === order.id;
                      const change = changes?.get(order.id);
                      const emergency = workType(order) === 'emergency';
                      const w = job.end_time_min - job.start_time_min;
                      const wide = (w / span) * 100 > 6;
                      return (
                        <span key={job.order_id}>
                          {job.travel_time_min > 0 && (
                            <span
                              className="absolute top-1/2 h-[2px] -translate-y-1/2"
                              style={{
                                left: pct(job.departure_time_min),
                                width: width(job.departure_time_min, job.arrival_time_min),
                                background: color,
                                opacity: 0.55,
                              }}
                            />
                          )}
                          <button
                            onClick={() => d.setSelection({ kind: 'order', id: order.id })}
                            onMouseEnter={(ev) => {
                              const box = boxRef.current;
                              if (!box) return;
                              const r = box.getBoundingClientRect();
                              const b = ev.currentTarget.getBoundingClientRect();
                              setHover({
                                x: Math.min(Math.max(b.left - r.left + b.width / 2, 130), r.width - 130),
                                y: b.top - r.top + box.scrollTop,
                                order,
                                job,
                                crew: e.name,
                              });
                            }}
                            onMouseLeave={() => setHover(null)}
                            className={cx(
                              'absolute top-1/2 flex h-[18px] -translate-y-1/2 items-center justify-center overflow-hidden rounded-[4px] text-micro font-semibold tabular',
                              selected && 'z-10 ring-2 ring-ink ring-offset-1',
                              job.status === 'done' && 'opacity-55',
                            )}
                            style={{
                              left: pct(job.start_time_min),
                              width: cancelled ? 6 : `max(4px, ${width(job.start_time_min, job.end_time_min)})`,
                              background: cancelled ? '#a6acb6' : color,
                              color: inkOn(color),
                              boxShadow: [
                                emergency && !cancelled ? 'inset 3px 0 0 #d03b3b' : '',
                                change ? `0 0 0 2px #fff, 0 0 0 4px ${CHANGE_HEX[change]}` : '0 0 0 1px #fff',
                              ]
                                .filter(Boolean)
                                .join(', ') || undefined,
                            }}
                          >
                            {wide && !cancelled && order.id}
                          </button>
                        </span>
                      );
                    })}
                  </div>
                </div>
              );
            })}
            {asOf != null && (
              <div className="pointer-events-none absolute inset-y-0 right-4 left-[150px] z-10">
                <span className="absolute inset-y-0 left-0 bg-surface/45" style={{ width: pct(asOf) }} title="Прошедшее время" />
                <span className="absolute inset-y-0 w-[2px] -translate-x-1/2 bg-ink" style={{ left: pct(asOf) }} />
              </div>
            )}
            {rows.length === 0 && (
              <div className="px-4 py-6 text-center text-caption text-ink-3">В плане пока нет маршрутов</div>
            )}
          </div>
          {hover && (
            <div
              className="pointer-events-none absolute z-30 w-[240px] -translate-x-1/2 -translate-y-full rounded-lg border border-line bg-surface px-3 py-2 text-caption shadow-[var(--shadow-pop)]"
              style={{ left: hover.x, top: hover.y - 6 }}
            >
              <div className="font-semibold">
                #{hover.order.id} · {WORK_TYPE_LABELS[workType(hover.order)]}
              </div>
              <div className="text-ink-3">
                {crewShort(hover.crew)} · {JOB_STATUS_LABELS[hover.job.status]}
              </div>
              <div className="mt-1 tabular text-ink-2">
                выезд {hover.job.departure_time} → начало {hover.job.start_time}, конец {hover.job.end_time}
              </div>
              <div className="tabular text-ink-2">
                {isAllDayEmergency(hover.order) ? windowLabel(hover.order) : `окно ${windowLabel(hover.order)}`} · дорога{' '}
                {hover.job.travel_time_min} мин
              </div>
              {isAtRisk(hover.order, hover.job) && (
                <div className="mt-1 text-warn">Запас до конца окна {slackMin(hover.order, hover.job)} мин — риск опоздания</div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
};
