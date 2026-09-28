import type {
  AlternativesResponse,
  DatasetMeta,
  DatasetResponse,
  EventResponse,
  ReplanEvent,
  ScenarioItem,
  SolveResponse,
} from './types';

export class ApiError extends Error {}

/** FastAPI отдаёт detail строкой (наши ошибки) или списком (ошибки валидации полей). */
const describeError = (body: unknown, status: number): string => {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { loc?: Array<string | number>; msg?: string };
    const where = (first.loc ?? []).filter((x) => x !== 'body').join(' → ');
    return `Данные не прошли проверку: ${where ? `${where}: ` : ''}${first.msg ?? 'ошибка'}${detail.length > 1 ? ` (и ещё ${detail.length - 1})` : ''}`;
  }
  return `Ошибка сервера (${status})`;
};

const request = async <T>(url: string, init?: RequestInit): Promise<T> => {
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    });
  } catch {
    throw new ApiError('Сервер недоступен. Проверьте, что бэкенд запущен.');
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(describeError(body, res.status));
  }
  return body as T;
};

export const api = {
  datasets: () => request<DatasetMeta[]>('/api/datasets'),
  upload: (payload: { name: string; orders: unknown[]; engineers: unknown[] }) =>
    request<DatasetMeta>('/api/datasets/upload', { method: 'POST', body: JSON.stringify(payload) }),
  dataset: (region: string) => request<DatasetResponse>(`/api/dataset/${region}`),
  plan: (region: string) => request<SolveResponse>(`/api/plan/${region}`),
  solve: (region: string) =>
    request<SolveResponse>('/api/plan/solve', {
      method: 'POST',
      body: JSON.stringify({ region, use_local_search: true }),
    }),
  reset: (region: string) => request<SolveResponse>(`/api/plan/reset/${region}`, { method: 'POST' }),
  event: (region: string, event: ReplanEvent) =>
    request<EventResponse>('/api/plan/event', { method: 'POST', body: JSON.stringify({ region, event }) }),
  scenarios: (region: string) => request<ScenarioItem[]>(`/api/scenarios/${region}`),
  alternatives: (region: string, orderId: string) =>
    request<AlternativesResponse>(`/api/alternatives/${region}/${encodeURIComponent(orderId)}`),
};
