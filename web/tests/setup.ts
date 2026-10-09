import { afterEach, vi } from 'vitest';
import { enableAutoUnmount } from '@vue/test-utils';
enableAutoUnmount(afterEach);
class ObservedSize {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ObservedSize);
afterEach(() => {
  vi.clearAllMocks();
});
