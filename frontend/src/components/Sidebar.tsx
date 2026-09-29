import { useMemo, useState } from 'react';
import { Ban, ChevronRight, Lock, Search, Timer, TriangleAlert, UserX } from 'lucide-react';
import type { Dispatcher } from '../state/useDispatcher';
import type { Engineer, Order } from '../types';
import { Badge, CrewSwatch, LoadBar, SkillIcons, TransportIcon, WORK_ICONS } from './ui';
import { cx } from '../lib/cx';
import {
  crewShort,
  shortAddress,
  TRANSPORT_LABELS,
  UNASSIGNED_SHORT,
  WORK_TYPE_LABELS,
  windowLabel,
  workType,
} from '../lib/labels';
import { WORK_COLORS } from '../lib/colors';
import { activeJobs, isAtRisk, shiftLoadPct, slackMin, type OrderState } from '../lib/plan';
import { num1, toHHMM } from '../lib/format';

export type SideTab = 'crews' | 'orders';
export type OrderFilter = 'all' | 'unassigned' | 'cancelled' | 'emergency' | 'risk';

interface SidebarProps {
  d: Dispatcher;
  colors: Record<string, string>;
  tab: SideTab;
  onTab: (t: SideTab) => void;
  filter: OrderFilter;
  onFilter: (f: OrderFilter) => void;
  onHoverCrew: (id: string | null) => void;
}

export const Sidebar = ({ d, colors, tab, onTab, filter, onFilter, onHoverCrew }: SidebarProps) => {
  const planned = d.stage === 'plan';
  const activeCount = planned ? d.plan?.metrics.active_engineers_count ?? 0 : d.engineers.length;
  return (
    <aside className="flex w-[300px] shrink-0 flex-col border-r border-line bg-surface min-[1400px]:w-[330px]">
      <div className="flex shrink-0 gap-1 border-b border-line px-3 pt-2" role="tablist" aria-label="Списки">
        {(
          [
            ['crews', 'Бригады', planned ? `${activeCount} из ${d.engineers.length}` : d.engineers.length],
            ['orders', 'Заявки', planned ? d.index.state.size : d.orders.length],
          ] as const
        ).map(([id, label, count]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => onTab(id)}
            className={cx(
              '-mb-px flex h-9 items-center gap-1.5 border-b-2 px-2.5 text-body font-medium',
              tab === id ? 'border-ink text-ink' : 'border-transparent text-ink-3 hover:text-ink',
            )}
          >
            {label}
            <span className="rounded bg-hover px-1 text-micro text-ink-3 tabular">{count}</span>
          </button>
        ))}
      </div>
      {tab === 'crews' ? (
        <CrewList d={d} colors={colors} onHoverCrew={onHoverCrew} />
      ) : (
        <OrderList d={d} colors={colors} filter={filter} onFilter={onFilter} />
      )}
    </aside>
  );
};

/* ---------- Бригады ---------- */

const CREW_GRID = 'grid grid-cols-[minmax(0,1fr)_48px_52px] gap-x-2';

const CrewRow = ({
  d,
  engineer,
  color,
  onHoverCrew,
}: {
  d: Dispatcher;
  engineer: Engineer;
  color: string;
  onHoverCrew: (id: string | null) => void;
}) => {
  const planned = d.stage === 'plan';
  const route = d.index.routes.get(engineer.id);
  const jobs = activeJobs(route);
  const selected = d.selection?.kind === 'crew' && d.selection.id === engineer.id;
  const off = route?.unavailable_from_min != null;
  const risky = jobs.filter((j) => {
    const o = d.index.orders.get(j.order_id);
    return o && isAtRisk(o, j);
  }).length;

  return (
    <button
      onClick={() => d.setSelection(selected ? null : { kind: 'crew', id: engineer.id })}
      onMouseEnter={() => onHoverCrew(engineer.id)}
      onMouseLeave={() => onHoverCrew(null)}
      aria-pressed={selected}
      className={cx(
        CREW_GRID,
        'relative w-full items-center gap-y-1 border-b border-line/70 px-4 py-2 text-left transition-colors',
        selected ? 'bg-sunken' : 'hover:bg-sunken/70',
      )}
    >
      {selected && <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: color }} aria-hidden />}
      <span className="flex min-w-0 items-center gap-2">
        <CrewSwatch color={color} />
        <span className="truncate text-body font-medium">{crewShort(engineer.name)}</span>
        {off && (
          <Badge tone="bad" icon={UserX}>
            сошла {toHHMM(route!.unavailable_from_min!)}
          </Badge>
        )}
      </span>
      {planned ? (
        <>
          <span className="text-right text-body tabular">{jobs.length || <span className="text-ink-4">—</span>}</span>
          <span className="text-right text-body tabular">
            {jobs.length ? num1(route!.total_distance_km) : <span className="text-ink-4">—</span>}
          </span>
        </>
      ) : (
        <span className="col-span-2" />
      )}
      <span className="col-span-3 flex min-w-0 items-center gap-2 pl-[18px] text-caption text-ink-3">
        <TransportIcon transport={engineer.transport} />
        <span className="tabular">
          {engineer.shift.start}–{engineer.shift.end}
        </span>
        <SkillIcons skills={engineer.skills} />
        {planned && jobs.length > 0 && (
          <span className="ml-auto flex items-center gap-2">
            {risky > 0 && (
              <span className="inline-flex items-center gap-0.5 text-warn" title={`Риск опоздания: ${risky}`}>
                <Timer className="size-3.5" aria-hidden />
                <span className="sr-only">Риск опоздания:</span>
                {risky}
              </span>
            )}
            <LoadBar pct={shiftLoadPct(route, engineer)} color={color} className="w-[52px]" />
          </span>
        )}
      </span>
    </button>
  );
};

const CrewList = ({
  d,
  colors,
  onHoverCrew,
}: {
  d: Dispatcher;
  colors: Record<string, string>;
  onHoverCrew: (id: string | null) => void;
}) => {
  const [showIdle, setShowIdle] = useState(false);
  const planned = d.stage === 'plan';
  const hasJobs = (e: Engineer) => (d.index.routes.get(e.id)?.jobs.length ?? 0) > 0;
  const active = planned ? d.engineers.filter(hasJobs) : d.engineers;
  const idle = planned ? d.engineers.filter((e) => !hasJobs(e)) : [];

  return (
    <div className="scroll-thin flex-1 overflow-y-auto">
      {planned && (
        <div
          className={cx(
            CREW_GRID,
            'sticky top-0 z-10 border-b border-line bg-surface px-4 py-1.5 text-micro font-semibold tracking-[0.05em] text-ink-3 uppercase',
          )}
        >
          <span>Бригада</span>
          <span className="text-right">Заявок</span>
          <span className="text-right">Км</span>
        </div>
      )}
      {active.map((e) => (
        <CrewRow key={e.id} d={d} engineer={e} color={colors[e.id]} onHoverCrew={onHoverCrew} />
      ))}
      {idle.length > 0 && (
        <>
          <button
            onClick={() => setShowIdle((v) => !v)}
            aria-expanded={showIdle}
            className="flex h-9 w-full items-center gap-1.5 px-4 text-left text-caption font-medium text-ink-3 hover:text-ink"
          >
            <ChevronRight className={cx('size-3.5 shrink-0 transition-transform', showIdle && 'rotate-90')} aria-hidden />
            Не на линии: {idle.length}
          </button>
          {showIdle &&
            idle.map((e) => <CrewRow key={e.id} d={d} engineer={e} color={colors[e.id]} onHoverCrew={onHoverCrew} />)}
        </>
      )}
    </div>
  );
};

/* ---------- Заявки ---------- */

const FILTERS: Array<[OrderFilter, string]> = [
  ['all', 'Все'],
  ['unassigned', 'Без исполнителя'],
  ['emergency', 'Аварии'],
  ['risk', 'Риск опоздания'],
  ['cancelled', 'Отменены'],
];

const matches = (f: OrderFilter, o: Order, st: OrderState | undefined) => {
  switch (f) {
    case 'all':
      return true;
    case 'unassigned':
      return st?.kind === 'unassigned';
    case 'cancelled':
      return st?.kind === 'cancelled';
    case 'emergency':
      return workType(o) === 'emergency';
    case 'risk':
      return st?.kind === 'assigned' && isAtRisk(o, st.job);
  }
};

const OrderList = ({
  d,
  colors,
  filter,
  onFilter,
}: {
  d: Dispatcher;
  colors: Record<string, string>;
  filter: OrderFilter;
  onFilter: (f: OrderFilter) => void;
}) => {
  const [query, setQuery] = useState('');
  const planned = d.stage === 'plan';
  // в показанном варианте плана может не быть заявок, поступивших днём
  const visible = useMemo(() => d.orders.filter((o) => !planned || d.index.state.has(o.id)), [d.orders, d.index, planned]);

  const counts = useMemo(() => {
    const c = Object.fromEntries(FILTERS.map(([f]) => [f, 0])) as Record<OrderFilter, number>;
    for (const o of visible) for (const [f] of FILTERS) if (matches(f, o, d.index.state.get(o.id))) c[f] += 1;
    return c;
  }, [visible, d.index]);

  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    return visible
      .filter((o) => {
        if (planned && !matches(filter, o, d.index.state.get(o.id))) return false;
        if (!q) return true;
        return (
          o.id.toLowerCase().includes(q) ||
          (o.covers ?? []).some((id) => id.includes(q)) ||
          o.location.address.toLowerCase().includes(q) ||
          o.location.district.toLowerCase().includes(q)
        );
      })
      .sort((a, b) => a.window.start_min - b.window.start_min || a.id.localeCompare(b.id));
  }, [visible, d.index, filter, query, planned]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 space-y-2 border-b border-line px-3 py-2.5">
        <label className="flex h-8 items-center gap-2 rounded-lg border border-line bg-sunken px-2.5 focus-within:border-ink-3">
          <Search className="size-3.5 text-ink-3" aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Номер, адрес или район"
            aria-label="Поиск заявки"
            className="w-full bg-transparent text-body outline-none placeholder:text-ink-3"
          />
        </label>
        {planned && (
          <div className="flex flex-wrap gap-1" role="group" aria-label="Фильтр заявок">
            {FILTERS.filter(([f]) => f === 'all' || counts[f] > 0 || f === filter).map(([f, label]) => (
              <button
                key={f}
                onClick={() => onFilter(f)}
                aria-pressed={filter === f}
                className={cx(
                  'inline-flex h-6 items-center gap-1 rounded-md px-2 text-caption font-medium',
                  filter === f ? 'bg-ink text-white' : 'bg-hover text-ink-2 hover:text-ink',
                  f === 'unassigned' && filter !== f && 'text-bad',
                )}
              >
                {label}
                <span className="tabular opacity-70">{counts[f]}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="scroll-thin flex-1 overflow-y-auto">
        {list.map((o) => (
          <OrderRow key={o.id} d={d} order={o} colors={colors} />
        ))}
        {list.length === 0 && <div className="px-4 py-10 text-center text-caption text-ink-3">Ничего не найдено</div>}
      </div>
    </div>
  );
};

const OrderStatus = ({ d, order, colors }: { d: Dispatcher; order: Order; colors: Record<string, string> }) => {
  const st = d.index.state.get(order.id);
  if (d.stage === 'data') {
    return order.required_transport ? (
      <span className="inline-flex items-center gap-1 text-ink-2">
        только <TransportIcon transport={order.required_transport} /> {TRANSPORT_LABELS[order.required_transport].toLowerCase()}
      </span>
    ) : null;
  }
  if (st?.kind === 'assigned') {
    const eng = d.index.engineers.get(st.engineerId);
    return (
      <span className="inline-flex items-center gap-1.5 text-ink-2">
        {isAtRisk(order, st.job) && (
          <Badge tone="warn" icon={Timer} title="Запас до конца окна меньше 15 минут">
            запас {slackMin(order, st.job)} мин
          </Badge>
        )}
        {st.job.is_frozen && <Lock className="size-3 text-ink-3" aria-label="Уже в работе — не переносится" />}
        <CrewSwatch color={colors[st.engineerId]} size={8} />
        {crewShort(eng?.name ?? '')}
        <span className="tabular text-ink">{st.job.start_time}</span>
      </span>
    );
  }
  if (st?.kind === 'unassigned') {
    const short =
      st.info && (st.info.engineer_codes.ok ?? 0) > 0 ? 'можно вывести бригаду' : st.info ? UNASSIGNED_SHORT[st.info.code] : 'без исполнителя';
    return (
      <span className="inline-flex min-w-0 items-center gap-1 text-bad" title={st.reason}>
        <TriangleAlert className="size-3.5 shrink-0" aria-hidden />
        <span className="truncate">{short}</span>
      </span>
    );
  }
  if (st?.kind === 'cancelled') {
    return (
      <span className="inline-flex items-center gap-1 text-ink-3">
        <Ban className="size-3.5" aria-hidden /> отменена
      </span>
    );
  }
  return null;
};

const OrderRow = ({ d, order, colors }: { d: Dispatcher; order: Order; colors: Record<string, string> }) => {
  const selected = d.selection?.kind === 'order' && d.selection.id === order.id;
  const wt = workType(order);
  const Icon = WORK_ICONS[wt];
  return (
    <button
      onClick={() => d.setSelection(selected ? null : { kind: 'order', id: order.id })}
      aria-pressed={selected}
      className={cx(
        'relative flex w-full flex-col gap-0.5 border-b border-line/70 px-4 py-2 text-left',
        selected ? 'bg-sunken' : 'hover:bg-sunken/70',
      )}
    >
      {selected && <span className="absolute inset-y-0 left-0 w-[3px] bg-ink" aria-hidden />}
      <span className="flex w-full items-center gap-1.5">
        <Icon className="size-3.5 shrink-0" style={{ color: WORK_COLORS[wt] }} strokeWidth={2.25} aria-hidden />
        <span className="text-body font-semibold tabular">#{order.id}</span>
        <span className="text-caption text-ink-3">{WORK_TYPE_LABELS[wt]}</span>
        <span className="ml-auto text-caption text-ink-2 tabular">{windowLabel(order)}</span>
      </span>
      <span className="flex w-full min-w-0 items-center gap-2 pl-5 text-caption">
        <span className="min-w-0 flex-1 truncate text-ink-3" title={shortAddress(order.location.address)}>
          {order.location.district}
        </span>
        <span className="flex min-w-0 shrink-0 justify-end">
          <OrderStatus d={d} order={order} colors={colors} />
        </span>
      </span>
    </button>
  );
};
