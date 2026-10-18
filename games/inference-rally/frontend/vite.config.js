import { defineConfig } from 'vite';

// Dev server on :5173 proxies the game API to the Python server on :8000.
// `npm run build` emits frontend/dist, which the Python server also serves directly.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    target: 'es2022',
    chunkSizeWarningLimit: 1200,
  },
});
