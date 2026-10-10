import type {
  Catalog,
  FeedbackProposal,
  PlanEnvelope,
  PlanningResult,
  RecipeGraph,
  RuntimeSession,
} from './types';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly requestId: string,
    readonly status: number,
  ) {
    super(message);
  }
}
export interface RequestTiming {
  client_elapsed_ms: number;
  server_elapsed_ms: number | null;
  request_id: string;
}
export async function request<T>(
  path: string,
  init?: RequestInit,
  onTiming?: (timing: RequestTiming) => void,
): Promise<T> {
  const began = performance.now();
  let serverElapsed: number | null = null;
  let requestId = '';
  const readOnly = !init?.method || init.method.toUpperCase() === 'GET';
  try {
    for (let attempt = 0; ; attempt++) {
      try {
        return await requestOnce<T>(path, init, (response) => {
          const duration = /(?:^|,)\s*application;dur=([\d.]+)/.exec(
            response.headers.get('Server-Timing') ?? '',
          );
          serverElapsed =
            duration && Number.isFinite(Number(duration[1])) ? Number(duration[1]) : null;
          requestId = response.headers.get('X-Request-ID') ?? '';
        });
      } catch (error) {
        const temporary =
          error instanceof ApiError &&
          (error.status === 0 ||
            [502, 503, 504].includes(error.status) ||
            (error.status === 200 && error.code === 'INVALID_RESPONSE'));
        // 只重试无副作用的读取；写请求保留原身份由使用者查询确认。
        if (!readOnly || !temporary || attempt >= 2 || init?.signal?.aborted) throw error;
        await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
      }
    }
  } finally {
    onTiming?.({
      client_elapsed_ms: Math.round(performance.now() - began),
      server_elapsed_ms: serverElapsed,
      request_id: requestId,
    });
  }
}
async function requestOnce<T>(
  path: string,
  init: RequestInit | undefined,
  onResponse: (response: Response) => void,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError('无法连接烹饪服务，请检查连接后重试。', 'NETWORK_ERROR', '', 0);
  }
  onResponse(response);
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    const unavailable = [502, 503, 504].includes(response.status);
    throw new ApiError(
      unavailable
        ? `服务暂时不可用（HTTP ${response.status}），请稍后重试。`
        : `服务返回了无法解析的内容（HTTP ${response.status}），请刷新状态。`,
      unavailable ? 'HTTP_UNAVAILABLE' : 'INVALID_RESPONSE',
      response.headers.get('X-Request-ID') ?? '',
      response.status,
    );
  }
  if (!response.ok) {
    const result = data as { status?: string; event?: { rejection_reason?: string } };
    if (result.status === 'EVENT_REJECTED' && result.event?.rejection_reason) return data as T;
    const error = data as { error?: { code?: string; message?: string; request_id?: string } };
    throw new ApiError(
      error.error?.message ?? '请求未完成，请查看当前状态。',
      error.error?.code ?? 'HTTP_ERROR',
      response.headers.get('X-Request-ID') ?? error.error?.request_id ?? '',
      response.status,
    );
  }
  return data as T; // 读写边界与后端 Pydantic DTO 对应，不在 UI 生成计划。
}
export const post = <T>(
  path: string,
  body: object,
  headers?: Record<string, string>,
  onTiming?: (timing: RequestTiming) => void,
) =>
  request<T>(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify(body),
    },
    onTiming,
  );
export const catalog = () => request<Catalog>('/api/v1/recipes');
export const session = (sid: string) =>
  request<RuntimeSession>(`/api/v1/sessions/${encodeURIComponent(sid)}?view=workbench`);
export const plan = (sid: string, version: number) =>
  request<PlanEnvelope>(
    `/api/v1/sessions/${encodeURIComponent(sid)}/plans/${version}?view=workbench`,
  );
export const feedback = (sid: string, task: string, completed: boolean) =>
  request<FeedbackProposal>(
    `/api/v1/sessions/${encodeURIComponent(sid)}/operations/${encodeURIComponent(task)}/feedback?completed=${completed}`,
  );
export const graph = (recipe: string, sid?: string) =>
  request<RecipeGraph>(
    `/api/v1/recipes/${encodeURIComponent(recipe)}/graph${sid ? `?session_id=${encodeURIComponent(sid)}` : ''}`,
  );
export const submitEvent = (sid: string, body: object) =>
  post<PlanningResult>(`/api/v1/sessions/${encodeURIComponent(sid)}/events`, body);
