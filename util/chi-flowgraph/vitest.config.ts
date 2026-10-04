import { defineConfig } from 'vitest/config';
export default defineConfig({test:{include:['tests/web/**/*.test.ts'],environment:'node',pool:'threads',maxWorkers:1}});
