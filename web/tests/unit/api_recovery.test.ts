// 合成 HTTP 响应仅控制网络故障；执行真实客户端解析与恢复逻辑。
import { afterEach, expect, it, vi } from 'vitest';
import { post, request } from '../../src/api/client';

it('records client elapsed time separately from server time and keeps the request identity', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', async () => {
    await new Promise((resolve) => setTimeout(resolve, 40));
    return Response.json(
      { status: 'PUBLISHED' },
      {
        headers: { 'Server-Timing': 'application;dur=25.5', 'X-Request-ID': 'timed-write' },
      },
    );
  });
  const observed = vi.fn();
  const result = post('/api/v1/sessions', { event_id: 'timed-write' }, undefined, observed);
  await vi.advanceTimersByTimeAsync(40);
  await expect(result).resolves.toEqual({ status: 'PUBLISHED' });
  expect(observed).toHaveBeenCalledOnce();
  expect(observed).toHaveBeenCalledWith({
    client_elapsed_ms: 40,
    server_elapsed_ms: 25.5,
    request_id: 'timed-write',
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('recovers a read after a temporary HTML gateway response', async () => {
  vi.useFakeTimers();
  let attempt = 0;
  vi.stubGlobal('fetch', async () =>
    ++attempt === 1
      ? new Response('<html>temporarily unavailable</html>', { status: 502 })
      : Response.json({ name: '菜谱工艺', operations: ['预热'] }),
  );
  const result = request('/api/v1/recipes/example/graph');
  const assertion = expect(result).resolves.toEqual({ name: '菜谱工艺', operations: ['预热'] });
  await Promise.all([assertion, vi.runAllTimersAsync()]);
});

it('recovers a read after a temporary database lock response', async () => {
  vi.useFakeTimers();
  let attempt = 0;
  vi.stubGlobal('fetch', async () =>
    ++attempt === 1
      ? Response.json(
          { error: { code: 'SERVICE_NOT_READY', message: '状态库暂不可用' } },
          { status: 503 },
        )
      : Response.json({ runtime: { current_plan_version: 2 } }),
  );
  const result = request('/api/v1/sessions/example');
  const assertion = expect(result).resolves.toEqual({ runtime: { current_plan_version: 2 } });
  await Promise.all([assertion, vi.runAllTimersAsync()]);
});

it('reports a failed write without automatically sending it again', async () => {
  let effects = 0;
  vi.stubGlobal('fetch', async () => {
    effects++;
    return new Response('<html>gateway timeout</html>', { status: 504 });
  });
  await expect(post('/api/v1/sessions', { event_id: 'same-write' })).rejects.toMatchObject({
    code: 'HTTP_UNAVAILABLE',
    status: 504,
  });
  expect(effects).toBe(1);
});

it('stops read recovery after a bounded number of failures', async () => {
  vi.useFakeTimers();
  let attempts = 0;
  vi.stubGlobal('fetch', async () => {
    attempts++;
    return new Response('<html>unavailable</html>', {
      status: 503,
      headers: { 'X-Request-ID': 'trace-id' },
    });
  });
  const result = request('/api/v1/recipes');
  const assertion = expect(result).rejects.toMatchObject({
    code: 'HTTP_UNAVAILABLE',
    status: 503,
    requestId: 'trace-id',
  });
  await Promise.all([assertion, vi.runAllTimersAsync()]);
  expect(attempts).toBe(3);
});
