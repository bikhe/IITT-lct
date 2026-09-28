import { useCallback, useEffect, useMemo, useState } from 'react';
import { CircleAlert, X } from 'lucide-react';
import { useDispatcher, type Selection } from './state/useDispatcher';
import { crewColors } from './lib/colors';
import { TopBar } from './components/TopBar';
import { DataStrip, KpiStrip } from './components/KpiStrip';
import { Sidebar, type OrderFilter, type SideTab } from './components/Sidebar';
import { MapView } from './components/MapView';
import { Timeline } from './components/Timeline';
import { Inspector } from './components/inspector/Inspector';
import type { EventPreset } from './components/inspector/OrderPanel';
import { EventDialog, type PickedPoint } from './components/EventDialog';
import { CompareView } from './components/CompareView';
import { Button, Spinner } from './components/ui';

export const App = () => {
  const d = useDispatcher();
  const colors = useMemo(() => crewColors(d.engineers), [d.engineers]);
  const [sideTab, setSideTab] = useState<SideTab>('crews');
  const [orderFilter, setOrderFilter] = useState<OrderFilter>('all');
  const [hoverCrew, setHoverCrew] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ preset: EventPreset | null; key: number } | null>(null);
  const [pickMode, setPickMode] = useState(false);
  const [picked, setPicked] = useState<PickedPoint | null>(null);

  const diff = d.changesIndex != null ? d.events[d.changesIndex] : undefined;
  const showDiff = !!diff && d.variant === 'optimized';
  const changes = useMemo(
    () => (showDiff && diff ? new Map(diff.changes.map((c) => [c.order_id, c.status])) : null),
    [showDiff, diff],
  );
  const oldPlan = showDiff && d.changesIndex != null ? d.prevPlans[d.changesIndex] ?? null : null;
  const changedCrews = useMemo(() => (showDiff && diff ? diff.engineer_km.map((r) => r.engineer_id) : []), [showDiff, diff]);

  const openEvent = useCallback((preset: EventPreset | null) => {
    setPicked(null);
    setDialog({ preset, key: Date.now() });
  }, []);

  const select = useCallback((s: Selection) => d.setSelection(s), [d]);

  // «Сводка» в правой панели: заявки без исполнителя и журнал событий
  const showSummary = useCallback(() => {
    d.setSelection(null);
    d.setChangesIndex(null);
  }, [d]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (pickMode) setPickMode(false);
      else if (dialog) setDialog(null);
      else if (d.selection) d.setSelection(null);
      else if (d.changesIndex != null) d.setChangesIndex(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [pickMode, dialog, d]);

  const loadingMap = d.busy === 'loading' || d.busy === 'solving' || d.busy === 'reset';

  return (
    <div className="flex h-full min-w-[1180px] flex-col">
      <TopBar d={d} onOpenEvent={() => openEvent(null)} onShowSummary={showSummary} />
      {d.stage === 'plan' && d.view === 'plan' && <KpiStrip d={d} onShowUnassigned={showSummary} />}
      {d.stage === 'data' && <DataStrip d={d} />}

      <div className="relative flex min-h-0 flex-1">
        <Sidebar
          d={d}
          colors={colors}
          tab={sideTab}
          onTab={setSideTab}
          filter={orderFilter}
          onFilter={setOrderFilter}
          onHoverCrew={setHoverCrew}
        />
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1">
            <MapView
              orders={d.orders}
              engineers={d.engineers}
              plan={d.plan}
              colors={colors}
              selection={d.selection}
              hoverCrew={hoverCrew}
              onSelect={select}
              changes={changes}
              oldPlan={oldPlan}
              changedCrews={changedCrews}
              fitKey={String(d.loadSeq)}
              pickMode={pickMode}
              onPick={(lat, lon) => {
                setPicked({ lat, lon });
                setPickMode(false);
              }}
              onCancelPick={() => setPickMode(false)}
            />
            {d.variant === 'baseline' && d.stage === 'plan' && (
              <div className="absolute top-3 left-3 z-[450] flex items-center gap-3 rounded-xl border border-warn/30 bg-warn-soft py-1.5 pr-1.5 pl-3.5 text-body text-ink shadow-card">
                <b className="font-semibold">Базовый вариант ТЗ</b>
                <span className="text-ink-2">по порядку, без оптимизации</span>
                <Button size="sm" onClick={() => d.setVariant('optimized')}>
                  К нашему плану
                </Button>
              </div>
            )}
            {loadingMap && (
              <div className="absolute inset-0 z-[600] grid place-items-center bg-surface/55 backdrop-blur-[1px]">
                <div className="flex items-center gap-2.5 rounded-xl bg-surface px-4 py-3 text-body font-medium shadow-[var(--shadow-pop)]">
                  <Spinner />
                  {d.busy === 'solving' ? 'Распределяем заявки и строим маршруты…' : d.busy === 'reset' ? 'Возвращаем утренний план…' : 'Загружаем данные…'}
                </div>
              </div>
            )}
          </div>
          {d.stage === 'plan' && (
            <Timeline d={d} colors={colors} changes={changes} onHoverCrew={setHoverCrew} />
          )}
        </main>
        <Inspector d={d} colors={colors} onEvent={openEvent} />

        {d.view === 'compare' && d.stage === 'plan' && d.solve && (
          <div className="absolute inset-0 z-[700] bg-canvas">
            <CompareView d={d} colors={colors} />
          </div>
        )}
      </div>

      {dialog && (
        <EventDialog
          key={dialog.key}
          d={d}
          preset={dialog.preset}
          hidden={pickMode}
          picked={picked}
          onRequestPick={() => setPickMode(true)}
          onClose={() => {
            setDialog(null);
            setPickMode(false);
          }}
        />
      )}

      {d.error && (
        <div className="animate-in fixed bottom-4 left-1/2 z-[1100] flex max-w-[640px] -translate-x-1/2 items-start gap-2.5 rounded-xl bg-ink px-4 py-3 text-body text-white shadow-[var(--shadow-pop)]">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-brand" />
          <span>{d.error}</span>
          <button aria-label="Скрыть" className="ml-2 text-white/60 hover:text-white" onClick={d.clearError}>
            <X className="size-4" />
          </button>
        </div>
      )}
    </div>
  );
};

export default App;
