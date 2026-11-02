import { defineConfig } from 'vite'
import base from './vite.config'
export default defineConfig({
  ...base,
  plugins: [...base.plugins, { name: 'local-welding-preview', transformIndexHtml: (html: string) => html.replace('/src/main.ts', '/.welding-project-preview.ts') }],
  server: { ...base.server, host: '127.0.0.1', port: 5184, strictPort: true },
})
