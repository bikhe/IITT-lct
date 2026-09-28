import { useEffect, useState, type ReactNode } from 'react';
import {
  Ban,
  Check,
  ChevronRight,
  Lightbulb,
  Lock,
  MapPin,
  Route as RouteIcon,
  Timer,
  TriangleAlert,
  XCircle,
} from 'lucide-react';
import type { Dispatcher } from '../../state/useDispatcher';
import type { AlternativesResponse, Candidate, Order, ReasonCode } from '../../types';
import { api } from '../../api';
import { parseExplanation } from '../../lib/explain';
import {
  arrivedBeforeDay,
  crewName,
  crewShort,
  isAllDayEmergency,
  JOB_STATUS_LABELS,
  reasonWithoutWindow,
  REASON_LABELS,
  shortAddress,
  SKILL_LABELS,
  TRANSPORT_LABELS,
  windowLabel,
  workType,
} from '../../lib/labels';
import { isAtRisk, reactionMin, SLA_MIN, slackMin, type OrderState } from '../../lib/plan';
import { num1 } from '../../lib/format';
import { Badge, Button, CrewSwatch, Disclosure, PanelSection, Spinner, TransportIcon, WorkTag } from '../ui';
import { cx } from '../../lib/cx';

export type EventPreset = { type: 'cancel_order'; orderId: string } | { type: 'engineer_unavailable'; engineerId: string };

export const OrderPanel = ({
  d,
  order,
  colors,
  onEvent,
}: {
  d: Dispatcher;
  order: Order;
  colors: Record<string, string>;
  onEvent: (p: EventPreset) => void;
}) => {
  const st = d.index.state.get(order.id);
  const planned = d.stage === 'plan' && !!d.plan;
  const optimized = planned && d.variant === 'optimized';

  return (
    <div className="animate-in">
      <div className="border-b border-line px-4 pt-3 pb-3.5">
        <div className="flex items-center gap-2">
          <span className="text-heading font-semibold tracking-[-0.01em] tabular">#{order.id}</span>
          <WorkTag order={order} />
          {order.priority === 'urgent' && workType(order) !== 'emergency' && <Badge tone="bad">срочная</Badge>}
        </div>
        <p className="mt-1 flex items-start gap-1.5 text-caption text-ink-2">
          <MapPin className="mt-px size-3.5 shrink-0 text-ink-3" aria-hidden />
          <span>
            {shortAddress(order.location.address)}
            <span className="text-ink-3"> · {order.location.district}</span>
          </span>
        </p>
      </div>

      {planned && st?.kind === 'assigned' && <Assignment d={d} order={order} st={st} color={colors[st.engineerId]} />}
      {planned && st?.kind === 'unassigned' && <Unassigned order={order} st={st} />}
      {planned && st?.kind === 'cancelled' && (
        <PanelSection>
          <p className="flex items-start gap-2 text-body text-ink-2">
            <Ban className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />
            {st.note}
          </p>
        </PanelSection>
      )}

      <Constraints d={d} order={order} st={planned ? st : undefined} />

      {optimized && d.region && <Alternatives key={`${order.id}:${d.events.length}`} d={d} order={order} colors={colors} />}

      {optimized && st?.kind !== 'cancelled' && (
        <div className="px-4 py-3">
          <Button
            tone="ghost"
            size="sm"
            icon={XCircle}
            className="-ml-2.5 text-bad hover:bg-bad-soft hover:text-bad"
            onClick={() => onEvent({ type: 'cancel_order', orderId: order.id })}
          >
            Клиент отменил заявку…
          </Button>
        </div>
      )}
    </div>
  );
};

/** Кто выполняет и когда: выезд → начало → конец. Прибытие совпадает с началом, кроме случаев ожидания. */
const Assignment = ({
  d,
  order,
  st,
  color,
}: {
  d: Dispatcher;
  order: Order;
  st: Extract<OrderState, { kind: 'assigned' }>;
  color: string;
}) => {
  const eng = d.index.engineers.get(st.engineerId);
  const { job } = st;
  if (!eng) return null;
  const times: Array<[string, string]> = [
    ['Выезд', job.departure_time],
    ['Начало', job.start_time],
    ['Конец', job.end_time],
  ];
  return (
    <PanelSection>
      <div className="flex items-center justify-between gap-2">
        <button
          className="flex min-w-0 items-center gap-2 rounded-md text-title font-semibold hover:underline"
          onClick={() => d.setSelection({ kind: 'crew', id: eng.id })}
        >
          <CrewSwatch color={color} size={12} />
          <span className="truncate">{crewName(eng.name)}</span>
          <ChevronRight className="size-4 shrink-0 text-ink-3" aria-hidden />
        </button>
        <span className="shrink-0 text-caption text-ink-3">
          визит {st.visit} из {st.total}
        </span>
      </div>
      <ol className="mt-2.5 grid grid-cols-3 gap-1 text-center">
        {times.map(([label, time]) => (
          <li key={label} className={cx('rounded-lg px-1 py-1.5', label === 'Начало' ? 'bg-ink text-white' : 'bg-sunken')}>
            <span className={cx('block text-micro', label === 'Начало' ? 'text-white/75' : 'text-ink-3')}>{label}</span>
            <span className="block text-title font-semibold tabular">{time}</span>
          </li>
        ))}
      </ol>
      <p className="mt-2 text-caption text-ink-3">
        дорога {job.travel_time_min} мин, {num1(job.travel_dist_km)} км
        {job.arrival_time_min < job.start_time_min &&
          ` · прибытие ${job.arrival_time}, ожидание ${job.start_time_min - job.arrival_time_min} мин`}
      </p>
      {(job.is_frozen || isAtRisk(order, job)) && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {job.is_frozen && (
            <Badge icon={Lock} title="Бригада уже выехала или работает — при перепланировании не переносится">
              {JOB_STATUS_LABELS[job.status].toLowerCase()} — не переносится
            </Badge>
          )}
          {isAtRisk(order, job) && (
            <Badge tone="warn" icon={Timer}>
              риск опоздания: запас {slackMin(order, job)} мин
            </Badge>
          )}
        </div>
      )}
    </PanelSection>
  );
};

const Unassigned = ({ order, st }: { order: Order; st: Extract<OrderState, { kind: 'unassigned' }> }) => (
  <PanelSection>
    <div className="rounded-lg border border-bad/20 bg-bad-soft px-3 py-2.5">
      <p className="flex items-center gap-1.5 text-body font-semibold text-bad">
        <TriangleAlert className="size-4" aria-hidden /> Без исполнителя
      </p>
      <p className="mt-1 text-body text-ink">{reasonWithoutWindow(order, st.reason)}</p>
      {st.info?.hint && (
        <p className="mt-1.5 flex items-start gap-1.5 text-caption text-ink-2">
          <Lightbulb className="mt-px size-3.5 shrink-0 text-ink-3" aria-label="Что можно сделать" />
          {reasonWithoutWindow(order, st.info.hint)}
        </p>
      )}
    </div>
  </PanelSection>
);

const Row = ({
  mark,
  label,
  children,
  note,
}: {
  mark: 'ok' | 'bad' | 'plain' | ReactNode;
  label: string;
  children: ReactNode;
  /** Пояснение под значением, мельче. */
  note?: ReactNode;
}) => (
  <li className="grid grid-cols-[20px_72px_minmax(0,1fr)] items-start gap-x-2">
    <span
      className={cx(
        'mt-px grid size-5 place-items-center rounded-full',
        mark === 'ok' ? 'bg-ok-soft text-ok' : mark === 'bad' ? 'bg-bad-soft text-bad' : 'bg-hover text-ink-3',
      )}
      role={mark === 'ok' || mark === 'bad' ? 'img' : undefined}
      aria-label={mark === 'ok' ? 'выполнено' : mark === 'bad' ? 'не выполнено' : undefined}
    >
      {mark === 'ok' ? (
        <Check className="size-3" strokeWidth={3} aria-hidden />
      ) : mark === 'bad' ? (
        <TriangleAlert className="size-3" aria-hidden />
      ) : mark === 'plain' ? (
        <span className="size-1.5 rounded-full bg-ink-4" aria-hidden />
      ) : (
        mark
      )}
    </span>
    <span className="pt-0.5 text-caption text-ink-3">{label}</span>
    <span className="min-w-0 text-body">
      {children}
      {note && <span className="block text-caption text-ink-3">{note}</span>}
    </span>
  </li>
);

/**
 * Требования заявки. Если она назначена — это и есть «почему эта бригада»: каждое ограничение
 * с отметкой, плюс чем бригада лучше по маршруту. Отдельного «паспорта» с теми же полями нет.
 */
const Constraints = ({ d, order, st }: { d: Dispatcher; order: Order; st: OrderState | undefined }) => {
  const assigned = st?.kind === 'assigned' ? st : null;
  const eng = assigned ? d.index.engineers.get(assigned.engineerId) : undefined;
  const skills = order.skills.map((s) => SKILL_LABELS[s]).join(', ');
  const emergencyAllDay = isAllDayEmergency(order);
  const arrival = arrivedBeforeDay(order) ? 'поступила до начала смен' : `поступила в ${order.window.start}`;

  if (!assigned || !eng) {
    return (
      <PanelSection title="Требования заявки">
        <ul className="space-y-2">
          <Row mark="plain" label="Навык">
            {skills}
          </Row>
          <Row mark="plain" label="Транспорт">
            {order.required_transport ? `только ${TRANSPORT_LABELS[order.required_transport].toLowerCase()}` : 'любой'}
          </Row>
          <Row
            mark="plain"
            label="Время"
            note={emergencyAllDay ? `начать как можно раньше · работа ${order.duration_min} мин` : `работа ${order.duration_min} мин`}
          >
            {emergencyAllDay ? arrival : `окно ${windowLabel(order)}`}
          </Row>
        </ul>
      </PanelSection>
    );
  }

  const { job } = assigned;
  const explanation = d.solve && (d.variant === 'optimized' ? d.solve.explanations : d.solve.baseline_explanations)[order.id];
  const routeFact = parseExplanation(explanation ?? undefined).facts.find((f) => f.kind === 'route');
  const added = routeFact?.text.match(/добавляет\s+([\d,]+)\s*км/)?.[1];
  const jobs = d.index.routes.get(eng.id)?.jobs ?? [];
  const i = jobs.findIndex((j) => j.order_id === order.id);
  const prev = i > 0 ? `#${jobs[i - 1].order_id}` : null;
  const next = i >= 0 && i < jobs.length - 1 ? `#${jobs[i + 1].order_id}` : null;
  const where = !prev && !next ? 'единственный визит' : !prev ? `первый визит, затем ${next}` : !next ? `последний, после ${prev}` : `между ${prev} и ${next}`;
  const reaction = reactionMin(order, job, d.index.dayStart);

  return (
    <PanelSection title="Почему эта бригада">
      <ul className="space-y-2">
        <Row mark="ok" label="Навык">
          {skills}
        </Row>
        <Row
          mark="ok"
          label="Транспорт"
          note={order.required_transport ? 'как требует заявка' : 'заявка не ограничивает'}
        >
          <span className="inline-flex items-center gap-1.5">
            <TransportIcon transport={eng.transport} />
            {TRANSPORT_LABELS[eng.transport]}
          </span>
        </Row>
        <Row mark="ok" label="Время" note={`смена ${eng.shift.start}–${eng.shift.end}`}>
          {emergencyAllDay ? arrival : `окно ${windowLabel(order)}`}
        </Row>
        {reaction != null && (
          <Row
            mark={reaction <= SLA_MIN ? 'ok' : 'bad'}
            label="Реакция"
            note={reaction <= SLA_MIN ? 'в пределах ориентира 2 ч' : 'дольше ориентира 2 ч'}
          >
            {reaction} мин {arrivedBeforeDay(order) ? 'от начала дня' : 'от поступления'}
          </Row>
        )}
        <Row mark={<RouteIcon className="size-3" aria-hidden />} label="Маршрут" note={where}>
          {added ? `добавляет ${added} км` : 'в маршруте'}
        </Row>
      </ul>
    </PanelSection>
  );
};

/** «Почему не другая бригада» + ручное переназначение. Проверка — тем же оценщиком, что и у планировщика. */
const Alternatives = ({ d, order, colors }: { d: Dispatcher; order: Order; colors: Record<string, string> }) => {
  const [data, setData] = useState<AlternativesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const region = d.region!;

  useEffect(() => {
    let alive = true;
    api
      .alternatives(region, order.id)
      .then((r) => alive && setData(r))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [region, order.id]);

  if (error) return <PanelSection>{<p className="text-caption text-bad">{error}</p>}</PanelSection>;
  if (!data)
    return (
      <PanelSection>
        <p className="flex items-center gap-2 text-caption text-ink-3">
          <Spinner className="size-3.5" /> Проверяем другие бригады…
        </p>
      </PanelSection>
    );

  const current = data.candidates.find((c) => c.is_current);
  const fitting = data.candidates.filter((c) => c.code !== 'skill' && c.code !== 'transport').length;
  const feasible = data.candidates.filter((c) => c.code === null && !c.is_current);
  const blocked = data.candidates.filter((c) => c.code !== null && !c.is_current);
  const groups = new Map<ReasonCode, Candidate[]>();
  blocked.forEach((c) => groups.set(c.code!, [...(groups.get(c.code!) ?? []), c]));
  const assign = (c: Candidate) => {
    setConfirm(null);
    void d
      .applyEvent({
        event_type: 'manual_assign',
        event_time: '00:00',
        order_id: order.id,
        engineer_id: c.engineer_id,
        description: `Ручное назначение: #${order.id} → ${c.engineer_name}`,
      })
      .catch(() => undefined);
  };

  return (
    <PanelSection title={current ? 'Другие бригады' : 'Кто мог бы взять'} aside={`по навыку подходят ${fitting}`}>
      {data.locked && <p className="mb-2 text-caption text-ink-3">Переназначить нельзя: {data.locked.toLowerCase()}</p>}
      {feasible.length > 0 ? (
        <ul className="-mx-2 space-y-0.5">
          {feasible.map((c) => {
            const worse = current?.delta_km != null && c.delta_km! > current.delta_km + 0.05;
            return (
              <li key={c.engineer_id} className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-sunken">
                <CrewSwatch color={colors[c.engineer_id]} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body font-medium">{crewShort(c.engineer_name)}</span>
                  <span className="block text-caption text-ink-3">
                    начало <span className="tabular">{c.start_time}</span>
                    {!c.is_active && ' · выведет на линию'}
                    {c.shift_min ? ` · сдвиг визитов ${c.shift_min} мин` : ''}
                  </span>
                </span>
                <span
                  className={cx('text-right text-body tabular', worse ? 'text-bad' : 'text-ink-2')}
                  title="Прирост пробега, если поставить заявку в маршрут этой бригады"
                >
                  +{num1(c.delta_km!)} км
                </span>
                {!data.locked &&
                  (confirm === c.engineer_id ? (
                    <Button size="sm" tone="primary" onClick={() => assign(c)} disabled={d.busy !== null}>
                      Подтвердить
                    </Button>
                  ) : (
                    <Button size="sm" onClick={() => setConfirm(c.engineer_id)} disabled={d.busy !== null}>
                      Назначить
                    </Button>
                  ))}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-caption text-ink-3">
          {current ? 'Больше никто не может взять её без нарушения ограничений.' : 'Никто не может взять её без нарушения ограничений.'}
        </p>
      )}
      {groups.size > 0 && (
        <div className="-mx-1.5 mt-1.5">
          {[...groups.entries()].map(([code, list]) => (
            <Disclosure
              key={code}
              summary={
                <>
                  <span className="font-medium">{REASON_LABELS[code]}</span>
                  <span className="ml-auto text-ink-3 tabular">{list.length}</span>
                </>
              }
            >
              <p className="text-caption text-ink-3">{list[0].reason}</p>
              <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                {list.map((c) => (
                  <span key={c.engineer_id} className="inline-flex items-center gap-1.5 text-caption text-ink-2">
                    <CrewSwatch color={colors[c.engineer_id]} size={8} />
                    {crewShort(c.engineer_name)}
                  </span>
                ))}
              </p>
            </Disclosure>
          ))}
        </div>
      )}
      {d.busy === 'event' && (
        <p className="mt-2 flex items-center gap-2 text-caption text-ink-3">
          <Spinner className="size-3.5" /> Перестраиваем маршруты…
        </p>
      )}
    </PanelSection>
  );
};
