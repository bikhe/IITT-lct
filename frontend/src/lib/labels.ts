import type {
  ChangeStatus,
  EventType,
  JobStatus,
  Order,
  ReasonCode,
  Skill,
  Transport,
  WorkType,
} from '../types';

export const SKILL_LABELS: Record<Skill, string> = {
  local: 'Локальные работы',
  connection: 'Подключение и дозаказы',
  emergency: 'Аварийные работы',
};

export const SKILL_ORDER: Skill[] = ['connection', 'local', 'emergency'];

export const TRANSPORT_LABELS: Record<Transport, string> = {
  car: 'Автомобиль',
  foot: 'Пешком',
  bike: 'Велосипед',
  transit: 'Общественный транспорт',
};

export const TRANSPORT_SHORT: Record<Transport, string> = {
  car: 'Авто',
  foot: 'Пешком',
  bike: 'Вело',
  transit: 'ОТ',
};

export const WORK_TYPE_LABELS: Record<WorkType, string> = {
  emergency: 'Авария',
  connection: 'Подключение',
  local: 'Ремонт',
  addon: 'Дозаказ',
};

export const WORK_TYPE_ORDER: WorkType[] = ['emergency', 'connection', 'local', 'addon'];

/** Чистое время работ по нормативу (норматив минус 20 мин брони на дорогу). */
export const WORK_TYPE_MINUTES: Record<WorkType, number> = {
  emergency: 80,
  connection: 70,
  local: 30,
  addon: 20,
};

export const WORK_TYPE_SKILL: Record<WorkType, Skill> = {
  emergency: 'emergency',
  connection: 'connection',
  local: 'local',
  addon: 'connection',
};

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  planned: 'Запланирована',
  en_route: 'В пути',
  in_progress: 'В работе',
  done: 'Выполнена',
  cancelled: 'Отменена на месте',
};

export const CHANGE_LABELS: Record<ChangeStatus, string> = {
  added: 'Новая',
  assigned: 'Назначена',
  reassigned: 'Перенесена',
  shifted: 'Сдвинута',
  unassigned: 'Снята',
  cancelled: 'Отменена',
};

export const EVENT_LABELS: Record<EventType, string> = {
  urgent_order: 'Авария',
  new_order: 'Новая заявка',
  cancel_order: 'Отмена заявки',
  engineer_unavailable: 'Сход бригады',
  manual_assign: 'Ручное назначение',
};

export const REASON_LABELS: Record<ReasonCode, string> = {
  skill: 'Нет навыка',
  transport: 'Другой транспорт',
  shift_window: 'Смена не покрывает окно',
  unavailable: 'Сошла с линии',
  busy: 'Маршрут занят',
  window_late: 'Не успевает в окно',
  shift_end: 'Не успевает до конца смены',
  window_passed: 'Окно прошло',
};

/** Короткая причина неназначения для строки списка; полная фраза — в карточке заявки. */
export const UNASSIGNED_SHORT: Record<ReasonCode, string> = {
  busy: 'все подходящие заняты',
  shift_window: 'смены не покрывают окно',
  transport: 'нет нужного транспорта',
  skill: 'нет навыка',
  unavailable: 'бригады сошли с линии',
  window_late: 'не успеть к окну',
  shift_end: 'не успеть до конца смены',
  window_passed: 'окно прошло',
};

/** Авария с окном до конца дня: начать как можно раньше. */
export const isAllDayEmergency = (order: Order): boolean =>
  workType(order) === 'emergency' && order.window.end_min >= 23 * 60 + 59;

/** Поступила ночью, до начала смен (в данных окно 00:01–23:59). */
export const arrivedBeforeDay = (order: Order): boolean => order.window.start_min < 6 * 60;

/** Окно заявки одной строкой; у аварии на весь день — время поступления или «срочно», если она ждёт с ночи. */
export const windowLabel = (order: Order): string => {
  if (!isAllDayEmergency(order)) return `${order.window.start}–${order.window.end}`;
  return arrivedBeforeDay(order) ? 'срочно' : `с ${order.window.start}`;
};

/** Причина неназначения без окна заявки — окно и так показано рядом. */
export const reasonWithoutWindow = (order: Order, reason: string): string => {
  const w = `${order.window.start}–${order.window.end}`;
  return reason.replace(`в окно ${w}`, 'в это время').replace(`окно ${w}`, 'окно');
};

/** Адрес без повторяющегося «Город Москва, г. Москва». */
export const shortAddress = (address: string): string => address.replace(/^(Город Москва|г\.\s*Москва),\s*/i, '');

export const workType = (order: Order): WorkType => {
  if (order.work_type) return order.work_type;
  if (order.priority === 'urgent' || order.skills.includes('emergency')) return 'emergency';
  if (order.skills.includes('connection')) return order.duration_min <= 20 ? 'addon' : 'connection';
  return 'local';
};

/** «Бригада Иванов» — единый формат имени, даже если в данных нет префикса. */
export const crewName = (name: string): string =>
  name.toLowerCase().startsWith('бригада') ? name : `Бригада ${name}`;

/** Фамилия без слова «Бригада» — для узких мест (Гант, подписи на карте). */
export const crewShort = (name: string): string => name.replace(/^бригада\s+/i, '');
