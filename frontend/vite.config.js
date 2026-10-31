import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In dev, /api and /health are proxied to FastAPI so the session cookie is same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
