import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import type { DatasetMeta, Engineer, Order, Plan, ReplanEvent, SolveResponse } from '../types';
import { indexPlan } from '../lib/plan';

export type Stage = 'boot' | 'data' | 'plan';
export type Variant = 'optimized' | 'baseline';
export type View = 'plan' | 'compare';
export type Selection = { kind: 'order'; id: string } | { kind: 'crew'; id: string } | null;
export type Busy = null | 'loading' | 'solving' | 'event' | 'reset';

/**
 * Состояние рабочего места диспетчера. Сервер хранит план и журнал событий по региону;
 * клиент дополнительно помнит план до каждого события, чтобы показать старый маршрут пунктиром.
 */
export const useDispatcher = () => {
  const [datasets, setDatasets] = useState<DatasetMeta[]>([]);
  const [region, setRegion] = useState<string | null>(null);
  const [stage, setStage] = useState<Stage>('boot');
  const [orders, setOrders] = useState<Order[]>([]);
  const [engineers, setEngineers] = useState<Engineer[]>([]);
  const [solve, setSolve] = useState<SolveResponse | null>(null);
  const [prevPlans, setPrevPlans] = useState<Record<number, Plan>>({});
  const [variant, setVariant] = useState<Variant>('optimized');
  const [view, setView] = useState<View>('plan');
  const [selection, setSelection] = useState<Selection>(null);
  const [changesIndex, setChangesIndex] = useState<number | null>(null);
  const [busy, setBusy] = useState<Busy>('loading');
  const [error, setError] = useState<string | null>(null);
  // растёт при каждой загрузке данных региона или нового расчёта: по нему карта заново вписывает точки
  const [loadSeq, setLoadSeq] = useState(0);
  const regionRef = useRef<string | null>(null);

  const fail = useCallback((err: unknown) => {
    setError(err instanceof Error ? err.message : String(err));
  }, []);

  const applySolve = useCallback((data: SolveResponse) => {
    setSolve(data);
    setOrders(data.orders);
    setEngineers(data.engineers);
    setStage('plan');
  }, []);

  const openRegion = useCallback(
    async (id: string, known?: DatasetMeta[]) => {
      regionRef.current = id;
      setRegion(id);
      setBusy('loading');
      setSelection(null);
      setChangesIndex(null);
      setPrevPlans({});
      setVariant('optimized');
      setView('plan');
      setError(null);
      try {
        const meta = (known ?? datasets).find((d) => d.id === id);
        if (meta?.solved) {
          const data = await api.plan(id);
          if (regionRef.current !== id) return;
          applySolve(data);
          setLoadSeq((n) => n + 1);
        } else {
          const data = await api.dataset(id);
          if (regionRef.current !== id) return;
          setSolve(null);
          setOrders(data.orders);
          setEngineers(data.engineers);
          setStage('data');
          setLoadSeq((n) => n + 1);
        }
      } catch (err) {
        fail(err);
      } finally {
        if (regionRef.current === id) setBusy(null);
      }
    },
    [datasets, applySolve, fail],
  );

  useEffect(() => {
    let alive = true;
    api
      .datasets()
      .then((list) => {
        if (!alive) return;
        setDatasets(list);
        if (list.length) void openRegion(list[0].id, list);
        else setBusy(null);
      })
      .catch((err) => {
        if (!alive) return;
        fail(err);
        setBusy(null);
      });
    return () => {
      alive = false;
    };
    // только при старте
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runSolve = useCallback(async () => {
    if (!region) return;
    setBusy('solving');
    setError(null);
    try {
      const data = await api.solve(region);
      applySolve(data);
      setLoadSeq((n) => n + 1);
      setPrevPlans({});
      setChangesIndex(null);
      setSelection(null);
      setVariant('optimized');
      setView('plan');
      setDatasets((list) => list.map((d) => (d.id === region ? { ...d, solved: true } : d)));
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  }, [region, applySolve, fail]);

  const applyEvent = useCallback(
    async (event: ReplanEvent) => {
      if (!region || !solve) return;
      setBusy('event');
      setError(null);
      const before = solve.optimized;
      try {
        const data = await api.event(region, event);
        setSolve((prev) =>
          prev
            ? {
                ...prev,
                optimized: data.optimized,
                morning: data.morning,
                diff: data.diff,
                orders: data.orders,
                engineers: data.engineers,
                explanations: data.explanations,
                route_explanations: data.route_explanations,
                replan_diff: data.plan_diff,
                events: data.events,
              }
            : prev,
        );
        setOrders(data.orders);
        setEngineers(data.engineers);
        const index = data.events.length - 1;
        setPrevPlans((p) => ({ ...p, [index]: before }));
        setChangesIndex(index);
        setSelection(null);
        setVariant('optimized');
        setView('plan');
      } catch (err) {
        fail(err);
        throw err;
      } finally {
        setBusy(null);
      }
    },
    [region, solve, fail],
  );

  const uploadDataset = useCallback(
    async (payload: { name: string; orders: unknown[]; engineers: unknown[] }) => {
      setError(null);
      try {
        const meta = await api.upload(payload);
        const list = [...datasets, meta];
        setDatasets(list);
        await openRegion(meta.id, list);
      } catch (err) {
        fail(err);
      }
    },
    [datasets, openRegion, fail],
  );

  const reset = useCallback(async () => {
    if (!region) return;
    setBusy('reset');
    setError(null);
    try {
      const data = await api.reset(region);
      applySolve(data);
      setPrevPlans({});
      setChangesIndex(null);
      setSelection(null);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  }, [region, applySolve, fail]);

  const plan: Plan | null = solve ? (variant === 'optimized' ? solve.optimized : solve.baseline) : null;
  const index = useMemo(() => indexPlan(plan, orders, engineers), [plan, orders, engineers]);
  const events = solve?.events ?? [];
  const regionName = datasets.find((d) => d.id === region)?.name ?? '';

  return {
    datasets,
    region,
    regionName,
    stage,
    orders,
    engineers,
    solve,
    plan,
    index,
    events,
    prevPlans,
    variant,
    view,
    selection,
    changesIndex,
    busy,
    error,
    loadSeq,
    openRegion,
    runSolve,
    applyEvent,
    reset,
    uploadDataset,
    setVariant,
    setView,
    setSelection,
    setChangesIndex,
    clearError: () => setError(null),
    fail,
  };
};

export type Dispatcher = ReturnType<typeof useDispatcher>;
