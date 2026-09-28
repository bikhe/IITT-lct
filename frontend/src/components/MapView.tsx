import { useEffect, useMemo, useRef } from 'react';
import L from 'leaflet';
import { Crosshair, X } from 'lucide-react';
import type { ChangeStatus, Engineer, Order, Plan } from '../types';
import type { Selection } from '../state/useDispatcher';
import { CHANGE_HEX, WORK_HEX, inkOn } from '../lib/colors';
import { crewName, crewShort, UNASSIGNED_SHORT, WORK_TYPE_LABELS, windowLabel, workType } from '../lib/labels';
import { countWord, num1 } from '../lib/format';
import { indexPlan } from '../lib/plan';
import { Button } from './ui';

interface MapViewProps {
  orders: Order[];
  engineers: Engineer[];
  plan: Plan | null;
  colors: Record<string, string>;
  selection: Selection;
  hoverCrew: string | null;
  onSelect: (s: Selection) => void;
  /** Изменения последнего события: подсветка меток и старые маршруты пунктиром */
  changes?: Map<string, ChangeStatus> | null;
  oldPlan?: Plan | null;
  changedCrews?: string[];
  fitKey: string;
  pickMode: boolean;
  onPick: (lat: number, lon: number) => void;
  onCancelPick: () => void;
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);

const pinHtml = ({
  size,
  fill,
  text,
  emergency,
  dashed,
  classes,
  halo,
}: {
  size: number;
  fill: string;
  text: string;
  emergency?: boolean;
  dashed?: string;
  classes: string;
  halo?: string;
}) => {
  const ink = dashed ?? inkOn(fill);
  const border = dashed ? `border:2px dashed ${dashed};` : '';
  const shape = emergency ? 'border-radius:5px;transform:rotate(45deg);' : '';
  const inner = emergency ? `<span style="transform:rotate(-45deg)">${text}</span>` : text;
  const haloEl = halo ? `<span class="pin-halo" style="color:${halo}"></span>` : '';
  return `<div style="position:relative;width:${size}px;height:${size}px">${haloEl}<div class="pin ${classes}" style="width:${size}px;height:${size}px;background:${fill};color:${ink};${border}${shape}">${inner}</div></div>`;
};

const DEPOT_SVG =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/><path d="M10 6h4M10 10h4M10 14h4M10 18h4"/></svg>';

export const MapView = ({
  orders,
  engineers,
  plan,
  colors,
  selection,
  hoverCrew,
  onSelect,
  changes,
  oldPlan,
  changedCrews = [],
  fitKey,
  pickMode,
  onPick,
  onCancelPick,
}: MapViewProps) => {
  const boxRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const onSelectRef = useRef(onSelect);
  const onPickRef = useRef(onPick);
  const pickRef = useRef(pickMode);

  useEffect(() => {
    onSelectRef.current = onSelect;
    onPickRef.current = onPick;
    pickRef.current = pickMode;
  });

  const index = useMemo(() => indexPlan(plan, orders, engineers), [plan, orders, engineers]);

  // Инициализация карты
  useEffect(() => {
    if (!boxRef.current || mapRef.current) return;
    const map = L.map(boxRef.current, {
      center: [55.72, 37.7],
      zoom: 11,
      zoomControl: false,
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      wheelPxPerZoomLevel: 90,
    });
    L.control.zoom({ position: 'topright' }).addTo(map);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
      className: 'map-tiles',
    }).addTo(map);
    map.on('click', (e: L.LeafletMouseEvent) => {
      if (pickRef.current) onPickRef.current(e.latlng.lat, e.latlng.lng);
    });
    layerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(boxRef.current);
    return () => {
      ro.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Отрисовка слоёв
  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;
    layer.clearLayers();

    const focusCrew =
      hoverCrew ??
      (selection?.kind === 'crew'
        ? selection.id
        : selection?.kind === 'order'
          ? (() => {
              const st = index.state.get(selection.id);
              return st && 'engineerId' in st ? st.engineerId ?? null : null;
            })()
          : null);
    // В режиме «что изменилось» на первом плане — бригады, которых коснулось событие
    const focus: Set<string> | null = focusCrew
      ? new Set([focusCrew])
      : changedCrews.length
        ? new Set(changedCrews)
        : null;
    const dimCrew = (eid: string) => focus !== null && !focus.has(eid);
    const selectedOrder = selection?.kind === 'order' ? selection.id : null;
    const tip: L.TooltipOptions = { className: 'map-tip', direction: 'top', offset: [0, -10], opacity: 1 };

    // Старые маршруты (до события) — серый пунктир под новыми
    if (oldPlan && changedCrews.length) {
      const oldIdx = indexPlan(oldPlan, orders, engineers);
      for (const eid of changedCrews) {
        const route = oldIdx.routes.get(eid);
        const eng = index.engineers.get(eid);
        if (!route || !eng || !route.jobs.length) continue;
        const pts: L.LatLngExpression[] = [[eng.depot.lat, eng.depot.lon]];
        route.jobs.forEach((j) => {
          const o = index.orders.get(j.order_id);
          if (o) pts.push([o.location.lat, o.location.lon]);
        });
        L.polyline(pts, { color: '#1f2430', weight: 3, opacity: focusCrew && focusCrew !== eid ? 0.25 : 0.85, dashArray: '1 7', lineCap: 'round', interactive: false }).addTo(layer);
      }
    }

    // Маршруты
    if (plan) {
      const routes = [...plan.routes].sort((a, b) => Number(!dimCrew(a.engineer_id)) - Number(!dimCrew(b.engineer_id)));
      for (const route of routes) {
        if (!route.jobs.length) continue;
        const eng = index.engineers.get(route.engineer_id);
        if (!eng) continue;
        const color = colors[eng.id] ?? '#2a78d6';
        const dim = dimCrew(eng.id);
        const focused = focusCrew === eng.id;
        const pts: L.LatLngExpression[] = [[eng.depot.lat, eng.depot.lon]];
        route.jobs.forEach((j) => {
          const o = index.orders.get(j.order_id);
          if (o) pts.push([o.location.lat, o.location.lon]);
        });
        const w = focused ? 5 : 3;
        L.polyline(pts, { color: '#ffffff', weight: w + 3, opacity: dim ? 0.3 : 0.95, interactive: false }).addTo(layer);
        const line = L.polyline(pts, {
          color,
          weight: w,
          opacity: dim ? 0.18 : 0.95,
          lineJoin: 'round',
          dashArray: route.unavailable_from_min != null ? '8 6' : undefined,
        }).addTo(layer);
        line.bindTooltip(
          `<b>${esc(crewName(eng.name))}</b><br/>${countWord(route.jobs.filter((j) => j.status !== 'cancelled').length, 'заявка', 'заявки', 'заявок')} · ${num1(route.total_distance_km)} км`,
          { ...tip, sticky: true },
        );
        line.on('click', (e) => {
          if (pickRef.current) return;
          L.DomEvent.stopPropagation(e);
          onSelectRef.current({ kind: 'crew', id: eng.id });
        });
      }
    }

    // Офисы и стартовые точки бригад
    const depots = new Map<string, { eng: Engineer[]; lat: number; lon: number; address: string }>();
    for (const e of engineers) {
      const key = `${e.depot.lat.toFixed(4)},${e.depot.lon.toFixed(4)}`;
      const item = depots.get(key) ?? { eng: [], lat: e.depot.lat, lon: e.depot.lon, address: e.depot.address };
      item.eng.push(e);
      depots.set(key, item);
    }
    for (const dp of depots.values()) {
      const icon = L.divIcon({ className: '', html: `<div class="depot">${DEPOT_SVG}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] });
      const names = dp.eng.length > 3 ? countWord(dp.eng.length, 'бригада', 'бригады', 'бригад') : dp.eng.map((e) => esc(crewShort(e.name))).join(', ');
      L.marker([dp.lat, dp.lon], { icon, zIndexOffset: -500, keyboard: false })
        .bindTooltip(
          `<b>${dp.eng.length > 3 ? 'Офис региона' : 'Старт бригады'}</b><br/>${esc(dp.address)}<br/><span style="color:#666d7a">Отсюда выезжают: ${names}</span>`,
          tip,
        )
        .addTo(layer);
    }

    // Заявки
    for (const order of orders) {
      const st = index.state.get(order.id);
      if (plan && !st) continue; // заявки нет в этом варианте плана (поступила днём, а базовый — утренний)
      const wt = workType(order);
      const emergency = wt === 'emergency';
      const change = changes?.get(order.id);
      const halo = change ? CHANGE_HEX[change] : undefined;
      const selected = selectedOrder === order.id;
      let html: string;
      let size: number;
      let tipBody: string;
      let dim = false;

      const head = `<b>#${esc(order.id)}</b> · ${WORK_TYPE_LABELS[wt]}<br/><span style="color:#666d7a">${esc(order.location.district)} · ${windowLabel(order)}</span>`;

      if (!plan) {
        size = emergency ? 13 : 12;
        html = pinHtml({ size, fill: WORK_HEX[wt], text: '', emergency, classes: selected ? 'pin-selected' : '' });
        tipBody = head;
      } else if (st?.kind === 'assigned') {
        const color = colors[st.engineerId] ?? '#2a78d6';
        const eng = index.engineers.get(st.engineerId);
        dim = dimCrew(st.engineerId) && !change;
        size = 22;
        const done = st.job.status === 'done';
        html = pinHtml({
          size,
          fill: color,
          text: String(st.visit),
          emergency,
          halo,
          classes: [selected && 'pin-selected', dim && 'pin-dim', done && 'opacity-60'].filter(Boolean).join(' '),
        });
        tipBody = `${head}<br/>${esc(crewShort(eng?.name ?? ''))} · визит ${st.visit} из ${st.total} · начало <b>${st.job.start_time}</b>`;
      } else if (st?.kind === 'unassigned') {
        size = 20;
        dim = focus !== null && !change;
        html = pinHtml({ size, fill: '#ffffff', text: '!', dashed: '#c62f2f', emergency, halo, classes: [selected && 'pin-selected', dim && 'pin-dim'].filter(Boolean).join(' ') });
        tipBody = `${head}<br/><span style="color:#c62f2f">Без исполнителя: ${st.info ? UNASSIGNED_SHORT[st.info.code] : 'нет подходящей бригады'}</span>`;
      } else {
        size = 18;
        dim = focus !== null && !change;
        html = pinHtml({ size, fill: '#a6acb6', text: '×', halo, classes: [selected && 'pin-selected', dim && 'pin-dim'].filter(Boolean).join(' ') });
        tipBody = `${head}<br/>Отменена клиентом`;
      }

      const icon = L.divIcon({ className: '', html, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
      const marker = L.marker([order.location.lat, order.location.lon], {
        icon,
        zIndexOffset: selected ? 2000 : change ? 1000 : dim ? -100 : emergency ? 200 : 0,
        riseOnHover: true,
      })
        .bindTooltip(tipBody, tip)
        .addTo(layer);
      marker.on('click', (e) => {
        if (pickRef.current) return;
        L.DomEvent.stopPropagation(e);
        onSelectRef.current({ kind: 'order', id: order.id });
      });
    }
  }, [plan, orders, engineers, colors, index, selection, hoverCrew, changes, oldPlan, changedCrews]);

  // Подгонка вида: при смене региона или плана
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !orders.length) return;
    const b = L.latLngBounds([]);
    orders.forEach((o) => b.extend([o.location.lat, o.location.lon]));
    engineers.forEach((e) => b.extend([e.depot.lat, e.depot.lon]));
    if (!b.isValid()) return;
    // после смены раскладки (полоса KPI, Гант) размер контейнера меняется — вписываем по факту
    const frame = requestAnimationFrame(() => {
      map.invalidateSize();
      map.fitBounds(b, { padding: [28, 28], maxZoom: 14 });
    });
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey]);

  // При выборе бригады — показать её маршрут целиком; при выборе заявки — центрировать, если она за кадром
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selection) return;
    if (selection.kind === 'crew') {
      const route = index.routes.get(selection.id);
      const eng = index.engineers.get(selection.id);
      if (!route || !eng || !route.jobs.length) return;
      const b = L.latLngBounds([[eng.depot.lat, eng.depot.lon]]);
      route.jobs.forEach((j) => {
        const o = index.orders.get(j.order_id);
        if (o) b.extend([o.location.lat, o.location.lon]);
      });
      map.flyToBounds(b, { padding: [60, 60], maxZoom: 14, duration: 0.5 });
    } else {
      const o = index.orders.get(selection.id);
      if (!o) return;
      const ll = L.latLng(o.location.lat, o.location.lon);
      if (!map.getBounds().pad(-0.15).contains(ll)) map.flyTo(ll, Math.max(map.getZoom(), 12), { duration: 0.5 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection]);

  return (
    <div className={`relative h-full w-full ${pickMode ? 'map-pick' : ''}`}>
      <div ref={boxRef} className="absolute inset-0 z-0" />
      {pickMode && (
        <div className="animate-in absolute top-3 left-1/2 z-[500] flex -translate-x-1/2 items-center gap-3 rounded-xl bg-ink px-4 py-2.5 text-body text-white shadow-[var(--shadow-pop)]">
          <Crosshair className="size-4 text-brand" />
          Кликните по карте — там будет адрес новой заявки
          <Button size="sm" tone="ghost" icon={X} className="text-white/80 hover:bg-white/10 hover:text-white" onClick={onCancelPick}>
            Отмена
          </Button>
        </div>
      )}
      <MapLegend planned={!!plan} showOld={!!(oldPlan && changedCrews.length)} />
    </div>
  );
};

/** Ключ к значкам карты. Всегда на виду: без него точки и линии не прочитать. */
const MapLegend = ({ planned, showOld }: { planned: boolean; showOld: boolean }) => (
  <div
    className="pointer-events-none absolute bottom-3 left-3 z-[400] flex max-w-[calc(100%-24px)] flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line bg-surface/95 px-2.5 py-1.5 text-caption text-ink-2 shadow-card backdrop-blur"
    aria-label="Обозначения на карте"
  >
    {planned ? (
      <>
        <span className="flex items-center gap-1.5">
          <span className="grid size-4 place-items-center rounded-full bg-[#2a78d6] text-[9px] font-bold text-white" aria-hidden>
            1
          </span>
          визит по порядку
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rotate-45 rounded-[2px] bg-ink-3" aria-hidden />
          авария
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="grid size-4 place-items-center rounded-full border-2 border-dashed border-bad bg-white text-[9px] font-bold text-bad"
            aria-hidden
          >
            !
          </span>
          без исполнителя
        </span>
      </>
    ) : (
      (['emergency', 'connection', 'local', 'addon'] as const).map((t) => (
        <span key={t} className="flex items-center gap-1.5">
          <span
            className={`size-2.5 ${t === 'emergency' ? 'rotate-45 rounded-[2px]' : 'rounded-full'}`}
            style={{ background: WORK_HEX[t] }}
            aria-hidden
          />
          {WORK_TYPE_LABELS[t]}
        </span>
      ))
    )}
    <span className="flex items-center gap-1.5">
      <span className="size-2.5 rounded-[3px] bg-ink" aria-hidden />
      офис
    </span>
    {showOld && (
      <>
        <span className="flex items-center gap-1.5">
          <svg width="18" height="4" aria-hidden>
            <line x1="1" y1="2" x2="17" y2="2" stroke="#4a5160" strokeWidth="2.5" strokeDasharray="2 5" strokeLinecap="round" />
          </svg>
          маршрут до события
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-3 rounded-full border-2 border-info" aria-hidden />
          изменение
        </span>
      </>
    )}
  </div>
);
