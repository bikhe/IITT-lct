import { FilePlus2, Hand, Lock, UserPlus, UserX, XCircle, Zap, type LucideIcon } from 'lucide-react';
import type { Dispatcher } from '../../state/useDispatcher';
import type { ChangeStatus, EventType, PlanDiff, ReassignedJob } from '../../types';
import { EVENT_LABELS, crewShort, workType } from '../../lib/labels';
import { CHANGE_HEX } from '../../lib/colors';
import { countWord, num1, signed, toMinutes } from '../../lib/format';
import { SLA_MIN } from '../../lib/plan';
import { Badge, CrewSwatch, PanelSection } from '../ui';
import { cx } from '../../lib/cx';

export const EVENT_ICONS: Record<EventType, LucideIcon> = {
  urgent_order: Zap,
  new_order: FilePlus2,
  cancel_order: XCircle,
  engineer_unavailable: UserX,
  manual_assign: Hand,
};

/** Группы в том же порядке и тех же цветах, что и кольца на карте и обводка в расписании. */
const GROUPS: Array<[ChangeStatus, string]> = [
  ['added', 'Новые заявки'],
  ['cancelled', 'Отменены'],
  ['unassigned', 'Сняты — без исполнителя'],
  ['reassigned', 'Перенесены к другой бригаде'],
  ['assigned', 'Назначены из очереди'],
  ['shifted', 'Сдвинуты по времени'],
];

const describe = (c: ReassignedJob): string => {
  switch (c.status) {
    case 'added':
    case 'assigned':
      return c.new_engineer_name ? `${crewShort(c.new_engineer_name)}, начало ${c.new_start_time}` : 'без исполнителя';
    case 'reassigned':
      return `${crewShort(c.old_engineer_name ?? '')} → ${crewShort(c.new_engineer_name ?? '')}, ${c.new_start_time}`;
    case 'shifted':
      return `${crewShort(c.new_engineer_name ?? '')}: ${c.old_start_time} → ${c.new_start_time}`;
    case 'unassigned':
      return c.reason ?? 'без исполнителя';
    case 'cancelled':
      return c.old_engineer_name ? `была у бригады ${crewShort(c.old_engineer_name)}` : 'не была назначена';
  }
};

export const ChangesPanel = ({
  d,
  diff,
  index,
  colors,
}: {
  d: Dispatcher;
  diff: PlanDiff;
  index: number;
  colors: Record<string, string>;
}) => {
  const type = diff.event.event_type;
  const Icon = EVENT_ICONS[type];
  const manual = type === 'manual_assign';
  const kmBefore = diff.engineer_km.reduce((s, r) => s + r.km_before, 0);
  const kmAfter = diff.engineer_km.reduce((s, r) => s + r.km_after, 0);

  // реакция на новую аварию: от поступления до начала работ
  const reaction = (c: ReassignedJob): number | null => {
    const o = d.index.orders.get(c.order_id);
    if (c.status !== 'added' || !o || workType(o) !== 'emergency' || !c.new_start_time) return null;
    return toMinutes(c.new_start_time) - (o.released_min ?? o.window.start_min);
  };

  return (
    <div className="animate-in">
      <div className="border-b border-line px-4 pt-3 pb-3.5">
        <div className="flex items-center gap-2.5">
          <span
            className={cx(
              'grid size-8 shrink-0 place-items-center rounded-lg',
              type === 'urgent_order' ? 'bg-bad-soft text-bad' : 'bg-brand-soft text-ink',
            )}
            aria-hidden
          >
            <Icon className="size-4" />
          </span>
          <div className="min-w-0">
            <h3 className="text-title font-semibold">
              {EVENT_LABELS[type]}
              {!manual && <span className="font-normal text-ink-2"> в {diff.event.event_time}</span>}
            </h3>
            {(manual || d.events.length > 1) && (
              <p className="text-caption text-ink-3">
                {manual ? 'решение диспетчера' : ''}
                {manual && d.events.length > 1 ? ' · ' : ''}
                {d.events.length > 1 ? `событие ${index + 1} из ${d.events.length}` : ''}
              </p>
            )}
          </div>
        </div>
        {(diff.frozen_jobs_count > 0 || diff.called_in_engineer_ids.length > 0) && (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {diff.frozen_jobs_count > 0 && (
              <Badge icon={Lock} title="Работы, к которым бригады уже выехали, начаты или выполнены">
                не трогали {countWord(diff.frozen_jobs_count, 'начатую работу', 'начатые работы', 'начатых работ')}
              </Badge>
            )}
            {diff.called_in_engineer_ids.map((id) => (
              <Badge key={id} tone="info" icon={UserPlus}>
                выведена на линию: {crewShort(d.index.engineers.get(id)?.name ?? id)}
              </Badge>
            ))}
          </div>
        )}
      </div>

      {diff.changes.length === 0 && (
        <PanelSection>
          <p className="text-body text-ink-3">Назначения и время визитов не изменились.</p>
        </PanelSection>
      )}

      {GROUPS.map(([status, title]) => {
        const items = diff.changes.filter((c) => c.status === status);
        if (!items.length) return null;
        return (
          <PanelSection
            key={status}
            title={
              <span className="inline-flex items-center gap-1.5">
                <span className="size-2.5 rounded-full border-2" style={{ borderColor: CHANGE_HEX[status] }} aria-hidden />
                {title}
              </span>
            }
            aside={items.length}
          >
            <ul className="-mx-1.5 space-y-0.5">
              {items.map((c) => {
                const r = reaction(c);
                return (
                  <li key={c.order_id}>
                    <button
                      onClick={() => d.setSelection({ kind: 'order', id: c.order_id })}
                      className="flex w-full items-start gap-2 rounded-md px-1.5 py-1 text-left text-body hover:bg-sunken"
                    >
                      <span className="min-w-14 shrink-0 font-semibold whitespace-nowrap tabular">#{c.order_id}</span>
                      <span className={cx('min-w-0 flex-1', c.status === 'unassigned' ? 'text-bad' : 'text-ink-2')}>
                        {describe(c)}
                        {r != null && (
                          <span className={r <= SLA_MIN ? 'text-ok' : 'text-bad'}>
                            {' '}
                            · реакция {r} мин
                          </span>
                        )}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </PanelSection>
        );
      })}

      {diff.engineer_km.length > 0 && (
        <PanelSection title="Маршруты: было → стало" className="border-b-0">
          <table className="w-full text-body">
            <thead>
              <tr className="text-left text-caption text-ink-3">
                <th className="py-1 font-normal">Бригада</th>
                <th className="py-1 text-right font-normal">Заявок</th>
                <th className="py-1 text-right font-normal">Пробег, км</th>
              </tr>
            </thead>
            <tbody>
              {diff.engineer_km.map((r) => {
                const dk = r.km_after - r.km_before;
                return (
                  <tr key={r.engineer_id} className="border-t border-line/70">
                    <td className="py-1.5">
                      <button
                        className="flex items-center gap-1.5 hover:underline"
                        onClick={() => d.setSelection({ kind: 'crew', id: r.engineer_id })}
                      >
                        <CrewSwatch color={colors[r.engineer_id]} size={8} />
                        {crewShort(r.engineer_name)}
                      </button>
                    </td>
                    <td className="py-1.5 text-right text-ink-2 tabular">
                      {r.orders_before} → <b className="font-semibold text-ink">{r.orders_after}</b>
                    </td>
                    <td className="py-1.5 text-right text-ink-2 tabular">
                      {num1(r.km_before)} → <b className="font-semibold text-ink">{num1(r.km_after)}</b>
                      <span className={cx('ml-1', dk > 0.05 ? 'text-bad' : dk < -0.05 ? 'text-ok' : 'text-ink-3')}>
                        {signed(dk, 1)}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {diff.engineer_km.length > 1 && (
                <tr className="border-t border-line-strong font-semibold">
                  <td className="py-1.5">Итого</td>
                  <td />
                  <td className="py-1.5 text-right tabular">
                    {num1(kmBefore)} → {num1(kmAfter)}
                    <span
                      className={cx('ml-1', kmAfter - kmBefore > 0.05 ? 'text-bad' : kmAfter - kmBefore < -0.05 ? 'text-ok' : 'text-ink-3')}
                    >
                      {signed(kmAfter - kmBefore, 1)}
                    </span>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </PanelSection>
      )}
    </div>
  );
};

/** Журнал событий дня: каждое можно открыть и посмотреть, что оно изменило. */
export const EventLog = ({ d }: { d: Dispatcher }) => (
  <ol className="-mx-2 space-y-0.5">
    {d.events.map((ev, i) => {
      const Icon = EVENT_ICONS[ev.event.event_type];
      return (
        <li key={i}>
          <button
            onClick={() => {
              d.setSelection(null);
              d.setChangesIndex(i);
            }}
            className="flex w-full items-start gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-sunken"
          >
            <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-md bg-hover text-ink-2" aria-hidden>
              <Icon className="size-3.5" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-1.5 text-body font-medium">
                {EVENT_LABELS[ev.event.event_type]}
                {ev.event.event_type !== 'manual_assign' && (
                  <span className="font-normal text-ink-3 tabular">{ev.event.event_time}</span>
                )}
                <span className="ml-auto text-caption font-normal text-ink-3">
                  {countWord(ev.changes.length, 'изменение', 'изменения', 'изменений')}
                </span>
              </span>
              <span className="line-clamp-2 text-caption text-ink-3">{ev.summary_ru}</span>
            </span>
          </button>
        </li>
      );
    })}
  </ol>
);
