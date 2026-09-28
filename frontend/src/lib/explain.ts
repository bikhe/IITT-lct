/**
 * Объяснение приходит с сервера текстом (единый источник формулировок — ExplanationGenerator).
 * Здесь оно раскладывается по строкам-фактам, чтобы показать ограничения списком с отметками.
 */

export type FactKind = 'skill' | 'transport' | 'time' | 'route' | 'sla' | 'competition' | 'frozen' | 'reason' | 'hint' | 'note';

export interface Fact {
  kind: FactKind;
  label: string;
  text: string;
}

const PREFIXES: Array<[RegExp, FactKind, string]> = [
  [/^Квалификация:\s*/, 'skill', 'Квалификация'],
  [/^Транспорт:\s*/, 'transport', 'Транспорт'],
  [/^Время:\s*/, 'time', 'Время'],
  [/^Маршрут:\s*/, 'route', 'Маршрут'],
  [/^Реакция на аварию:\s*/, 'sla', 'Реакция на аварию'],
  [/^Подходят по навыку и транспорту:\s*/, 'competition', 'По навыку и транспорту подходят'],
  [/^На \d{2}:\d{2}:\s*/, 'frozen', 'Статус'],
  [/^Причина:\s*/, 'reason', 'Причина'],
  [/^Что можно сделать:\s*/, 'hint', 'Что можно сделать'],
];

export const parseExplanation = (text: string | undefined): { title: string; facts: Fact[] } => {
  if (!text) return { title: '', facts: [] };
  const [title, ...lines] = text.split('\n').map((l) => l.trim()).filter(Boolean);
  const facts = lines.map((line): Fact => {
    for (const [re, kind, label] of PREFIXES) {
      if (re.test(line)) {
        const body = line.replace(re, '');
        return { kind, label, text: body ? `${body[0].toUpperCase()}${body.slice(1)}` : body };
      }
    }
    return { kind: 'note', label: '', text: line };
  });
  return { title, facts };
};
