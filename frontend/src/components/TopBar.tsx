import { useRef } from 'react';
import {
  ChevronDown,
  Clock,
  Download,
  FileJson,
  MoreHorizontal,
  Play,
  RefreshCw,
  RotateCcw,
  Route as RouteIcon,
  Siren,
  Upload,
} from 'lucide-react';
import type { Dispatcher } from '../state/useDispatcher';
import { Button, Menu, Segmented, Spinner, type MenuEntry } from './ui';
import { countWord, toHHMM } from '../lib/format';
import { downloadDataset, downloadPlanCsv } from '../lib/export';
import { cx } from '../lib/cx';

export const TopBar = ({
  d,
  onOpenEvent,
  onShowSummary,
}: {
  d: Dispatcher;
  onOpenEvent: () => void;
  onShowSummary: () => void;
}) => {
  const asOf = d.solve?.optimized.as_of_min;
  const nEvents = d.events.length;
  const busy = d.busy !== null;
  const planned = d.stage === 'plan';
  const fileRef = useRef<HTMLInputElement>(null);

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    let data: unknown;
    try {
      data = JSON.parse(await file.text());
    } catch {
      d.fail('Файл не похож на JSON. Нужен объект с полями orders и engineers — как в «Скачать набор (JSON)».');
      return;
    }
    const obj = data as { name?: string; orders?: unknown; engineers?: unknown };
    if (!obj || !Array.isArray(obj.orders) || !Array.isArray(obj.engineers)) {
      d.fail('В файле нет списков orders и engineers. Образец формата — «Скачать набор (JSON)» в меню региона.');
      return;
    }
    const name = (obj.name || file.name.replace(/.json$/i, '')).slice(0, 60);
    await d.uploadDataset({ name, orders: obj.orders, engineers: obj.engineers });
  };

  const regionItems: MenuEntry[] = [
    { heading: 'Набор данных' },
    ...d.datasets.map((ds) => ({
      label: ds.name,
      hint: countWord(ds.orders_count, 'заявка', 'заявки', 'заявок'),
      checked: ds.id === d.region,
      onSelect: () => ds.id !== d.region && void d.openRegion(ds.id),
    })),
    { separator: true },
    { label: 'Загрузить свой набор (JSON)…', icon: Upload, onSelect: () => fileRef.current?.click() },
    {
      label: 'Скачать этот набор (JSON)',
      icon: FileJson,
      disabled: !d.orders.length,
      onSelect: () => downloadDataset(d.regionName, d.orders, d.engineers),
    },
  ];

  const planItems: MenuEntry[] = [
    {
      label: d.variant === 'baseline' ? 'Выгрузить базовый вариант (CSV)' : 'Выгрузить план (CSV)',
      icon: Download,
      disabled: !d.plan,
      onSelect: () =>
        d.plan &&
        downloadPlanCsv(`${d.regionName}-${d.variant === 'baseline' ? 'базовый' : 'наш'}`, d.plan, d.orders, d.engineers),
    },
    { separator: true },
    ...(nEvents > 0 ? [{ label: 'Вернуть утренний план', icon: RotateCcw, onSelect: () => void d.reset() }] : []),
    { label: 'Пересчитать план заново', icon: RefreshCw, onSelect: () => void d.runSolve() },
  ];

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
      <div className="flex items-center gap-2.5 pr-1">
        <span className="grid size-8 place-items-center rounded-lg bg-brand text-ink" aria-hidden>
          <RouteIcon className="size-[18px]" strokeWidth={2.25} />
        </span>
        <span className="text-title font-semibold tracking-[-0.01em] whitespace-nowrap">Маршруты бригад</span>
      </div>

      <span className="h-6 w-px bg-line" aria-hidden />

      <Menu
        label={`Набор данных: ${d.regionName || 'не выбран'}`}
        items={regionItems}
        align="start"
        disabled={busy}
        triggerClassName="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-body font-semibold hover:bg-hover disabled:opacity-50"
      >
        {d.regionName || 'Набор данных'}
        <ChevronDown className="size-4 text-ink-3" aria-hidden />
      </Menu>
      <input
        ref={fileRef}
        type="file"
        accept="application/json,.json"
        className="hidden"
        onChange={(e) => {
          void onFile(e.target.files?.[0]);
          e.target.value = '';
        }}
      />

      {planned && (
        <Segmented
          label="Экран"
          value={d.view}
          onChange={d.setView}
          options={[
            { value: 'plan', label: 'План' },
            { value: 'compare', label: 'Сравнение с базовым' },
          ]}
        />
      )}

      <div className="ml-auto flex items-center gap-2">
        {planned && nEvents > 0 && (
          <button
            type="button"
            onClick={onShowSummary}
            title="Журнал событий дня"
            className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-caption whitespace-nowrap text-ink-2 hover:bg-hover"
          >
            <Clock className="size-3.5 text-ink-3" aria-hidden />
            {asOf != null && <b className="font-semibold text-ink tabular">{toHHMM(asOf)}</b>}
            {countWord(nEvents, 'событие', 'события', 'событий')}
          </button>
        )}

        {planned && (
          <Menu
            label="Действия с планом"
            items={planItems}
            disabled={busy}
            triggerClassName={cx('grid size-8 place-items-center rounded-lg text-ink-2 hover:bg-hover disabled:opacity-50')}
          >
            <MoreHorizontal className="size-[18px]" aria-hidden />
          </Menu>
        )}

        {d.stage === 'data' && (
          <Button tone="primary" icon={d.busy === 'solving' ? undefined : Play} onClick={d.runSolve} disabled={busy}>
            {d.busy === 'solving' && <Spinner className="size-4" />}
            Рассчитать план
          </Button>
        )}

        {planned && (
          <Button
            tone="primary"
            icon={Siren}
            onClick={onOpenEvent}
            disabled={busy || d.variant === 'baseline'}
            title={d.variant === 'baseline' ? 'События применяются к нашему плану' : 'Авария, новая заявка, отмена или сход бригады'}
          >
            Событие дня
          </Button>
        )}
      </div>
    </header>
  );
};
