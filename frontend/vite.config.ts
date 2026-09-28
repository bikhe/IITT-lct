import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  build: {
    // React + Leaflet одним файлом ~150 КБ gzip — для локального прототипа приемлемо
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // порт бэкенда для dev-сервера: API_PORT=8010 npm run dev
        target: `http://127.0.0.1:${process.env.API_PORT ?? 8000}`,
        changeOrigin: true,
      },
    },
  },
})
