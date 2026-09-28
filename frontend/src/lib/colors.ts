import type { Engineer, WorkType } from '../types';

/**
 * Цвет идентичности бригады. Порядок фиксирован и проверен валидатором палитры
 * (соседние пары различимы при дейтеро- и протанопии). Цвет закрепляется за бригадой
 * по её порядку в наборе данных, а не по рангу, — после события цвета не «перекрашиваются».
 * На карте цвет всегда дублируется номером визита и подписью бригады во всплывающей подсказке.
 */
export const CREW_PALETTE = [
  '#2a78d6',
  '#eb6834',
  '#1baf7a',
  '#4a3aa7',
  '#e87ba4',
  '#008300',
  '#eda100',
  '#c0266d',
  '#0891b2',
  '#a14a1a',
  '#7c3aed',
  '#5b7a00',
  '#184f95',
  '#b45309',
];

export const crewColors = (engineers: Engineer[]): Record<string, string> => {
  const out: Record<string, string> = {};
  engineers.forEach((e, i) => {
    out[e.id] = CREW_PALETTE[i % CREW_PALETTE.length];
  });
  return out;
};

/** Тёмный или белый текст поверх цветной заливки (по относительной яркости). */
export const inkOn = (hex: string): string => {
  const n = parseInt(hex.slice(1), 16);
  const lin = (c: number) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const l = 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
  return l > 0.36 ? '#0f1115' : '#ffffff';
};

export const WORK_COLORS: Record<WorkType, string> = {
  emergency: 'var(--color-work-emergency)',
  connection: 'var(--color-work-connection)',
  local: 'var(--color-work-local)',
  addon: 'var(--color-work-addon)',
};

export const WORK_HEX: Record<WorkType, string> = {
  emergency: '#d03b3b',
  connection: '#2a78d6',
  local: '#0e9f6e',
  addon: '#7c3aed',
};

export const CHANGE_HEX = {
  added: '#0f7a3a',
  assigned: '#0f7a3a',
  reassigned: '#2361c7',
  shifted: '#a35f00',
  unassigned: '#c62f2f',
  cancelled: '#7c8390',
} as const;
