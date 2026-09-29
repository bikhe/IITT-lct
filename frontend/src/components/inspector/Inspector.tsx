import type { ReactNode } from 'react';
import { ArrowLeft, Check, CheckCircle2, MousePointerClick, RotateCcw, TriangleAlert, X } from 'lucide-react';
import type { Dispatcher } from '../../state/useDispatcher';
import {
  reasonWithoutWindow,
  UNASSIGNED_SHORT,
  WORK_TYPE_LABELS,
  WORK_TYPE_MINUTES,
  WORK_TYPE_ORDER,
  windowLabel,
  workType,
} from '../../lib/labels';
import { WORK_COLORS } from '../../lib/colors';
import { countWord } from '../../lib/format';
import { Badge, Button, IconButton, PanelSection, WORK_ICONS } from '../ui';
import { OrderPanel, type EventPreset } from './OrderPanel';
import { CrewPanel } from './CrewPanel';
import { ChangesPanel, EventLog } from './ChangesPanel';

export const Inspector = ({
  d,
  colors,
  onEvent,
}: {
  d: Dispatcher;
  colors: Record<string, string>;
  onEvent: (p: EventPreset) => void;
}) => {
  const sel = d.selection;
  const order = sel?.kind === 'order' ? d.index.orders.get(sel.id) : undefined;
  const engineer = sel?.kind === 'crew' ? d.index.engineers.get(sel.id) : undefined;
  const diff = d.changesIndex != null && d.variant === 'optimized' ? d.events[d.changesIndex] : undefined;

  let title = d.stage === 'data' ? 'Как будет построен план' : 'Сводка';
  if (order) title = 'Заявка';
  else if (engineer) title = 'Бригада';
  else if (diff) title = 'Что изменилось';

  const canBack = !!(order || engineer || diff);
  const back = () => {
    if (order || engineer) d.setSelection(null);
    else d.setChangesIndex(null);
  };

  return (
    <aside className="flex w-[340px] shrink-0 flex-col border-l border-line bg-surface min-[1400px]:w-[372px]" aria-label={title}>
      <div className="flex h-11 shrink-0 items-center gap-1 border-b border-line px-2">
        {canBack ? (
          <IconButton icon={ArrowLeft} label={diff && !order && !engineer ? 'К сводке' : 'Назад'} onClick={back} />
        ) : (
          <span className="w-2" />
        )}
        <h2 className="text-body font-semibold">{title}</h2>
        {d.variant === 'baseline' && d.stage === 'plan' && (
          <Badge tone="warn" className="ml-1.5">
            базовый вариант
          </Badge>
        )}
        {canBack && (
          <IconButton
            icon={X}
            label="Закрыть"
            className="ml-auto"
            onClick={() => {
              d.setSelection(null);
              d.setChangesIndex(null);
            }}
          />
        )}
      </div>
      <div
        key={order ? `o:${order.id}` : engineer ? `c:${engineer.id}` : diff ? `d:${d.changesIndex}` : 'summary'}
        className="scroll-thin min-h-0 flex-1 overflow-y-auto"
      >
        {order ? (
          <OrderPanel key={order.id} d={d} order={order} colors={colors} onEvent={onEvent} />
        ) : engineer ? (
          <CrewPanel key={engineer.id} d={d} engineer={engineer} color={colors[engineer.id]} onEvent={onEvent} />
        ) : diff ? (
          <ChangesPanel d={d} diff={diff} index={d.changesIndex!} colors={colors} />
        ) : d.stage === 'data' ? (
          <DataIntro />
        ) : (
          <PlanSummary d={d} />
        )}
      </div>
    </aside>
  );
};

const Step = ({ n, title, children }: { n: number; title: string; children: ReactNode }) => (
  <li className="flex gap-3">
    <span className="grid size-6 shrink-0 place-items-center rounded-full bg-ink text-micro font-semibold text-white" aria-hidden>
      {n}
    </span>
    <span>
      <span className="block text-body font-semibold">{title}</span>
      <span className="block text-caption text-ink-2">{children}</span>
    </span>
  </li>
);

const CheckItem = ({ title, children }: { title: string; children: ReactNode }) => (
  <li className="flex gap-2.5">
    <span className="mt-px grid size-5 shrink-0 place-items-center rounded-full bg-ok-soft text-ok" aria-hidden>
      <Check className="size-3" strokeWidth={3} />
    </span>
    <span className="text-body">
      <b className="font-semibold">{title}</b> <span className="text-ink-2">— {children}</span>
    </span>
  </li>
);

/** До расчёта: что сделает планировщик и какие ограничения проверит (шаг 1–2 демо). */
const DataIntro = () => (
  <div className="animate-in">
    <PanelSection title="Что сделает планировщик">
      <ol className="space-y-3">
        <Step n={1} title="Распределит заявки">
          сначала аварии (начать в пределах 2 ч), затем подключения, ремонты и дозаказы
        </Step>
        <Step n={2} title="Задействует минимум бригад">
          новую — только если заявку больше некуда поставить или авария иначе опоздает
        </Step>
        <Step n={3} title="Построит короткие маршруты">
          без переездов туда-обратно между городами
        </Step>
      </ol>
    </PanelSection>
    <PanelSection title="Проверяется для каждой заявки">
      <ul className="space-y-2">
        <CheckItem title="Навык">есть у бригады</CheckItem>
        <CheckItem title="Транспорт">совпадает, если заявка его требует</CheckItem>
        <CheckItem title="Время">начало в окне клиента, дорога и работа — в смене</CheckItem>
      </ul>
    </PanelSection>
    <PanelSection title="Нормативы работ" aside="без дороги" className="border-b-0">
      <ul className="divide-y divide-line/70">
        {WORK_TYPE_ORDER.map((t) => {
          const Icon = WORK_ICONS[t];
          return (
            <li key={t} className="flex items-center gap-2 py-1.5 text-body">
              <Icon className="size-3.5 shrink-0" style={{ color: WORK_COLORS[t] }} strokeWidth={2.25} aria-hidden />
              <span className="text-ink-2">{WORK_TYPE_LABELS[t]}</span>
              <span className="ml-auto font-semibold tabular">{WORK_TYPE_MINUTES[t]} мин</span>
            </li>
          );
        })}
      </ul>
    </PanelSection>
  </div>
);

/** Ничего не выбрано: то, что требует внимания диспетчера, — заявки без исполнителя и события дня. */
const PlanSummary = ({ d }: { d: Dispatcher }) => {
  if (!d.plan || !d.solve) return null;
  const plan = d.plan;
  const m = plan.metrics;
  const isBase = d.variant === 'baseline';
  const unassigned = Object.keys(plan.unassigned_orders)
    .map((id) => d.index.orders.get(id))
    .filter((o): o is NonNullable<typeof o> => !!o)
    .sort((a, b) => a.window.start_min - b.window.start_min);

  return (
    <div className="animate-in">
      {unassigned.length === 0 ? (
        <PanelSection>
          <p className="flex items-center gap-2 text-body font-semibold text-ok">
            <CheckCircle2 className="size-4" aria-hidden /> Все заявки назначены
          </p>
        </PanelSection>
      ) : (
        <PanelSection
          title={`Без исполнителя · ${unassigned.length}`}
          aside={
            !isBase && m.extra_crews_needed > 0
              ? `нужно ещё ${countWord(m.extra_crews_needed, 'бригада', 'бригады', 'бригад')}`
              : undefined
          }
        >
          <ul className="-mx-2 space-y-0.5">
            {unassigned.map((o) => {
              const wt = workType(o);
              const Icon = WORK_ICONS[wt];
              const info = plan.unassigned_details[o.id];
              const reason = plan.unassigned_orders[o.id];
              return (
                <li key={o.id}>
                  <button
                    onClick={() => d.setSelection({ kind: 'order', id: o.id })}
                    className="w-full rounded-lg px-2 py-1.5 text-left hover:bg-sunken"
                  >
                    <span className="flex items-center gap-1.5">
                      <Icon className="size-3.5 shrink-0" style={{ color: WORK_COLORS[wt] }} strokeWidth={2.25} aria-hidden />
                      <b className="text-body font-semibold tabular">#{o.id}</b>
                      <span className="text-caption text-ink-3">{WORK_TYPE_LABELS[wt]}</span>
                      <span className="ml-auto text-caption text-ink-2 tabular">{windowLabel(o)}</span>
                    </span>
                    <span className="mt-0.5 flex gap-1.5 pl-5 text-caption text-bad">
                      <TriangleAlert className="mt-px size-3.5 shrink-0" aria-hidden />
                      <span>{reason ? reasonWithoutWindow(o, reason) : info ? UNASSIGNED_SHORT[info.code] : 'без исполнителя'}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </PanelSection>
      )}

      {!isBase && d.events.length > 0 && (
        <PanelSection
          title="События дня"
          aside={
            <Button size="sm" tone="ghost" icon={RotateCcw} onClick={d.reset} disabled={d.busy !== null} className="-mr-2">
              Утренний план
            </Button>
          }
        >
          <EventLog d={d} />
        </PanelSection>
      )}

      <p className="flex items-start gap-2 px-4 py-4 text-caption text-ink-3">
        <MousePointerClick className="mt-px size-4 shrink-0" aria-hidden />
        Выберите бригаду или заявку — покажем маршрут и объясним назначение.
      </p>
    </div>
  );
};
