import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
        configure: (proxy) => {
          // Suppress benign EPIPE / ECONNRESET errors that fire when a browser
          // tab closes while a WebSocket connection is still open — these are
          // normal and not indicative of a real problem.
          proxy.on('error', (err: NodeJS.ErrnoException, _req, _res) => {
            if (err.code === 'EPIPE' || err.code === 'ECONNRESET') return;
            console.error('[vite proxy]', err.message);
          });
        },
      },
    },
  },
  build: {
    target: 'es2022'
  },
  define: {
    'globalThis.__DEV__': JSON.stringify(true)
  }
})