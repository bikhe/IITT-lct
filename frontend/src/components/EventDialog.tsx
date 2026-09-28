import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Crosshair, Info, MapPin, Play, X } from 'lucide-react';
import type { Dispatcher } from '../state/useDispatcher';
import type { EventType, Order, ReplanEvent, ScenarioItem, WorkType } from '../types';
import { api } from '../api';
import { crewName, EVENT_LABELS, TRANSPORT_SHORT, WORK_TYPE_LABELS, WORK_TYPE_MINUTES, WORK_TYPE_SKILL, workType } from '../lib/labels';
import { toHHMM, toMinutes } from '../lib/format';
import { Button, IconButton, Segmented, Spinner } from './ui';
import { cx } from '../lib/cx';
import { EVENT_ICONS } from './inspector/ChangesPanel';
import type { EventPreset } from './inspector/OrderPanel';

type Kind = Exclude<EventType, 'manual_assign'>;

/** Правило перепланирования для каждого типа события — одной строкой. */
const POLICY: Record<Kind, string> = {
  urgent_order: 'Начать как можно раньше (ориентир — 2 ч); хвост дня бригады перестраивается',
  new_order: 'Встаёт в свободное время одной из бригад, чужие заявки не двигаем',
  cancel_order: 'Освободившееся время отдаём заявкам без исполнителя',
  engineer_unavailable: 'Начатые работы остаются за ней, остальные заявки — другим бригадам',
};

/** Из описания сценария берём только первую фразу — конкретику; правило показываем отдельно. */
const firstSentence = (text: string) => text.split(/(?<=[.!?])\s/)[0];

const KINDS: Kind[] = ['urgent_order', 'new_order', 'cancel_order', 'engineer_unavailable'];

export interface PickedPoint {
  lat: number;
  lon: number;
}

interface Props {
  d: Dispatcher;
  preset: EventPreset | null;
  hidden: boolean;
  picked: PickedPoint | null;
  onRequestPick: () => void;
  onClose: () => void;
}

const nearestOrder = (orders: Order[], lat: number, lon: number) =>
  orders.reduce<Order | null>((best, o) => {
    const dd = (o.location.lat - lat) ** 2 + (o.location.lon - lon) ** 2;
    if (!best) return o;
    const bd = (best.location.lat - lat) ** 2 + (best.location.lon - lon) ** 2;
    return dd < bd ? o : best;
  }, null);

export const EventDialog = ({ d, preset, hidden, picked, onRequestPick, onClose }: Props) => {
  const asOf = d.solve?.optimized.as_of_min ?? null;
  const defaultTime = asOf != null ? Math.min(23 * 60, Math.ceil((asOf + 1) / 30) * 30) : 12 * 60;
  const [tab, setTab] = useState<'presets' | 'custom'>(preset ? 'custom' : 'presets');
  const [scenarios, setScenarios] = useState<ScenarioItem[] | null>(null);
  const [kind, setKind] = useState<Kind>(preset?.type ?? 'urgent_order');
  const [time, setTime] = useState(toHHMM(defaultTime));
  const [newType, setNewType] = useState<WorkType>('connection');
  const [slot, setSlot] = useState<number>(() => Math.min(20, Math.max(10, Math.ceil((defaultTime + 60) / 120) * 2)));
  const [where, setWhere] = useState<'near' | 'map'>(picked ? 'map' : 'near');
  const [nearId, setNearId] = useState(() => d.orders[Math.floor(d.orders.length / 2)]?.id ?? '');
  const [orderId, setOrderId] = useState(preset?.type === 'cancel_order' ? preset.orderId : '');
  const [engineerId, setEngineerId] = useState(preset?.type === 'engineer_unavailable' ? preset.engineerId : '');
  const [error, setError] = useState<string | null>(null);
  const busy = d.busy === 'event';
  const boxRef = useRef<HTMLDivElement>(null);

  // Фокус — внутрь диалога, при закрытии — обратно на кнопку, с которой его открыли
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    boxRef.current?.focus();
    return () => prev?.focus?.();
  }, []);

  // Tab не уходит за пределы диалога
  const trapTab = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Tab' || !boxRef.current) return;
    const els = [
      ...boxRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), select:not([disabled]), input:not([disabled])'),
    ];
    if (!els.length) return;
    const first = els[0];
    const last = els[els.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === boxRef.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  useEffect(() => {
    let alive = true;
    if (d.region)
      api
        .scenarios(d.region)
        .then((s) => alive && setScenarios(s))
        .catch(() => alive && setScenarios([]));
    return () => {
      alive = false;
    };
  }, [d.region]);

  // Точка выбрана на карте — переключаемся на неё. Синхронизация с внешним выбором, не каскад.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (picked) setWhere('map');
  }, [picked]);

  const cancellable = useMemo(() => {
    const rows: Array<{ order: Order; label: string }> = [];
    for (const o of d.orders) {
      const st = d.index.state.get(o.id);
      if (!st || st.kind === 'cancelled') continue;
      if (st.kind === 'assigned') {
        if (st.job.status === 'done') continue;
        const e = d.index.engineers.get(st.engineerId);
        rows.push({ order: o, label: `${crewName(e?.name ?? '')}, начало ${st.job.start_time}` });
      } else rows.push({ order: o, label: 'без исполнителя' });
    }
    return rows.sort((a, b) => a.order.window.start_min - b.order.window.start_min);
  }, [d.orders, d.index]);

  const crews = d.engineers.filter((e) => d.index.routes.get(e.id)?.unavailable_from_min == null);
  const tMin = toMinutes(time);
  const timeError = asOf != null && tMin < asOf ? `Не раньше ${toHHMM(asOf)} — события идут по порядку` : null;
  const slots = [10, 12, 14, 16, 18, 20].filter((h) => (h + 2) * 60 > tMin);

  const buildOrder = (k: WorkType): Order => {
    let lat: number;
    let lon: number;
    let address: string;
    let district: string;
    if (where === 'map' && picked) {
      lat = +picked.lat.toFixed(5);
      lon = +picked.lon.toFixed(5);
      const near = nearestOrder(d.orders, lat, lon);
      district = near?.location.district ?? '—';
      address = `Точка на карте (${lat.toFixed(4)}, ${lon.toFixed(4)})`;
    } else {
      const ref = d.orders.find((o) => o.id === nearId) ?? d.orders[0];
      lat = +(ref.location.lat + 0.003).toFixed(5);
      lon = +(ref.location.lon - 0.004).toFixed(5);
      district = ref.location.district;
      address = `${ref.location.address} (соседний дом)`;
    }
    const base = `${k === 'emergency' ? 'AV' : 'N'}-${time.replace(':', '')}`;
    let id = base;
    for (let i = 2; d.orders.some((o) => o.id === id); i += 1) id = `${base}-${i}`;
    const emergency = k === 'emergency';
    const start = emergency ? tMin : Math.max(slot * 60, 0);
    const end = emergency ? 23 * 60 + 59 : slot * 60 + 120;
    return {
      id,
      skills: [WORK_TYPE_SKILL[k]],
      priority: emergency ? 'urgent' : 'normal',
      work_type: k,
      window: { start: toHHMM(start), end: emergency ? '23:59' : toHHMM(end), start_min: start, end_min: end },
      duration_min: WORK_TYPE_MINUTES[k],
      location: { lat, lon, address, district },
    };
  };

  const buildEvent = (): ReplanEvent | null => {
    if (kind === 'urgent_order' || kind === 'new_order') {
      if (where === 'map' && !picked) return null;
      const k: WorkType = kind === 'urgent_order' ? 'emergency' : newType;
      return {
        event_type: kind,
        event_time: time,
        new_order: buildOrder(k),
        description: kind === 'urgent_order' ? 'Авария: нет связи у абонентов' : `Новая заявка: ${WORK_TYPE_LABELS[k].toLowerCase()}`,
      };
    }
    if (kind === 'cancel_order') {
      if (!orderId) return null;
      return { event_type: kind, event_time: time, order_id: orderId, description: 'Клиент отказался от визита' };
    }
    if (!engineerId) return null;
    return { event_type: kind, event_time: time, engineer_id: engineerId, description: 'Бригада сошла с линии' };
  };

  const apply = async (event: ReplanEvent) => {
    setError(null);
    try {
      await d.applyEvent(event);
      onClose();
    } catch (err) {
      d.clearError();
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const ready = buildEvent();
  const select =
    'h-8 w-full rounded-lg border border-line bg-surface px-2.5 text-body outline-none focus:border-ink-3';

  return (
    <div className={cx('fixed inset-0 z-[1000] grid place-items-center bg-ink/30 p-4 backdrop-blur-[2px]', hidden && 'hidden')}>
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="event-dialog-title"
        tabIndex={-1}
        onKeyDown={trapTab}
        className="animate-in flex max-h-[88vh] w-full max-w-[720px] flex-col overflow-hidden rounded-2xl bg-surface shadow-[var(--shadow-pop)] focus:outline-none"
      >
        <div className="flex items-start gap-3 border-b border-line px-5 pt-4 pb-3">
          <div>
            <h2 id="event-dialog-title" className="text-title font-semibold tracking-[-0.01em]">
              Событие рабочего дня
            </h2>
            <p className="mt-0.5 text-caption text-ink-3">
              План перестроится с момента события. Работы, к которым бригады уже выехали, не переносятся.
            </p>
          </div>
          <IconButton icon={X} label="Закрыть" className="ml-auto" onClick={onClose} />
        </div>
        <div className="flex items-center gap-2 border-b border-line px-5 py-2">
          <Segmented
            value={tab}
            onChange={setTab}
            options={[
              { value: 'presets', label: 'Готовые сценарии' },
              { value: 'custom', label: 'Задать своё' },
            ]}
          />
        </div>

        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {error && (
            <div className="mb-3 rounded-lg border border-bad/20 bg-bad-soft px-3 py-2 text-caption text-bad">{error}</div>
          )}
          {tab === 'presets' ? (
            scenarios === null ? (
              <div className="flex items-center gap-2 py-8 text-body text-ink-3">
                <Spinner /> Подбираем сценарии по текущему плану…
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2.5">
                {scenarios.map((s) => {
                  const Icon = EVENT_ICONS[s.event_type];
                  return (
                    <button
                      key={s.id}
                      disabled={busy}
                      onClick={() => void apply(s.event)}
                      className="group flex flex-col items-start gap-2 rounded-xl border border-line p-3.5 text-left transition-colors hover:border-ink-3 hover:bg-sunken disabled:opacity-50"
                    >
                      <span className="flex w-full items-center gap-2">
                        <span
                          className={cx('grid size-7 place-items-center rounded-lg', s.event_type === 'urgent_order' ? 'bg-bad-soft text-bad' : 'bg-hover text-ink-2')}
                          aria-hidden
                        >
                          <Icon className="size-4" />
                        </span>
                        <span className="text-body font-semibold">{s.title}</span>
                        <Play className="ml-auto size-3.5 shrink-0 text-ink-3 group-hover:text-ink" aria-hidden />
                      </span>
                      <span className="text-caption text-ink-2">{firstSentence(s.description)}</span>
                      {s.event_type !== 'manual_assign' && (
                        <span className="text-caption text-ink-3">{POLICY[s.event_type as Kind]}</span>
                      )}
                    </button>
                  );
                })}
              </div>
            )
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-4 gap-2">
                {KINDS.map((k) => {
                  const Icon = EVENT_ICONS[k];
                  return (
                    <button
                      key={k}
                      onClick={() => setKind(k)}
                      className={cx(
                        'flex flex-col items-center gap-1.5 rounded-xl border px-2 py-2.5 text-caption font-medium',
                        kind === k ? 'border-ink bg-sunken text-ink' : 'border-line text-ink-3 hover:text-ink',
                      )}
                    >
                      <Icon className="size-4" />
                      {EVENT_LABELS[k]}
                    </button>
                  );
                })}
              </div>
              <p className="flex gap-2 rounded-lg bg-sunken px-3 py-2 text-caption text-ink-2">
                <Info className="mt-px size-3.5 shrink-0 text-ink-3" aria-hidden />
                {POLICY[kind]}
              </p>

              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="mb-1 block text-caption font-medium text-ink-2">Время события</span>
                  <input type="time" value={time} onChange={(e) => setTime(e.target.value)} className={cx(select, 'tabular')} />
                  {timeError && <span className="mt-1 block text-caption text-bad">{timeError}</span>}
                </label>

                {kind === 'new_order' && (
                  <label className="block">
                    <span className="mb-1 block text-caption font-medium text-ink-2">Вид работ</span>
                    <select value={newType} onChange={(e) => setNewType(e.target.value as WorkType)} className={select}>
                      {(['connection', 'local', 'addon'] as WorkType[]).map((k) => (
                        <option key={k} value={k}>
                          {WORK_TYPE_LABELS[k]} — {WORK_TYPE_MINUTES[k]} мин
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {kind === 'new_order' && (
                  <label className="block">
                    <span className="mb-1 block text-caption font-medium text-ink-2">Окно клиента</span>
                    <select value={slot} onChange={(e) => setSlot(parseInt(e.target.value, 10))} className={select}>
                      {slots.map((h) => (
                        <option key={h} value={h}>
                          {h}:00–{h + 2}:00
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {kind === 'urgent_order' && (
                  <div className="self-end rounded-lg border border-line px-3 py-2 text-caption text-ink-2">
                    Работа 80 мин, окно — до конца дня
                  </div>
                )}

                {kind === 'cancel_order' && (
                  <label className="col-span-2 block">
                    <span className="mb-1 block text-caption font-medium text-ink-2">Какую заявку отменил клиент</span>
                    <select value={orderId} onChange={(e) => setOrderId(e.target.value)} className={select}>
                      <option value="">Выберите заявку…</option>
                      {cancellable.map(({ order, label }) => (
                        <option key={order.id} value={order.id}>
                          #{order.id} · {WORK_TYPE_LABELS[workType(order)]} · окно {order.window.start}–{order.window.end} · {label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}

                {kind === 'engineer_unavailable' && (
                  <label className="col-span-2 block">
                    <span className="mb-1 block text-caption font-medium text-ink-2">Какая бригада сошла с линии</span>
                    <select value={engineerId} onChange={(e) => setEngineerId(e.target.value)} className={select}>
                      <option value="">Выберите бригаду…</option>
                      {crews.map((e) => {
                        const n = d.index.routes.get(e.id)?.jobs.length ?? 0;
                        return (
                          <option key={e.id} value={e.id}>
                            {crewName(e.name)} · {TRANSPORT_SHORT[e.transport]} · смена {e.shift.start}–{e.shift.end} · заявок {n}
                          </option>
                        );
                      })}
                    </select>
                  </label>
                )}
              </div>

              {(kind === 'urgent_order' || kind === 'new_order') && (
                <div>
                  <span className="mb-1 block text-caption font-medium text-ink-2">Адрес</span>
                  <div className="flex items-center gap-2">
                    <Segmented
                      size="sm"
                      value={where}
                      onChange={setWhere}
                      options={[
                        { value: 'near', label: 'Рядом с заявкой' },
                        { value: 'map', label: 'Точка на карте' },
                      ]}
                    />
                    {where === 'map' && (
                      <Button size="sm" icon={Crosshair} onClick={onRequestPick}>
                        {picked ? 'Выбрать другую' : 'Указать на карте'}
                      </Button>
                    )}
                  </div>
                  {where === 'near' ? (
                    <select value={nearId} onChange={(e) => setNearId(e.target.value)} className={cx(select, 'mt-2')}>
                      {[...d.orders]
                        .sort((a, b) => a.location.district.localeCompare(b.location.district, 'ru'))
                        .map((o) => (
                          <option key={o.id} value={o.id}>
                            {o.location.district} — {o.location.address.replace(/^Город Москва,\s*/i, '')}
                          </option>
                        ))}
                    </select>
                  ) : (
                    <p className="mt-2 flex items-center gap-1.5 text-caption text-ink-2">
                      <MapPin className="size-3.5 text-ink-3" />
                      {picked
                        ? `Выбрана точка ${picked.lat.toFixed(4)}, ${picked.lon.toFixed(4)} · район ${nearestOrder(d.orders, picked.lat, picked.lon)?.location.district ?? '—'}`
                        : 'Точка ещё не выбрана'}
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {tab === 'custom' && (
          <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-3">
            <Button tone="ghost" onClick={onClose}>
              Отмена
            </Button>
            <Button tone="primary" icon={busy ? undefined : Play} disabled={!ready || !!timeError || busy} onClick={() => ready && void apply(ready)}>
              {busy && <Spinner />}
              Перепланировать
            </Button>
          </div>
        )}
      </div>
    </div>
  );
};
