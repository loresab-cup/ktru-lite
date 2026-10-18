import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development /api goes to the backend container; in Docker nginx does the same.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': process.env.API_PROXY_URL || 'http://localhost:8000' } },
  // antd is one ~900 kB chunk; it is cached by nginx, splitting gives nothing here
  build: { chunkSizeWarningLimit: 1200 },
})
