import { useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type KeyboardEvent, type ReactNode } from 'react';
import {
  ArrowDownRight,
  ArrowUpRight,
  Bike,
  Car,
  Check,
  ChevronRight,
  Footprints,
  Minus,
  PackagePlus,
  Plug,
  TramFront,
  Wrench,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import type { Order, Skill, Transport, WorkType } from '../types';
import { SKILL_LABELS, SKILL_ORDER, TRANSPORT_LABELS, WORK_TYPE_LABELS, workType } from '../lib/labels';
import { WORK_COLORS } from '../lib/colors';
import { signed } from '../lib/format';

import { cx } from '../lib/cx';

type ButtonTone = 'primary' | 'secondary' | 'ghost' | 'danger';

export const Button = ({
  tone = 'secondary',
  size = 'md',
  icon: Icon,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: ButtonTone;
  size?: 'sm' | 'md';
  icon?: LucideIcon;
}) => (
  <button
    type="button"
    className={cx(
      'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-colors disabled:opacity-50',
      size === 'sm' ? 'h-7 px-2.5 text-caption' : 'h-8 px-3 text-body',
      tone === 'primary' && 'bg-brand text-ink hover:bg-brand-hover font-semibold shadow-[inset_0_-1px_0_rgba(0,0,0,0.08)]',
      tone === 'secondary' && 'bg-surface text-ink border border-line hover:bg-hover shadow-card',
      tone === 'ghost' && 'text-ink-2 hover:bg-hover hover:text-ink',
      tone === 'danger' && 'bg-surface text-bad border border-line hover:bg-bad-soft',
      className,
    )}
    {...rest}
  >
    {Icon && <Icon className={size === 'sm' ? 'size-3.5' : 'size-4'} strokeWidth={2} aria-hidden />}
    {children}
  </button>
);

export const IconButton = ({
  icon: Icon,
  label,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: LucideIcon; label: string }) => (
  <button
    type="button"
    aria-label={label}
    title={label}
    className={cx('grid size-7 place-items-center rounded-md text-ink-3 hover:bg-hover hover:text-ink disabled:opacity-40', className)}
    {...rest}
  >
    <Icon className="size-4" aria-hidden />
  </button>
);

type Tone = 'neutral' | 'ok' | 'bad' | 'warn' | 'info' | 'brand' | 'dark';

const TONES: Record<Tone, string> = {
  neutral: 'bg-sunken text-ink-2 border-line',
  ok: 'bg-ok-soft text-ok border-transparent',
  bad: 'bg-bad-soft text-bad border-transparent',
  warn: 'bg-warn-soft text-warn border-transparent',
  info: 'bg-info-soft text-info border-transparent',
  brand: 'bg-brand-soft text-ink border-transparent',
  dark: 'bg-ink text-white border-transparent',
};

export const Badge = ({
  tone = 'neutral',
  icon: Icon,
  children,
  className,
  title,
}: {
  tone?: Tone;
  icon?: LucideIcon;
  children: ReactNode;
  className?: string;
  title?: string;
}) => (
  <span
    title={title}
    className={cx(
      'inline-flex items-center gap-1 rounded-md border px-1.5 py-px text-micro font-medium whitespace-nowrap',
      TONES[tone],
      className,
    )}
  >
    {Icon && <Icon className="size-3" strokeWidth={2.25} aria-hidden />}
    {children}
  </span>
);

export const Segmented = <T extends string>({
  value,
  options,
  onChange,
  size = 'md',
  label,
}: {
  value: T;
  options: Array<{ value: T; label: ReactNode; hint?: string; disabled?: boolean }>;
  onChange: (v: T) => void;
  size?: 'sm' | 'md';
  label?: string;
}) => (
  <div className="inline-flex rounded-lg bg-hover p-0.5" role="tablist" aria-label={label}>
    {options.map((o) => (
      <button
        key={o.value}
        type="button"
        role="tab"
        title={o.hint}
        aria-selected={o.value === value}
        disabled={o.disabled}
        onClick={() => onChange(o.value)}
        className={cx(
          'inline-flex items-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-colors disabled:opacity-40',
          size === 'sm' ? 'h-6 px-2 text-caption' : 'h-7 px-3 text-body',
          o.value === value ? 'bg-surface text-ink shadow-card' : 'text-ink-3 hover:text-ink',
        )}
      >
        {o.label}
      </button>
    ))}
  </div>
);

export const TRANSPORT_ICONS: Record<Transport, LucideIcon> = {
  car: Car,
  foot: Footprints,
  bike: Bike,
  transit: TramFront,
};

export const WORK_ICONS: Record<WorkType, LucideIcon> = {
  emergency: Zap,
  connection: Plug,
  local: Wrench,
  addon: PackagePlus,
};

/** Навык рисуется значком того вида работ, который он открывает: подключение, ремонт, авария. */
export const SKILL_ICONS: Record<Skill, LucideIcon> = {
  connection: Plug,
  local: Wrench,
  emergency: Zap,
};

const SKILL_COLOR: Record<Skill, string> = {
  connection: WORK_COLORS.connection,
  local: WORK_COLORS.local,
  emergency: WORK_COLORS.emergency,
};

export const TransportIcon = ({ transport, className = 'size-3.5' }: { transport: Transport; className?: string }) => {
  const Icon = TRANSPORT_ICONS[transport];
  return (
    <span title={TRANSPORT_LABELS[transport]} role="img" aria-label={TRANSPORT_LABELS[transport]} className="inline-flex shrink-0">
      <Icon className={className} aria-hidden />
    </span>
  );
};

/** Вид работ: иконка + подпись; цвет только вспомогательный. */
export const WorkTag = ({ order, compact = false }: { order: Order; compact?: boolean }) => {
  const wt = workType(order);
  const Icon = WORK_ICONS[wt];
  return (
    <span
      className="inline-flex items-center gap-1 text-caption font-medium whitespace-nowrap text-ink-2"
      title={compact ? WORK_TYPE_LABELS[wt] : undefined}
    >
      <Icon className="size-3.5 shrink-0" style={{ color: WORK_COLORS[wt] }} strokeWidth={2.25} aria-hidden />
      {compact ? <span className="sr-only">{WORK_TYPE_LABELS[wt]}</span> : WORK_TYPE_LABELS[wt]}
    </span>
  );
};

/** Навыки бригады значками (в плотных списках). Полные названия — в подсказке и для скринридера. */
export const SkillIcons = ({ skills }: { skills: Skill[] }) => {
  const list = SKILL_ORDER.filter((s) => skills.includes(s));
  const names = list.map((s) => SKILL_LABELS[s]).join(', ');
  return (
    <span className="inline-flex items-center gap-1" role="img" aria-label={`Навыки: ${names}`} title={names}>
      {list.map((s) => {
        const Icon = SKILL_ICONS[s];
        return <Icon key={s} className="size-3.5" style={{ color: SKILL_COLOR[s] }} strokeWidth={2.25} aria-hidden />;
      })}
    </span>
  );
};

/** Навыки полными названиями (профиль бригады). */
export const SkillChips = ({ skills }: { skills: Skill[] }) => (
  <span className="flex flex-wrap gap-1">
    {SKILL_ORDER.filter((s) => skills.includes(s)).map((s) => {
      const Icon = SKILL_ICONS[s];
      return (
        <span key={s} className="inline-flex items-center gap-1 rounded-md bg-hover px-1.5 py-0.5 text-caption text-ink-2">
          <Icon className="size-3.5" style={{ color: SKILL_COLOR[s] }} strokeWidth={2.25} aria-hidden />
          {SKILL_LABELS[s]}
        </span>
      );
    })}
  </span>
);

export const CrewSwatch = ({ color, size = 10 }: { color: string; size?: number }) => (
  <span
    className="inline-block shrink-0 rounded-[3px]"
    style={{ width: size, height: size, background: color }}
    aria-hidden
  />
);

/** Блок панели: подпись-«бровь» и содержимое. Разделитель снизу. */
export const PanelSection = ({
  title,
  aside,
  children,
  className,
}: {
  title?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) => (
  <section className={cx('border-b border-line px-4 py-3.5', className)}>
    {title && (
      <div className="mb-2 flex min-h-5 items-center justify-between gap-2">
        <h3 className="text-micro font-semibold tracking-[0.05em] text-ink-3 uppercase">{title}</h3>
        {aside && <span className="text-caption text-ink-3">{aside}</span>}
      </div>
    )}
    {children}
  </section>
);

export const Card = ({ children, className }: { children: ReactNode; className?: string }) => (
  <div className={cx('rounded-xl border border-line bg-surface shadow-card', className)}>{children}</div>
);

export const Spinner = ({ className = 'size-4' }: { className?: string }) => (
  <svg className={cx('animate-spin', className)} viewBox="0 0 24 24" fill="none" aria-hidden>
    <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
    <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
  </svg>
);

/** Горизонтальная шкала загрузки смены. */
export const LoadBar = ({ pct, color, className }: { pct: number; color: string; className?: string }) => (
  <span
    className={cx('relative block h-1.5 overflow-hidden rounded-full bg-hover', className ?? 'w-full')}
    role="img"
    aria-label={`Загрузка смены ${pct} %`}
    title={`Загрузка смены ${pct} %`}
  >
    <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.min(100, pct)}%`, background: color }} />
  </span>
);

/**
 * Изменение показателя: стрелка + число. good — направление, которое считается лучше.
 * С чем сравниваем — подписывается один раз рядом с группой чипов, а не в каждом.
 */
export const DeltaChip = ({
  value,
  digits = 0,
  good,
  suffix = '',
  size = 'sm',
}: {
  value: number;
  digits?: number;
  good: 'up' | 'down';
  suffix?: string;
  size?: 'sm' | 'md';
}) => {
  const zero = Math.abs(value) < (digits ? 0.05 : 0.5);
  const better = !zero && (good === 'up' ? value > 0 : value < 0);
  const Icon = zero ? Minus : value > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      title={zero ? 'без изменений' : better ? 'лучше' : 'хуже'}
      className={cx(
        'inline-flex items-center gap-0.5 rounded font-semibold whitespace-nowrap tabular',
        size === 'md' ? 'px-1.5 py-0.5 text-body' : 'px-1 text-caption',
        zero ? 'bg-hover text-ink-3' : better ? 'bg-ok-soft text-ok' : 'bg-bad-soft text-bad',
      )}
    >
      <Icon className={size === 'md' ? 'size-3.5' : 'size-3'} strokeWidth={2.5} aria-hidden />
      {signed(value, digits)}
      {suffix}
    </span>
  );
};

/** Сворачиваемая строка: заголовок-кнопка и содержимое по клику. */
export const Disclosure = ({
  summary,
  children,
  className,
}: {
  summary: ReactNode;
  children: ReactNode;
  className?: string;
}) => {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="flex min-h-7 w-full items-center gap-1.5 rounded-md px-1.5 text-left text-caption text-ink-2 hover:bg-sunken"
      >
        <ChevronRight className={cx('size-3.5 shrink-0 text-ink-3 transition-transform', open && 'rotate-90')} aria-hidden />
        {summary}
      </button>
      {open && (
        <div id={id} className="pt-1 pb-1.5 pl-6">
          {children}
        </div>
      )}
    </div>
  );
};

export type MenuItem = {
  label: ReactNode;
  icon?: LucideIcon;
  onSelect: () => void;
  disabled?: boolean;
  /** Для выбора одного из вариантов (регион): отмечается галочкой. */
  checked?: boolean;
  /** Приглушённый текст справа (число заявок и т. п.). */
  hint?: ReactNode;
};

export type MenuEntry = MenuItem | { separator: true } | { heading: string };

const ITEM_SELECTOR = '[role^="menuitem"]:not([disabled])';

/**
 * Выпадающее меню: Esc и клик мимо закрывают, стрелки ходят по пунктам, фокус возвращается на кнопку.
 * children — содержимое кнопки-триггера.
 */
export const Menu = ({
  label,
  children,
  items,
  align = 'end',
  width = 264,
  disabled,
  triggerClassName,
}: {
  label: string;
  children: ReactNode;
  items: MenuEntry[];
  align?: 'start' | 'end';
  width?: number;
  disabled?: boolean;
  triggerClassName?: string;
}) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey, true);
    const frame = requestAnimationFrame(() => listRef.current?.querySelector<HTMLElement>(ITEM_SELECTOR)?.focus());
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [open]);

  const onListKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End', 'Tab'].includes(e.key)) return;
    if (e.key === 'Tab') {
      setOpen(false);
      return;
    }
    e.preventDefault();
    const els = [...(listRef.current?.querySelectorAll<HTMLElement>(ITEM_SELECTOR) ?? [])];
    if (!els.length) return;
    const i = els.indexOf(document.activeElement as HTMLElement);
    const next =
      e.key === 'Home' ? 0 : e.key === 'End' ? els.length - 1 : e.key === 'ArrowDown' ? (i + 1) % els.length : (i - 1 + els.length) % els.length;
    els[next].focus();
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title={label}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={triggerClassName}
      >
        {children}
      </button>
      {open && (
        <div
          ref={listRef}
          role="menu"
          aria-label={label}
          onKeyDown={onListKey}
          style={{ width }}
          className={cx(
            'animate-pop absolute top-full z-[1200] mt-1.5 rounded-xl border border-line bg-surface p-1 shadow-[var(--shadow-pop)]',
            align === 'end' ? 'right-0' : 'left-0',
          )}
        >
          {items.map((it, i) => {
            if ('separator' in it) return <div key={`s${i}`} className="my-1 h-px bg-line" role="separator" />;
            if ('heading' in it)
              return (
                <div key={`h${i}`} className="px-2.5 pt-1.5 pb-1 text-micro font-semibold tracking-[0.05em] text-ink-3 uppercase">
                  {it.heading}
                </div>
              );
            const Icon = it.icon;
            const radio = it.checked !== undefined;
            return (
              <button
                key={i}
                type="button"
                role={radio ? 'menuitemradio' : 'menuitem'}
                aria-checked={radio ? it.checked : undefined}
                disabled={it.disabled}
                onClick={() => {
                  setOpen(false);
                  triggerRef.current?.focus();
                  it.onSelect();
                }}
                className="flex min-h-8 w-full items-center gap-2 rounded-lg px-2.5 text-left text-body text-ink hover:bg-hover focus-visible:bg-hover disabled:opacity-40"
              >
                {radio ? (
                  <Check className={cx('size-4 shrink-0', it.checked ? 'text-ink' : 'invisible')} strokeWidth={2.5} aria-hidden />
                ) : (
                  Icon && <Icon className="size-4 shrink-0 text-ink-3" aria-hidden />
                )}
                <span className="min-w-0 flex-1 truncate">{it.label}</span>
                {it.hint != null && <span className="shrink-0 text-caption text-ink-3 tabular">{it.hint}</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};
