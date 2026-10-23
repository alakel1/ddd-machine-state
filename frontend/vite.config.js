import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: { port: 5173, proxy: { '/api': process.env.API_TARGET || 'http://127.0.0.1:8000' } },
  build: { chunkSizeWarningLimit: 3000 },
})
