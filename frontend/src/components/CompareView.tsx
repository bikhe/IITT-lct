import { useMemo, useState, type ReactNode } from 'react';
import { Info, Map as MapIcon } from 'lucide-react';
import type { Dispatcher } from '../state/useDispatcher';
import type { Plan, PlanMetrics } from '../types';
import { crewShort } from '../lib/labels';
import { countWord, num1, signed } from '../lib/format';
import { Button, Card, CrewSwatch, DeltaChip, Segmented } from './ui';
import { cx } from '../lib/cx';

/** Форма «акцент»: наш план — синим, базовый — серым (контраст к белому ≥ 3:1, чтобы полосы читались). */
const OURS = '#2a78d6';
const BASE = '#8a919c';

interface CrewRow {
  id: string;
  name: string;
  oursKm: number;
  baseKm: number;
  oursN: number;
  baseN: number;
}

const perCrew = (plan: Plan) => {
  const out = new Map<string, { km: number; n: number }>();
  for (const r of plan.routes) {
    out.set(r.engineer_id, { km: r.total_distance_km, n: r.jobs.filter((j) => j.status !== 'cancelled').length });
  }
  return out;
};

const kmPerOrder = (m: PlanMetrics) => (m.assigned_orders ? m.total_distance_km / m.assigned_orders : 0);

export const CompareView = ({ d, colors }: { d: Dispatcher; colors: Record<string, string> }) => {
  const [which, setWhich] = useState<'morning' | 'current'>('morning');
  const [mode, setMode] = useState<'chart' | 'table'>('chart');
  const [hover, setHover] = useState<string | null>(null);
  const solve = d.solve!;
  const hasEvents = d.events.length > 0;
  const ours = which === 'morning' || !hasEvents ? solve.morning : solve.optimized;
  const base = solve.baseline;
  const om = ours.metrics;
  const bm = base.metrics;

  const rows = useMemo<CrewRow[]>(() => {
    const a = perCrew(ours);
    const b = perCrew(base);
    return d.engineers
      .map((e) => ({
        id: e.id,
        name: crewShort(e.name),
        oursKm: a.get(e.id)?.km ?? 0,
        baseKm: b.get(e.id)?.km ?? 0,
        oursN: a.get(e.id)?.n ?? 0,
        baseN: b.get(e.id)?.n ?? 0,
      }))
      .filter((r) => r.oursN > 0 || r.baseN > 0)
      .sort((x, y) => Math.max(y.oursKm, y.baseKm) - Math.max(x.oursKm, x.baseKm));
  }, [ours, base, d.engineers]);

  const maxKm = Math.max(1, ...rows.map((r) => Math.max(r.oursKm, r.baseKm)));
  const kmSaved = bm.total_distance_km - om.total_distance_km;
  const kmPct = bm.total_distance_km ? (kmSaved / bm.total_distance_km) * 100 : 0;
  const crewDelta = bm.active_engineers_count - om.active_engineers_count;

  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <div className="mx-auto max-w-[1120px] px-6 py-6">
        <div className="flex flex-wrap items-end gap-4">
          <div className="min-w-0 flex-1">
            <h1 className="text-heading font-semibold tracking-[-0.01em]">Наш план и базовый вариант</h1>
            <p className="mt-1 max-w-[720px] text-body text-ink-2">
              Базовый вариант ТЗ: заявки по порядку поступления — первому подходящему инженеру, без оптимизации. Данные и
              расстояния у обоих планов одни и те же.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {hasEvents && (
              <Segmented
                size="sm"
                label="Какой наш план сравнивать"
                value={which}
                onChange={setWhich}
                options={[
                  { value: 'morning', label: 'Утренний план', hint: 'Базовый вариант события дня не обрабатывает' },
                  { value: 'current', label: 'После событий' },
                ]}
              />
            )}
            <Button
              icon={MapIcon}
              onClick={() => {
                d.setVariant('baseline');
                d.setSelection(null);
                d.setView('plan');
              }}
            >
              Базовый на карте
            </Button>
          </div>
        </div>

        {which === 'current' && hasEvents && (
          <p className="mt-3 flex items-center gap-2 rounded-lg bg-warn-soft px-3 py-2 text-body text-warn">
            <Info className="size-4 shrink-0" aria-hidden /> После событий в нашем плане другой набор заявок, а базовый — утренний:
            сравнение ориентировочное.
          </p>
        )}

        <h2 className="mt-6 text-micro font-semibold tracking-[0.05em] text-ink-3 uppercase">
          Обязательные метрики ТЗ · меньше — лучше
        </h2>
        <div className="mt-2 grid grid-cols-2 gap-4">
          <MetricCard
            title="Бригад задействовано"
            ours={om.active_engineers_count}
            base={bm.active_engineers_count}
            verdict={
              crewDelta === 0
                ? 'столько же'
                : `на ${countWord(Math.abs(crewDelta), 'бригаду', 'бригады', 'бригад')} ${crewDelta > 0 ? 'меньше' : 'больше'}`
            }
            tone={crewDelta > 0 ? 'good' : crewDelta < 0 ? 'bad' : 'neutral'}
          >
            <Pips total={om.total_engineers_count} used={om.active_engineers_count} color={OURS} label="наш" />
            <Pips total={bm.total_engineers_count} used={bm.active_engineers_count} color={BASE} label="базовый" />
          </MetricCard>
          <MetricCard
            title="Суммарный пробег"
            ours={num1(om.total_distance_km)}
            base={num1(bm.total_distance_km)}
            unit="км"
            verdict={Math.abs(kmSaved) <= 0.05 ? 'так же' : `${signed(-kmSaved, 1)} км (${signed(-kmPct, 1)} %)`}
            tone={kmSaved > 0.05 ? 'good' : kmSaved < -0.05 ? 'bad' : 'neutral'}
          >
            <Bar value={om.total_distance_km} max={Math.max(om.total_distance_km, bm.total_distance_km)} color={OURS} label="наш" />
            <Bar value={bm.total_distance_km} max={Math.max(om.total_distance_km, bm.total_distance_km)} color={BASE} label="базовый" />
          </MetricCard>
        </div>

        <div className="mt-4 grid grid-cols-5 gap-4">
          <Small
            label="Назначено заявок"
            ours={om.assigned_orders}
            base={bm.assigned_orders}
            better="up"
            of={om.total_orders - om.cancelled_orders}
          />
          <Small label="Пробег на заявку, км" ours={kmPerOrder(om)} base={kmPerOrder(bm)} better="down" digits={1} />
          <Small
            label="Аварии начаты за 2 ч"
            ours={om.emergency_within_sla}
            base={bm.emergency_within_sla}
            better="up"
            of={om.emergency_orders}
          />
          <Small label="Время в пути, ч" ours={om.total_travel_time_min / 60} base={bm.total_travel_time_min / 60} better="down" digits={1} />
          <Small
            label="Бригад с загрузкой < 50 %"
            ours={om.low_load_crews ?? 0}
            base={bm.low_load_crews ?? 0}
            better="down"
            title="Загрузка — дорога и работа от длины смены. Лишний человек на линии с загрузкой ниже 50 % хуже неравной нагрузки (разъяснение организаторов)"
          />
        </div>

        <Card className="mt-4 p-5">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <h2 className="text-body font-semibold">Пробег по каждой бригаде, км</h2>
            <span className="flex items-center gap-4 text-caption text-ink-2">
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-4 rounded-sm" style={{ background: OURS }} aria-hidden /> Наш план
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-4 rounded-sm" style={{ background: BASE }} aria-hidden /> Базовый
              </span>
            </span>
            <span className="ml-auto">
              <Segmented
                size="sm"
                label="Вид"
                value={mode}
                onChange={setMode}
                options={[
                  { value: 'chart', label: 'График' },
                  { value: 'table', label: 'Таблица' },
                ]}
              />
            </span>
          </div>

          {mode === 'chart' ? (
            <div className="mt-4 space-y-2.5" role="img" aria-label="Пробег по бригадам: наш план и базовый вариант. Точные значения — во вкладке «Таблица».">
              {rows.map((r) => (
                <div
                  key={r.id}
                  className={cx('grid grid-cols-[150px_1fr] items-center gap-3 rounded-md px-1 py-0.5', hover === r.id && 'bg-sunken')}
                  onMouseEnter={() => setHover(r.id)}
                  onMouseLeave={() => setHover(null)}
                  title={`${r.name}: наш план ${r.oursN ? `${num1(r.oursKm)} км, ${countWord(r.oursN, 'заявка', 'заявки', 'заявок')}` : 'не задействована'}; базовый ${r.baseN ? `${num1(r.baseKm)} км, ${countWord(r.baseN, 'заявка', 'заявки', 'заявок')}` : 'не задействована'}`}
                >
                  <span className="flex min-w-0 items-center gap-1.5 text-body">
                    <CrewSwatch color={colors[r.id]} size={8} />
                    <span className="truncate">{r.name}</span>
                  </span>
                  <div className="space-y-[2px]">
                    <RowBar value={r.oursKm} n={r.oursN} max={maxKm} color={OURS} />
                    <RowBar value={r.baseKm} n={r.baseN} max={maxKm} color={BASE} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <table className="mt-3 w-full text-body">
              <thead className="text-left text-caption text-ink-3">
                <tr className="border-b border-line">
                  <th className="py-2 pr-4 font-medium">Бригада</th>
                  <th className="py-2 pr-4 text-right font-medium">Заявок: наш</th>
                  <th className="py-2 pr-4 text-right font-medium">базовый</th>
                  <th className="py-2 pr-4 text-right font-medium">Км: наш</th>
                  <th className="py-2 pr-4 text-right font-medium">базовый</th>
                  <th className="py-2 text-right font-medium">Разница, км</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const dk = r.oursKm - r.baseKm;
                  return (
                    <tr key={r.id} className="border-b border-line/70">
                      <td className="py-1.5 pr-4">
                        <span className="flex items-center gap-1.5">
                          <CrewSwatch color={colors[r.id]} size={8} /> {r.name}
                        </span>
                      </td>
                      <td className="py-1.5 pr-4 text-right tabular">{r.oursN || '—'}</td>
                      <td className="py-1.5 pr-4 text-right text-ink-3 tabular">{r.baseN || '—'}</td>
                      <td className="py-1.5 pr-4 text-right tabular">{r.oursN ? num1(r.oursKm) : '—'}</td>
                      <td className="py-1.5 pr-4 text-right text-ink-3 tabular">{r.baseN ? num1(r.baseKm) : '—'}</td>
                      <td className={cx('py-1.5 text-right tabular', dk < -0.05 ? 'text-ok' : dk > 0.05 ? 'text-bad' : 'text-ink-3')}>
                        {signed(dk, 1)}
                      </td>
                    </tr>
                  );
                })}
                <tr className="font-semibold">
                  <td className="py-2 pr-4">Итого</td>
                  <td className="py-2 pr-4 text-right tabular">{om.assigned_orders}</td>
                  <td className="py-2 pr-4 text-right text-ink-3 tabular">{bm.assigned_orders}</td>
                  <td className="py-2 pr-4 text-right tabular">{num1(om.total_distance_km)}</td>
                  <td className="py-2 pr-4 text-right text-ink-3 tabular">{num1(bm.total_distance_km)}</td>
                  <td className={cx('py-2 text-right tabular', kmSaved > 0 ? 'text-ok' : 'text-bad')}>{signed(-kmSaved, 1)}</td>
                </tr>
              </tbody>
            </table>
          )}
        </Card>

        <Card className="mt-4 p-5">
          <h2 className="text-body font-semibold">Откуда выигрыш</h2>
          <ul className="mt-2 list-disc space-y-1.5 pl-5 text-body text-ink-2 marker:text-ink-4">
            <li>
              Базовый вариант отдаёт заявку первому подходящему инженеру и больше её не двигает: ранние заявки занимают смены,
              и к вечеру свободных бригад не остаётся.
            </li>
            <li>
              Наш план сначала ставит аварии и подключения, выбирает место с наименьшим приростом пробега, убирает лишние
              бригады и улучшает порядок визитов.
            </li>
          </ul>
        </Card>
      </div>
    </div>
  );
};

const MetricCard = ({
  title,
  ours,
  base,
  unit,
  verdict,
  tone,
  children,
}: {
  title: string;
  ours: string | number;
  base: string | number;
  unit?: string;
  verdict: string;
  tone: 'good' | 'bad' | 'neutral';
  children: ReactNode;
}) => (
  <Card className="p-5">
    <h3 className="text-body font-semibold text-ink-2">{title}</h3>
    <div className="mt-3 flex items-end gap-6">
      <BigNumber label="Наш план" value={ours} unit={unit} color={OURS} />
      <BigNumber label="Базовый" value={base} unit={unit} color={BASE} muted />
      <span
        className={cx(
          'mb-1 ml-auto rounded-lg px-2.5 py-1 text-body font-semibold whitespace-nowrap',
          tone === 'neutral' ? 'bg-hover text-ink-2' : tone === 'good' ? 'bg-ok-soft text-ok' : 'bg-bad-soft text-bad',
        )}
      >
        {verdict}
      </span>
    </div>
    <div className="mt-4 space-y-1.5">{children}</div>
  </Card>
);

const BigNumber = ({
  label,
  value,
  unit,
  color,
  muted,
}: {
  label: string;
  value: string | number;
  unit?: string;
  color: string;
  muted?: boolean;
}) => (
  <div>
    <div className="flex items-center gap-1.5 text-caption text-ink-3">
      <span className="size-2.5 rounded-sm" style={{ background: color }} aria-hidden />
      {label}
    </div>
    <div className={cx('mt-1 text-hero font-semibold tracking-[-0.03em]', muted && 'text-ink-3')}>
      {value}
      {unit && <span className="ml-1 text-title font-medium text-ink-3">{unit}</span>}
    </div>
  </div>
);

const Pips = ({ total, used, color, label }: { total: number; used: number; color: string; label: string }) => (
  <div className="flex items-center gap-2" role="img" aria-label={`${label}: ${used} из ${total}`}>
    <span className="w-16 text-caption text-ink-3">{label}</span>
    <div className="flex gap-[3px]">
      {Array.from({ length: total }, (_, i) => (
        <span key={i} className="size-4 rounded-[3px]" style={{ background: i < used ? color : 'var(--color-hover)' }} />
      ))}
    </div>
  </div>
);

const Bar = ({ value, max, color, label }: { value: number; max: number; color: string; label: string }) => (
  <div className="flex items-center gap-2" role="img" aria-label={`${label}: ${num1(value)} км`}>
    <span className="w-16 text-caption text-ink-3">{label}</span>
    <div className="h-4 flex-1 overflow-hidden rounded-[3px] bg-hover">
      <div className="h-full rounded-r-[4px]" style={{ width: `${(value / Math.max(1, max)) * 100}%`, background: color }} />
    </div>
  </div>
);

const RowBar = ({ value, n, max, color }: { value: number; n: number; max: number; color: string }) => (
  <div className="flex h-[11px] items-center gap-2">
    <div className="h-full rounded-r-[4px]" style={{ width: `${(value / max) * 88}%`, minWidth: n ? 2 : 0, background: color }} />
    <span className="text-micro whitespace-nowrap text-ink-2 tabular">{n ? num1(value) : 'не задействована'}</span>
  </div>
);

const Small = ({
  label,
  ours,
  base,
  better,
  of,
  digits = 0,
  title,
}: {
  label: string;
  ours: number;
  base: number;
  better: 'up' | 'down';
  /** «из N» — общий знаменатель для обоих вариантов */
  of?: number;
  digits?: number;
  title?: string;
}) => {
  const fmt = (v: number) => (digits ? num1(v) : String(Math.round(v)));
  return (
    <Card className="px-4 py-3">
      <div className="text-caption text-ink-3" title={title}>
        {label}
      </div>
      <div className="mt-1 flex items-center gap-2">
        <span className="text-metric font-semibold tracking-[-0.02em] whitespace-nowrap">
          {fmt(ours)}
          {of != null && <span className="text-body font-medium text-ink-3"> из {of}</span>}
        </span>
        <span className="ml-auto">
          <DeltaChip value={ours - base} digits={digits} good={better} />
        </span>
      </div>
      <div className="text-caption text-ink-3">базовый {fmt(base)}</div>
    </Card>
  );
};
