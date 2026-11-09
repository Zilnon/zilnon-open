import { defineConfig } from 'astro/config';
export default defineConfig({
  output: 'static',
  outDir: '../dist',
  server: { host: '127.0.0.1', port: 5180 },
  vite: { server: { strictPort: true }, preview: { strictPort: true } },
});
