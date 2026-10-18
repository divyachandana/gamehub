import { defineConfig } from 'vite';

// Dev server on :5174 proxies the game API to the San Francisco game server on :8002
// (Inference Rally's dev server uses :5173 -> :8001/8000).
// `npm run build` emits frontend/dist, which the Python server also serves directly.
export default defineConfig({
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8002', changeOrigin: true },
    },
  },
  build: {
    target: 'es2022',
    chunkSizeWarningLimit: 1200,
  },
});
