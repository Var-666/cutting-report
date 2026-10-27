import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import UnoCSS from 'unocss/vite'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [UnoCSS(), vue()],
  define: { BUILD_TIME: JSON.stringify(new Date().toISOString()) },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@sa/color': fileURLToPath(new URL('./src/vendor/soybean-admin/packages/color/index.ts', import.meta.url)),
      '@sa/hooks': fileURLToPath(new URL('./src/vendor/soybean-admin/hooks/index.ts', import.meta.url)),
      '@sa/materials': fileURLToPath(new URL('./src/vendor/soybean-admin/materials/index.ts', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/reports': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})

