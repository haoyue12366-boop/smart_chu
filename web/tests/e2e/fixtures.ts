import { expect, test as base } from '@playwright/test';

// 共用服务但每个用例独立桌次；结束旧会话，避免推进测试时钟影响其他用例。
export const test = base.extend<{ isolatedSessions: void }>({
  isolatedSessions: [
    async ({ request }, use) => {
      const before = await request.post('/__test/sessions/end');
      expect(before.status()).toBe(200);
      await use();
      const after = await request.post('/__test/sessions/end');
      expect(after.status()).toBe(200);
    },
    { auto: true },
  ],
});

export { expect } from '@playwright/test';
