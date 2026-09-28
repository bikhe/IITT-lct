export const toMinutes = (hhmm: string): number => {
  const [h, m] = hhmm.split(':').map((x) => parseInt(x, 10));
  return (h || 0) * 60 + (m || 0);
};

export const toHHMM = (minutes: number): string => {
  const h = Math.floor(minutes / 60) % 24;
  const m = Math.round(minutes % 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
};

export const plural = (n: number, one: string, few: string, many: string): string => {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (b === 1 && a !== 11) return one;
  if (b >= 2 && b <= 4 && (a < 12 || a > 14)) return few;
  return many;
};

export const countWord = (n: number, one: string, few: string, many: string): string =>
  `${n} ${plural(n, one, few, many)}`;

const nf1 = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

export const km = (value: number): string => `${nf1.format(value)} км`;
export const num1 = (value: number): string => nf1.format(value);
export const num0 = (value: number): string => nf0.format(value);

/** Знаковое число: «+3», «−2», «0». Минус — типографский. */
export const signed = (value: number, digits = 0): string => {
  const abs = digits > 0 ? nf1.format(Math.abs(value)) : nf0.format(Math.abs(value));
  if (Math.abs(value) < (digits > 0 ? 0.05 : 0.5)) return digits > 0 ? '0,0' : '0';
  return `${value > 0 ? '+' : '−'}${abs}`;
};

export const duration = (minutes: number): string => {
  const m = Math.round(minutes);
  if (m < 60) return `${m} мин`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${h} ч ${rest} мин` : `${h} ч`;
};
