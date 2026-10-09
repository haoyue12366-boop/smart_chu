import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000' },
      '/health': { target: 'http://127.0.0.1:8000' },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/unit/**/*.test.ts'],
    setupFiles: ['tests/setup.ts'],
    reporters: ['default', 'junit'],
    outputFile: { junit: '../data/verification/P5-web-unit.xml' },
  },
});
